/**
 * Infraestrutura na Railway como código (Infrastructure as Code).
 *
 * Substitui o railway.json e o railway.worker.json (Config as Code), que a Railway
 * deixa de ler em 01/12/2026.
 *
 * Diferente do railway.json, este arquivo NÃO é lido no deploy. Ele é aplicado
 * com a CLI da Railway:
 *
 *   npm install              # SDK "railway" (package.json na raiz)
 *   railway config plan      # mostra o que mudaria, sem mudar nada
 *   railway config apply     # aplica, depois de confirmar
 *
 * É um "partial": gerencia só os serviços declarados aqui (web e worker); o
 * PostgreSQL fica de fora e não é tocado.
 *
 * Atenção: um serviço declarado aqui tem TODAS as variáveis gerenciadas por este
 * arquivo. Variável que não estiver em VARIAVEIS é APAGADA no próximo apply.
 * Os valores ficam no painel da Railway: preserve() mantém o que já está lá, sem
 * escrever segredo no código. Criou uma variável nova no painel? Acrescente o
 * nome em VARIAVEIS antes de rodar o próximo apply.
 */
import { defineRailway, github, preserve, project, service } from "railway/iac";

export const partial = "relatorio-atendimentos";

// Deploy a cada push na main (branch configurado no ambiente), esperando o CI.
const source = github("lucascarper/RelatorioAtendimentoMultilife", { checkSuites: true });

// Uma imagem para os dois serviços (Dockerfile na raiz); muda só o comando de início.
const build = { builder: "DOCKERFILE", dockerfilePath: "/Dockerfile" } as const;
const migracoes = "alembic upgrade head";

// Variáveis dos dois serviços (ver .env.example). Valores só no painel.
const VARIAVEIS = [
  "ADMIN_PASSWORD_HASH",
  "ADMIN_URL",
  "ADMIN_USER",
  "APP_ENV",
  "COLETOR_HABILITADO",
  "DATABASE_URL",
  "EMAIL_ALERTA_TECNICO",
  "EMAIL_BACKEND",
  "EMAIL_DESTINATARIOS_OVERRIDE",
  "EMAIL_FROM",
  "LOG_LEVEL",
  "SECRET_KEY",
  "SGG_API_KEY",
  "SGG_BASE_URL",
  "SGG_MAX_RPM",
  "SMTP_HOST",
  "SMTP_PASSWORD",
  "SMTP_PORT",
  "SMTP_USER",
  "TZ",
] as const;
const env = () => Object.fromEntries(VARIAVEIS.map((nome) => [nome, preserve()]));

export default defineRailway((ctx) => {
  // Admin, monitor ao vivo e /health. O deploy espera por /health/live, que não
  // depende do worker.
  const web = service("web", {
    source,
    build,
    env: env(),
    start:
      "sh -c \"exec uvicorn relatorio.interfaces.web.app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'\"",
    preDeploy: migracoes,
    healthcheck: "/health/live",
    healthcheckTimeout: 60,
    replicas: 1,
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 5 },
  });

  // Coleta do SGG, consolidação e envio do e-mail (APScheduler). Uma réplica fixa,
  // sempre ligada: duas réplicas não duplicam trabalho (advisory lock), mas também
  // não ajudam.
  const worker = service("worker", {
    source,
    build,
    env: env(),
    start: "python -m relatorio.infrastructure.scheduler",
    preDeploy: migracoes,
    replicas: 1,
    deploy: { restartPolicyType: "ALWAYS" },
  });

  return project(ctx.projectName ?? "RelatórioAtendimento", {
    resources: [web, worker],
  });
});
