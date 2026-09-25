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
 * É um "partial": gerencia só os serviços declarados aqui (web e worker). O
 * PostgreSQL e as variáveis de ambiente continuam como estão no painel da
 * Railway; este arquivo não os cria, não os altera e não os apaga.
 */
import { defineRailway, project, service } from "railway/iac";

export const partial = "relatorio-atendimentos";

// Uma imagem para os dois serviços (Dockerfile na raiz); muda só o comando de início.
const build = { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" } as const;
const migracoes = "alembic upgrade head";

export default defineRailway((ctx) => {
  // Admin, monitor ao vivo e /health. O deploy espera por /health/live, que não
  // depende do worker.
  const web = service("web", {
    build,
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
    build,
    start: "python -m relatorio.infrastructure.scheduler",
    preDeploy: migracoes,
    replicas: 1,
    deploy: { restartPolicyType: "ALWAYS" },
  });

  return project(ctx.projectName ?? "RelatórioAtendimento", {
    resources: [web, worker],
  });
});
