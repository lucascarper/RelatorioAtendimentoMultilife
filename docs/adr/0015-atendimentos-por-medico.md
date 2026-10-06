# ADR 0015: Atendimentos por médico (seção e planilha no e-mail de atendimentos)

- **Status:** aceita
- **Data:** 2026-10-06

## Contexto

A gerência pediu um relatório diário de atendimentos por médico, com Empresa, Funcionário, Médico, Tipo exame e Data exame, só dos médicos escolhidos em Configurações, no mesmo e-mail do relatório de atendimentos e com uma planilha Excel anexa. Não precisa de acompanhamento ao vivo: pode ser processado à noite, quando a cota da API é maior, e também manualmente a qualquer momento.

Até aqui o sistema nunca guardou nem enviou nome de trabalhador (RNF06): os relatórios usam só agregados ou "Funcionário #código". O pedido traz o nome do funcionário junto do tipo de exame ocupacional, que é dado pessoal de saúde (sensível na LGPD).

## Decisão

- **Fonte:** `GET /exames-realizados/` com `dataExame_aPartirDe = dataExame_ate = dia` e `exame=Clínico`. Cada exame clínico é uma consulta médica; os complementares (hemograma, audiometria...) ficam fora. Em 05/10/2026 foram 98 clínicos de 6 médicos numa única consulta. O nome da empresa vem de `GET /empresa/?codigo=` (pessoa física vira "Empresa #id", como no SESMT) e fica guardado: no dia seguinte só as empresas novas são consultadas.
- **Tipo exame** é o tipo do SGG (Admissional, Periódico, Demissional, Mudança de Função, Retorno ao Trabalho, Outro), nessa ordem.
- **Escolha dos médicos** (Configurações → aba **Médicos**): todo médico que aparece num exame clínico é registrado (CRM e nome, tabela `medico_relatorio`); quem estiver marcado entra no relatório. Também dá para cadastrar um CRM que ainda não apareceu: o nome é conferido em `GET /medico/?crm=`. Só os exames dos médicos marcados são gravados (`exame_clinico`); os demais não são processados.
- **Quando roda:** `processar_exames` às 23:20 (o próprio dia), com retentativas às 02:20 e 05:20 (o dia anterior), fora do expediente. O botão **Processar uma data** e o comando `relatorio processar-exames --data` rodam na hora. O envio das 07:59 processa de novo se a noite não rodou ou se a escolha de médicos mudou depois (`coleta_exames` guarda os CRMs escolhidos em cada processamento).
- **LGPD (exceção controlada):** o nome do trabalhador **só aparece na planilha anexa**. Ele é lido do SGG no momento do envio (uma consulta), usado para montar a planilha em memória e descartado: não vai para o banco, nem para logs, nem para o corpo do e-mail. O corpo traz só a contagem por médico e por tipo. O rodapé avisa que a planilha tem nomes de trabalhadores e não deve ser encaminhada. Se o SGG não responder no envio, a planilha sai com "Funcionário #código".
- **E-mail:** a seção **Atendimentos por médico** entra no relatório de atendimentos (antes dos alertas) e a planilha `atendimentos-por-medico-AAAA-MM-DD.xlsx` vai anexa (`multipart/mixed`), com uma aba por médico escolhido que teve atendimento. Sem nenhum médico escolhido, o e-mail sai exatamente como antes e o SGG não é consultado no envio.
- **Nunca derruba o relatório principal:** se a seção ou a planilha falharem, o relatório de atendimentos é enviado sem elas e o técnico recebe um alerta.
- **Retenção:** os exames gravados seguem a mesma retenção de 24 meses dos demais dados.

## Consequências

- A lista de médicos só aparece depois do primeiro processamento (ou de um cadastro manual por CRM).
- A planilha anexa é um documento com dados pessoais de saúde: quem recebe o e-mail de atendimentos passa a receber esses dados. A lista de destinatários do relatório de atendimentos deve se limitar a quem precisa deles.
- O custo na API é baixo: uma consulta por noite, uma por empresa nova e uma no envio.
- Exames lançados no SGG depois do processamento das 23:20 ficam fora até um novo processamento (o envio só reprocessa se a escolha de médicos mudou). Para incluí-los, use **Processar uma data** antes de reenviar.
