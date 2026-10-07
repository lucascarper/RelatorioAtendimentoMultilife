# Relatório Diário de Atendimentos · MultiLife

Todos os dias às **07:59**, a gestão da MultiLife recebe por e-mail o resumo operacional do dia anterior: atendimentos por turno, faltas, espera na recepção, tempo de atendimento por agenda e **TMA por consultório**. Os números vêm da API REST do SGG (v3), sem digitação manual.

<p align="center">
  <img src="docs/img/email-desktop.png" alt="Prévia do e-mail diário (dados fictícios)" width="560">
  &nbsp;
  <img src="docs/img/email-mobile.png" alt="O mesmo e-mail no celular" width="220">
</p>

> As imagens usam **dados fictícios** gerados pelo simulador do projeto (`relatorio previa --simulado`).

## Como funciona

A API do SGG devolve só o **status atual** de cada agendamento. Por isso o sistema faz polling durante o expediente e grava cada mudança de status com o horário observado. À noite, só consolida esses eventos ([ADR 0001](docs/adr/0001-polling-durante-o-expediente.md)).

```mermaid
flowchart LR
    SGG[API SGG v3] -->|GET /agendamento a cada 5 s| COL[Coletor<br/>compartilhado com o painel]
    SGG -->|GET /agenda 05:30| SYNC[Sync de agendas]
    COL --> DB[(PostgreSQL<br/>agendamento_evento)]
    SYNC --> DB
    DB --> CONS[Consolidação 23:00<br/>+ conferência de faltas]
    CONS --> RES[(resumo_diario)]
    RES --> MAIL[Envio 07:59]
    MAIL --> SMTP[KingHost SMTP]
    ADM[Admin web] --> DB
    DB -.->|futuro| RT[Consulta em tempo real]
```

| Horário | Job | Se falhar |
| --- | --- | --- |
| 05:30 | `sync_agendas`: cadastro de agendas/consultórios | usa o cadastro anterior |
| 06:00–18:00, a cada 5 s (`COLETA_INTERVALO_S`) | `coletar_ciclo`: polling incremental com cursor no banco | o próximo ciclo recupera (2 min de sobreposição); alerta após 10 min falhando |
| 06:00–18:00, a cada 5 min | `varrer_dia`: varredura do dia inteiro; fecha quem sumiu do dia no SGG (remarcado, excluído ou com situação desconhecida) | a próxima varredura recupera |
| 18:30 | `reconciliar_dia`: varredura do dia inteiro | 19:00 e 22:00 |
| 23:00 | `consolidar_dia`: métricas + conferência final no SGG (RF11) | 02:00 e 05:00; na 3ª falha, alerta técnico |
| 07:59 | `enviar_relatorio`: e-mail do dia anterior | 08:01 e 08:03; depois, alerta técnico |
| 08:10 | `verificar_envio`: rede de segurança | alerta se não foi entregue |
| Dia 1, 03:00 | `limpar_retencao`: apaga o que tiver mais de 24 meses | dia 2 |
| 03:20 | `compactar_execucoes`: deixa 1 ciclo de coleta bem-sucedido por minuto nos dias anteriores | dia seguinte |
| 05:45 | `consolidar_financeiro`: lê os endpoints financeiros e grava `resumo_financeiro` (só com `FINANCEIRO_HABILITADO`) | 06:30 e 07:15; na 3ª falha, alerta técnico |
| 07:59 | `enviar_financeiro`: e-mail financeiro do dia anterior, lista própria | 08:01 e 08:03; verificação às 08:10 |
| 02:00 | `consolidar_sesmt`: lê empresas, contratos, programas/laudos e eventos do eSocial e grava `resumo_sesmt` (só com `SESMT_HABILITADO`; leva mais de 1 h no limite de requisições) | 03:30 e 04:30; na 3ª falha, alerta técnico |
| 08:00 | `enviar_sesmt`: e-mail de gestão do SESMT, lista própria | 08:02 e 08:04; verificação às 08:12 |
| 23:20 | `processar_exames`: exames clínicos do dia dos médicos escolhidos (atendimentos por médico) | 02:20 e 05:20; o envio das 07:59 tenta de novo |

Todos os jobs usam `max_instances=1` e `coalesce=True`, com **advisory lock** do PostgreSQL (dois containers nunca rodam o mesmo job), e cada execução fica registrada em `execucao_job`.

### Métricas (seção 4 da documentação técnica)

- **Tempo de Espera** = "Em Atendimento" − "Aguardando". **Tempo de Atendimento** (ou de Consulta, no consultório) = "Atendido" − "Em Atendimento".
- **Tempo Médio Total de Permanência** (com guichês marcados): soma das quatro médias (espera e atendimento na recepção, espera e consulta no consultório). O SGG não liga o agendamento do guichê ao do consultório da mesma pessoa, então é a soma das médias de cada etapa, não uma medida pessoa a pessoa.
- **Turno pela hora agendada**: é da tarde quando a hora agendada for 13:00 ou depois (configurável).
- **Atípicos** (menos de 1 min ou mais de 180 min) entram nos totais, mas ficam fora das médias e do TMA e aparecem nos alertas.
- **Salto de status** (ex.: Aguardando → Atendido): o atendimento conta nos totais, mas fica "sem tempo medido".
- **Sem baixa**: terminou o dia em Agendado ou Aguardando. Antes de contar, o sistema confere no SGG se há falta registrada (RF11).
- O cálculo é versionado (`versao_regra` no resumo). Mudou uma regra? Suba a versão e reprocesse.

Detalhes e ajustes de interpretação: [ADR 0005](docs/adr/0005-conferencia-final-antes-da-consolidacao.md), [ADR 0006](docs/adr/0006-interpretacoes-das-regras.md) e [ADR 0012](docs/adr/0012-tempo-medio-total-de-permanencia.md) (rótulos e Tempo Médio Total de Permanência).

### O e-mail (boas práticas de BI)

Leitura em até 1 minuto, em pirâmide de atenção:

1. **Topo:** manchete em uma frase ("102 atendimentos e 11 faltas… espera média 15 min, +93,7% sobre a semana anterior") e selo de alertas.
2. **KPIs:** atendimentos, faltas (% dos agendados), espera média e TMA, cada um com variação contra o mesmo dia da semana anterior. A seta sempre vem com o valor e o rótulo, nunca só a cor. Verde e vermelho aparecem só quando subir é claramente bom ou ruim; o TMA é neutro. Taxas variam em pontos percentuais (p.p.).
3. **Meio (evidência):** tabela por turno (com os **guichês** separados das demais agendas, quando houver agendas marcadas como guichê em *Atendimento → Configurar Agendas*), TMA por consultório e tempos por agenda (qtd., média, mediana, maior), **um bloco por turno**, porque na troca de turno troca o médico. As barras de TMA usam uma única cor, a azul da marca, e a mesma escala nos dois turnos.
4. **Base:** comparativo semanal completo, alertas por severidade (ícone + rótulo + cor) e rodapé com a versão da regra e o link do admin.

Tecnicamente: layout em tabelas compatível com Outlook e Gmail, CSS inline (premailer), logo anexada via CID (Outlook não bloqueia), responsivo (KPIs em 2×2 no celular), versão em texto puro e contraste WCAG AA (texto ≥ 4,5:1). O template recebe só o JSON de `resumo_diario.metricas`, e nenhuma regra de cálculo fica nele.

### Relatório financeiro diário

Um segundo e-mail às 07:59, para financeiro e diretoria, com lista de destinatários própria (admin → **Financeiro**). Os dados vêm dos endpoints financeiros do SGG (`contasReceber`, `contasPagar`, `contratoCliente`, `fornecedor-valores`):

1. **Fluxo de caixa:** receita recebida, despesas pagas e saldo operacional, no dia e no mês.
2. **Projeção de entradas e saídas:** títulos em aberto (a receber e a pagar) previstos para 7, 15 e 30 dias, mais uma coluna até o fim do mês atual.
3. **Faturamento por serviço:** volume, faturado e ticket médio por categoria (exames clínicos, complementares, PGR/PCMSO, mensalidades…).
4. **Receita recorrente, contratos e margem:** MRR, faturamento por vidas, contratos a vencer em 30 dias, margem bruta estimada por exame e rateio por centro de custo.

Regras e decisões no [ADR 0011](docs/adr/0011-relatorio-financeiro-diario.md). O envio automático fica desligado até `FINANCEIRO_HABILITADO=true` no worker. A prévia e o reenvio pelo admin funcionam sempre.

### Relatório de gestão do SESMT

Um terceiro e-mail às 08:00, para o SESMT, com lista de destinatários própria (admin → **SESMT**), no mesmo padrão visual dos outros dois. Três seções:

1. **Contratos e documentos a vencer** (próximos 30 dias): contratos e programas/laudos (PGR, PCMSO, LTCAT) com grupo do cliente, vencimento, status (*A vencer*, *Vencendo* em até 7 dias) e ação recomendada.
2. **Contratos e documentos vencidos** (atenção crítica): com os dias de atraso, do mais atrasado ao menos. Contratos vencidos há mais de 90 dias (clientes que saíram) ficam só na contagem.
3. **Envios para o eSocial** (dia anterior): total de eventos gerados, com recibo e sem recibo (pendentes ou rejeitados), a lista dos sem recibo e a dos transmitidos com recibo e protocolo.

O SGG só responde documentos e eventos do eSocial **por empresa**, então a coleta roda de madrugada (02:00): consulta os documentos das empresas com contrato em andamento e os eventos das empresas com o eSocial habilitado (cerca de 1.700 consultas, mais de uma hora no limite de requisições por minuto). O relatório traz só o código do funcionário, nunca nome ou CPF (LGPD). Sem o item "Exames pendentes" da primeira versão do layout, porque o escopo não trazia o endpoint.

Regras e decisões no [ADR 0013](docs/adr/0013-relatorio-sesmt.md). O envio automático fica desligado até `SESMT_HABILITADO=true` no worker. A prévia e o reenvio pelo admin funcionam sempre (a coleta manual também leva mais de uma hora).

### Atendimentos por médico (no e-mail de atendimentos)

Em **Configurações → Médicos** ficam os médicos que apareceram nos exames clínicos do SGG; quem estiver marcado entra no relatório. O e-mail diário de atendimentos ganha a seção **Atendimentos por médico** (total de exames clínicos por médico e por tipo) e a planilha anexa `atendimentos-por-medico-AAAA-MM-DD.xlsx`, com uma aba por médico e as colunas Empresa, Funcionário, Médico, Tipo exame e Data exame.

- Processado às 23:20 (uma consulta ao SGG, mais uma por empresa nova), fora do expediente; o botão **Processar uma data** roda na hora. O envio processa de novo se a escolha de médicos mudou.
- Só os exames dos médicos marcados são gravados. O **nome do trabalhador nunca é gravado**: é lido do SGG no envio, só para a planilha anexa. O corpo do e-mail traz apenas contagens.
- A planilha vai **só para os destinatários marcados em "Planilha (LGPD)"** (Atendimento → Destinatários; desmarcado por padrão; marcar exige o módulo Configurações e desativar o destinatário desmarca). Os demais recebem o mesmo e-mail sem o anexo, enviado primeiro; se um dos dois falhar, a nova tentativa manda só para quem ainda não recebeu.
- Sem médico marcado, o e-mail sai como antes. Se a seção falhar, o relatório principal sai mesmo assim e o técnico é alertado.
- Regras e decisões no [ADR 0015](docs/adr/0015-atendimentos-por-medico.md).

### Exportar período (planilha)

Cada relatório tem o cartão **Exportar período** (Atendimento, Financeiro e SESMT na página do módulo; atendimentos por médico em **Configurações → Médicos**). Escolha a data inicial e a final (até 31 dias) e a planilha é gerada em segundo plano, com uma barra que enche como água até ficar pronta para baixar (fica disponível por 24 horas).

- A planilha traz a aba **Sobre**, as abas do relatório (o que o e-mail mostrou em cada dia) e as abas **Fonte** (os dados usados no cálculo).
- Fonte do Atendimento: agendamentos e mudanças de status do banco. Financeiro: títulos e itens faturados relidos do SGG. SESMT: empresas, contratos, documentos e eventos do eSocial relidos do SGG, **só das 20h às 5h** (mais de mil consultas). Médicos: exames com o nome do trabalhador lido do SGG na hora, nunca gravado.
- **Processar período** (cartão ao lado, nos mesmos quatro lugares): recalcula e grava o resumo de cada dia do período (até 31 dias), com a mesma barra. **Não reenvia e-mail.** O SESMT faz uma única leitura do SGG para todos os dias e só roda das 20h às 5h.
- Regras e decisões no [ADR 0016](docs/adr/0016-exportacao-por-periodo.md).

### Painel administrativo: módulos e usuários

O painel é o **Sistema de Relatórios**, organizado em cinco módulos no menu:

| Módulo | O que tem |
| --- | --- |
| **Atendimento** | Painel do relatório de atendimentos (coleta, últimos 14 dias, reprocessar) e os **destinatários**. Os botões **Acompanhar AO VIVO** (monitor) e **Configurar Agendas** (unidades, agendas e guichês) abrem as telas de apoio |
| **Financeiro** | Relatório financeiro diário e seus destinatários |
| **SESMT** | Relatório de gestão do SESMT e seus destinatários |
| **Configurações** | Abas *Regras do relatório* (turnos, atípicos, e-mail técnico), **Médicos** (atendimentos por médico) e **Usuários** |
| **Execuções** | Histórico dos jobs |

Em **Configurações → Usuários** ficam a lista de usuários, com **Adicionar usuário**, **Editar** e **Excluir**. O cadastro é simples (nome, usuário e senha) e, abaixo, as **permissões de visualização**: um quadro por módulo. Quem não tem um módulo não vê o item no menu e recebe "Sem acesso" se abrir o endereço. As permissões são lidas do banco a cada página, então editar ou excluir um usuário vale na hora.

- O **administrador do deploy** (`ADMIN_USER` e `ADMIN_PASSWORD_HASH`) sempre tem todos os módulos e não é guardado no banco: aparece na lista, mas não é editado nem excluído por ali. Assim ninguém perde o acesso ao sistema por engano.
- Usuário: 3 a 40 caracteres (letras minúsculas, números, ponto, hífen ou sublinhado), único sem diferenciar maiúsculas. Senha: de 8 a 72 caracteres, guardada com bcrypt. Ao editar, a senha em branco mantém a atual.
- Ninguém exclui o próprio usuário nem tira o próprio acesso a Configurações.
- Regras e decisões no [ADR 0014](docs/adr/0014-usuarios-e-modulos.md).

### Monitor ao vivo (tela do gerente)

`/admin/monitor` (em *Atendimento*, botão **Acompanhar AO VIVO**), dentro do login do admin. São os mesmos indicadores do e-mail, calculados para **hoje até agora** e atualizados sozinhos a cada 5 s:

1. **Topo:** situação da coleta ("Dados do SGG de 10:40:00"), uma frase-resumo e os cartões de agora. Com guichês marcados, a espera e o atendimento aparecem por área: **espera recepção** (no guichê), **espera consultório** (aguardando o médico), **em atendimento no guichê** e **no consultório**, cada um com a maior espera ou atendimento em curso. Também mostra quem **ainda não chegou**, destacando os de horário vencido.
2. **Hoje até agora:** atendimentos, faltas, espera média e TMA, comparados com o mesmo dia da semana anterior **até o mesmo horário**. Com guichês marcados, os cartões ficam em três linhas: **Consultórios** (atendimentos, Tempo de Espera - Consultório, Tempo de Consulta e faltas), **Recepção** (atendimentos nos guichês, Tempo de Espera - Recepção e Tempo de Atendimento - Recepção) e **Permanência total** (Tempo Médio Total de Permanência). O e-mail diário segue o mesmo padrão.
3. **Meio:** movimento por hora (chegadas × atendimentos finalizados, com dica ao passar o mouse e tabela alternativa) e TMA por consultório.
4. **Base:** por turno, tempos por agenda e alertas da coleta.

![Monitor ao vivo (dados simulados)](docs/img/monitor-ao-vivo.png)

O monitor **não faz nenhuma requisição ao SGG**. Ele lê os eventos que o coletor já grava a cada 5 s, então pode ficar aberto em quantas telas for sem gastar a cota da API. O botão **Tela cheia** esconde o menu, para deixar numa TV. Detalhes no [ADR 0007](docs/adr/0007-monitor-em-tempo-real.md).

## Estrutura

```
src/relatorio/
├── domain/            # entidades, turnos, detecção de transição, métricas, fila ao vivo (funções puras)
├── application/       # casos de uso + portas (interfaces) + montagem
├── infrastructure/
│   ├── sgg/           # cliente HTTP (httpx+tenacity), DTO → domínio, limitador, simulador
│   ├── db/            # modelos SQLAlchemy 2, repositórios, unidade de trabalho
│   ├── email/         # apresentação, renderizador (Jinja2+premailer), SMTP
│   ├── jobs.py        # executor com advisory lock e auditoria
│   ├── scheduler.py   # worker (APScheduler, America/Sao_Paulo)
│   └── container.py   # raiz de composição
├── interfaces/
│   ├── web/           # FastAPI: admin (Jinja2+HTMX), monitor ao vivo e /health
│   ├── cli.py         # relatorio reprocessar | enviar | previa | demo | spike-sgg …
│   └── demo.py        # simulação de dias completos (prévia, demo e teste ponta a ponta)
└── config.py          # pydantic-settings
templates/email/       # resumo_diario.html/.txt, alerta.html/.txt
templates/admin/       # telas do admin
static/                # logos da marca, CSS e JS do admin, htmx (sem CDN)
migrations/            # Alembic
tests/unit|integration # 240+ testes; fixtures sintéticas (sem dados reais)
docs/adr/              # registro de decisões
```

O domínio não importa `httpx`, `sqlalchemy` nem `fastapi`: tudo entra por portas (inversão de dependência). Dá para testar 100% do cálculo sem API nem banco.

## Rodando localmente

### Com Docker Compose (tudo pronto)

```bash
cp .env.example .env
# gere o hash da senha do admin e cole em ADMIN_PASSWORD_HASH; preencha SECRET_KEY
docker compose run --rm web relatorio gerar-hash-senha
docker compose up --build
docker compose run --rm web relatorio demo --dias 8   # 8 dias fictícios para explorar o admin
```

Admin: <http://localhost:8000>. Localmente, os e-mails são gravados em arquivo (`EMAIL_BACKEND=arquivo`), sem envio real.

### Com uv (desenvolvimento)

```bash
uv sync                                  # Python 3.12 + dependências (lockfile)
uv run pre-commit install
export DATABASE_URL=postgresql://relatorio:relatorio@localhost:5432/relatorio
uv run alembic upgrade head
uv run relatorio demo --dias 8
uv run uvicorn relatorio.interfaces.web.app:app --reload     # admin
uv run python -m relatorio.infrastructure.scheduler          # worker
```

### Comandos úteis

| Comando | O que faz |
| --- | --- |
| `relatorio reprocessar --data 2026-09-23 [--reconciliar] [--enviar]` | Recalcula um dia; `--enviar` reenvia o e-mail (forçado) (RF08) |
| `relatorio enviar --data 2026-09-23 [--forcar]` | Envia o e-mail de um dia |
| `relatorio consolidar --data …` / `reconciliar --data …` / `coletar` | Roda um job na hora |
| `relatorio processar-exames --data …` | Lê os exames clínicos de um dia (atendimentos por médico) |
| `relatorio previa --data … --saida previa.html` | Grava o HTML do e-mail para abrir no navegador |
| `relatorio previa --simulado` | Prévia com um dia fictício, sem banco |
| `relatorio spike-sgg --data …` | Valida a API real (só leitura, só agregados) |
| `relatorio gerar-hash-senha` | Gera o `ADMIN_PASSWORD_HASH` (bcrypt) |
| `relatorio verificar-config` | Lista as variáveis obrigatórias faltando |

## Variáveis de ambiente

Veja [`.env.example`](.env.example). Segredos só em variáveis de ambiente (RNF05): nunca no código nem nos logs. Os logs mascaram campos sensíveis, e a chave e as senhas são `SecretStr`.

| Variável | Exemplo | Uso |
| --- | --- | --- |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Conexão com o banco |
| `SGG_API_KEY` | (secreta) | Chave de 32 caracteres do SGG |
| `SGG_BASE_URL` / `SGG_MAX_RPM` | `https://app.sgg.net.br/api/v3/` / `40` | API e orçamento de requisições por minuto (a API aceita 60) |
| `COLETA_INTERVALO_S` | `5` | Segundos entre ciclos de coleta (divisor de 60, de 5 a 60); cada ciclo é 1 requisição |
| `COLETOR_HABILITADO` | `true` | `false` quando o painel em tempo real grava os eventos |
| `FINANCEIRO_HABILITADO` | `false` | Liga a coleta (05:45) e o envio (07:59) do relatório financeiro no worker |
| `SESMT_HABILITADO` | `false` | Liga a coleta (02:00) e o envio (08:00) do relatório de gestão do SESMT no worker |
| `SMTP_HOST` / `SMTP_PORT` | `mail.kinghost.net` / `587` | KingHost: 587 = STARTTLS, 465 = SSL direto (`SMTP_SSL` força) |
| `SMTP_USER` / `SMTP_PASSWORD` | (secretas) | Caixa usada no envio |
| `EMAIL_FROM` / `EMAIL_ALERTA_TECNICO` | `relatorios@…` / `tecnologia@…` | Remetente e alertas técnicos |
| `EMAIL_DESTINATARIOS_OVERRIDE` | `tecnologia@multilife.com.br` | Staging: todo e-mail só para o time de TI |
| `ADMIN_USER` / `ADMIN_PASSWORD_HASH` / `SECRET_KEY` | (secretas) | Administrador do sistema (acesso a todos os módulos, não editável pelo painel) e assinatura da sessão |
| `ADMIN_URL` | `https://….up.railway.app/admin` | Link no rodapé do e-mail |
| `APP_ENV` / `TZ` / `LOG_LEVEL` | `production` / `America/Sao_Paulo` / `INFO` | Ambiente, fuso e logs |

Turnos, limites de atípico, e-mail técnico, unidades e agendas incluídas também se ajustam no **admin** (tabela `configuracao`, que prevalece sobre as variáveis).

## Deploy na Railway

Um projeto com **3 serviços**, todos a partir deste repositório (deploy automático a cada merge na `main`):

| Serviço | Início | Observação |
| --- | --- | --- |
| `web` | `uvicorn relatorio.interfaces.web.app:app …` | Admin, monitor ao vivo e `/health`; gere um domínio |
| `worker` | `python -m relatorio.infrastructure.scheduler` | 1 réplica fixa, sempre ligado, sem domínio |
| `Postgres` | modelo PostgreSQL | Ative os backups |

A configuração de build e deploy do `web` e do `worker` fica em **[`.railway/railway.ts`](.railway/railway.ts)**, o formato Infrastructure as Code da Railway. Ele guarda Dockerfile, comando de início, pre-deploy, health check, réplicas e política de reinício. Diferente do antigo `railway.json`, **esse arquivo não é lido no deploy**. Toda vez que ele mudar, aplique com a CLI da Railway (versão 5.42.1 ou mais nova):

```bash
npm install            # SDK "railway" que a CLI usa para ler o arquivo (é o único Node do projeto)
railway link           # escolha o projeto e o ambiente (production)
railway config plan    # mostra o que mudaria, sem alterar nada
railway config apply   # aplica, depois de confirmar
```

O arquivo é um *partial*: gerencia só o `web` e o `worker`, e o PostgreSQL fica de fora. Os **nomes** das variáveis dos dois serviços estão listados no arquivo com `preserve()`: os valores (e os segredos) continuam só no painel. **Criou uma variável nova no painel? Acrescente o nome em `VARIAVEIS`**, senão o próximo `apply` apaga essa variável. Se um `plan` mostrar algo para apagar (`destroy`/`delete`), pare e revise antes de aplicar. Detalhes no [ADR 0008](docs/adr/0008-infraestrutura-como-codigo-na-railway.md).

Instalação do zero:

1. Crie o projeto e adicione o **PostgreSQL**.
2. Crie os serviços `web` e `worker` a partir deste repositório (GitHub).
3. Em *Variables* dos dois serviços, cadastre as variáveis acima (`DATABASE_URL=${{Postgres.DATABASE_URL}}`).
4. Rode `railway config apply` (acima) para configurar os dois serviços.
5. O **pre-deploy** (`alembic upgrade head`) roda nos dois serviços. As migrações têm trava própria, então as duas execuções simultâneas não competem.
6. Use os *Environments* da Railway: `staging` (com `EMAIL_DESTINATARIOS_OVERRIDE` para o time de TI) e `production`.

**Health check.** A Railway usa `/health/live` no deploy, uma liveness que não depende do worker. Para monitoramento externo, use `/health`: ele responde **503** se o último ciclo de coleta tiver mais de 5 min dentro do horário de coleta, ou se o banco estiver fora.

## Testes e qualidade

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy                                         # strict
TEST_DATABASE_URL=postgresql://…/relatorio_teste uv run pytest --cov=relatorio
```

- **Unitários (domínio e aplicação):** todos os casos obrigatórios da seção 15 (12:59→13:00, atípicos, salto, dia sem agendamentos, paciente em duas agendas, idempotência da detecção). Cobertura do domínio acima de 99% (meta ≥ 80%).
- **Integração:** cliente SGG com `respx` (paginação, HTTP 429 com backoff, `D001`, códigos S/E/A), repositórios no PostgreSQL (UNIQUE, corrida de 4 threads no `status_envio`), advisory lock, admin (login, bloqueio, CSRF, HTMX), SMTP.
- **Ponta a ponta:** um dia simulado passa por coleta minuto a minuto, PostgreSQL, reconciliação, conferência, consolidação e e-mail. O resultado é comparado com um oráculo independente (a "conferência manual"): contagens exatas e tempos iguais ao segundo.
- O CI (`.github/workflows/ci.yml`) roda tudo isso, além de `alembic check`, `pip-audit` e o build da imagem Docker.

## Segurança e LGPD

- **Somente leitura no SGG.** Do agendamento, só lemos ID, agenda, unidade, data/hora, situação e data de edição. Nome, CPF, nascimento, setor, cargo e observações são descartados no DTO e nunca chegam ao banco, aos logs nem ao e-mail (RNF06).
- **Atendimentos por médico (exceção):** o nome do trabalhador aparece só na planilha anexa ao e-mail de atendimentos. É lido do SGG no envio e não é gravado no banco nem nos logs (ADR 0015).
- **Admin:** senha com hash bcrypt, bloqueio por 15 min após 5 tentativas, CSRF em todos os POSTs, cookie de sessão assinado (`HttpOnly`, `SameSite=Lax`, `Secure` em produção), CSP sem scripts externos (o htmx é servido localmente) e HSTS em produção.
- **Rotação da chave do SGG:** gere uma chave nova no SGG, atualize `SGG_API_KEY` nos serviços `web` e `worker`, faça o redeploy, confira com `relatorio spike-sgg` e só então revogue a chave antiga.

## Antes da produção (spike da seção 10)

O ambiente em que o código foi desenvolvido não tinha acesso à rede do `app.sgg.net.br`. O cliente foi escrito a partir da documentação da API e testado com respostas simuladas. Rode `relatorio spike-sgg --data <dia útil recente>` num ambiente com acesso e confirme:

- [ ] Mudar a situação atualiza `data_hora_edicao` e faz o registro aparecer em `editado_aPartirDe`.
- [ ] O fuso de `data_hora_edicao` é o de Brasília (o spike mostra a diferença para a hora atual).
- [ ] O volume diário e as páginas por ciclo cabem no orçamento de 40 req/min (12 req/min com coleta a cada 5 s).
- [ ] Faltas registradas na agenda aparecem como `situacao = Faltou` em `GET /agendamento/`.
- [ ] O `resultado` vem como lista (o cliente aceita lista ou objeto) e `sala`/`id_unidade_atendimento` estão preenchidos.
- [ ] A Railway libera saída SMTP (587/465) para a KingHost. Se não liberar, veja o [ADR 0004](docs/adr/0004-email-smtp-kinghost.md).
- [ ] Homologação de 5 dias úteis em staging, com os números do e-mail conferidos manualmente no SGG (critério de aceite da v1).

## Decisões (ADRs)

- [0001 — Polling durante o expediente](docs/adr/0001-polling-durante-o-expediente.md)
- [0002 — Coletor compartilhado com o painel](docs/adr/0002-coletor-compartilhado.md)
- [0003 — Monólito modular hexagonal](docs/adr/0003-monolito-modular-hexagonal.md)
- [0004 — E-mail via SMTP da KingHost](docs/adr/0004-email-smtp-kinghost.md)
- [0005 — Conferência final antes da consolidação](docs/adr/0005-conferencia-final-antes-da-consolidacao.md)
- [0006 — Interpretações das regras](docs/adr/0006-interpretacoes-das-regras.md)
- [0007 — Monitor em tempo real lendo só o banco](docs/adr/0007-monitor-em-tempo-real.md)
- [0008 — Infraestrutura como código na Railway](docs/adr/0008-infraestrutura-como-codigo-na-railway.md)
- [0009 — Coleta e monitor a cada 5 segundos](docs/adr/0009-coleta-a-cada-5-segundos.md)
- [0010 — Sistema visual do admin e do monitor](docs/adr/0010-sistema-visual-do-admin.md)
- [0011 — Relatório financeiro diário](docs/adr/0011-relatorio-financeiro-diario.md)
- [0013 — Relatório de gestão do SESMT](docs/adr/0013-relatorio-sesmt.md)
- [0014 — Usuários, permissões e módulos do painel](docs/adr/0014-usuarios-e-modulos.md)
- [0015 — Atendimentos por médico](docs/adr/0015-atendimentos-por-medico.md)
- [0016 — Exportação de relatórios por período](docs/adr/0016-exportacao-por-periodo.md)
