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
    SGG[API SGG v3] -->|GET /agendamento a cada 60 s| COL[Coletor<br/>compartilhado com o painel]
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
| 06:00–18:00, a cada 60 s | `coletar_ciclo`: polling incremental com cursor no banco | o próximo ciclo recupera (2 min de sobreposição) |
| 18:30 | `reconciliar_dia`: varredura do dia inteiro | 19:00 e 22:00 |
| 23:00 | `consolidar_dia`: métricas + conferência final no SGG (RF11) | 02:00 e 05:00; na 3ª falha, alerta técnico |
| 07:59 | `enviar_relatorio`: e-mail do dia anterior | 08:01 e 08:03; depois, alerta técnico |
| 08:10 | `verificar_envio`: rede de segurança | alerta se não foi entregue |
| Dia 1, 03:00 | `limpar_retencao`: apaga o que tiver mais de 24 meses | dia 2 |

Todos os jobs usam `max_instances=1` e `coalesce=True`, com **advisory lock** do PostgreSQL (dois containers nunca rodam o mesmo job), e cada execução fica registrada em `execucao_job`.

### Métricas (seção 4 da documentação técnica)

- **Espera na recepção** = "Em Atendimento" − "Aguardando". **Tempo de atendimento** = "Atendido" − "Em Atendimento".
- **Turno pela hora agendada**: é da tarde quando a hora agendada for 13:00 ou depois (configurável).
- **Atípicos** (menos de 1 min ou mais de 180 min) entram nos totais, mas ficam fora das médias e do TMA e aparecem nos alertas.
- **Salto de status** (ex.: Aguardando → Atendido): o atendimento conta nos totais, mas fica "sem tempo medido".
- **Sem baixa**: terminou o dia em Agendado ou Aguardando. Antes de contar, o sistema confere no SGG se há falta registrada (RF11).
- O cálculo é versionado (`versao_regra` no resumo). Mudou uma regra? Suba a versão e reprocesse.

Detalhes e ajustes de interpretação: [ADR 0005](docs/adr/0005-conferencia-final-antes-da-consolidacao.md) e [ADR 0006](docs/adr/0006-interpretacoes-das-regras.md).

### O e-mail (boas práticas de BI)

Leitura em até 1 minuto, em pirâmide de atenção:

1. **Topo:** manchete em uma frase ("102 atendimentos e 11 faltas… espera média 15 min, +93,7% sobre a semana anterior") e selo de alertas.
2. **KPIs:** atendimentos, faltas (% dos agendados), espera média e TMA, cada um com variação contra o mesmo dia da semana anterior. A seta sempre vem com o valor e o rótulo, nunca só a cor. Verde e vermelho aparecem só quando subir é claramente bom ou ruim; o TMA é neutro. Taxas variam em pontos percentuais (p.p.).
3. **Meio (evidência):** tabela por turno (com os **guichês** separados das demais agendas, quando houver agendas marcadas como guichê em *Unidades e agendas*), TMA por consultório e tempos por agenda (qtd., média, mediana, maior), **um bloco por turno**, porque na troca de turno troca o médico. As barras de TMA usam uma única cor, a azul da marca, e a mesma escala nos dois turnos.
4. **Base:** comparativo semanal completo, alertas por severidade (ícone + rótulo + cor) e rodapé com a versão da regra e o link do admin.

Tecnicamente: layout em tabelas compatível com Outlook e Gmail, CSS inline (premailer), logo anexada via CID (Outlook não bloqueia), responsivo (KPIs em 2×2 no celular), versão em texto puro e contraste WCAG AA (texto ≥ 4,5:1). O template recebe só o JSON de `resumo_diario.metricas`, e nenhuma regra de cálculo fica nele.

### Monitor ao vivo (tela do gerente)

`/admin/monitor`, dentro do login do admin. São os mesmos indicadores do e-mail, calculados para **hoje até agora** e atualizados sozinhos a cada 30 s:

1. **Topo:** situação da coleta ("Dados do SGG de 10:40:00"), uma frase-resumo e os cartões de agora: **na recepção** (com a maior espera em curso), **em atendimento** e **ainda não chegaram** (com os de horário vencido).
2. **Hoje até agora:** atendimentos, faltas, espera média e TMA, comparados com o mesmo dia da semana anterior **até o mesmo horário**.
3. **Meio:** movimento por hora (chegadas × atendimentos finalizados, com dica ao passar o mouse e tabela alternativa) e TMA por consultório.
4. **Base:** por turno, tempos por agenda e alertas da coleta.

![Monitor ao vivo (dados simulados)](docs/img/monitor-ao-vivo.png)

O monitor **não faz nenhuma requisição ao SGG**. Ele lê os eventos que o coletor já grava a cada minuto, então pode ficar aberto em quantas telas for sem gastar a cota da API. O botão **Tela cheia** esconde o menu, para deixar numa TV. Detalhes no [ADR 0007](docs/adr/0007-monitor-em-tempo-real.md).

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
| `SGG_BASE_URL` / `SGG_MAX_RPM` | `https://app.sgg.net.br/api/v3/` / `20` | API e orçamento de requisições por minuto |
| `COLETOR_HABILITADO` | `true` | `false` quando o painel em tempo real grava os eventos |
| `SMTP_HOST` / `SMTP_PORT` | `mail.kinghost.net` / `587` | KingHost: 587 = STARTTLS, 465 = SSL direto (`SMTP_SSL` força) |
| `SMTP_USER` / `SMTP_PASSWORD` | (secretas) | Caixa usada no envio |
| `EMAIL_FROM` / `EMAIL_ALERTA_TECNICO` | `relatorios@…` / `tecnologia@…` | Remetente e alertas técnicos |
| `EMAIL_DESTINATARIOS_OVERRIDE` | `tecnologia@multilife.com.br` | Staging: todo e-mail só para o time de TI |
| `ADMIN_USER` / `ADMIN_PASSWORD_HASH` / `SECRET_KEY` | (secretas) | Login do admin e assinatura da sessão |
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
- **Admin:** senha com hash bcrypt, bloqueio por 15 min após 5 tentativas, CSRF em todos os POSTs, cookie de sessão assinado (`HttpOnly`, `SameSite=Lax`, `Secure` em produção), CSP sem scripts externos (o htmx é servido localmente) e HSTS em produção.
- **Rotação da chave do SGG:** gere uma chave nova no SGG, atualize `SGG_API_KEY` nos serviços `web` e `worker`, faça o redeploy, confira com `relatorio spike-sgg` e só então revogue a chave antiga.

## Antes da produção (spike da seção 10)

O ambiente em que o código foi desenvolvido não tinha acesso à rede do `app.sgg.net.br`. O cliente foi escrito a partir da documentação da API e testado com respostas simuladas. Rode `relatorio spike-sgg --data <dia útil recente>` num ambiente com acesso e confirme:

- [ ] Mudar a situação atualiza `data_hora_edicao` e faz o registro aparecer em `editado_aPartirDe`.
- [ ] O fuso de `data_hora_edicao` é o de Brasília (o spike mostra a diferença para a hora atual).
- [ ] O volume diário e as páginas por ciclo cabem no orçamento de 20 req/min.
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
