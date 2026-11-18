"""Dashboards (atendimento, financeiro e SESMT): resumo do período → valores para a tela.

Reaproveita os cartões e as setas do e-mail (``montar_delta``, ``delta_do_item``). Os
gráficos são desenhados no navegador pelo Chart.js a partir da configuração que a página
leva em JSON (``dashboard.js``): rótulos, séries, tipo (barra ou linha) e cor.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from relatorio.application.manutencao import subtrair_meses
from relatorio.domain.comparativo import Sentido, comparar, comparar_valor
from relatorio.domain.dashboard import ResumoPeriodo
from relatorio.domain.dashboard_financeiro import PeriodoFinanceiro
from relatorio.domain.exportacao import validar_periodo
from relatorio.domain.resumo import para_json
from relatorio.domain.sesmt import TIPOS_DOCUMENTO
from relatorio.infrastructure.email.apresentacao import (
    CartaoKpi,
    Delta,
    delta_do_item,
    duracao,
    montar_delta,
    numero,
    percentual,
)
from relatorio.infrastructure.email.apresentacao_financeira import moeda
from relatorio.infrastructure.email.apresentacao_sesmt import (
    MESES,
    documentos_texto,
    manchete,
    nome_mes,
    notas,
    plural,
    tipos_texto,
)
from relatorio.infrastructure.email.apresentacao_sesmt import percentual as percentual_br


class _Repositorio(Protocol):
    def listar_indicadores(
        self, inicio: date, fim: date, caminhos: Sequence[tuple[str, ...]]
    ) -> list[tuple[date, dict[str, Any]]]: ...


@dataclass(frozen=True, slots=True)
class PainelDashboard:
    periodo: ResumoPeriodo
    anterior: ResumoPeriodo
    cartoes: tuple[CartaoKpi, ...]
    comparacao: str
    grafico: dict[str, Any]


def periodo_padrao(hoje: date) -> tuple[date, date]:
    """Do dia 1º até ontem; no dia 1º, o mês anterior inteiro (o de hoje ainda não tem dado)."""
    ontem = hoje - timedelta(days=1)
    return ontem.replace(day=1), ontem


def escolher_periodo(inicio: str, fim: str, hoje: date) -> tuple[date, date, str | None]:
    """Período pedido na URL; se inválido, o padrão e a mensagem de erro para a tela."""
    if not inicio and not fim:
        return (*periodo_padrao(hoje), None)
    try:
        de, ate = date.fromisoformat(inicio), date.fromisoformat(fim)
    except ValueError:
        return (*periodo_padrao(hoje), "Informe a data inicial e a data final.")
    try:
        validar_periodo(de, ate, hoje)  # máximo de 31 dias, sem data futura
    except ValueError as erro:
        return (*periodo_padrao(hoje), str(erro))
    return de, ate, None


def periodo_anterior(inicio: date, fim: date) -> tuple[date, date]:
    """O mesmo número de dias a partir do mesmo dia do mês anterior (1–27/out → 1–27/set).

    Contar os dias, e não subtrair um mês do fim, evita que 29–31/03 vire só 28/02. O fim
    para no dia antes do início: o mês inteiro de março compara com 01–28/02, sem repetir dia.
    """
    de = subtrair_meses(inicio, 1)
    return de, min(de + (fim - inicio), inicio - timedelta(days=1))


def carregar_periodos[Resumo: (ResumoPeriodo, PeriodoFinanceiro)](
    repositorio: _Repositorio,
    de: date,
    ate: date,
    caminhos: Sequence[tuple[str, ...]],
    resumir: Callable[[date, date, Iterable[tuple[date, Mapping[str, Any]]]], Resumo],
) -> tuple[Resumo, Resumo]:
    """O período e o mesmo intervalo do mês anterior, comparado só até o último dia com
    resumo: um dia ainda sem resumo não pesa contra."""
    atual = resumir(de, ate, repositorio.listar_indicadores(de, ate, caminhos))
    antes_de, antes_ate = periodo_anterior(de, atual.ultimo_dia or ate)
    return atual, resumir(
        antes_de, antes_ate, repositorio.listar_indicadores(antes_de, antes_ate, caminhos)
    )


def grafico(
    id_: str,
    titulo: str,
    nota: str,
    dias: Iterable[date | str],
    series: Sequence[tuple[str, Sequence[float | None], str, str]],
    *,
    formato: str = "numero",
    empilhado: bool = False,
    horizontal: bool = False,
    eixo: str = "Dia",
) -> dict[str, Any]:
    """Configuração de um gráfico. ``series``: (rótulo, valores, cor, tipo), com a cor como
    token do CSS (ex.: ``--serie-atendidos``) e o tipo ``bar`` ou ``line``. Os rótulos são
    dias (viram DD/MM) ou textos; ``eixo`` nomeia a coluna na tabela alternativa. Valor None
    vira lacuna (dia sem resumo)."""
    return {
        "id": id_,
        "titulo": titulo,
        "nota": nota,
        "formato": formato,
        "empilhado": empilhado,
        "horizontal": horizontal,
        "eixo": eixo,
        "rotulos": [f"{d:%d/%m}" if isinstance(d, date) else d for d in dias],
        "series": [{"rotulo": r, "dados": list(v), "cor": c, "tipo": t} for r, v, c, t in series],
    }


@dataclass(frozen=True, slots=True)
class GrupoCartoes:
    titulo: str
    comparacao: str
    cartoes: tuple[CartaoKpi, ...]


@dataclass(frozen=True, slots=True)
class PainelRelatorio:
    """Dashboard do financeiro ou do SESMT: grupos de cartões, gráficos e uma tabela extra."""

    inicio: date
    fim: date
    ultimo_dia: date | None
    vazio: bool
    grupos: tuple[GrupoCartoes, ...]
    graficos: tuple[dict[str, Any], ...]
    tabela: dict[str, Any] | None = None


def _seta(
    chave: str, atual: float | None, anterior: float | None, sentido: Sentido, rotulo: str
) -> Delta | None:
    if anterior is None:
        return None
    return delta_do_item(para_json(comparar_valor(chave, chave, atual, anterior, sentido)), rotulo)


def _grupos(
    periodo: tuple[Any, Any, Sequence[tuple[str, str, str, str, Sentido]]],
    foto: tuple[Any, Any, Sequence[tuple[str, str, str, str, Sentido]]],
    intervalo: str,
) -> tuple[GrupoCartoes, ...]:
    """Cartões "No período" (somas) e "Em DD/MM" (foto do último dia), cada um com a sua
    base de comparação. Cada cartão: (rótulo, valor formatado, contexto, atributo, sentido)."""
    atual, base, cartoes = periodo
    foto_atual, foto_base, cartoes_foto = foto

    def montar(
        origem: Any, comparar_com: Any, itens: Sequence[Any], rotulo: str
    ) -> tuple[CartaoKpi, ...]:
        return tuple(
            CartaoKpi(
                rotulo=r,
                valor=valor,
                contexto=contexto,
                delta=_seta(
                    attr,
                    getattr(origem, attr),
                    getattr(comparar_com, attr) if comparar_com is not None else None,
                    sentido,
                    rotulo,
                ),
            )
            for r, valor, contexto, attr, sentido in itens
        )

    grupos = [
        GrupoCartoes(
            titulo="No período",
            comparacao=(
                f"comparado com {intervalo}"
                if base is not None
                else "sem resumos no mesmo período do mês anterior"
            ),
            cartoes=montar(atual, base, cartoes, f"vs {intervalo}"),
        )
    ]
    if foto_atual is not None:
        base_foto = f"{foto_base.data:%d/%m}" if foto_base is not None else ""
        grupos.append(
            GrupoCartoes(
                titulo=f"Em {foto_atual.data:%d/%m}",
                comparacao=(
                    f"último dia com resumo, comparado com {base_foto}"
                    if foto_base is not None
                    else "último dia com resumo; sem base de comparação"
                ),
                cartoes=montar(foto_atual, foto_base, cartoes_foto, f"vs {base_foto}"),
            )
        )
    return tuple(grupos)


def montar_financeiro(atual: PeriodoFinanceiro, anterior: PeriodoFinanceiro) -> PainelRelatorio:
    base = None if anterior.vazio else anterior
    f = atual.foto
    intervalo = f"{anterior.inicio:%d/%m} a {anterior.fim:%d/%m}"
    maior, menor = Sentido.MAIOR_MELHOR, Sentido.MENOR_MELHOR
    periodo = (
        (
            "Receita recebida",
            moeda(atual.recebido),
            f"{numero(atual.titulos_recebidos)} títulos",
            "recebido",
            maior,
        ),
        (
            "Despesas pagas",
            moeda(atual.pago),
            f"{numero(atual.titulos_pagos)} títulos",
            "pago",
            menor,
        ),
        ("Saldo operacional", moeda(atual.saldo), "recebido menos pago", "saldo", maior),
        (
            "Faturamento",
            moeda(atual.faturamento),
            f"{numero(atual.titulos_faturados)} títulos emitidos",
            "faturamento",
            maior,
        ),
    )
    fotos = (
        (
            (
                "MRR",
                moeda(f.mrr),
                f"{numero(f.clientes_mrr)} clientes com mensalidade",
                "mrr",
                maior,
            ),
            ("Contratos ativos", numero(f.contratos_ativos), "no SGG", "contratos_ativos", maior),
            (
                "Contratos a vencer",
                numero(f.contratos_a_vencer),
                f"{moeda(f.mensalidades_a_vencer)} em mensalidades",
                "contratos_a_vencer",
                menor,
            ),
        )
        if f
        else ()
    )
    return PainelRelatorio(
        inicio=atual.inicio,
        fim=atual.fim,
        ultimo_dia=atual.ultimo_dia,
        vazio=atual.vazio,
        grupos=_grupos((atual, base, periodo), (f, base.foto if base else None, fotos), intervalo),
        graficos=(
            grafico(
                "grafico-caixa",
                "Caixa por dia",
                "Recebido e pago em cada dia; a linha é o saldo acumulado no período.",
                (d.data for d in atual.dias),
                (
                    ("Recebido", [d.recebido for d in atual.dias], "--serie-atendidos", "bar"),
                    ("Pago", [d.pago for d in atual.dias], "--serie-chegadas", "bar"),
                    ("Saldo acumulado", [d.saldo_acumulado for d in atual.dias], "--ok", "line"),
                ),
                formato="moeda",
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class PainelSesmt:
    """Dashboard do SESMT: a foto da última coleta (o mesmo do e-mail) e o histórico."""

    referencia: date
    gerado_em: datetime
    manchete: str
    notas: tuple[str, ...]
    cartoes: tuple[CartaoKpi, ...]
    grupos: dict[str, Any]  # gráfico de vencidos por grupo
    mes_seguinte: str
    vencem: list[dict[str, str]]
    mes_esocial: str
    rejeitados: list[dict[str, str]]
    historico: tuple[dict[str, Any], ...]


CORES_TIPO = {"PGR": "--serie-atendidos", "PCMSO": "--serie-chegadas", "LTCAT": "--serie-terceira"}


def montar_sesmt(
    m: Mapping[str, Any],
    anterior: Mapping[str, Any] | None,
    vencidos_por_dia: Sequence[tuple[date, int | None]],
) -> PainelSesmt:
    """``m``: métricas da última coleta; ``anterior``: as da coleta antes dela (setas)."""
    v, p, e = m["vencidos"], m["proximo_mes"], m["esocial"]
    ant = e["anterior"]
    base_esocial = (
        f"vs {date.fromisoformat(ant['inicio']):%d/%m} a {date.fromisoformat(ant['fim']):%d/%m}"
    )
    base_dia = f"vs {date.fromisoformat(anterior['referencia']):%d/%m}" if anterior else ""
    mesmo_mes = anterior is not None and anterior["proximo_mes"]["inicio"] == p["inicio"]
    mes = nome_mes(p["inicio"])
    menor, neutro = Sentido.MENOR_MELHOR, Sentido.NEUTRO
    cartoes = (
        CartaoKpi(
            "Documentos vencidos",
            numero(v["total"]),
            f"em {plural(v['empresas'], 'empresa', 'empresas')}",
            _seta(
                "vencidos",
                v["total"],
                anterior["vencidos"]["total"] if anterior else None,
                menor,
                base_dia,
            ),
        ),
        CartaoKpi(
            f"Vencem em {mes.split(' de ')[0]}",
            numero(p["total"]),
            f"em {plural(p['empresas_total'], 'empresa', 'empresas')}",
            _seta(
                "vencem",
                p["total"],
                anterior["proximo_mes"]["total"] if anterior and mesmo_mes else None,
                menor,
                base_dia,
            ),
        ),
        CartaoKpi(
            "eSocial enviados",
            numero(e["enviados"]),
            "eventos gerados no mês",
            _seta("enviados", e["enviados"], ant["enviados"], neutro, base_esocial),
        ),
        CartaoKpi(
            "eSocial rejeitados",
            numero(e["rejeitados"]),
            f"{percentual_br(e['rejeitados'], e['enviados'])} dos enviados, sem recibo",
            _seta("rejeitados", e["rejeitados"], ant["rejeitados"], menor, base_esocial),
        ),
    )
    por_grupo = v["por_grupo"]
    mensal = m["esocial_mensal"]
    return PainelSesmt(
        referencia=date.fromisoformat(m["referencia"]),
        gerado_em=datetime.fromisoformat(m["gerado_em"]),
        manchete=manchete(m),
        notas=notas(m),
        cartoes=cartoes,
        grupos=grafico(
            "grafico-grupos",
            "Vencidos por grupo de empresas",
            "PGR, PCMSO e LTCAT vencidos (o documento mais recente de cada empresa e tipo).",
            [g["grupo"] for g in por_grupo],
            [
                (t, [g["por_tipo"].get(t, 0) for g in por_grupo], CORES_TIPO[t], "bar")
                for t in TIPOS_DOCUMENTO
            ],
            empilhado=True,
            horizontal=True,
            eixo="Grupo",
        ),
        mes_seguinte=mes,
        vencem=[
            {
                "empresa": x["empresa"],
                "cnpj": x.get("cnpj") or "",
                "grupo": x["grupo"],
                "documentos": documentos_texto(x["documentos"]),
            }
            for x in p["empresas"]
        ],
        mes_esocial=nome_mes(e["inicio"]),
        rejeitados=[
            {
                "empresa": x["empresa"],
                "cnpj": x.get("cnpj") or "",
                "grupo": x["grupo"],
                "quantidade": numero(x["quantidade"]),
                "tipos": tipos_texto(x["tipos"]),
            }
            for x in e["rejeitados_por_empresa"]
        ],
        historico=(
            grafico(
                "grafico-vencidos-dia",
                "Documentos vencidos dia a dia",
                "Total na coleta de cada dia, desde a nova versão do relatório.",
                [d for d, _ in vencidos_por_dia],
                [("Vencidos", [n for _, n in vencidos_por_dia], "--erro-borda", "line")],
            ),
            grafico(
                "grafico-esocial-mensal",
                "eSocial mês a mês",
                "Gerados e rejeitados (sem recibo) em 12 meses; o mês atual vai até a coleta.",
                [f"{MESES[int(x['mes'][5:]) - 1][:3]}/{x['mes'][2:4]}" for x in mensal],
                (
                    ("Enviados", [x["enviados"] for x in mensal], "--serie-atendidos", "bar"),
                    ("Rejeitados", [x["rejeitados"] for x in mensal], "--erro-borda", "line"),
                ),
                eixo="Mês",
            ),
        ),
    )


def montar_dashboard(atual: ResumoPeriodo, anterior: ResumoPeriodo) -> PainelDashboard:
    base = None if anterior.vazio else anterior
    comparativo = para_json(comparar(atual.kpis, base.kpis if base else None, anterior.inicio))
    comparativo["itens"].append(
        para_json(
            comparar_valor(
                "alertas",
                "Alertas",
                atual.alertas,
                base.alertas if base else None,
                Sentido.MENOR_MELHOR,
            )
        )
    )
    intervalo = f"{anterior.inicio:%d/%m} a {anterior.fim:%d/%m}"

    def delta(chave: str) -> Delta | None:
        d = montar_delta(comparativo, chave)
        return replace(d, rotulo=f"vs {intervalo}") if d else None

    k = atual.kpis
    cartoes = (
        CartaoKpi(
            rotulo="Atendimentos realizados",
            valor=numero(k.atendimentos),
            contexto=f"de {numero(k.agendados)} agendados",
            delta=delta("atendimentos"),
        ),
        CartaoKpi(
            rotulo="Faltas",
            valor=numero(k.faltas),
            contexto=f"{percentual(k.taxa_faltas)} dos agendados",
            delta=delta("taxa_faltas"),
        ),
        CartaoKpi(
            rotulo="Espera média",
            valor=duracao(k.espera_media_s),
            contexto=f"maior espera {duracao(k.espera_maxima_s)}",
            delta=delta("espera_media_s"),
        ),
        CartaoKpi(
            rotulo="TMA geral",
            valor=duracao(k.tma_s),
            contexto=f"{numero(k.atendimentos_medidos)} atendimentos medidos",
            delta=delta("tma_s"),
        ),
        CartaoKpi(
            rotulo="Tempo Médio Total de Permanência",
            valor=duracao(k.permanencia_total_s),
            contexto=(
                "da chegada na recepção até a saída do consultório"
                if k.permanencia_total_s is not None
                else "só com guichês marcados nas agendas"
            ),
            delta=delta("permanencia_total_s"),
        ),
        CartaoKpi(
            rotulo="Alertas",
            valor=numero(atual.alertas),
            contexto="somados nos dias do período",
            delta=delta("alertas"),
        ),
    )
    return PainelDashboard(
        periodo=atual,
        anterior=anterior,
        cartoes=cartoes,
        comparacao=(
            f"comparado com {intervalo}"
            if base
            else "sem dados consolidados no mesmo período do mês anterior"
        ),
        grafico=grafico(
            "grafico-atendimento",
            "Atendimentos e faltas por dia",
            "Dias sem movimento ou ainda não consolidados ficam sem barra.",
            (d.data for d in atual.dias),
            (
                ("Atendimentos", [d.atendimentos for d in atual.dias], "--serie-atendidos", "bar"),
                ("Faltas", [d.faltas for d in atual.dias], "--serie-chegadas", "bar"),
            ),
        ),
    )
