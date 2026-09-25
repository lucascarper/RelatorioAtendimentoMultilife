# ADR 0009 — Coleta e monitor a cada 5 segundos

- **Status:** aceita
- **Data:** 2026-09-25
- **Revê:** o intervalo de 60 s do [ADR 0001](0001-polling-durante-o-expediente.md) e os tempos de atualização do [ADR 0007](0007-monitor-em-tempo-real.md)

## Contexto

O gerente quer o monitor atualizado a cada 5 s. O monitor só lê o banco, então os dados na tela nunca são mais novos que o último ciclo do coletor, que rodava 1×/min. A chave da API do SGG é exclusiva deste sistema, e a API aceita 60 req/min das 05:00 às 20:00.

## Decisão

- **Coleta a cada `COLETA_INTERVALO_S` segundos (padrão 5).** O valor divide 60, de 5 a 60, e o APScheduler roda nos segundos `*/5` de cada minuto. Um ciclo sem movimento é uma requisição, então são 12 req/min. O cursor e a sobreposição de 2 min continuam iguais.
- **Orçamento `SGG_MAX_RPM` sobe de 20 para 40.** Isso cobre uma segunda página nos ciclos cheios e deixa 20 req/min livres abaixo do limite da API. Se o orçamento esgotar, o limitador segura o ciclo, os seguintes são coalescidos (`max_instances=1`) e a coleta desacelera sozinha, sem erro 429.
- **Tela a cada 5 s, cache de 4 s.** O cache compartilhado continua garantindo um cálculo por vez, quantas telas estiverem abertas. No navegador, a próxima busca só sai depois que a anterior termina.
- **O alerta de coleta é por tempo, não por contagem.** O técnico recebe o e-mail depois de 10 min de falhas seguidas (120 ciclos de 5 s), como antes, e não depois de 50 s de instabilidade.
- **Histórico compactado.** O job `compactar_execucoes` (03:20) apaga os ciclos bem-sucedidos dos dias anteriores e mantém o primeiro de cada minuto. As falhas ficam. Isso basta para detectar lacunas de coleta (tolerância de 5 min), inclusive ao reprocessar datas antigas, e mantém a tabela no tamanho que tinha com 1 ciclo/min.

## Consequências

- Os tempos passam a ter precisão de ±5 s (ou a do `data_hora_edicao`), e o monitor fica no máximo ~10 s atrás do SGG.
- O consumo sobe de ~1 para ~12 req/min. Se a chave passar a ser dividida com outra integração, basta subir `COLETA_INTERVALO_S` (10, 15, 30 ou 60), sem mudar código.
