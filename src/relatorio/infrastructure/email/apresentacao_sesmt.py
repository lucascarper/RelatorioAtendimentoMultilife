"""View-model do e-mail do SESMT: só formatação (datas, textos e cores).

Recebe o JSON de ``resumo_sesmt.metricas`` (``domain.sesmt``) e devolve o que o template
exibe. Nenhuma regra de cálculo fica aqui nem no template. O dashboard (``/admin/sesmt``)
usa as mesmas frases (``manchete``, ``nome_mes``) para os dois dizerem o mesmo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from relatorio.domain.resumo import dia_semana
from relatorio.domain.sesmt import TIPOS_DOCUMENTO

AZUL = "#2F4A80"
VERMELHO = "#B43432"
VERDE = "#1E7A3A"
NEUTRO = "#20295A"
MUDO = "#626883"
NEGATIVO = "#A3060B"
ALERTA = "#B45309"  # laranja escuro: contraste 5,0:1 no branco

COR_TIPO = {"PGR": AZUL, "PCMSO": ALERTA, "LTCAT": "#0F766E"}
MAX_LINHAS = 40  # listas do e-mail; o dashboard pagina a lista completa
DIAS_PLANILHA_ATUAL = 8  # enviada toda semana: mais velha que isso, o e-mail avisa

MESES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

DEFINICOES = (
    (
        "Documento",
        "PGR, PCMSO e LTCAT das planilhas de vencimento do SGG enviadas pelo SESMT (uma"
        " linha por empresa, com o documento atual), menos o grupo EM CANCELAMENTO.",
    ),
    ("Vencido", "a validade do documento já passou."),
    ("Mês que vem", "documentos que vencem no mês civil seguinte ao do relatório."),
    (
        "eSocial",
        "eventos gerados no mês até a data da coleta, nas empresas com o eSocial habilitado."
        " Rejeitado = sem recibo de entrega (inclui o que ainda não foi transmitido: a API"
        " do SGG não separa os dois).",
    ),
)


def _data(texto: str | None) -> date | None:
    return date.fromisoformat(texto[:10]) if texto else None


def _data_br(texto: str | None, formato: str = "%d/%m/%Y") -> str:
    dia = _data(texto)
    return f"{dia:{formato}}" if dia else "-"


def plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def nome_mes(texto: str) -> str:
    """'2026-12-01' → 'dezembro de 2026'."""
    dia = date.fromisoformat(texto[:10])
    return f"{MESES[dia.month - 1]} de {dia.year}"


def percentual(parte: int, total: int) -> str:
    return f"{parte / total:.1%}".replace(".", ",") if total else "0%"


@dataclass(frozen=True, slots=True)
class Celula:
    texto: str
    cor: str = ""
    negrito: bool = False
    nota: str = ""  # linha menor abaixo do texto


@dataclass(frozen=True, slots=True)
class Tabela:
    cabecalho: tuple[str, ...]
    linhas: tuple[tuple[Celula, ...], ...]
    vazio: str = "Nada a mostrar."
    rodape: str = ""  # "e mais N empresas"
    ocultar_no_celular: tuple[int, ...] = ()  # índices das colunas
    numericas: tuple[int, ...] = ()  # colunas que não quebram linha


@dataclass(frozen=True, slots=True)
class Cartao:
    rotulo: str
    valor: str
    contexto: str = ""
    cor: str = NEUTRO


@dataclass(frozen=True, slots=True)
class Segmento:
    largura: int  # % da barra do grupo
    cor: str
    rotulo: str  # "PGR: 12"


@dataclass(frozen=True, slots=True)
class Barra:
    """Uma linha do gráfico de barras por grupo, em tabela (funciona em todo cliente)."""

    rotulo: str
    total: int
    largura: int  # % da maior barra
    segmentos: tuple[Segmento, ...]


@dataclass(frozen=True, slots=True)
class Secao:
    numero: int
    titulo: str
    subtitulo: str
    cor: str  # cor da barra ao lado do título
    cartoes: tuple[Cartao, ...] = ()
    barras: tuple[Barra, ...] = ()
    legenda: tuple[tuple[str, str], ...] = ()  # (cor, rótulo) das barras
    tabela: Tabela | None = None


@dataclass(frozen=True, slots=True)
class ApresentacaoSesmt:
    assunto: str
    preheader: str
    titulo_data: str
    manchete: str
    secoes: tuple[Secao, ...]
    notas: tuple[str, ...]
    gerado_em: str
    versao_regra: str
    admin_url: str
    amostra: bool = False
    definicoes: tuple[tuple[str, str], ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- seções


def barras_por_grupo(por_grupo: Sequence[Mapping[str, Any]]) -> tuple[Barra, ...]:
    maior = max((int(g["total"]) for g in por_grupo), default=0)
    barras = []
    for g in por_grupo:
        total = int(g["total"])
        segmentos = tuple(
            Segmento(round(100 * n / total), COR_TIPO[t], f"{t}: {n}")
            for t in TIPOS_DOCUMENTO
            if (n := int(g["por_tipo"].get(t, 0)))
        )
        barras.append(Barra(str(g["grupo"]), total, max(1, round(100 * total / maior)), segmentos))
    return tuple(barras)


def _rodape(ocultos: int, o_que: str) -> str:
    return f"e mais {ocultos} {o_que}: lista completa no dashboard" if ocultos > 0 else ""


def _secao_vencidos(m: Mapping[str, Any]) -> Secao:
    v = m["vencidos"]
    por_tipo = " · ".join(f"{t}: {v['por_tipo'].get(t, 0)}" for t in TIPOS_DOCUMENTO)
    return Secao(
        numero=1,
        titulo="Programas e laudos vencidos",
        subtitulo=f"Por grupo de empresas, divididos por tipo. {por_tipo}.",
        cor=VERMELHO,
        cartoes=(
            Cartao(
                "Documentos vencidos", str(v["total"]), por_tipo, NEGATIVO if v["total"] else MUDO
            ),
            Cartao("Empresas com vencido", str(v["empresas"]), "ao menos um documento vencido"),
        ),
        barras=barras_por_grupo(v["por_grupo"]),
        legenda=tuple((COR_TIPO[t], t) for t in TIPOS_DOCUMENTO),
    )


def documentos_texto(documentos: Sequence[Mapping[str, Any]]) -> str:
    """'PGR 05/12 · PCMSO 20/12'."""
    return " · ".join(f"{d['tipo']} {_data_br(d['vencimento'], '%d/%m')}" for d in documentos)


def _secao_proximo_mes(m: Mapping[str, Any]) -> Secao:
    p = m["proximo_mes"]
    mes = nome_mes(p["inicio"])
    empresas = p["empresas"]
    linhas = tuple(
        (
            Celula(str(e["empresa"]), negrito=True, nota=str(e.get("cnpj") or "")),
            Celula(str(e["grupo"])),
            Celula(documentos_texto(e["documentos"]), ALERTA),
        )
        for e in empresas[:MAX_LINHAS]
    )
    return Secao(
        numero=2,
        titulo=f"Vencem em {mes}",
        subtitulo=(
            f"{plural(p['total'], 'documento', 'documentos')} em"
            f" {plural(p['empresas_total'], 'empresa', 'empresas')}, pelo primeiro vencimento."
        ),
        cor=ALERTA,
        tabela=Tabela(
            cabecalho=("Empresa", "Grupo", "Documentos e vencimento"),
            linhas=linhas,
            vazio=f"Nenhum PGR, PCMSO ou LTCAT vence em {mes}.",
            rodape=_rodape(len(empresas) - MAX_LINHAS, "empresas"),
            ocultar_no_celular=(1,),
        ),
    )


def tipos_texto(tipos: Mapping[str, int]) -> str:
    # Hífen inseparável (U+2011): "S-2240" não quebra no meio em tela estreita.
    return " · ".join(f"{t.replace('-', chr(0x2011))}: {n}" for t, n in tipos.items())


def _secao_esocial(m: Mapping[str, Any]) -> Secao:
    e = m["esocial"]
    ant = e["anterior"]
    periodo = f"{_data_br(e['inicio'], '%d/%m')} a {_data_br(e['fim'], '%d/%m')}"
    base = f"{_data_br(ant['inicio'], '%d/%m')} a {_data_br(ant['fim'], '%d/%m')}"
    enviados, rejeitados = int(e["enviados"]), int(e["rejeitados"])
    empresas = e["rejeitados_por_empresa"]
    linhas = tuple(
        (
            Celula(str(x["empresa"]), negrito=True, nota=str(x.get("cnpj") or "")),
            Celula(str(x["quantidade"]), NEGATIVO, negrito=True),
            Celula(tipos_texto(x["tipos"])),
        )
        for x in empresas[:MAX_LINHAS]
    )
    return Secao(
        numero=3,
        titulo=f"Eventos do eSocial em {nome_mes(e['inicio'])}",
        subtitulo=f"Gerados de {periodo}. Comparação com {base}.",
        cor=VERDE,
        cartoes=(
            Cartao("Enviados", str(enviados), f"{ant['enviados']} de {base}", AZUL),
            Cartao(
                "Rejeitados (sem recibo)",
                str(rejeitados),
                f"{percentual(rejeitados, enviados)} dos enviados · {ant['rejeitados']} de {base}",
                NEGATIVO if rejeitados else MUDO,
            ),
            Cartao(
                "Empresas com rejeitado",
                str(e["empresas_rejeitadas"]),
                "listadas abaixo",
            ),
        ),
        tabela=Tabela(
            cabecalho=("Empresa", "Rejeitados", "Eventos"),
            linhas=linhas,
            vazio="Nenhum evento rejeitado no mês.",
            rodape=_rodape(len(empresas) - MAX_LINHAS, "empresas"),
            numericas=(1,),
        ),
    )


# --------------------------------------------------------------------------- montagem


def manchete(m: Mapping[str, Any]) -> str:
    v, p, e = m["vencidos"], m["proximo_mes"], m["esocial"]
    return (
        f"{plural(v['total'], 'documento vencido', 'documentos vencidos')} em"
        f" {plural(v['empresas'], 'empresa', 'empresas')};"
        f" {p['total']} {'vence' if p['total'] == 1 else 'vencem'} em"
        f" {nome_mes(p['inicio']).split(' de ')[0]}."
        f" No eSocial de {nome_mes(e['inicio']).split(' de ')[0]},"
        f" {plural(e['rejeitados'], 'evento rejeitado', 'eventos rejeitados')}"
        f" de {e['enviados']} enviados."
    )


def _notas_planilhas(m: Mapping[str, Any]) -> list[str]:
    planilhas, hoje = m.get("planilhas") or {}, date.fromisoformat(m["hoje"])
    faltam = [t for t in TIPOS_DOCUMENTO if t not in planilhas]
    antigas = [
        f"{t} ({_data_br(planilhas[t], '%d/%m')})"
        for t in TIPOS_DOCUMENTO
        if t in planilhas
        and (hoje - date.fromisoformat(planilhas[t][:10])).days > DIAS_PLANILHA_ATUAL
    ]
    avisos = []
    if faltam:
        avisos.append(
            f"Atenção: nenhuma planilha de vencimento de {', '.join(faltam)} foi enviada;"
            " esses documentos não entram nos números."
        )
    if antigas:
        avisos.append(
            f"Atenção: planilha de vencimento desatualizada: {', '.join(antigas)}."
            " Envie a da semana na gestão do relatório."
        )
    return avisos


def notas(m: Mapping[str, Any]) -> tuple[str, ...]:
    avisos = _notas_planilhas(m)
    n = int(m.get("empresas_sem_consulta") or 0)
    if n:
        sujeito = "empresa não pôde" if n == 1 else "empresas não puderam"
        avisos.append(
            f"Atenção: {n} {sujeito} ser consultada no eSocial nesta coleta; os números do"
            " eSocial podem estar incompletos."
        )
    return tuple(avisos)


def montar_apresentacao_sesmt(m: Mapping[str, Any], admin_url: str = "") -> ApresentacaoSesmt:
    hoje = date.fromisoformat(m["hoje"])
    gerado = datetime.fromisoformat(m["gerado_em"])
    texto = manchete(m)
    return ApresentacaoSesmt(
        assunto=f"Relatório de Gestão SESMT — {hoje:%d/%m/%Y} ({dia_semana(hoje)})",
        preheader=texto,
        titulo_data=f"{dia_semana(hoje).capitalize()}, {hoje:%d/%m/%Y}",
        manchete=texto,
        secoes=(_secao_vencidos(m), _secao_proximo_mes(m), _secao_esocial(m)),
        notas=notas(m),
        gerado_em=f"{gerado:%d/%m/%Y} às {gerado:%H:%M}",
        versao_regra=str(m.get("versao_regra", "")),
        admin_url=admin_url,
        amostra=bool(m.get("amostra")),
        definicoes=DEFINICOES,
    )
