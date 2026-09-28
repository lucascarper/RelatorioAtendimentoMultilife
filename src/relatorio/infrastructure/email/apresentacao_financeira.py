"""View-model do e-mail financeiro: só formatação (moeda, datas, textos e cores).

Recebe o JSON de ``resumo_financeiro.metricas`` (``domain.financeiro``) e devolve o que o
template exibe. Nenhuma regra de cálculo fica aqui nem no template.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from relatorio.domain.resumo import dia_semana

NEGATIVO = "#A3060B"
POSITIVO = "#1E7A3A"
NEUTRO = "#1B2533"
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


@dataclass(frozen=True, slots=True)
class Secao:
    titulo: str
    subtitulo: str
    tabelas: tuple[Tabela, ...]
    etiqueta: str = ""


@dataclass(frozen=True, slots=True)
class ApresentacaoFinanceira:
    assunto: str
    preheader: str
    titulo_data: str
    manchete: str
    cartoes: tuple[Cartao, ...]
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
            rotulo="Faturamento",
            valor=moeda(fat["dia"]),
            rotulo_valor=f"em {ref:%d/%m}",
            valor_2=moeda(fat["mes"]),
            rotulo_valor_2=f"no mês até {ref:%d/%m}",
            contexto=f"{inteiro(fat['titulos_mes'])} títulos emitidos no mês",
        ),
    )


def _secao_caixa(m: Mapping[str, Any], ref: date) -> Secao:
    c = m["caixa"]
    return Secao(
        titulo="Fluxo de caixa",
        subtitulo=(
            "Regime de caixa: o que entrou e saiu pela data de pagamento."
            " Recebido inclui juros e multas."
        ),
        tabelas=(
            Tabela(
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
            ),
        ),
    )


def _secao_projecao(m: Mapping[str, Any], hoje: date) -> Secao:
    proj = m["projecao"]
    horizontes = [f"{p['dias']} dias" for p in proj]
    projecao = (
        Linha("Entradas previstas", tuple(moeda(p["entradas"]) for p in proj)),
        Linha("Saídas previstas", tuple(moeda(p["saidas"]) for p in proj)),
        Linha(
            "Saldo projetado",
            tuple(moeda(p["saldo"]) for p in proj),
            destaque=True,
            cores=tuple(_cor(p["saldo"]) for p in proj),
        ),
    )
    return Secao(
        titulo="Projeção de entradas e saídas",
        subtitulo=(
            f"Foto de {hoje:%d/%m}: títulos em aberto (a receber e a pagar)"
            " que vencem de hoje em diante."
        ),
        tabelas=(
            Tabela(
                cabecalho=("Próximos", *horizontes),
                linhas=projecao,
            ),
        ),
    )


def _secao_faturamento(m: Mapping[str, Any], ref: date) -> Secao:
    fat = m["faturamento"]
    linhas = [
        Linha(
            c["categoria"],
            (
                inteiro(c["qtde"]),
                moeda(c["valor"]),
                moeda(c["ticket"]) if c["ticket"] is not None else "-",
            ),
        )
        for c in fat["categorias"]
    ]
    if fat.get("ajuste_mes"):
        linhas.append(Linha("Descontos, acréscimos e ajustes", ("", moeda(fat["ajuste_mes"]), "")))
    linhas.append(Linha("Total faturado no mês", ("", moeda(fat["mes"]), ""), destaque=True))
    return Secao(
        titulo="Faturamento por serviço",
        subtitulo=(
            f"Regime de competência: contas emitidas de 01/{ref:%m} a {ref:%d/%m},"
            " sem as canceladas. "
            "Ticket médio considera só os itens cobrados."
        ),
        tabelas=(
            Tabela(
                cabecalho=("Categoria", "Volume", "Faturado", "Ticket médio"),
                linhas=tuple(linhas),
                vazio="Nenhuma conta emitida no mês.",
            ),
        ),
    )


def _secao_recorrente(m: Mapping[str, Any], hoje: date, sem: Sequence[str]) -> Secao:
    rec = m["recorrente"]
    resumo = (
        Linha("MRR (mensalidades dos últimos 30 dias)", (moeda(rec["mrr"]),), destaque=True),
        Linha("Clientes com mensalidade faturada", (inteiro(rec["clientes_mrr"]),)),
        Linha("Contratos ativos", (inteiro(rec["contratos_ativos"]),)),
        Linha(
            "Faturamento por vidas no mês",
            (moeda(rec["mensalidades_mes"]),),
            nota=f"{inteiro(rec['titulos_mensalidade_mes'])} contas",
        ),
        Linha(
            "Excedentes faturados no mês",
            (moeda(rec["excedentes_mes"]),),
            nota=f"{inteiro(rec['contratos_com_excedente'])} contratos com excedente configurado",
        ),
    )
    contratos = tuple(
        Linha(
            c["cliente"],
            (
                f"#{c['id']}",
                f"{_data(c['vencimento']):%d/%m/%Y}" if c["vencimento"] else "-",
                f"{c['dias']} dias" if c["dias"] is not None else "-",
                moeda(c["mensalidade"]) if c["mensalidade"] else "-",
            ),
        )
        for c in m["contratos_a_vencer"]
    )
    margem = tuple(
        Linha(
            s["servico"],
            (
                inteiro(s["qtde"]),
                moeda(s["faturado"]),
                moeda(s["custo"]) if s["custo"] is not None else "sem custo na tabela",
                moeda(s["margem"]) if s["margem"] is not None else "-",
                percentual(s["margem_pct"]),
            ),
            cores=(NEUTRO, NEUTRO, NEUTRO, _cor(s["margem"]), _cor(s["margem"])),
        )
        for s in m["margem"]
    )
    rateio = m["rateio"]
    centros = tuple(
        Linha(c["centro"], (moeda(c["recebido"]), moeda(c["pago"]))) for c in rateio["centros"]
    )
    despesas = tuple(
        Linha(d["classificacao"], (inteiro(d["titulos"]), moeda(d["valor"])))
        for d in rateio["despesas_por_classificacao"]
    )
    return Secao(
        titulo="Receita recorrente, contratos e margem",
        subtitulo=(
            "MRR medido pelas mensalidades faturadas a clientes com contrato ativo"
            " (o SGG não guarda o valor mensal no contrato)."
        ),
        etiqueta=_etiqueta("mrr", sem),
        tabelas=(
            Tabela(cabecalho=("Indicador", "Valor"), linhas=resumo),
            Tabela(
                titulo=f"Contratos a vencer em 30 dias (até {hoje + timedelta(days=30):%d/%m})",
                cabecalho=("Cliente", "Contrato", "Vencimento", "Faltam", "Mensalidade"),
                linhas=contratos,
                ocultar_no_celular=(1, 3),
                vazio="Nenhum contrato vence nos próximos 30 dias.",
                etiqueta=_etiqueta("contratos_a_vencer", sem),
            ),
            Tabela(
                titulo="Margem bruta estimada por exame (mês)",
                cabecalho=("Serviço", "Qtd.", "Faturado", "Custo estimado", "Margem", "%"),
                linhas=margem,
                ocultar_no_celular=(1, 3),
                vazio="Nenhum exame faturado no mês.",
                etiqueta=_etiqueta("margem", sem),
            ),
            Tabela(
                titulo="Rateio por centro de custo (mês)",
                cabecalho=("Centro de custo", "Recebido", "Pago"),
                linhas=centros,
                vazio="Nenhum pagamento no mês.",
                etiqueta=_etiqueta("rateio", sem),
            ),
            Tabela(
                titulo="Despesas pagas por classificação (mês)",
                cabecalho=("Classificação", "Títulos", "Valor"),
                linhas=despesas,
                vazio="Nenhuma despesa paga no mês.",
            ),
        ),
    )


DEFINICOES = (
    ("Receita recebida", "títulos a receber pagos na data (caixa), pelo valor cobrado."),
    ("Faturamento", "contas a receber emitidas no período (competência)."),
    ("Saldo operacional", "receita recebida menos despesas pagas."),
    ("Projeção", "títulos em aberto (a receber e a pagar) que vencem de hoje em diante."),
    ("MRR", "mensalidades faturadas nos últimos 30 dias a clientes com contrato ativo."),
    ("Margem bruta", "faturado menos o custo médio dos credenciados na tabela de preços do SGG."),
)


def montar_apresentacao_financeira(
    m: Mapping[str, Any], admin_url: str = ""
) -> ApresentacaoFinanceira:
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
        secoes=(
            _secao_caixa(m, ref),
            _secao_projecao(m, hoje),
            _secao_faturamento(m, ref),
            _secao_recorrente(m, hoje, sem),
        ),
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
