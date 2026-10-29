# ADR 0018: Dashboards do financeiro e do SESMT

- **Status:** aceita
- **Data:** 2026-10-29

## Contexto

O módulo Atendimento ganhou um dashboard do mês, com as ferramentas de operação numa tela de gestão separada (ADR 0017). Foi pedido o mesmo para o Financeiro e o SESMT.

Os resumos diários desses dois relatórios guardam três tipos de dado:
- **Do dia:** caixa recebido e pago, faturamento do dia, eventos do eSocial do dia. Esses se somam num período.
- **Mês até a data:** faturamento por categoria, margem e rateio do financeiro. Esses só fecham para um mês a partir do dia 1º.
- **Foto do dia:** MRR, contratos ativos e a vencer; pendências do SESMT. Esses valem só no dia em que foram calculados.

## Decisão

- **Mesma estrutura do atendimento:**
  - `/admin/financeiro` e `/admin/sesmt` são os dashboards.
  - As telas antigas foram, sem mudar o conteúdo, para `/gestao`, com o título "Gestão do relatório …" e o link "← Dashboard".
  - As ações dos formulários voltam para a gestão.
  - Período, seletor de até 31 dias e comparação com o mês anterior funcionam como no ADR 0017. Mesmos módulos de permissão.
- **Dois grupos de cartões:**
  - **"No período":** somas dos dias, comparadas com o mesmo intervalo do mês anterior.
  - **"Em DD/MM":** foto do último dia com resumo, comparada com o último dia com resumo do período anterior.
  - Os dados "mês até a data" ficam de fora, porque dariam números errados para períodos que não começam no dia 1º. Eles continuam no e-mail diário.
- **Financeiro:**
  - No período: receita recebida, despesas pagas, saldo operacional e faturamento.
  - Em DD/MM: MRR, contratos ativos e contratos a vencer, com o total de mensalidades em risco.
  - Gráfico: barras de recebido e pago por dia, e uma linha com o saldo acumulado.
- **SESMT:**
  - No período: eventos do eSocial, com recibo, sem recibo e empresas com eventos. Empresas são somadas dia a dia: a mesma empresa conta em cada dia em que teve evento, porque o resumo não guarda a lista completa.
  - Em DD/MM: a vencer em 30 dias, vencendo em 7 dias e vencidos, com a divisão entre contratos e documentos.
  - Gráficos lado a lado: barras empilhadas de eventos com e sem recibo; linhas de a vencer e vencidos por dia.
  - Tabela de eventos por tipo, somados no período.
- **Setas:**
  - Verde quando sobe: recebido, saldo, faturamento, MRR, contratos ativos.
  - Vermelho quando sobe: pago, contratos a vencer, a vencer, vencendo, vencidos, sem recibo.
  - Neutro: eventos, com recibo, empresas.
  - A variação usa o valor absoluto da base, para um saldo que sai de negativo para positivo aparecer como alta.
- **Status no topo:** "Envio automático ligado/desligado", com a data do último resumo gerado. Com o relatório desligado, o dashboard mostra os resumos que já existem.
- **Base comum, sem cópia:**
  - `listar_indicadores` recebe os caminhos do JSON que cada dashboard lê. O banco monta só esse recorte com `jsonb_build_object`, e as listas grandes do resumo não saem do banco.
  - Os gráficos são descritos em JSON (rótulos, séries, tipo, cor) e `dashboard.js` desenha qualquer um.
  - O formulário de período, o cartão e o gráfico com a tabela alternativa são partials usados pelos três dashboards.

## Consequências

- Os dashboards nunca mostram o dia de hoje: o resumo de cada dia sai na madrugada seguinte.
- Categorias, margem e rateio só aparecem no e-mail e na prévia. Para vê-los num dashboard, seria preciso guardar esses dados por dia no resumo.
- "Empresas com eventos" não é o número de empresas distintas do período. Para isso, o resumo precisaria guardar a lista de empresas de cada dia.
