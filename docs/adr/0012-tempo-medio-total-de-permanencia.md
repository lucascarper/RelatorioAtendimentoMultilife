# ADR 0012: Renomeação dos tempos e Tempo Médio Total de Permanência

- **Status:** aceita
- **Data:** 2026-09-29

## Contexto

O termo geral para os indicadores de tempo do relatório (e-mail e monitor ao vivo) passou a
ser "Tempos de Atendimento". Pedido de negócio: renomear os quatro cartões existentes (com
guichês marcados) e acrescentar uma métrica consolidada com o tempo total que uma pessoa fica
na clínica.

## Decisão

- **Renomeação dos rótulos** (só o texto exibido; as chaves internas `espera_consultorio_s`,
  `espera_recepcao_s`, `tma_consultorios_s` e `tma_guiches_s` não mudam, para não quebrar o
  histórico de `resumo_diario.metricas`):

  | Rótulo antigo | Rótulo novo |
  | --- | --- |
  | Espera na recepção | Tempo de Espera - Recepção |
  | TMA dos guichês | Tempo de Atendimento - Recepção |
  | Espera no consultório | Tempo de Espera - Consultório |
  | TMA dos consultórios | Tempo de Consulta |

- **Tempo Médio Total de Permanência** (`permanencia_total_s`, novo campo em `Kpis`): pedido
  como "o tempo médio que uma pessoa fica na clínica, do Aguardando no guichê até o Atendido
  no último consultório". Isso pressupõe ligar o agendamento do guichê ao do consultório da
  mesma pessoa — mas o SGG não guarda essa ligação, e o domínio (RNF06) não lê nenhum dado
  pessoal que permitiria inferi-la. Decisão: calcular como a **soma das quatro médias** já
  medidas (Tempo de Espera - Recepção + Tempo de Atendimento - Recepção + Tempo de Espera -
  Consultório + Tempo de Consulta). Por linearidade da média, isso é matematicamente a média
  do tempo total **se** a população de pacientes do guichê e dos consultórios for
  essencialmente a mesma no dia (o fluxo normal da clínica) — não é uma medida pessoa a
  pessoa. Só aparece com guichês marcados (`tem_guiche`); sem guichê, fica `None`.
- Aparece como um terceiro grupo de cartões ("Permanência total"), depois de "Consultórios" e
  "Recepção (guichês)", no e-mail e no monitor (mesma função `montar_apresentacao`), e como um
  indicador a mais no comparativo semanal.
- `VERSAO_REGRA` sobe para 1.3.0.

## Consequências

- Se um dia o SGG passar a expor uma ligação entre o agendamento do guichê e o do consultório
  da mesma pessoa, a permanência total pode virar uma medida pessoa a pessoa (nova versão da
  regra).
- A soma de médias fica sensível a dias em que o volume de guichê e de consultório for muito
  diferente (ex.: um exame sem passagem pelo guichê nesse dia); o rótulo e a nota do e-mail
  deixam claro que é uma média consolidada, não uma medida individual.
