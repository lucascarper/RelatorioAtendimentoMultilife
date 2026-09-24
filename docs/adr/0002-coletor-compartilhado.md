# ADR 0002 — Coletor compartilhado com o Painel de Tempo de Atendimento

- **Status:** aceita
- **Data:** 2026-09-24

## Contexto

O painel em tempo real e o relatório diário precisam do mesmo log de transições. Dois pollers gastariam o dobro da cota da API (60 req/min no expediente) e poderiam gravar dados conflitantes.

## Decisão

Um único coletor grava `agendamento_evento`. Como o relatório ficou pronto primeiro, o coletor está implementado aqui, isolado em `application/coleta.py` e `infrastructure/sgg/`. Ele não depende de nada do relatório.

- `COLETOR_HABILITADO=false` desliga o polling e a reconciliação deste serviço. O relatório passa a só ler a tabela gravada pelo painel.
- A leitura é genérica: `ObterMetricasPeriodo.executar(inicio, fim)`. A consolidação noturna usa `ontem 00:00–23:59`, e uma futura consulta ao vivo usará `hoje 00:00–agora`, sem novo coletor nem nova tabela.

## Consequências

- Orçamento do sistema: ≤ 20 req/min (limitador em janela deslizante e backoff em HTTP 429). Sobra folga para o painel.
- Com o coletor desligado, o alerta "falha de coleta" do e-mail também é desligado, porque o monitoramento da coleta passa a ser responsabilidade do painel.
