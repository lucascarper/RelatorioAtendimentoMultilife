# ADR 0001 — Polling durante o expediente em vez de coleta noturna

- **Status:** aceita
- **Data:** 2026-09-24

## Contexto

`GET /agendamento/` devolve apenas a **situação atual** e a `data_hora_edicao` da última edição. Uma consulta feita à noite mostraria todos como "Atendido", sem os horários de "Aguardando" e de "Em Atendimento". Sem esses horários, não há tempo de espera, tempo de atendimento nem TMA.

## Decisão

Fazer polling incremental a cada 60 s, das 06:00 às 18:00 (`editado_aPartirDe`/`editado_ate`), gravar cada transição em `agendamento_evento` (log imutável) e só consolidar à noite.

- O cursor fica no banco (`coletor_cursor`), então o coletor retoma após reinício (RNF04). Cada ciclo relê 2 min antes do cursor.
- `ocorrido_em = data_hora_edicao` quando essa edição é mais nova que a última conhecida e não está no futuro. Nos demais casos, vale o horário da observação.
- Duplicatas são barradas duas vezes: pela comparação com o snapshot e pelo `UNIQUE (id_agendamento, status_novo, ocorrido_em)`.
- A reconciliação das 18:30 varre o dia inteiro por `data_hora_agendamento`.

## Consequências

- Precisão de ±60 s no pior caso (na prática, a do próprio `data_hora_edicao`).
- Duas mudanças dentro do mesmo minuto aparecem como uma só, um "salto". O atendimento conta nos totais, mas fica "sem tempo medido", e o e-mail lista esses casos.
- Um worker sempre ligado (APScheduler). O Cron da Railway não serve: o intervalo mínimo dele é de 5 min e o horário é em UTC.
