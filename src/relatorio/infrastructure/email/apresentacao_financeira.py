"""View-model do e-mail financeiro: só formatação (moeda, datas, textos e cores).

Recebe o JSON de ``resumo_financeiro.metricas`` (``domain.financeiro``) e devolve o que o
template exibe. Nenhuma regra de cálculo fica aqui nem no template.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from relatorio.domain.resumo import dia_semana

NEGATIVO = "#A3060B"
POSITIVO = "#1E7A3A"
NEUTRO = "#20295A"
SEM_ALTERACAO = "sem alteração desde ontem"


def moeda(valor: Any) -> str:
    """1234.5 → "R$ 1.234,50"; negativo → "-R$ 10,00"; vazio → "-"."""
    if valor is None or valor == "":
        return "-"
    numero = float(valor)
    texto = f"{abs(numero):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if numero < 0 else ''}R$ {texto}"


def inteiro(valor: Any) -> str:
    if valor is None or valor == "":
        return "-"
    return f"{int(valor):,}".replace(",", ".")


def percentual(valor: Any) -> str:
    if valor is None:
        return "-"
    return f"{float(valor) * 100:.1f}%".replace(".", ",")


def _data(texto: str | None) -> date | None:
    return date.fromisoformat(texto[:10]) if texto else None


def _cor(valor: Any) -> str:
    if valor is None:
        return NEUTRO
    return NEGATIVO if float(valor) < 0 else NEUTRO


@dataclass(frozen=True, slots=True)
class Cartao:
    rotulo: str
    valor: str
    contexto: str
    cor: str = NEUTRO
    valor_2: str = ""  # segundo número do cartão (faturamento: hoje e mês)
    rotulo_valor: str = ""
    rotulo_valor_2: str = ""
    etiqueta: str = ""


@dataclass(frozen=True, slots=True)
class Linha:
    rotulo: str
    valores: tuple[str, ...]
    destaque: bool = False
    cores: tuple[str, ...] = ()
    nota: str = ""


@dataclass(frozen=True, slots=True)
class Tabela:
    cabecalho: tuple[str, ...]
    linhas: tuple[Linha, ...]
    titulo: str = ""
    vazio: str = "Nada no período."
    etiqueta: str = ""
    ocultar_no_celular: tuple[int, ...] = ()  # índices das colunas (0 = rótulo)
    alinhar: tuple[str, ...] = ()  # "left"/"right" por coluna; padrão: rótulo à esquerda


@dataclass(frozen=True, slots=True)
class Secao:
    titulo: str
    subtitulo: str
    tabelas: tuple[Tabela, ...]
    etiqueta: str = ""


@dataclass(frozen=True, slots=True)
class ItemLegenda:
    rotulo: str
    valor: str
    cor: str


@dataclass(frozen=True, slots=True)
class Projecao:
    """Seção "Projeção e gráfico": três períodos de 30 dias e as linhas acumuladas."""

    titulo: str
    subtitulo: str
    tabela: Tabela
    legenda: tuple[ItemLegenda, ...] = ()
    grafico_cid: str = ""  # vazio: resumo antigo, sem a série diária
    grafico_alt: str = ""
    nota: str = ""


@dataclass(frozen=True, slots=True)
class Fluxo:
    """Seção "Fluxo de caixa": lista retrátil com todas as entradas e saídas do dia."""

    titulo: str
    subtitulo: str
    botao: str
    resumo: tuple[ItemLegenda, ...]
    entradas: Tabela | None = None
    saidas: Tabela | None = None
    indisponivel: str = ""


@dataclass(frozen=True, slots=True)
class ApresentacaoFinanceira:
    assunto: str
    preheader: str
    titulo_data: str
    manchete: str
    cartoes: tuple[Cartao, ...]
    indicadores: Tabela
    projecao: Projecao
    fluxo: Fluxo
    secoes: tuple[Secao, ...]
    notas: tuple[str, ...]
    gerado_em: str
    versao_regra: str
    admin_url: str
    amostra: bool = False
    definicoes: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _etiqueta(bloco: str, sem_alteracao: Sequence[str]) -> str:
    return SEM_ALTERACAO if bloco in sem_alteracao else ""


def _cartoes(m: Mapping[str, Any], ref: date) -> tuple[Cartao, ...]:
    caixa, fat = m["caixa"], m["faturamento"]
    return (
        Cartao(
            rotulo=f"Saldo operacional de {ref:%d/%m}",
            valor=moeda(caixa["saldo_dia"]),
            contexto=f"Recebido {moeda(caixa['recebido_dia'])} e pago {moeda(caixa['pago_dia'])}",
            cor=_cor(caixa["saldo_dia"]),
        ),
        Cartao(
            rotulo=f"Faturamento do mês até {ref:%d/%m}",
            valor=moeda(fat["mes"]),
            contexto=(
                f"{inteiro(fat['titulos_mes'])} "
                f"{'título emitido' if fat['titulos_mes'] == 1 else 'títulos emitidos'} no mês"
            ),
        ),
    )


MAX_DESCRICAO = 90
RECEITA = "#1F5FBF"  # azul: entrada (vermelho é saída)


def _descricao(texto: Any) -> str:
    limpo = " ".join(str(texto or "").split())
    return limpo if len(limpo) <= MAX_DESCRICAO else limpo[: MAX_DESCRICAO - 1].rstrip() + "…"


def _indicadores(m: Mapping[str, Any], ref: date) -> Tabela:
    c = m["caixa"]
    return Tabela(
        cabecalho=("Indicador", f"Dia ({ref:%d/%m})", f"Mês até {ref:%d/%m}"),
        linhas=(
            Linha("Receita recebida", (moeda(c["recebido_dia"]), moeda(c["recebido_mes"]))),
            Linha("Despesas pagas", (moeda(c["pago_dia"]), moeda(c["pago_mes"]))),
            Linha(
                "Saldo operacional",
                (moeda(c["saldo_dia"]), moeda(c["saldo_mes"])),
                destaque=True,
                cores=(_cor(c["saldo_dia"]), _cor(c["saldo_mes"])),
            ),
        ),
    )


def _lista_movimentos(movimentos: Sequence[Mapping[str, Any]], despesa: bool, vazio: str) -> Tabela:
    cor = NEGATIVO if despesa else RECEITA
    linhas = [
        Linha(
            mov["conta"],
            (
                mov["classificacao"] or "Sem classificação",
                _descricao(mov["descricao"]),
                moeda(-float(mov["valor"]) if despesa else float(mov["valor"])),
            ),
            cores=("#444B68", "#444B68", cor),
        )
        for mov in movimentos
    ]
    total = sum(float(mov["valor"]) for mov in movimentos)
    if linhas:
        linhas.append(
            Linha(
                "Total",
                ("", "", moeda(-total if despesa else total)),
                destaque=True,
                cores=("#20295A", "#20295A", cor),
            )
        )
    return Tabela(
        cabecalho=("Conta", "Classificação", "Descrição", "Valor"),
        linhas=tuple(linhas),
        alinhar=("left", "left", "left", "right"),
        ocultar_no_celular=(1, 2),
        vazio=vazio,
    )


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{inteiro(n)} {singular if n == 1 else plural}"


def _secao_fluxo(fluxo: Mapping[str, Any] | None, ref: date) -> Fluxo:
    titulo = f"Fluxo de caixa de {ref:%d/%m}"
    subtitulo = (
        "Regime de caixa: o que entrou e saiu pela data de pagamento. O recebido inclui juros e"
        " multas."
    )
    if fluxo is None:
        return Fluxo(
            titulo=titulo,
            subtitulo=subtitulo,
            botao="",
            resumo=(),
            indisponivel="Detalhamento indisponível: o SGG não respondeu na hora do envio.",
        )
    movimentos = fluxo["linhas"]
    entradas = [mov for mov in movimentos if mov["natureza"] != "despesa"]
    saidas = [mov for mov in movimentos if mov["natureza"] == "despesa"]
    total_e = sum(float(mov["valor"]) for mov in entradas)
    total_s = sum(float(mov["valor"]) for mov in saidas)
    return Fluxo(
        titulo=titulo,
        subtitulo=subtitulo,
        botao=(
            f"Ver {_plural(len(entradas), 'entrada', 'entradas')}"
            f" e {_plural(len(saidas), 'saída', 'saídas')}"
        ),
        resumo=(
            ItemLegenda(_plural(len(entradas), "entrada", "entradas"), moeda(total_e), RECEITA),
            ItemLegenda(_plural(len(saidas), "saída", "saídas"), moeda(-total_s), NEGATIVO),
        ),
        entradas=_lista_movimentos(entradas, False, f"Nenhuma conta recebida em {ref:%d/%m}."),
        saidas=_lista_movimentos(saidas, True, f"Nenhuma conta paga em {ref:%d/%m}."),
    )


def _rotulo_horizonte(p: Mapping[str, Any]) -> str:
    if p.get("fim_mes"):
        data = _data(p["ate"])
        return f"Até {data:%d/%m}" if data else "Até o fim do mês"
    return f"{p['dias']} dias"


MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def _mes_ano(dia: date) -> str:
    return f"{MESES[dia.month - 1]}/{dia:%Y}"


def _subtitulo_projecao(base: Mapping[str, Any] | None, hoje: date) -> str:
    if not base:  # resumos gravados antes da regra 1.4 (títulos em aberto)
        return (
            f"Foto de {hoje:%d/%m}: títulos em aberto (a receber e a pagar)"
            " que vencem de hoje em diante."
        )
    de, ate = date.fromisoformat(base["de"]), date.fromisoformat(base["ate"])
    return (
        f"Pelo histórico: média do que entrou e saiu em cada dia do mês de {_mes_ano(de)}"
        f" a {_mes_ano(ate)} ({base['meses']} meses), somada a partir de {hoje:%d/%m}."
    )


def _projecao_antiga(m: Mapping[str, Any], hoje: date) -> Projecao:
    """Resumos gravados antes da regra 1.5: horizontes acumulados, sem gráfico."""
    proj = m["projecao"]
    return Projecao(
        titulo="Projeção e gráfico",
        subtitulo=_subtitulo_projecao(m.get("projecao_base"), hoje),
        tabela=Tabela(
            cabecalho=("Horizonte", *[_rotulo_horizonte(p) for p in proj]),
            linhas=(
                Linha("Entradas previstas", tuple(moeda(p["entradas"]) for p in proj)),
                Linha("Saídas previstas", tuple(moeda(p["saidas"]) for p in proj)),
                Linha(
                    "Saldo projetado",
                    tuple(moeda(p["saldo"]) for p in proj),
                    destaque=True,
                    cores=tuple(_cor(p["saldo"]) for p in proj),
                ),
            ),
        ),
    )


def _secao_projecao(m: Mapping[str, Any], hoje: date, grafico_cid: str = "") -> Projecao:
    periodos = m.get("projecao_periodos")
    if not periodos:
        return _projecao_antiga(m, hoje)
    serie = m.get("projecao_serie")
    ultimo = periodos[-1]
    tabela = Tabela(
        cabecalho=("", *[f"Mês {p['periodo']}" for p in periodos]),
        linhas=(
            Linha(
                "Período",
                tuple(
                    f"{date.fromisoformat(p['de']):%d/%m} a {date.fromisoformat(p['ate']):%d/%m}"
                    for p in periodos
                ),
            ),
            Linha("Entradas previstas", tuple(moeda(p["entradas"]) for p in periodos)),
            Linha("Saídas previstas", tuple(moeda(p["saidas"]) for p in periodos)),
            Linha(
                "Saldo operacional",
                tuple(moeda(p["saldo"]) for p in periodos),
                cores=tuple(_cor(p["saldo"]) for p in periodos),
            ),
            Linha(
                "Saldo acumulado",
                tuple(moeda(p["saldo_acumulado"]) for p in periodos),
                destaque=True,
                cores=tuple(_cor(p["saldo_acumulado"]) for p in periodos),
            ),
        ),
    )
    legenda: tuple[ItemLegenda, ...] = ()
    alt = ""
    if serie and grafico_cid:
        entradas, saidas, saldo = serie["entradas"][-1], serie["saidas"][-1], serie["saldo"][-1]
        legenda = (
            ItemLegenda("Entradas acumuladas", moeda(entradas), "#2F4A80"),
            ItemLegenda("Saídas acumuladas", moeda(saidas), "#B43432"),
            ItemLegenda("Saldo acumulado", moeda(saldo), "#1E7A3A"),
        )
        alt = (
            f"Gráfico de linhas dos próximos 90 dias: entradas acumuladas {moeda(entradas)},"
            f" saídas acumuladas {moeda(saidas)} e saldo acumulado {moeda(saldo)}."
        )
    return Projecao(
        titulo="Projeção e gráfico",
        subtitulo=(
            _subtitulo_projecao(m.get("projecao_base"), hoje)
            + f" Três períodos seguidos de 30 dias: o saldo de cada um entra no seguinte"
            f" (90 dias até {date.fromisoformat(ultimo['ate']):%d/%m})."
        ),
        tabela=tabela,
        legenda=legenda,
        grafico_cid=grafico_cid if legenda else "",
        grafico_alt=alt,
        nota=(
            "O SGG não informa o saldo bancário: o saldo acumulado parte de zero e mostra só a"
            " variação projetada do caixa. Some o saldo atual da conta para ter o caixa total."
            " A base é o que de fato foi recebido e pago, então atrasos e inadimplência de sempre"
            " já estão descontados."
        ),
    )


def _secao_rateio(m: Mapping[str, Any], ref: date, sem: Sequence[str]) -> Secao:
    centros = tuple(
        Linha(c["centro"], (moeda(c["recebido"]), moeda(c["pago"]))) for c in m["rateio"]["centros"]
    )
    return Secao(
        titulo="Rateio por centro de custo",
        subtitulo=f"Recebido e pago no mês até {ref:%d/%m}, pelo rateio de cada conta.",
        etiqueta=_etiqueta("rateio", sem),
        tabelas=(
            Tabela(
                cabecalho=("Centro de custo", "Recebido", "Pago"),
                linhas=centros,
                vazio="Nenhum pagamento no mês.",
            ),
        ),
    )


DEFINICOES = (
    ("Receita recebida", "títulos a receber pagos na data (caixa), pelo valor cobrado."),
    ("Faturamento", "contas a receber emitidas no mês até o dia anterior (competência)."),
    ("Saldo operacional", "receita recebida menos despesas pagas."),
    (
        "Projeção",
        "para cada dia do mês, a média do que entrou e saiu nesse dia nos últimos 6 meses"
        " completos, somada nos 90 dias a partir de hoje, em três períodos de 30 dias. O"
        " saldo operacional de cada período (entradas menos saídas) se acumula no seguinte."
        " Contas já lançadas em aberto não entram; o padrão do histórico é que manda.",
    ),
    ("Rateio", "recebido e pago no mês por centro de custo, pelo rateio de cada conta."),
)


def montar_apresentacao_financeira(
    m: Mapping[str, Any], admin_url: str = "", grafico_cid: str = ""
) -> ApresentacaoFinanceira:
    fluxo: Mapping[str, Any] | None = m.get("fluxo_dia")
    ref = date.fromisoformat(m["referencia"])
    hoje = date.fromisoformat(m["hoje"])
    sem = tuple(m.get("sem_alteracao") or ())
    caixa, fat = m["caixa"], m["faturamento"]
    manchete = (
        f"Em {ref:%d/%m}, entraram {moeda(caixa['recebido_dia'])}"
        f" e saíram {moeda(caixa['pago_dia'])}"
        f" (saldo de {moeda(caixa['saldo_dia'])}). No mês, o faturamento soma {moeda(fat['mes'])}."
    )
    gerado = datetime.fromisoformat(m["gerado_em"])
    return ApresentacaoFinanceira(
        assunto=f"Relatório Financeiro — {ref:%d/%m/%Y} ({dia_semana(ref)})",
        preheader=manchete,
        titulo_data=f"{dia_semana(ref).capitalize()}, {ref:%d/%m/%Y}",
        manchete=manchete,
        cartoes=_cartoes(m, ref),
        indicadores=_indicadores(m, ref),
        projecao=_secao_projecao(m, hoje, grafico_cid),
        fluxo=_secao_fluxo(fluxo, ref),
        secoes=(_secao_rateio(m, ref, sem),),
        notas=(
            'Blocos marcados com "sem alteração desde ontem" repetem o número'
            " do relatório anterior.",
        ),
        gerado_em=f"{gerado:%d/%m/%Y} às {gerado:%H:%M}",
        versao_regra=str(m.get("versao_regra", "")),
        admin_url=admin_url,
        amostra=bool(m.get("amostra")),
        definicoes=DEFINICOES,
    )
