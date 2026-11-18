# ADR 0020: SESMT refeito: vencidos por grupo, vencimentos do mês seguinte e eSocial do mês

- **Status:** aceita (substitui as regras do ADR 0013 e a parte do SESMT do ADR 0018)
- **Data:** 2026-11-18

## Contexto

O SESMT pediu para descartar o relatório anterior (contratos e documentos a vencer em 30 dias, vencidos, eventos do eSocial do dia) e montar outro, no e-mail e no dashboard, com só três blocos:

1. Programas e laudos **vencidos**, num gráfico por grupo de empresas.
2. Programas e laudos que **vencem no mês que vem**, em lista de empresas.
3. Eventos do **eSocial**: quantos foram enviados e quantos rejeitados, com a lista dos rejeitados.

As respostas da entrevista com o SESMT (11/2026):

| Pergunta | Decisão |
| --- | --- |
| Tipos de documento | PGR, PCMSO e LTCAT (como antes) |
| Quais empresas | **Todas** as do SGG (1.216 em 11/2026), não só as com contrato em andamento |
| "Mês que vem" | O mês civil seguinte ao do e-mail (e-mail de 18/11 → dezembro) |
| Período do eSocial | Do dia 1º do mês até a coleta |
| Rejeitado | Evento **sem recibo**: a API não separa rejeitado de ainda não transmitido, e o SESMT aceitou tratar os dois como rejeitados |
| Gráfico de vencidos | Barras por grupo, empilhadas por tipo |
| Lista do mês seguinte | Uma linha por empresa, com os documentos e as datas |
| Rejeitados | Agrupados por empresa (quantidade e tipos de evento), sem funcionário |
| Coleta | Completa toda noite |
| Agenda | 20:00, retentativa 00:00 |
| E-mail | Diário, às 08:00 |
| Dashboard | O mesmo do e-mail, mais o histórico |
| Listas | No dashboard, com seletor de tamanho e páginas; no e-mail, 40 linhas e "e mais N" |
| Exportação | Planilha da última coleta, sem reler o SGG |

A API foi testada de novo em 11/2026: `programasLaudos/` e `getEvtEsocial/` exigem **um** `id_empresa` por consulta, `programasLaudos/` exige **um** tipo, e os dois ignoram filtros de data. O cadastro de empresas devolve todas como `situacao = ativa`, então não há como separar ex-clientes por ele.

## Decisão

- **Coleta (`application/sesmt.py`):**
  - Empresas, documentos (3 por empresa, de todas) e eventos (1 por empresa com o eSocial habilitado). São cerca de 4.300 consultas, perto de 3h30 a 20 por minuto.
  - Contratos saíram da coleta.
  - Mantidas a tolerância a falhas por empresa e a regra de abortar com 5 falhas seguidas ou mais de 10%.
- **Agenda:**
  - `consolidar_sesmt` às 20:00 (depois do coletor, que para às 18:00) coleta o próprio dia.
  - A retentativa das 00:00 refaz o dia que acabou de fechar. Duas tentativas: na segunda falha, alerta técnico.
  - O e-mail das 08:00 lê o resumo da noite anterior.
- **Regras (`domain/sesmt.py`, `VERSAO_REGRA_SESMT` 2.0.0):**
  - `referencia` = o dia da coleta; `hoje` = o dia do e-mail (referência + 1).
  - Documento corrente: o mais recente de cada empresa e tipo (sem mudança).
  - Vencido: vencimento anterior a `hoje`. Vem com o total, o número de empresas, a divisão por tipo e por grupo e a lista completa (planilha).
  - Mês seguinte: vencimento no mês civil seguinte ao de `hoje`, agrupado por empresa e ordenado pelo primeiro vencimento.
  - eSocial: do dia 1º até `referencia`. Enviados = gerados; rejeitados = sem recibo. Inclui a comparação com o mesmo período do mês anterior e o mês a mês dos últimos 12 meses, calculado a cada coleta, porque a API devolve todo o histórico da empresa.
- **Dashboard (`/admin/sesmt`):** sai o seletor de período, porque o dado é uma foto da coleta e não uma soma de dias.
  - **Topo:** a frase de status e 4 cartões (vencidos, vencem no mês seguinte, enviados, rejeitados). As setas comparam com a coleta anterior; as do eSocial, com o mesmo período do mês anterior.
  - **Meio:** o gráfico de barras deitadas por grupo, a lista do mês seguinte e a dos rejeitados, cada uma com o seletor de 10/25/50/100 linhas e as páginas da paginação padrão do admin.
  - **Base:** vencidos dia a dia (90 dias) e eSocial mês a mês.
- **E-mail:** as mesmas três seções e a mesma frase de status. O gráfico por grupo é feito com uma tabela de barras, que funciona em qualquer cliente de e-mail.
- **Grupos:** sem grupo no SGG aparece como "Sem grupo". Um código sem nome aparece como "Grupo N". O grupo 14 ("EM CANCELAMENTO", 10 empresas em 11/2026) entrou nos nomes padrão.
- **Exportação:** a planilha traz o resumo de cada dia do período e as listas da última coleta até a data final (vencidos, mês seguinte, rejeitados e mês a mês). Não relê o SGG, por isso não tem mais a trava de horário. O **Processar período** do SESMT foi retirado, porque a coleta não depende do dia.
- **Resumos antigos (regra 1.x):** continuam no banco. Ficam fora do dashboard e do histórico, a prévia deles responde 404 e a gestão mostra "Formato antigo". Para refazer uma data, é só gerar de novo.
- **LGPD:** o código do funcionário e o XML do evento são descartados na leitura, e os rejeitados saem só por empresa.

## Consequências

- **Cota da API:** a coleta ocupa a cota das 20:00 até perto de 23:30. Com o limite compartilhado no worker, ela fica mais lenta enquanto rodam os jobs das 22:00 e das 23:00.
  - Se o número de empresas crescer, a coleta pode passar da meia-noite e encostar na retentativa, que é ignorada enquanto a primeira ainda roda, pela trava do job.
  - A saída seria guardar os documentos no banco e reconsultar só os que estão perto de vencer.
- **Eventos depois das 20:00:** um evento gerado depois da coleta entra só no dia seguinte (ou no mês seguinte, no último dia do mês).
- **Ex-clientes:** todas as empresas entram, então ex-clientes com PGR vencido há anos contam como vencidos para sempre. Se isso poluir o gráfico, a saída é filtrar pelo contrato.
- **Rejeitado:** inclui o que só ainda não foi transmitido. Um evento gerado no dia da coleta aparece como rejeitado até receber o recibo.
- **Histórico:** o de vencidos começa na primeira coleta da versão 2.0.0. O do eSocial mês a mês já sai completo na primeira coleta.
