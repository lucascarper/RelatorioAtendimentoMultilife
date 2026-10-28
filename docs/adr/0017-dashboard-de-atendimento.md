# ADR 0017: Dashboard do atendimento

- **Status:** aceita
- **Data:** 2026-10-28

## Contexto

A tela do módulo Atendimento juntava ferramentas de operação: reprocessar, últimos 14 dias, processar e exportar período, destinatários. Faltava uma visão dos indicadores do mês. Foi pedido que a tela principal do módulo vire um dashboard, com o mês atual como padrão, e que as funções de hoje fiquem numa tela à parte, aberta por um botão.

## Decisão

- **Duas telas no mesmo módulo** (`atendimento`, sem permissão nova):
  - `/admin/atendimento` é o dashboard. `/admin` continua levando a ele.
  - `/admin/atendimento/gestao` é a tela antiga, com o título "Gestão do relatório" e o link "← Dashboard". Mantém "Configurar Agendas", que só fica nela, e perdeu o botão do ao vivo.
- **Menu:** o novo item "Atendimento ao VIVO" fica logo abaixo de Atendimento, com o ícone em vermelho, e leva ao monitor. No monitor, só esse item fica marcado.
- **Período:**
  - Padrão: do dia 1º até ontem. No dia 1º, o mês anterior inteiro.
  - A data inicial e a final ficam na URL (`?inicio=&fim=`), com no máximo 31 dias e sem data futura (as mesmas regras e mensagens da exportação).
  - Um período inválido mostra o erro e cai no padrão.
- **Fonte:** só os resumos diários já consolidados (às 23:00). O dia de hoje não entra, porque o ao vivo cobre o dia. Assim os números batem com os e-mails.
- **Agregação** (`domain/dashboard.py`):
  - Contagens são somadas, e a taxa de faltas é recalculada pelas somas.
  - O TMA é a média ponderada pelos atendimentos medidos.
  - A espera e os tempos por grupo (consultório e recepção) são ponderados pelos atendimentos, porque o resumo não grava quantas esperas foram medidas.
  - A permanência total é a soma das quatro médias, como no dia.
  - Não há mediana do período.
- **Comparação:** o mesmo intervalo no mês anterior (1–27/out contra 1–27/set), com as setas e as regras de `comparativo.py`. Os alertas entram por `comparar_contagem`. Sem dados no período anterior, o card mostra "sem base de comparação".
- **Tela:**
  - Uma linha com a situação da coleta.
  - Seis cards: atendimentos, faltas, espera média, TMA, permanência total e alertas.
  - Tabela por turno (manhã e tarde), sem comparação.
  - Gráfico diário de atendimentos e faltas. Dias sem movimento ou sem resumo ficam como lacuna.
  - Em tela larga (acima de 1300 px), a tabela por turno fica ao lado dos cards. Abaixo disso, fica embaixo do gráfico.
- **Gráfico com Chart.js 4.4.9** (MIT):
  - O arquivo fica no repositório (`static/admin/chart.umd.min.js`, sha256 conferido com o publicado no jsDelivr), como o htmx. O CSP continua `script-src 'self'`.
  - Os dados vão num `<script type="application/json">`, e `dashboard.js` monta o gráfico com as cores dos tokens do CSS.
  - Uma tabela "Ver os números em tabela" serve de alternativa acessível.

## Consequências

- O dashboard nunca mostra o dia de hoje. Para o dia corrente, use o Atendimento ao VIVO.
- A espera média do período é uma aproximação, porque é ponderada pelos atendimentos e não pelas esperas medidas. Se a diferença passar a importar, o resumo diário precisa gravar a contagem de esperas.
- Mudar regras e reprocessar dias muda o dashboard, porque ele lê os resumos gravados.
- Atualizar o Chart.js significa trocar o arquivo e esta versão no ADR.
