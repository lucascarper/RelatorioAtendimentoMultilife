# ADR 0005 — Conferência final no SGG antes da consolidação (RF11 ampliado)

- **Status:** aceita
- **Data:** 2026-09-24

## Contexto

O RF11 pede que, antes de contar um agendamento como "sem baixa", o sistema confira se há falta registrada na agenda do consultório. Na simulação apareceram dois casos que só o RF11 literal não cobre:

1. Faltas lançadas pela recepção depois das 18:30 (após a reconciliação).
2. Atendimentos que começaram por fila no fim da tarde e terminaram depois das 18:30. Eles ficariam "Em Atendimento" para sempre, e o número de atendimentos do dia sairia menor.

## Decisão

Às 23:00, se houver agendamentos pendentes (Agendado, Aguardando ou Em Atendimento), a consolidação relê o dia inteiro no SGG. Isso custa o mesmo número de requisições que só as faltas.

- Os pendentes que viraram "Faltou" são gravados com origem `verificacao_falta` e contados como faltas (RF11). O e-mail informa quantas faltas vieram da agenda.
- As demais finalizações tardias entram no log com origem `reconciliacao`.
- Se o SGG estiver fora, a consolidação segue com o que foi coletado, e o e-mail avisa "conferência de faltas indisponível".

## Consequências

As contagens do dia batem com o SGG às 23:00. Isso foi verificado pelo teste ponta a ponta, contra um oráculo independente.
