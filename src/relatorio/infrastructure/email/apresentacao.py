"""Camada de apresentação do e-mail: JSON do resumo → valores prontos para exibir.

Aqui só há formatação e escolhas visuais (textos, setas, cores, largura das barras).
Nenhuma métrica é calculada: todos os números vêm de ``resumo_diario.metricas``.
Assim o template Jinja2 fica sem lógica e o mesmo JSON gera o HTML e o texto puro.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# Tokens de cor (contraste WCAG AA conferido: texto ≥ 4,5:1 na superfície branca).
AZUL = "#164F95"  # azul MultiLife — marca e barras (8,1:1)
VERMELHO = "#D0080F"  # vermelho MultiLife — destaque e alertas críticos (5,6:1)
TINTA = "#1B2533"
TINTA_SECUNDARIA = "#4A5568"
TINTA_MUDA = "#6B7280"
VERDE = "#1E7A3A"
TRILHO = "#DCE6F3"

COR_AVALIACAO = {"positiva": VERDE, "negativa": VERMELHO, "neutra": TINTA_MUDA}

SEVERIDADES = {
    # severidade: (ícone, rótulo, cor do texto, fundo, borda)
    "critico": ("●", "Crítico", "#A3060B", "#FDECEC", VERMELHO),
    "atencao": ("▲", "Atenção", "#7A4D00", "#FFF4DB", "#E0A100"),
    "info": ("i", "Informativo", AZUL, "#EAF1FA", AZUL),
    "ok": ("✓", "Tudo certo", VERDE, "#E8F5EC", VERDE),
}

MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip
DIAS_CURTOS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
MAX_ITENS_ALERTA = 10
MAX_IDS = 20


# ------------------------------------------------------------------ formatação


def numero(valor: float | None) -> str:
    if valor is None:
        return "—"
    return f"{round(valor):,}".replace(",", ".")


def percentual(valor: float | None, casas: int = 1) -> str:
    if valor is None:
        return "—"
    return f"{valor * 100:.{casas}f}%".replace(".", ",")


def duracao(segundos: float | None) -> str:
    """600 → "10 min"; 3900 → "1 h 05 min"; 45 → "45 s"."""
    if segundos is None:
        return "—"
    total = round(segundos)
    if total < 60:
        return f"{total} s"
    minutos = round(total / 60)
    if minutos < 60:
        return f"{minutos} min"
    horas, resto = divmod(minutos, 60)
    return f"{horas} h {resto:02d} min"


def data_curta(texto: str | None) -> str:
    return date.fromisoformat(texto[:10]).strftime("%d/%m/%Y") if texto else "—"


def data_extensa(texto: str) -> str:
    dia = date.fromisoformat(texto[:10])
    return f"{dia.day} de {MESES[dia.month - 1]} de {dia.year}"


def hora(texto: str | None) -> str:
    return datetime.fromisoformat(texto).strftime("%H:%M") if texto else "—"


def _variacao(item: Mapping[str, Any]) -> str:
    valor = item.get("variacao")
    if valor is None:
        return "—"
    sinal = "+" if valor > 0 else "−" if valor < 0 else ""
    if item.get("em_pontos"):
        return f"{sinal}{abs(valor) * 100:.1f} p.p.".replace(".", ",", 1)
    return f"{sinal}{abs(valor) * 100:.1f}%".replace(".", ",")


def _valor_formatado(valor: float | None, formato: str) -> str:
    if formato == "percentual":
        return percentual(valor)
    if formato == "duracao":
        return duracao(valor)
    return numero(valor)


# ------------------------------------------------------------------ view-model


@dataclass(frozen=True, slots=True)
class Delta:
    seta: str
    texto: str
    cor: str
    rotulo: str


@dataclass(frozen=True, slots=True)
class CartaoKpi:
    rotulo: str
    valor: str
    contexto: str
    delta: Delta | None


@dataclass(frozen=True, slots=True)
class Alerta:
    severidade: str
    titulo: str
    descricao: str
    itens: tuple[str, ...] = ()
    tabela: tuple[tuple[str, ...], ...] = ()
    cabecalho: tuple[str, ...] = ()
    restantes: int = 0

    @property
    def icone(self) -> str:
        return SEVERIDADES[self.severidade][0]

    @property
    def rotulo(self) -> str:
        return SEVERIDADES[self.severidade][1]

    @property
    def cor(self) -> str:
        return SEVERIDADES[self.severidade][2]

    @property
    def fundo(self) -> str:
        return SEVERIDADES[self.severidade][3]

    @property
    def borda(self) -> str:
        return SEVERIDADES[self.severidade][4]


@dataclass(frozen=True, slots=True)
class Apresentacao:
    assunto: str
    titulo_data: str
    data_curta: str
    dia_semana: str
    unidades: str
    sem_movimento: bool
    amostra: bool
    manchete: str
    preheader: str
    kpis: tuple[CartaoKpi, ...]
    turnos: tuple[dict[str, Any], ...]
    linhas_turno: tuple[dict[str, Any], ...]
    consultorios: tuple[dict[str, Any], ...]
    agendas: tuple[dict[str, Any], ...]
    grupos_turno: tuple[dict[str, Any], ...]
    consultorios_turnos: tuple[dict[str, Any], ...]
    agendas_turnos: tuple[dict[str, Any], ...]
    comparativo_disponivel: bool
    comparativo_base: str
    coluna_atual: str
    coluna_base: str
    comparativo: tuple[dict[str, Any], ...]
    alertas: tuple[Alerta, ...]
    quantidade_alertas: int
    notas: tuple[str, ...]
    gerado_em: str
    versao_regra: str
    admin_url: str
    cores: dict[str, str] = field(
        default_factory=lambda: {
            "azul": AZUL,
            "vermelho": VERMELHO,
            "tinta": TINTA,
            "secundaria": TINTA_SECUNDARIA,
            "muda": TINTA_MUDA,
            "trilho": TRILHO,
        }
    )


def _delta(comparativo: Mapping[str, Any], chave: str) -> Delta | None:
    if not comparativo.get("disponivel"):
        return None
    item = next((i for i in comparativo.get("itens", []) if i.get("chave") == chave), None)
    if item is None or item.get("direcao") is None:
        return None
    base = date.fromisoformat(comparativo["data_base"])
    rotulo = f"vs {DIAS_CURTOS[base.weekday()]} {base:%d/%m}"
    direcao = item["direcao"]
    seta = {"alta": "▲", "baixa": "▼"}.get(direcao, "■")
    texto = "estável" if direcao == "estavel" else _variacao(item)
    cor = COR_AVALIACAO.get(item.get("avaliacao") or "neutra", TINTA_MUDA)
    return Delta(seta=seta, texto=texto, cor=cor, rotulo=rotulo)


def _kpis(m: Mapping[str, Any]) -> tuple[CartaoKpi, ...]:
    k = m["kpis"]
    comp = m.get("comparativo", {})
    return (
        CartaoKpi(
            rotulo="Atendimentos realizados",
            valor=numero(k["atendimentos"]),
            contexto=f"de {numero(k['agendados'])} agendados",
            delta=_delta(comp, "atendimentos"),
        ),
        CartaoKpi(
            rotulo="Faltas",
            valor=numero(k["faltas"]),
            contexto=f"{percentual(k.get('taxa_faltas'))} dos agendados",
            delta=_delta(comp, "taxa_faltas"),
        ),
        CartaoKpi(
            rotulo="Espera média na recepção",
            valor=duracao(k.get("espera_media_s")),
            contexto=(
                f"mediana {duracao(k.get('espera_mediana_s'))} · "
                f"maior {duracao(k.get('espera_maxima_s'))}"
            ),
            delta=_delta(comp, "espera_media_s"),
        ),
        CartaoKpi(
            rotulo="TMA geral",
            valor=duracao(k.get("tma_s")),
            contexto=f"{numero(k.get('atendimentos_medidos'))} atendimentos medidos",
            delta=_delta(comp, "tma_s"),
        ),
    )


def _turnos(
    m: Mapping[str, Any], por_turno: Mapping[str, Any] | None = None
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    por_turno = m.get("por_turno", {}) if por_turno is None else por_turno
    chaves = [c for c in ("manha", "tarde") if c in por_turno]
    cabecalho = tuple(
        {"rotulo": por_turno[c]["rotulo"], "faixa": por_turno[c]["faixa"]} for c in chaves
    )

    def linha(rotulo: str, valores: list[str], destaque: bool = False) -> dict[str, Any]:
        return {"rotulo": rotulo, "valores": valores, "destaque": destaque}

    t = [por_turno[c] for c in chaves]
    linhas = (
        linha("Atendimentos", [numero(x["atendimentos"]) for x in t], destaque=True),
        linha(
            "Faltas",
            [f"{numero(x['faltas'])} ({percentual(x.get('taxa_faltas'))})" for x in t],
        ),
        linha("Espera média", [duracao(x.get("espera_media_s")) for x in t]),
        linha("Espera mediana", [duracao(x.get("espera_mediana_s")) for x in t]),
        linha("Maior espera", [duracao(x.get("espera_maxima_s")) for x in t]),
        linha("TMA", [duracao(x.get("tma_s")) for x in t], destaque=True),
    )
    return cabecalho, linhas


TURNOS = ("manha", "tarde")


def _faixas(m: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    por_turno = m.get("por_turno", {})
    padrao = {"manha": "Manhã", "tarde": "Tarde"}
    return {
        c: {
            "rotulo": por_turno.get(c, {}).get("rotulo", padrao[c]),
            "faixa": por_turno.get(c, {}).get("faixa", ""),
        }
        for c in TURNOS
    }


def _grupos_turno(m: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Análise por turno; com guichês marcados, guichês e demais agendas separados."""
    grupos = m.get("por_turno_grupos") or {}
    guiches = grupos.get("guiches", {})
    tem_guiche = any(guiches.get(c, {}).get("agendados") for c in TURNOS)
    if not tem_guiche:
        cabecalho, linhas = _turnos(m)
        return ({"titulo": "", "turnos": cabecalho, "linhas": linhas},)
    resultado = []
    for titulo, chave in (("Consultórios e demais agendas", "agendas"), ("Guichês", "guiches")):
        cabecalho, linhas = _turnos(m, grupos.get(chave, {}))
        resultado.append({"titulo": titulo, "turnos": cabecalho, "linhas": linhas})
    return tuple(resultado)


def _barra(tma: float | None, maior: float) -> int:
    largura = round(tma / maior * 100) if tma and maior else 0
    # Largura mínima visível para valores pequenos, sem distorcer a leitura.
    return max(largura, 3) if largura else 0


def _consultorios_turnos(m: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """TMA por consultório, um bloco por turno (na troca de turno troca o médico).

    A barra usa a mesma escala nos dois turnos, para comparar manhã com tarde.
    """
    faixas = _faixas(m)
    por_turno = m.get("consultorios_por_turno")
    if por_turno is None:  # resumo antigo (regra 1.0): células manhã/tarde por consultório
        por_turno = {
            c: [
                {
                    "consultorio": x["consultorio"],
                    "agenda": x["agenda"],
                    "guiche": False,
                    "atendimentos": x[c]["atendimentos"],
                    "tma_s": x[c]["tma_s"],
                }
                for x in m.get("consultorios", [])
                if x[c]["atendimentos"]
            ]
            for c in TURNOS
        }
    maior = max((x["tma_s"] or 0 for c in TURNOS for x in por_turno.get(c, [])), default=0)
    blocos = []
    for c in TURNOS:
        linhas = sorted(
            por_turno.get(c, []),
            key=lambda x: (x["tma_s"] is None, -(x["tma_s"] or 0), x["consultorio"].lower()),
        )
        blocos.append(
            {
                **faixas[c],
                "linhas": tuple(
                    {
                        "consultorio": x["consultorio"],
                        "agenda": x["agenda"] if x["agenda"] != x["consultorio"] else "",
                        "guiche": bool(x.get("guiche")),
                        "atendimentos": numero(x["atendimentos"]),
                        "tma": duracao(x["tma_s"]),
                        "barra": _barra(x["tma_s"], maior),
                    }
                    for x in linhas
                ),
            }
        )
    return tuple(blocos)


def _linhas_agenda(linhas: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "agenda": a["agenda"],
            "consultorio": a["consultorio"] if a["consultorio"] != a["agenda"] else "",
            "guiche": bool(a.get("guiche")),
            "atendimentos": numero(a["atendimentos"]),
            "media": duracao(a.get("media_s")),
            "mediana": duracao(a.get("mediana_s")),
            "maximo": duracao(a.get("maximo_s")),
        }
        for a in linhas
    )


def _agendas_turnos(m: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Tempo de atendimento por agenda, um bloco por turno."""
    por_turno = m.get("agendas_por_turno")
    if por_turno is None:  # resumo antigo: só o total do dia
        return ({"rotulo": "Dia", "faixa": "", "linhas": _linhas_agenda(m.get("agendas", []))},)
    faixas = _faixas(m)
    return tuple({**faixas[c], "linhas": _linhas_agenda(por_turno.get(c, []))} for c in TURNOS)


def _consultorios(m: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    linhas = m.get("consultorios", [])
    maior = max((c["total"]["tma_s"] or 0 for c in linhas), default=0)
    resultado = []
    for c in linhas:
        tma = c["total"]["tma_s"]
        largura = round(tma / maior * 100) if tma and maior else 0
        resultado.append(
            {
                "consultorio": c["consultorio"],
                "agenda": c["agenda"] if c["agenda"] != c["consultorio"] else "",
                "manha_n": numero(c["manha"]["atendimentos"]),
                "manha_tma": duracao(c["manha"]["tma_s"]),
                "tarde_n": numero(c["tarde"]["atendimentos"]),
                "tarde_tma": duracao(c["tarde"]["tma_s"]),
                "tma": duracao(tma),
                # Largura mínima visível para valores pequenos, sem distorcer a leitura.
                "barra": max(largura, 3) if largura else 0,
            }
        )
    return tuple(resultado)


def _agendas(m: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "agenda": a["agenda"],
            "consultorio": a["consultorio"] if a["consultorio"] != a["agenda"] else "",
            "atendimentos": numero(a["atendimentos"]),
            "media": duracao(a.get("media_s")),
            "mediana": duracao(a.get("mediana_s")),
            "maximo": duracao(a.get("maximo_s")),
        }
        for a in m.get("agendas", [])
    )


def _comparativo(m: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    comp = m.get("comparativo", {})
    linhas = []
    for item in comp.get("itens", []):
        delta = _delta(comp, item["chave"])
        linhas.append(
            {
                "rotulo": item["rotulo"],
                "atual": _valor_formatado(item.get("atual"), item["formato"]),
                "anterior": _valor_formatado(item.get("anterior"), item["formato"]),
                "delta": delta,
            }
        )
    return tuple(linhas)


def _lista_ids(ids: Sequence[int]) -> tuple[str, int]:
    visiveis = ", ".join(str(i) for i in ids[:MAX_IDS])
    return visiveis, max(0, len(ids) - MAX_IDS)


def _alertas(m: Mapping[str, Any]) -> tuple[Alerta, ...]:
    a = m.get("alertas", {})
    limites = m.get("limites_atipico", {"min_minutos": 1, "max_minutos": 180})
    alertas: list[Alerta] = []

    for falha in a.get("falhas_coleta", []):
        alertas.append(
            Alerta(
                "critico",
                "Falha de coleta",
                f"Coleta indisponível das {hora(falha['inicio'])} às {hora(falha['fim'])} "
                f"({falha['minutos']} min). Tempos desse intervalo podem estar incompletos.",
            )
        )
    if a.get("verificacao_faltas_indisponivel"):
        alertas.append(
            Alerta(
                "atencao",
                "Conferência de faltas indisponível",
                "O SGG não respondeu na conferência de faltas; agendamentos sem baixa "
                "podem incluir faltas registradas na agenda.",
            )
        )
    atipicos = a.get("atipicos", [])
    if atipicos:
        alertas.append(
            Alerta(
                "atencao",
                f"Atendimentos atípicos ({len(atipicos)})",
                f"Duração abaixo de {limites['min_minutos']} min ou acima de "
                f"{limites['max_minutos']} min: contam nos totais, mas ficam fora das médias.",
                cabecalho=("ID SGG", "Consultório", "Agendado", "Duração"),
                tabela=tuple(
                    (
                        str(x["id_agendamento"]),
                        x["consultorio"],
                        x.get("hora_agendada") or "—",
                        duracao(x["duracao_s"]),
                    )
                    for x in atipicos[:MAX_ITENS_ALERTA]
                ),
                restantes=max(0, len(atipicos) - MAX_ITENS_ALERTA),
            )
        )
    for chave, severidade, titulo, descricao in (
        (
            "sem_baixa",
            "atencao",
            "Agendamentos sem baixa",
            "Terminaram o dia como Agendado ou Aguardando, sem falta registrada no SGG.",
        ),
        (
            "em_atendimento_aberto",
            "atencao",
            "Atendimentos não finalizados",
            "Terminaram o dia em Em Atendimento (sem passar para Atendido).",
        ),
        (
            "sem_tempo_medido",
            "info",
            "Atendimentos sem tempo medido",
            "O status pulou etapas no SGG; contam nos totais, mas não nas médias.",
        ),
    ):
        lista = a.get(chave, {"total": 0, "ids": []})
        if lista["total"]:
            visiveis, restantes = _lista_ids(lista["ids"])
            alertas.append(
                Alerta(
                    severidade,
                    f"{titulo} ({lista['total']})",
                    descricao,
                    itens=(f"IDs no SGG: {visiveis}",),
                    restantes=restantes,
                )
            )
    return tuple(alertas)


def _manchete(m: Mapping[str, Any], alertas: Sequence[Alerta]) -> str:
    k = m["kpis"]
    partes = [
        f"{numero(k['atendimentos'])} atendimentos e {numero(k['faltas'])} faltas "
        f"({percentual(k.get('taxa_faltas'))} dos agendados)."
    ]
    comp = m.get("comparativo", {})
    espera = _delta(comp, "espera_media_s")
    if k.get("espera_media_s") is not None:
        frase = f"Espera média de {duracao(k['espera_media_s'])}"
        if espera is not None and espera.texto != "estável":
            sentido = "acima" if espera.seta == "▲" else "abaixo"
            frase += f", {espera.texto.lstrip('+−')} {sentido} da semana anterior"
        partes.append(frase + ".")
    return " ".join(partes)


def _relevantes(alertas: Sequence[Alerta]) -> int:
    """Alertas que pedem ação (crítico/atenção); os informativos não entram no selo."""
    return sum(1 for x in alertas if x.severidade in ("critico", "atencao"))


def _preheader(manchete: str, alertas: Sequence[Alerta]) -> str:
    """Texto de pré-visualização na caixa de entrada (manchete + alertas)."""
    relevantes = _relevantes(alertas)
    if relevantes:
        return f"{manchete} {relevantes} alerta(s) para revisar."
    return manchete


def montar_apresentacao(m: Mapping[str, Any], admin_url: str = "") -> Apresentacao:
    data_ref = m["data_referencia"]
    unidades = ", ".join(m.get("unidades", [])) or "Todas as unidades"
    comp = m.get("comparativo", {})
    base = comp.get("data_base")
    dia_semana = m.get("dia_semana", "")
    rotulo_data = f"{dia_semana.capitalize()}, {data_extensa(data_ref)}"

    if m.get("sem_movimento"):
        alertas = _alertas(m)
        manchete = f"Sem movimento em {data_curta(data_ref)}: não houve agendamentos."
        return Apresentacao(
            assunto=m["assunto"],
            titulo_data=rotulo_data,
            data_curta=data_curta(data_ref),
            dia_semana=dia_semana,
            unidades=unidades,
            sem_movimento=True,
            amostra=bool(m.get("amostra")),
            manchete=manchete,
            preheader=manchete,
            kpis=(),
            turnos=(),
            linhas_turno=(),
            consultorios=(),
            agendas=(),
            grupos_turno=(),
            consultorios_turnos=(),
            agendas_turnos=(),
            comparativo_disponivel=False,
            comparativo_base="",
            coluna_atual="",
            coluna_base="",
            comparativo=(),
            alertas=alertas,
            quantidade_alertas=_relevantes(alertas),
            notas=(),
            gerado_em=_gerado_em(m),
            versao_regra=m.get("versao_regra", ""),
            admin_url=admin_url,
        )

    alertas = _alertas(m)
    cabecalho_turnos, linhas_turno = _turnos(m)
    k = m["kpis"]
    notas = [
        "Turno pela hora agendada. Médias e TMA excluem atendimentos atípicos e os que "
        "pularam etapas no SGG.",
    ]
    if k.get("sem_turno"):
        notas.append(
            f"{k['sem_turno']} agendamento(s) sem hora e sem chegada registrada entram "
            "só nos totais do dia."
        )
    if m.get("alertas", {}).get("faltas_confirmadas_na_agenda", {}).get("total"):
        total = m["alertas"]["faltas_confirmadas_na_agenda"]["total"]
        notas.append(f"{total} falta(s) confirmada(s) na agenda do consultório (RF11).")

    manchete = _manchete(m, alertas)
    return Apresentacao(
        assunto=m["assunto"],
        titulo_data=rotulo_data,
        data_curta=data_curta(data_ref),
        dia_semana=dia_semana,
        unidades=unidades,
        sem_movimento=False,
        amostra=bool(m.get("amostra")),
        manchete=manchete,
        preheader=_preheader(manchete, alertas),
        kpis=_kpis(m),
        turnos=cabecalho_turnos,
        linhas_turno=linhas_turno,
        consultorios=_consultorios(m),
        agendas=_agendas(m),
        grupos_turno=_grupos_turno(m),
        consultorios_turnos=_consultorios_turnos(m),
        agendas_turnos=_agendas_turnos(m),
        comparativo_disponivel=bool(comp.get("disponivel")),
        comparativo_base=(
            f"{DIAS_CURTOS[date.fromisoformat(base).weekday()]} {data_curta(base)}" if base else ""
        ),
        coluna_atual=date.fromisoformat(data_ref).strftime("%d/%m"),
        coluna_base=date.fromisoformat(base).strftime("%d/%m") if base else "",
        comparativo=_comparativo(m),
        alertas=alertas,
        quantidade_alertas=_relevantes(alertas),
        notas=tuple(notas),
        gerado_em=_gerado_em(m),
        versao_regra=m.get("versao_regra", ""),
        admin_url=admin_url,
    )


def _gerado_em(m: Mapping[str, Any]) -> str:
    gerado = m.get("gerado_em")
    if not gerado:
        return "—"
    instante = datetime.fromisoformat(gerado)
    return f"{instante:%d/%m/%Y} às {instante:%H:%M}"
