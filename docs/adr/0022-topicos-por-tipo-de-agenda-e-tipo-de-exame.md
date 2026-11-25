# ADR 0022: Tópicos por tipo de agenda e detalhe por tipo de exame

- **Status:** aceita
- **Data:** 2026-11-25

## Contexto

O relatório de atendimento separava só duas áreas: guichês (agendas marcadas no admin) e
"consultórios e demais agendas". Salas de exames (audiometria, raio-x, laboratório) e
consultórios médicos caíam no mesmo grupo, com tempos de natureza diferente misturados. O
pedido: classificar cada agenda como **sala de exames**, **consultório** ou **guichê**, mostrar
o relatório em três tópicos (totais, tempo de espera e tempo de atendimento ou consulta) e, em
cada cartão, o subtotal por tipo de exame (admissional, demissional, periódico, mudança de
riscos ocupacionais, retorno ao trabalho).

## Decisão

- **Classificação:** `agenda.guiche` (booleano) vira `agenda.tipo` (`sala_exames`,
  `consultorio` ou `guiche`, com check constraint). A migração 0013 leva os guichês marcados
  para `guiche` e o resto para `consultorio`, que era como o relatório já os tratava. A
  escolha é do admin (*Atendimento → Configurar Agendas*, coluna Tipo); a sincronização com o
  SGG nunca sobrescreve. `Agenda.guiche` continua como propriedade, então monitor, espera do
  consultório (última espera quando há guichês) e permanência total não mudam.
- **Tipo de exame:** é o campo `tipo` do agendamento no SGG, que a coleta já recebe. Passa a
  ser gravado em `agendamento_snapshot.tipo_exame` (não é dado pessoal). A exportação continua
  relendo o SGG (ADR 0016); o relatório diário usa o gravado, então reprocessar não depende do
  SGG. O texto é normalizado por prefixo, sem acento nem caixa; "Mudança de Função" (nome
  antigo na NR-7) conta como Mudança de Riscos Ocupacionais. Vazio ou outro texto vai para
  **Outros**, que só aparece quando há algum.
- **Cálculo:** `MetricasPeriodo.por_topico` (`resumo_diario.metricas.por_topico`) tem, para
  cada tipo de agenda, o resumo do tópico e um resumo por tipo de exame, com as mesmas regras do
  resto do relatório: cancelados fora, atípicos fora das médias. Agenda sem cadastro conta como
  consultório. O dashboard soma os dias como já fazia com os turnos: contagens somadas, tempos
  ponderados.
- **Apresentação:** no e-mail e no monitor, os cartões passam a ser "Visão geral" (atendimentos
  e faltas), um grupo por tópico com movimento (salas de exames, consultórios, guichês) e
  "Permanência total" quando há guichês. No e-mail o detalhe por tipo fica sempre aberto,
  numa lista com o título "Por tipo de exame" dentro do cartão. No dashboard do admin, cada
  cartão de tópico é um `<details>`/`<summary>` que abre a lista logo abaixo, pelo mouse ou
  pelo teclado. Os cartões de tópico não têm variação contra a semana anterior; o comparativo
  semanal continua no fim do e-mail.
- **Resumos antigos** (sem `por_topico`) seguem com os grupos de antes (consultórios × recepção)
  até serem reprocessados. Dias coletados antes desta versão não têm tipo de exame gravado:
  reprocessados, os exames deles aparecem como Outros.
- `VERSAO_REGRA` sobe para 1.4.0.
