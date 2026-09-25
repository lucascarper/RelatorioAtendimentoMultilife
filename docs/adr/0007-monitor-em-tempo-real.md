# ADR 0007 — Monitor em tempo real lendo só o banco

- **Status:** aceita (intervalos revistos no [ADR 0009](0009-coleta-a-cada-5-segundos.md): coleta e tela a cada 5 s, cache de 4 s)
- **Data:** 2026-09-25

## Contexto

O gerente pediu uma tela de monitoramento ao vivo, dentro do admin, com os mesmos indicadores do e-mail diário. A API do SGG tem cota de requisições (`SGG_MAX_RPM`), e o coletor já consulta a API uma vez por minuto (ADR 0001).

## Decisão

- **O monitor não chama o SGG.** Ele lê os eventos que o coletor já gravou no PostgreSQL. Abrir a tela em vários navegadores (TV da recepção, celular do gerente) não muda o consumo da API. Os dados chegam com até ~1 min de atraso, que é o ciclo do coletor. A tela mostra o horário do último ciclo bem-sucedido ("Dados do SGG de 10:40:00") e avisa quando a coleta atrasa.
- **Mesmo cálculo do e-mail.** `calcular_metricas` roda sobre `[hoje 00:00, agora]` (a "evolução futura" prevista na seção 7). A apresentação também é a do e-mail: mesmos cartões, turnos, consultórios, agendas e alertas.
- **Comparação justa.** Hoje até agora é comparado com o mesmo dia da semana anterior **até o mesmo horário**. Comparar a manhã de hoje com o dia inteiro da semana passada distorceria todos os totais.
- **Indicadores que só existem ao vivo** (`domain/ao_vivo.py`): quem está na recepção e a maior espera em curso, quem está em atendimento, quem ainda não chegou (e quantos estão com o horário vencido há mais de 15 min), e chegadas × atendimentos finalizados por hora.
- **"Sem baixa" e "não finalizados" não são alerta durante o dia.** São a fila normal e aparecem nos cartões de agora. Os demais alertas (falha de coleta, atípicos, status que pulou etapas) continuam.
- **Carga controlada.** O navegador busca um fragmento HTML a cada 30 s com HTMX, só com a aba visível. No servidor, o resultado fica 20 s em cache compartilhado, com trava: monitores simultâneos dividem um único cálculo.
- **Gráfico em SVG gerado no servidor**, sem biblioteca de gráficos. A CSP continua `script-src 'self'`, e os números aparecem sem JavaScript, com uma tabela alternativa para cada gráfico. Paleta validada para daltonismo: azul da marca e laranja (ΔE CVD 25,5). O vermelho da marca fica reservado para alertas.

## Consequências

- A atualização mais rápida possível continua sendo o ciclo do coletor (60 s). Diminuir esse intervalo é decisão do ADR 0001, não do monitor, porque aumentaria o consumo da API.
- Se no futuro o painel em tempo real do SGG gravar os eventos (`COLETOR_HABILITADO=false`, ADR 0002), o monitor continua funcionando sem mudança: ele só lê a tabela de eventos.
