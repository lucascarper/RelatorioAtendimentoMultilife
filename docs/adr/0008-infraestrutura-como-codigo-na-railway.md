# ADR 0008 — Infraestrutura como código na Railway (`.railway/railway.ts`)

- **Status:** aceita
- **Data:** 2026-09-25

## Contexto

O `web` e o `worker` eram configurados por Config as Code: o `railway.json` e o `railway.worker.json`, este apontado nas configurações do serviço. A Railway descontinuou esse formato, que deixa de ser lido em **01/12/2026**. O substituto é o Infrastructure as Code (`.railway/railway.ts`), que tem duas diferenças importantes:

1. **Não é lido no deploy.** A CLI compara o arquivo com o ambiente e aplica as mudanças depois de confirmar (`railway config plan` / `railway config apply`).
2. **Um arquivo "de projeto inteiro" apaga o que não declara.** Isso vale para serviços, bancos e variáveis.

## Decisão

- `.railway/railway.ts` declara o `web` e o `worker` com **exatamente** os mesmos valores dos dois JSON: builder Dockerfile, comando de início, `alembic upgrade head` no pre-deploy, `/health/live` com timeout de 60 s no `web`, 1 réplica e política de reinício (`ON_FAILURE`/5 no `web`, `ALWAYS` no `worker`). A equivalência foi conferida campo a campo avaliando o arquivo com o SDK.
- O arquivo é um **partial** (`export const partial = "relatorio-atendimentos"`). Ele só é dono do `web` e do `worker`, e o PostgreSQL fica de fora.
- **Um serviço declarado tem todas as variáveis gerenciadas pelo arquivo.** Variável que não estiver na lista é apagada no `apply`, como mostrou o primeiro `plan` (40 exclusões). Por isso os 20 nomes estão em `VARIAVEIS` com `preserve()`: o valor continua só no painel e nenhum segredo vai para o código. Variável nova precisa entrar na lista antes do próximo `apply`.
- A origem GitHub é declarada (`github(..., { checkSuites: true })`) para manter o repositório conectado e o "Wait for CI".
- O SDK `railway` (TypeScript) entra como dependência de desenvolvimento num `package.json` na raiz, com versão fixa, porque o SDK está em beta. É o único Node do projeto, e a imagem Docker ignora esses arquivos.

## Consequências

- Mudou `.railway/railway.ts`? Rode `railway config plan` e depois `railway config apply`. Um merge sozinho não aplica a mudança. Dá para automatizar com a GitHub Action `railwayapp/config` (plan no PR, apply no merge) usando um token de projeto no secret `RAILWAY_TOKEN`.
- **Virada (uma única vez):** o merge que remove os JSON e o `apply` precisam acontecer juntos, fora dos horários dos jobs (entre 19:10 e 21:50). Entre um e outro, o `worker` pode subir com o comando padrão da imagem (o do `web`), e nessa faixa isso não afeta coleta, consolidação nem envio.
