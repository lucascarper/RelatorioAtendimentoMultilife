# ADR 0011: Relatório financeiro diário

- **Status:** aceita (envio automático desligado até a conferência dos números)
- **Data:** 2026-09-28
- **Escopo:** documento "Relatório Financeiro Diário: Escopo e Layout" (28/09/2026)

## Contexto

Financeiro e diretoria querem um e-mail diário às 07:59, separado do relatório de atendimentos, com caixa, inadimplência, faturamento e saúde de contratos. Os dados vêm da mesma API do SGG. A documentação da API (Swagger em `/api/v3/doc/api.json`) e respostas reais mostraram:

| Endpoint | Uso | O que a resposta real ensinou |
| --- | --- | --- |
| `contasReceber/` | receita recebida, faturamento, inadimplência, projeção de entradas | `retornar_faturamento=Simplificado` traz os serviços de cada conta. `valor_cobrado` inclui juros de quem pagou atrasado. `situacao` filtra pelo **texto** ("Vencida"); o número da documentação não filtra. `centro_de_custos` vem como lista de rateio (`centro_de_custo`, `valor`, `valor_pago`) |
| `contasPagar/` | despesas pagas, projeção de saídas, rateio | `centro_de_resultados` é a classificação da despesa (Administrativas, Credenciados externos, Impostos…) |
| `contratoCliente/` | contratos ativos e a vencer | Todos mensais, mas **sem o valor da mensalidade** (vem vazio). O valor está nas contas: o serviço "Outro" é a mensalidade dos planos de gestão |
| `fornecedor-valores/` | margem | Exige filtro. Os campos reais são `valor_a_cobrar`/`valor_a_pagar` (a documentação diz `valor_cobrar`/`valor_pagar`). Há uma linha por fornecedor; o fornecedor próprio tem custo zero |
| `centro_de_custos/` | (não usado) | É o centro de custo das **empresas clientes** (RH), não o financeiro; os códigos 2 e 3 do rateio não aparecem ali |

## Decisão

- **Mesmo deploy, job próprio.** No worker: coleta às 05:45 (retentativas 06:30 e 07:15, antes da janela do coletor de agendamentos), e-mail às 07:59 (08:01, 08:03) e verificação às 08:10. São cerca de 40 requisições, divididas em janelas de até 31 dias.
- **Mesmo ciclo de envio do relatório de atendimentos.** `EnviarRelatorio`/`VerificarEnvio` recebem um `TipoRelatorio` (tabela de resumo, lista de destinatários e template). Tabelas novas `resumo_financeiro` e `destinatario_financeiro` (migração 0003). A reserva de envio impede e-mail duplicado.
- **Referência:**
  - Caixa e faturamento são do **dia anterior** e do mês até ele.
  - Projeções e contratos são a **foto do momento da coleta**, porque a API só devolve a situação atual.
  - Reprocessar uma data antiga refaz a foto com a situação de hoje.
- **Regras** (`domain/financeiro.py`, `VERSAO_REGRA_FINANCEIRO` 1.2.0; ver revisões abaixo):
  - Recebido pelo valor cobrado.
  - Projeção de hoje até hoje + N − 1, mais uma coluna até o último dia do mês atual.
  - MRR = mensalidades faturadas nos últimos 30 dias a clientes com contrato ativo.
  - Margem = faturado − quantidade × média do custo dos credenciados (sem os de custo zero).
- **Categorias de serviço por regra de nome**, porque o SGG não classifica os serviços: exames clínicos, complementares (código entre parênteses), programas e laudos (PGR, PCMSO, LTCAT, AET…), mensalidades, faltas e outros. As regras foram conferidas contra os 87 serviços faturados em setembro.
- **"Sem alteração desde ontem"**: MRR, contratos, margem e rateio são comparados com o resumo do dia anterior.
- **LGPD:** de cada conta ficam valores, datas, situação, classificação e o nome do cliente pessoa jurídica. CPF vira "Pessoa física #id"; descrição, links e documentos são descartados.
- **`FINANCEIRO_HABILITADO=false` por padrão.** A prévia e o reenvio pelo admin (`/admin/financeiro`) funcionam sempre, e o envio automático só liga depois da conferência com o financeiro.

## Revisão: remoção do indicador de inadimplência (v1.1.0)

O pedido original de negócio era contar como inadimplente só as contas a receber vencidas **com cobrança cadastrada** (o SGG tem esse filtro na tela "Possui cobrança?" de contas a receber). Não há, porém, nenhum campo ou parâmetro correspondente na API pública do SGG:

- O Swagger (`/api/v3/doc/api.json`) não lista nenhum parâmetro de cobrança em `contasReceber/`.
- O candidato mais próximo, `link_cobranca`, veio vazio em 100% de mais de 1.200 títulos reais testados (emitidas, abertas e vencidas), inclusive nos 160 títulos vencidos reais — ou seja, usá-lo zeraria a inadimplência todo dia.
- Sete nomes de parâmetro plausíveis (`possui_cobranca`, `possuiCobranca`, `tem_cobranca`, `cobranca`, `com_cobranca`, `cobranca_cadastrada`, `possui_cobranca_cadastrada`, cada um com várias variantes de valor) foram testados ao vivo contra `contasReceber/?situacao=Vencida`: a API ignora silenciosamente qualquer parâmetro que não reconhece, então nenhum teste comprova nem refuta a existência do filtro certo.

Sem uma forma confiável de aplicar o filtro pedido, calcular a inadimplência sem ele publicaria um número que não é o que o financeiro pediu (títulos sem cobrança cadastrada entrando na conta). A decisão foi **remover o indicador de inadimplência do relatório** (cartão, seção "Inadimplência e projeção", faixas de atraso e maiores devedores) em vez de publicar um valor sabidamente incorreto. A seção de projeção de entradas e saídas (7/15/30 dias) não depende de "cobrança cadastrada" e foi mantida, em seção própria.

Se um dia o SGG expuser o campo certo (API ou exportação), a inadimplência pode voltar como uma nova versão da regra.

## Revisão: coluna de projeção até o fim do mês (v1.2.0)

Além dos horizontes fixos de 7/15/30 dias, a tabela de projeção ganhou uma coluna com o horizonte até o último dia do mês corrente (calendário, não um N fixo de dias), para responder "quanto ainda entra e sai até fechar o mês". Usa a mesma regra dos demais horizontes (títulos em aberto que vencem de `hoje` até essa data). A janela de coleta de `a_receber`/`a_pagar` já cobre até 30 dias à frente (o maior horizonte fixo), o que é suficiente mesmo no pior caso (dia 1 de um mês de 31 dias), então não precisou mudar.

## Consequências

- O total faturado (soma das contas) difere da soma dos itens de faturamento. O e-mail mostra a diferença como "Descontos, acréscimos e ajustes" para fechar a conta.
- Os nomes dos centros de custo do rateio são configurados no admin, porque a API só informa o código.
- A margem é **estimada** pela tabela de preços. O custo real de cada exame dependeria de cruzar `exames-realizados` com o fornecedor que atendeu, o que fica para uma evolução.
- Mudou uma regra ou uma categoria? Suba `VERSAO_REGRA_FINANCEIRO` e regere as datas no admin.


## Revisão: e-mail enxuto e fluxo de caixa detalhado (v1.3.0)

O e-mail passa a trazer só o essencial de D-1:

- Cartões: saldo operacional de D-1 e faturamento do mês até D-1.
- **Fluxo de caixa de D-1** com cada conta paga ou recebida: conta (cliente ou fornecedor), classificação (`centro_de_resultados`), descrição e valor. Receita em azul; despesa em vermelho com "-".
- **Projeção do caixa** até o fim do mês, 60 e 90 dias (`HORIZONTES_PROJECAO = (60, 90)`; antes 7, 15 e 30).
- Rateio por centro de custo (mês até D-1).
- Saem do e-mail: faturamento por serviço, receita recorrente (MRR), contratos a vencer, margem bruta e despesas por classificação. Esses números continuam sendo calculados e gravados no resumo; só não aparecem no e-mail.
- **LGPD:** a descrição das contas pode ter nome de pessoa. Ela é lida do SGG no envio (`FluxoDoDia`, complemento do `EnviarRelatorio`), vive só em memória (`Titulo.descricao` fora do `repr`) e não entra no resumo gravado. Se o SGG não responder, o e-mail sai sem o detalhamento e o técnico é alertado.

## Revisão: projeção pelo histórico do dia do mês (v1.4.0)

A projeção pela "foto" dos títulos em aberto ficava fora do plausível: o SGG só tem lançados os títulos já emitidos, então as saídas recorrentes (folha, impostos, fornecedores) e as entradas de faturas ainda não emitidas não apareciam, e o resultado dependia de quanto já estava cadastrado no dia.

- **Base:** os 6 meses completos antes do mês da referência (`MESES_HISTORICO = 6`; referência em 15/10 → 01/04 a 30/09). Lidos com `receber_pagos`/`pagar_pagos` numa só coleta, que também alimenta o caixa do mês (o recorte do mês sai da mesma lista). As chamadas de títulos a vencer deixaram de ser feitas.
- **Perfil por dia do mês:** para cada dia 1..31, a soma do valor liquidado (pago/recebido, sem cancelados) nesse dia dividida pelo número de meses da base que têm esse dia (o dia 31 é a média dos meses de 31 dias).
- **Projeção:** de `hoje` até o fim do mês, hoje + 59 e hoje + 89, soma o perfil de cada dia. No último dia de um mês curto entra também o que costuma cair nos dias que ele não tem (30/09 recebe o "dia 31"; 28/02 recebe 29, 30 e 31), para não perder os vencimentos de fim de mês.
- **E-mail:** a tabela continua com entradas, saídas e saldo por horizonte, e o subtítulo diz a base ("média do que entrou e saiu em cada dia do mês de abr/2026 a set/2026 (6 meses)"). Resumos antigos, gravados sem `projecao_base`, mantêm o texto de antes.
- **Limites:** é uma média, então não vê sazonalidade nem contratos novos ou encerrados; um mês atípico na base pesa 1/6. A regra fica em `_perfil_dia_do_mes`/`_previsto` para trocar por outra se o financeiro pedir.

## Revisão: três períodos, gráfico e fluxo retrátil (v1.5.0)

Revisão do cálculo contra um modelo de projeção a 90 dias em meses seguidos (saldo inicial, entradas, saídas, saldo final que vira o inicial do mês seguinte, regime de caixa e desconto de inadimplência).

- **Regime de caixa e inadimplência:** já estavam certos. A base é só o que de fato foi pago ou recebido, então atraso e calote de sempre já estão descontados, sem taxa extra.
- **Três períodos de 30 dias (`projecao_periodos`):** os 90 dias a partir de hoje viram Mês 1, 2 e 3, cada um com entradas, saídas, saldo operacional e saldo acumulado. O acumulado de um período é o saldo inicial do seguinte. As linhas até o fim do mês, 60 e 90 dias (`projecao`) continuam no resumo para telas e resumos antigos.
- **Saldo inicial:** o SGG não informa o saldo bancário, então o acumulado parte de zero e o e-mail avisa que é só a variação projetada. Se o financeiro quiser o caixa total, falta uma fonte (por exemplo, uma configuração com o saldo do dia).
- **Correção, base no mês de hoje:** a base era contada a partir do mês da referência. No dia 1º (relatório do dia 30/09, hoje 01/10), o mês que acabou de fechar ficava fora e a projeção usava um mês velho a menos. Agora a base são os 6 meses completos antes do mês de `hoje`, e a leitura do SGG começa por esse mês.
- **Correção, arredondamento:** o saldo era calculado sobre os valores sem arredondar, e o e-mail podia mostrar entradas menos saídas diferente do saldo por 1 centavo. Agora o saldo sai dos valores já arredondados.
- **Gráfico (`projecao_serie`):** entradas, saídas e saldo acumulados dia a dia (91 pontos). E-mail não executa SVG nem JavaScript, então o gráfico vai como PNG inline (`infrastructure/email/grafico.py`, sem biblioteca de imagem, com fonte de pontos embutida para os rótulos).
- **Estrutura do e-mail:** Indicadores principais, Projeção e gráfico, Fluxo de caixa (rateio e definições depois). O fluxo de caixa do dia é uma lista retrátil (`<details>`) com todas as entradas e todas as saídas; Gmail e Outlook não suportam o elemento e mostram a lista aberta.
