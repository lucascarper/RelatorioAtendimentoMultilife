# ADR 0006 — Interpretações das regras de cálculo e ajustes operacionais

- **Status:** aceita
- **Data:** 2026-09-24

Pontos em que a documentação técnica não era explícita e a escolha feita:

| Tema | Decisão | Por quê |
| --- | --- | --- |
| Agendamento sem hora (agenda por ordem de chegada) | Turno pela hora da chegada ("Aguardando"); sem chegada, pelo primeiro evento do dia. Sem nenhum dos dois, entra só nos totais (o e-mail avisa) | O turno é "pela hora agendada", mas essas agendas não têm hora |
| Paciente volta para a espera | Espera = da 1ª chegada até a 1ª chamada; atendimento = da última chamada até "Atendido" | Mede a espera percebida e o atendimento efetivo |
| Tempos com eventos de outro dia | Espera e atendimento usam só eventos do próprio dia do agendamento | Evita "esperas de vários dias" (remarcações, criação antiga) |
| "Em Atendimento" no fim do dia | Não entra em "sem baixa" (que é Agendado ou Aguardando, pela spec); vira o alerta "atendimentos não finalizados" | Fiel à spec, sem esconder o problema |
| Base da taxa de faltas | Faltas ÷ agendados do dia, excluindo cancelados | Um cancelamento não é oportunidade de falta |
| Comparativo | Mesmo dia da semana anterior. Taxas variam em p.p. e o resto em %. Abaixo de 0,5% conta como "estável". TMA é neutro (sem verde/vermelho) | Boas práticas de leitura: cor só quando a direção é inequívoca |
| Falha de coleta | Lacuna maior que 5 min entre ciclos bem-sucedidos, dentro da janela. Começa no ciclo seguinte ao último sucesso | Pega API fora e worker parado; mesma tolerância do `/health` |
| Turnos configuráveis | Admin define início da manhã, início da tarde (corte) e fim da tarde; fora da janela, vale o turno mais próximo | Nenhum agendamento fica sem turno por um encaixe fora do horário |
| 8ª tabela | `coletor_cursor` guarda o cursor do polling | RNF04, sem misturar estado interno com a configuração do admin |
| Health check | `/health/live` para o deploy da Railway; `/health` (com a regra dos 5 min) para monitoramento | Um worker parado não pode impedir o deploy do web |
| Exemplo de assunto | 23/09/2026 é **quarta-feira** (a spec cita "terça-feira") | O dia da semana é calculado, não digitado |
