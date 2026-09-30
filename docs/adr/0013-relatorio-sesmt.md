# ADR 0013: Relatório de gestão do SESMT

- **Status:** aceita (envio automático desligado até a conferência dos números)
- **Data:** 2026-09-30
- **Escopo:** layout "Relatório Diário de Gestão SESMT" (três seções) e o mapeamento de endpoints enviado pelo SESMT

## Contexto

O SESMT quer um e-mail diário com os contratos e documentos a vencer, os vencidos e os envios ao eSocial, no mesmo padrão visual dos relatórios financeiro e de atendimentos. A conferência das respostas reais da API mostrou diferenças em relação ao mapeamento inicial:

| Endpoint | O que a resposta real ensinou |
| --- | --- |
| `programasLaudos/` | Exige **`id_empresa` e `tipo`** (o mapeamento dizia só o paginador). Tipos aceitos: PPRA, LTCAT, PCMSO, PGR, PGRTR, PCA, PPR, LTIP, LI, LP, PAE. Devolve **todo o histórico** de renovações de cada tipo |
| `contratoCliente/` | O campo `ultimo` ("Sim"/"Não") marca o contrato corrente do cliente; sem ele, os contratos antigos e já substituídos apareceriam como vencidos (440 dos 571 "Vencido") |
| `empresa/` | Traz `id_grupo` (só o código), `situacao_esocial` ("Habilitada"/"Não Habilitada") e um CNPJ de mentira (`00.000.000/0000-00`) quando não há. Não há endpoint com o nome do grupo |
| `getEvtEsocial/` | Exige **`id_empresa` e `evento`** (`Todos` ou um tipo). Sem filtro de data: devolve todo o histórico da empresa, do mais antigo ao mais novo. `recibo` e `protocolo` vêm vazios enquanto o evento não foi transmitido. O XML traz o CPF do trabalhador |
| `exames-a-realizar/` | Devolve o **nome do grupo** ("GESTÃO PREMIUM", "EVENTUAL"…), mas é uma lista de trabalhadores com dados pessoais: usada só uma vez, para descobrir os nomes dos 11 grupos |

## Decisão

- **Mesmo deploy, job próprio.** Coleta às 02:00 (retentativas 03:30 e 04:30), e-mail às 08:00 (08:02 e 08:04), verificação às 08:12. Tabelas novas `resumo_sesmt` e `destinatario_sesmt` (migração 0004); `EnviarRelatorio` e `VerificarEnvio` recebem o `TipoRelatorio` `SESMT`, com a mesma trava de duplicidade dos outros.
- **Coleta de madrugada e seletiva.** O SGG só responde documentos e eventos **por empresa** (1.138 empresas), no limite de 20 requisições por minuto, e o coletor de agendamentos usa 12 delas por minuto entre 06:00 e 18:00. Por isso a coleta roda de madrugada e consulta só o necessário: documentos (PGR, PCMSO e LTCAT, uma consulta por tipo) das empresas com contrato em andamento (cerca de 330) e eventos das empresas com o eSocial habilitado (cerca de 600). São cerca de 1.700 consultas, mais de uma hora. Reprocessar uma data pelo admin leva o mesmo tempo.
- **Falha de uma empresa não derruba a coleta.** A empresa fica de fora e o e-mail avisa que o relatório está parcial. Cinco falhas seguidas (API fora, chave revogada) abortam; acima de 10% de falhas também: melhor não enviar do que mandar um relatório com buracos sem aviso.
- **Regras** (`domain/sesmt.py`, `VERSAO_REGRA_SESMT` 1.0.0):
  - Documento corrente = o de maior vencimento de cada empresa e tipo; os anteriores foram renovados.
  - Contrato corrente = o marcado como `ultimo`.
  - A vencer: hoje até hoje + 30 dias; "Vencendo" em até 7 dias. Vencido: vencimento anterior a hoje.
  - Contratos vencidos há mais de 90 dias são de clientes que saíram: entram só na contagem. Documento vencido de cliente com contrato em andamento fica sempre na lista (risco legal), do mais atrasado ao menos.
  - eSocial: eventos com `data_geracao` igual à referência (o dia anterior). Com recibo = transmitido; sem recibo = ainda não transmitido ou rejeitado, porque a API não informa qual dos dois.
  - Listas limitadas a 40 linhas por tabela (30 nos eventos sem recibo); os totais e os cartões contam tudo.
- **Grupo do cliente:** a API só informa o código. Os nomes vêm de uma configuração no admin (`sesmt_grupos`, formato `1=GESTÃO PREMIUM; 2=GESTÃO 2`), já preenchida com os 11 grupos cadastrados hoje no SGG.
- **Horário do evento:** vem do `Id` do XML do eSocial (padrão do leiaute: tipo de inscrição, CNPJ, data e hora de geração, sequencial) e só vale se a data bater com `data_geracao`. É a hora de geração, não a de transmissão.
- **LGPD:** nenhum nome de trabalhador nem CPF. O evento sai como "Funcionário #código". O XML é lido só para extrair a hora e descartado; o link e o emissor dos documentos também. Empresa com CPF vira "Empresa #id".
- **`SESMT_HABILITADO=false` por padrão.** A prévia e o reenvio pelo admin (`/admin/sesmt`) funcionam sempre.

## Fora do escopo desta versão

- **Exames pendentes** (linhas "Exames Pend." do layout): o mapeamento enviado não trazia o endpoint. `exames-a-realizar/` existe, mas devolve trabalhadores com dados pessoais e pede uma decisão de escopo (o que agrupar e como não expor nomes).
- **Colunas "Colaborador / CPF"** do layout: substituídas pelo código do funcionário, pela regra de LGPD do projeto.

## Consequências

- A coleta ocupa a cota da API por mais de uma hora de madrugada. Se o volume de empresas crescer, o tempo cresce junto; a saída seria guardar o último vencimento conhecido de cada documento e reconsultar só os que estão perto de vencer.
- O "Vencendo/A vencer" e os dias de atraso refletem a situação no momento da coleta. Reprocessar uma data antiga mostra a situação de hoje.
- Mudou uma regra? Suba `VERSAO_REGRA_SESMT` e regere as datas no admin.
