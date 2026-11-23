# ADR 0021: SESMT: documentos pelas planilhas de vencimento do SGG, e-mail semanal

- **Status:** aceita (substitui a fonte dos documentos e a agenda do e-mail do [ADR 0020](0020-sesmt-vencidos-mes-seguinte-e-esocial-do-mes.md))
- **Data:** 2026-11-23

## Contexto

O primeiro relatório da regra 2.0.0 mostrou 183 programas e laudos vencidos. As planilhas "VENCIMENTO(s) DE PGR(s)", "de PCMSO(s)" e "de LTCAT(s)", exportadas do SGG em 23/11/2026, mostravam 109 vencidos (36 PGR, 36 PCMSO e 37 LTCAT) e 34 vencendo em dezembro.

A comparação mostrou que o erro está na fonte:

- **Planilhas:** têm uma linha por empresa, com o documento atual, de cerca de 456 empresas por tipo.
- **API (`programasLaudos/`):** chamada como manda a documentação (`id_empresa` e `tipo`, com ou sem paginador, pelo código ou pelo CNPJ), devolve só 198 documentos, de 49 a 65 empresas por tipo. Quase todos são de 2022 a 2024.
- **Emissor:** só aparecem documentos emitidos por dois emissores antigos. Os emitidos pela equipe atual (cerca de 420 por tipo) nunca vêm.
- **Efeito no relatório:** a empresa que renovou no SGG continuava vencida, porque a API devolvia o documento antigo e não a renovação.
- **Outros caminhos:** cerca de 30 caminhos testados na API devolveram "página não encontrada".

O painel de alertas da página inicial do SGG também não serve de referência, porque conta documentos já renovados.

## Decisão

- **Documentos pelas planilhas:**
  - **Envio:** o SESMT exporta do SGG as três planilhas "Vencimento(s) de ..." toda semana, com a coluna **Grupo**, e envia na gestão do relatório (`/admin/sesmt/gestao`). Cada uma substitui a anterior do mesmo tipo, e o tipo é lido do título da planilha, não do nome do arquivo.
  - **O que fica gravado:** só código, empresa, CNPJ, grupo e validade, na tabela `planilha_sesmt` (migração 0012). E-mails, telefones e emissor são descartados na leitura. Empresa com CPF vira "Empresa #código".
  - **Regras:** valem as mesmas (vencido = validade antes do dia do e-mail; mês seguinte = mês civil seguinte). A regra do documento mais recente fica, caso a planilha traga uma empresa repetida.
- **Grupo EM CANCELAMENTO fora dos documentos** (pedido do SESMT). Vale pelo nome do grupo, que vem na planilha. O eSocial continua com essas empresas.
- **eSocial continua pela API:** uma consulta por empresa com o eSocial habilitado, toda noite às 20:00 (retentativa 00:00). São cerca de 600 consultas, perto de 30 minutos.
- **Toda a consulta de documentos à API foi retirada:** `documentos_sst`, `para_documento` e o método da porta `SesmtGateway`.
- **E-mail semanal:** segunda-feira às 08:00 (retentativas 08:02 e 08:04, verificação às 08:12), com a coleta de domingo à noite. As planilhas precisam estar enviadas até domingo.
- **Avisos no e-mail e no dashboard:** quando falta a planilha de um tipo, e quando a de algum tipo tem mais de 8 dias.
- **`VERSAO_REGRA_SESMT` 2.1.0.** O resumo ganhou `planilhas`, com a data de envio de cada tipo.

## Consequências

- **Atualização semanal:** os documentos ficam tão atuais quanto a última planilha enviada. Uma renovação feita no meio da semana só entra na próxima.
- **Planilha esquecida:** o e-mail sai com o aviso e com a planilha anterior. Se nunca houve planilha de um tipo, os documentos desse tipo não entram.
- **Se o SGG corrigir a API:** voltar a coletar pela API é possível. As regras não mudam, só a origem dos documentos em `ConsolidarSesmt.carregar`.
