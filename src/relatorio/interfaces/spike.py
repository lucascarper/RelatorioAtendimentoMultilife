"""Spike da seção 10: valida a API real do SGG antes da homologação.

Somente leitura, poucas requisições (≤ 10) e saída só com agregados — nenhum dado de
paciente é impresso. Rode onde a API for acessível:

    SGG_API_KEY=... relatorio spike-sgg --data 2026-09-23
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.infrastructure.sgg.cliente import ClienteSgg
from relatorio.infrastructure.sgg.erros import ErroSgg


def executar_spike(cliente: ClienteSgg, dia: date, agora: datetime) -> dict[str, Any]:
    relatorio: dict[str, Any] = {"data_analisada": dia.isoformat()}

    try:
        agendas = cliente.agendas()
    except ErroSgg as erro:
        return {**relatorio, "autenticacao": f"FALHOU: {erro}"}
    relatorio["autenticacao"] = "ok"
    relatorio["agendas"] = {
        "total": len(agendas),
        "ativas": sum(a.ativa for a in agendas),
        "com_sala": sum(bool(a.sala) for a in agendas),
        "unidades": sorted({a.unidade_atendimento or "—" for a in agendas}),
    }

    antes = cliente.requisicoes_realizadas
    registros = cliente.agendamentos_do_dia(dia)
    paginas = cliente.requisicoes_realizadas - antes
    situacoes = Counter(r.situacao.value for r in registros)
    edicoes = [r.data_hora_edicao for r in registros if r.data_hora_edicao]
    relatorio["agendamentos_do_dia"] = {
        "total": len(registros),
        "paginas_de_100": paginas,
        "por_situacao": dict(situacoes),
        "sem_hora_agendada": sum(r.hora_agendamento is None for r in registros),
        "sem_id_unidade": sum(r.id_unidade_atendimento is None for r in registros),
        "agendas_sem_cadastro": sorted(
            {r.agenda_nome for r in registros} - {a.nome for a in agendas}
        ),
        "edicao_mais_recente": max(edicoes).isoformat() if edicoes else None,
    }
    # Orçamento: 1 página por ciclo de polling cobre até 100 edições por minuto.
    relatorio["orcamento_estimado"] = {
        "requisicoes_por_ciclo": 1 if len(registros) < 100 else paginas,
        "reconciliacao_por_dia": paginas,
        "limite_do_sistema_por_minuto": 20,
    }

    janela = cliente.agendamentos_editados(agora - timedelta(minutes=15), agora)
    relatorio["filtro_editado_ultimos_15_min"] = {
        "registros": len(janela),
        "edicoes_no_futuro": sum(
            1 for r in janela if r.data_hora_edicao and r.data_hora_edicao > agora
        ),
        "observacao": (
            "Mude a situação de um agendamento de teste no SGG e rode de novo: ele "
            "deve aparecer aqui com data_hora_edicao próxima do horário da mudança."
        ),
    }
    relatorio["fuso"] = {
        "agora_brasilia": agora.astimezone(FUSO_BRASILIA).isoformat(),
        "diferenca_ultima_edicao_min": (
            round((agora - max(edicoes)).total_seconds() / 60) if edicoes else None
        ),
        "dica": "Diferença negativa indica data_hora_edicao em outro fuso (ex.: UTC).",
    }
    relatorio["faltas_aparecem_em_agendamento"] = situacoes.get("Faltou", 0) > 0
    relatorio["requisicoes_usadas"] = cliente.requisicoes_realizadas
    return relatorio
