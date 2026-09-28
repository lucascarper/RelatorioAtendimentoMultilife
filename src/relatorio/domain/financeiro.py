"""Relatório financeiro diário: indicadores de caixa, inadimplência, faturamento e contratos.

Funções puras sobre títulos (contas a receber e a pagar), contratos e a tabela de preços
de fornecedores, já lidos do SGG. As regras seguem o glossário do escopo:

* **Receita recebida** (caixa): títulos a receber com pagamento na data. Vale o valor
  cobrado (com juros e multa), que é o que entrou; sem ele, o valor do título.
* **Despesas pagas** (caixa): títulos a pagar com pagamento na data, mesmo critério.
* **Faturamento** (competência): títulos a receber emitidos no período, sem os
  cancelados. É outro número, mesmo quando coincide com a receita do dia.
* **Inadimplência**: títulos a receber vencidos e sem pagamento, contados a partir do
  dia seguinte ao vencimento, sem carência.
* **Projeções**: títulos em aberto que vencem de hoje até hoje + N - 1 dias.
* **MRR**: mensalidades (planos de gestão, cobrança por vidas) faturadas nos últimos
  30 dias para clientes com contrato ativo. O SGG não guarda o valor mensal no
  contrato, então a receita recorrente é medida pelo que foi de fato faturado.
* **Contratos a vencer**: contratos em andamento que vencem nos próximos 30 dias.
* **Margem bruta**: faturado no mês menos o custo estimado pela tabela de preços dos
  fornecedores credenciados (média do valor a pagar, sem os de custo zero).
* **Rateio**: recebido e pago no mês por centro de custo, pelo rateio de cada título.

Mudou alguma regra? Suba ``VERSAO_REGRA_FINANCEIRO`` e reprocesse os dias afetados.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

VERSAO_REGRA_FINANCEIRO = "1.0.0"

ZERO = Decimal("0")
CENTAVO = Decimal("0.01")
JANELA_MRR_DIAS = 30
JANELA_CONTRATOS_DIAS = 30
HORIZONTES_PROJECAO = (7, 15, 30)
FAIXAS_ATRASO: tuple[tuple[str, int, int | None], ...] = (
    ("1 a 30 dias", 1, 30),
    ("31 a 60 dias", 31, 60),
    ("61 a 90 dias", 61, 90),
    ("91 a 180 dias", 91, 180),
    ("Mais de 180 dias", 181, None),
)
MAIORES_DEVEDORES = 5
MAXIMO_SERVICOS_MARGEM = 10
MAXIMO_CLASSIFICACOES = 8
SEM_CENTRO = "Sem centro de custo"
SEM_CLASSIFICACAO = "Sem classificação"


# ------------------------------------------------------------------ categorias de serviço


class Categoria:
    CLINICOS = "Exames clínicos"
    COMPLEMENTARES = "Exames complementares"
    PROGRAMAS = "Programas e laudos (PGR, PCMSO)"
    MENSALIDADES = "Mensalidades (vidas)"
    FALTAS = "Faltas cobradas"
    OUTROS = "Outros serviços"


ORDEM_CATEGORIAS = (
    Categoria.CLINICOS,
    Categoria.COMPLEMENTARES,
    Categoria.PROGRAMAS,
    Categoria.MENSALIDADES,
    Categoria.FALTAS,
    Categoria.OUTROS,
)
CATEGORIAS_COM_MARGEM = frozenset({Categoria.CLINICOS, Categoria.COMPLEMENTARES})

# Ordem importa: a primeira regra que casar define a categoria. Os nomes vêm do cadastro
# de serviços do SGG ("Outro" é a mensalidade dos contratos de gestão).
_REGRAS_CATEGORIA: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        Categoria.MENSALIDADES,
        re.compile(r"^outro$|^plano\b|mensalidade|\bvidas?\b|excedente", re.I),
    ),
    (Categoria.FALTAS, re.compile(r"falta", re.I)),
    (
        Categoria.PROGRAMAS,
        re.compile(
            r"\bPGR|PCMSO|LTCAT|\bLAUDO|\bAET\b|ERGON|INSALUBRIDADE|PERICULOSIDADE|MEDI[CÇ][OÕ]ES"
            r"|CARACTERIZA|DOC\.?\s*TEC|ACR[EÉ]SCIMO DE FUN|PPP\b|PGRSS",
            re.I,
        ),
    ),
    (Categoria.CLINICOS, re.compile(r"cl[ií]nico|atestado|\bASO\b", re.I)),
    (
        Categoria.COMPLEMENTARES,
        re.compile(
            r"\(\d{4}\)|audiometri|acuidade|raio|\brx\b|hemograma|glic|colesterol|hepatite"
            r"|eletro|espirometri|vacina|exame|\btest[e]? de|avalia[cç][aã]o (psico|oftalmo)"
            r"|beta ?hcg|urin|toxicol",
            re.I,
        ),
    ),
)


def categorizar(servico: str) -> str:
    nome = " ".join((servico or "").split())
    for categoria, regra in _REGRAS_CATEGORIA:
        if regra.search(nome):
            return categoria
    return Categoria.OUTROS


# ------------------------------------------------------------------ entidades


@dataclass(frozen=True, slots=True)
class ItemFaturado:
    id_servico: int | None
    servico: str
    qtde: int
    valor: Decimal

    @property
    def categoria(self) -> str:
        return categorizar(self.servico)


@dataclass(frozen=True, slots=True)
class Titulo:
    """Parcela de conta a receber ou a pagar, sem dados de pessoas físicas."""

    id: int
    valor: Decimal
    situacao: str
    cancelada: bool
    emissao: date | None
    vencimento: date | None
    pagamento: date | None
    valor_cobrado: Decimal | None = None
    id_cliente: int | None = None
    nome: str = ""
    classificacao: str = ""
    rateio: tuple[tuple[str, Decimal], ...] = ()
    itens: tuple[ItemFaturado, ...] = ()

    @property
    def valor_liquidado(self) -> Decimal:
        """O que de fato entrou ou saiu: o valor cobrado (com juros), senão o do título."""
        if self.valor_cobrado is not None and self.valor_cobrado > 0:
            return self.valor_cobrado
        return self.valor

    @property
    def em_aberto(self) -> bool:
        return not self.cancelada and self.pagamento is None


@dataclass(frozen=True, slots=True)
class Contrato:
    id: int
    id_cliente: int | None
    vencimento: date | None
    situacao: str
    excedente_configurado: bool = False


@dataclass(frozen=True, slots=True)
class PrecoFornecedor:
    id_servico: int
    id_fornecedor: int | None
    id_empresa: int | None  # None: tabela padrão do fornecedor
    valor_cobrar: Decimal
    valor_pagar: Decimal


@dataclass(frozen=True, slots=True)
class DadosFinanceiros:
    """Tudo o que o cálculo precisa, já lido do SGG."""

    referencia: date  # dia do relatório (ontem, no envio das 07:59)
    hoje: date  # dia da coleta: base da inadimplência, das projeções e dos contratos
    recebidos_mes: Sequence[Titulo]
    pagos_mes: Sequence[Titulo]
    emitidos: Sequence[Titulo]
    vencidos: Sequence[Titulo]
    a_receber: Sequence[Titulo]
    a_pagar: Sequence[Titulo]
    contratos: Sequence[Contrato]
    precos: Mapping[int, Sequence[PrecoFornecedor]] = field(default_factory=dict)
    nomes_centros: Mapping[str, str] = field(default_factory=dict)


def inicio_do_mes(dia: date) -> date:
    return dia.replace(day=1)


def inicio_janela_emissao(referencia: date) -> date:
    """Primeiro dia de emissão que o cálculo usa (mês corrente e janela do MRR)."""
    return min(inicio_do_mes(referencia), referencia - timedelta(days=JANELA_MRR_DIAS - 1))


# ------------------------------------------------------------------ cálculo


def _dinheiro(valor: Decimal) -> float:
    return float(valor.quantize(CENTAVO, rounding=ROUND_HALF_UP))


def _soma(titulos: Iterable[Titulo]) -> Decimal:
    return sum((t.valor_liquidado for t in titulos), ZERO)


def _soma_valor(titulos: Iterable[Titulo]) -> Decimal:
    return sum((t.valor for t in titulos), ZERO)


def _caixa(dados: DadosFinanceiros) -> dict[str, Any]:
    ref, mes = dados.referencia, inicio_do_mes(dados.referencia)

    def no(titulos: Sequence[Titulo], de: date, ate: date) -> list[Titulo]:
        return [t for t in titulos if not t.cancelada and t.pagamento and de <= t.pagamento <= ate]

    recebidos_dia, pagos_dia = no(dados.recebidos_mes, ref, ref), no(dados.pagos_mes, ref, ref)
    recebidos_mes, pagos_mes = no(dados.recebidos_mes, mes, ref), no(dados.pagos_mes, mes, ref)
    return {
        "recebido_dia": _dinheiro(_soma(recebidos_dia)),
        "pago_dia": _dinheiro(_soma(pagos_dia)),
        "saldo_dia": _dinheiro(_soma(recebidos_dia) - _soma(pagos_dia)),
        "titulos_recebidos_dia": len(recebidos_dia),
        "titulos_pagos_dia": len(pagos_dia),
        "recebido_mes": _dinheiro(_soma(recebidos_mes)),
        "pago_mes": _dinheiro(_soma(pagos_mes)),
        "saldo_mes": _dinheiro(_soma(recebidos_mes) - _soma(pagos_mes)),
    }


def _nome_cliente(titulo: Titulo) -> str:
    if titulo.nome.strip():
        return " ".join(titulo.nome.split())
    return f"Cliente #{titulo.id_cliente}" if titulo.id_cliente else "Cliente sem cadastro"


def _inadimplencia(dados: DadosFinanceiros) -> dict[str, Any]:
    vencidos = [
        t for t in dados.vencidos if t.em_aberto and t.vencimento and t.vencimento < dados.hoje
    ]
    faixas = []
    for rotulo, minimo, maximo in FAIXAS_ATRASO:
        na_faixa = [
            t
            for t in vencidos
            if t.vencimento
            and minimo <= (dados.hoje - t.vencimento).days
            and (maximo is None or (dados.hoje - t.vencimento).days <= maximo)
        ]
        faixas.append(
            {"rotulo": rotulo, "valor": _dinheiro(_soma_valor(na_faixa)), "titulos": len(na_faixa)}
        )
    por_cliente: dict[str, list[Titulo]] = defaultdict(list)
    for t in vencidos:
        por_cliente[_nome_cliente(t)].append(t)
    maiores = sorted(por_cliente.items(), key=lambda par: -_soma_valor(par[1]))
    return {
        "valor": _dinheiro(_soma_valor(vencidos)),
        "titulos": len(vencidos),
        "clientes": len(por_cliente),
        "faixas": faixas,
        "maiores": [
            {
                "cliente": cliente,
                "valor": _dinheiro(_soma_valor(titulos)),
                "titulos": len(titulos),
                "dias_max": max((dados.hoje - t.vencimento).days for t in titulos if t.vencimento),
            }
            for cliente, titulos in maiores[:MAIORES_DEVEDORES]
        ],
    }


def _projecao(dados: DadosFinanceiros) -> list[dict[str, Any]]:
    linhas = []
    for dias in HORIZONTES_PROJECAO:
        ate = dados.hoje + timedelta(days=dias - 1)

        def no_prazo(titulos: Sequence[Titulo], ate: date = ate) -> list[Titulo]:
            return [
                t
                for t in titulos
                if t.em_aberto and t.vencimento and dados.hoje <= t.vencimento <= ate
            ]

        entradas, saidas = no_prazo(dados.a_receber), no_prazo(dados.a_pagar)
        linhas.append(
            {
                "dias": dias,
                "ate": ate.isoformat(),
                "entradas": _dinheiro(_soma_valor(entradas)),
                "saidas": _dinheiro(_soma_valor(saidas)),
                "saldo": _dinheiro(_soma_valor(entradas) - _soma_valor(saidas)),
                "titulos_entrada": len(entradas),
                "titulos_saida": len(saidas),
            }
        )
    return linhas


def _emitidos(dados: DadosFinanceiros, de: date, ate: date) -> list[Titulo]:
    return [t for t in dados.emitidos if not t.cancelada and t.emissao and de <= t.emissao <= ate]


def _faturamento(dados: DadosFinanceiros) -> dict[str, Any]:
    ref = dados.referencia
    do_dia = _emitidos(dados, ref, ref)
    do_mes = _emitidos(dados, inicio_do_mes(ref), ref)
    qtde: dict[str, int] = defaultdict(int)
    cobrados: dict[str, int] = defaultdict(int)
    valor: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for titulo in do_mes:
        for item in titulo.itens:
            qtde[item.categoria] += item.qtde
            valor[item.categoria] += item.valor
            if item.valor > 0:
                cobrados[item.categoria] += item.qtde
    categorias = [
        {
            "categoria": c,
            "qtde": qtde[c],
            "valor": _dinheiro(valor[c]),
            "ticket": _dinheiro(valor[c] / cobrados[c]) if cobrados[c] else None,
        }
        for c in ORDEM_CATEGORIAS
        if qtde[c] or valor[c]
    ]
    total_mes = _soma_valor(do_mes)
    return {
        "dia": _dinheiro(_soma_valor(do_dia)),
        "titulos_dia": len(do_dia),
        "mes": _dinheiro(total_mes),
        "titulos_mes": len(do_mes),
        "categorias": categorias,
        # Descontos, acréscimos e itens sem detalhamento: fecha a soma das categorias
        # com o total faturado.
        "ajuste_mes": _dinheiro(total_mes - sum(valor.values(), ZERO)),
    }


def _mensalidades(titulos: Iterable[Titulo]) -> list[tuple[Titulo, ItemFaturado]]:
    return [
        (t, item) for t in titulos for item in t.itens if item.categoria == Categoria.MENSALIDADES
    ]


def _recorrente(dados: DadosFinanceiros) -> tuple[dict[str, Any], dict[int, Decimal]]:
    ref = dados.referencia
    clientes_ativos = {c.id_cliente for c in dados.contratos if c.id_cliente is not None}
    janela = _emitidos(dados, ref - timedelta(days=JANELA_MRR_DIAS - 1), ref)
    mensal_por_cliente: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for titulo, item in _mensalidades(janela):
        if titulo.id_cliente in clientes_ativos:
            mensal_por_cliente[titulo.id_cliente] += item.valor
    do_mes = _emitidos(dados, inicio_do_mes(ref), ref)
    mensalidades_mes = _mensalidades(do_mes)
    excedentes = [(t, i) for t, i in mensalidades_mes if "excedente" in i.servico.lower()]
    return (
        {
            "mrr": _dinheiro(sum(mensal_por_cliente.values(), ZERO)),
            "clientes_mrr": sum(1 for v in mensal_por_cliente.values() if v > 0),
            "contratos_ativos": len(dados.contratos),
            "mensalidades_mes": _dinheiro(sum((i.valor for _, i in mensalidades_mes), ZERO)),
            "titulos_mensalidade_mes": len({t.id for t, _ in mensalidades_mes}),
            "excedentes_mes": _dinheiro(sum((i.valor for _, i in excedentes), ZERO)),
            "contratos_com_excedente": sum(1 for c in dados.contratos if c.excedente_configurado),
        },
        mensal_por_cliente,
    )


def _contratos_a_vencer(
    dados: DadosFinanceiros, mensal_por_cliente: Mapping[int, Decimal]
) -> list[dict[str, Any]]:
    limite = dados.hoje + timedelta(days=JANELA_CONTRATOS_DIAS)
    nomes: dict[int, str] = {}
    for t in (*dados.emitidos, *dados.recebidos_mes, *dados.vencidos, *dados.a_receber):
        if t.id_cliente is not None and t.nome.strip():
            nomes.setdefault(t.id_cliente, " ".join(t.nome.split()))
    vencendo = sorted(
        (c for c in dados.contratos if c.vencimento and dados.hoje <= c.vencimento <= limite),
        key=lambda c: (c.vencimento, c.id),
    )
    return [
        {
            "id": c.id,
            "cliente": nomes.get(c.id_cliente or -1)
            or (f"Cliente #{c.id_cliente}" if c.id_cliente else "Cliente sem cadastro"),
            "vencimento": c.vencimento.isoformat() if c.vencimento else None,
            "dias": (c.vencimento - dados.hoje).days if c.vencimento else None,
            "mensalidade": _dinheiro(mensal_por_cliente.get(c.id_cliente or -1, ZERO)),
        }
        for c in vencendo
    ]


def custo_medio(precos: Sequence[PrecoFornecedor]) -> Decimal | None:
    """Custo de credenciado: média do valor a pagar (tabela padrão), sem os de custo zero."""
    padrao = [p.valor_pagar for p in precos if p.id_empresa is None and p.valor_pagar > 0]
    valores = padrao or [p.valor_pagar for p in precos if p.valor_pagar > 0]
    return sum(valores, ZERO) / len(valores) if valores else None


def servicos_para_margem(emitidos_mes: Iterable[Titulo]) -> list[int]:
    """IDs dos serviços de exame mais faturados no mês (os que entram na tabela de margem)."""
    faturado: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for titulo in emitidos_mes:
        if titulo.cancelada:
            continue
        for item in titulo.itens:
            if item.id_servico is not None and item.categoria in CATEGORIAS_COM_MARGEM:
                faturado[item.id_servico] += item.valor
    ordem = sorted((v, k) for k, v in faturado.items() if v > 0)
    return [k for _, k in reversed(ordem)][:MAXIMO_SERVICOS_MARGEM]


def _margem(dados: DadosFinanceiros) -> list[dict[str, Any]]:
    do_mes = _emitidos(dados, inicio_do_mes(dados.referencia), dados.referencia)
    nomes: dict[int, str] = {}
    qtde: dict[int, int] = defaultdict(int)
    faturado: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for titulo in do_mes:
        for item in titulo.itens:
            if item.id_servico is None or item.valor <= 0:
                continue
            nomes.setdefault(item.id_servico, item.servico)
            qtde[item.id_servico] += item.qtde
            faturado[item.id_servico] += item.valor
    linhas = []
    for id_servico in servicos_para_margem(do_mes):
        custo_unitario = custo_medio(dados.precos.get(id_servico, ()))
        receita = faturado[id_servico]
        custo = custo_unitario * qtde[id_servico] if custo_unitario is not None else None
        margem = receita - custo if custo is not None else None
        linhas.append(
            {
                "servico": " ".join(nomes[id_servico].split()),
                "qtde": qtde[id_servico],
                "faturado": _dinheiro(receita),
                "preco_medio": _dinheiro(receita / qtde[id_servico]) if qtde[id_servico] else None,
                "custo_medio": _dinheiro(custo_unitario) if custo_unitario is not None else None,
                "custo": _dinheiro(custo) if custo is not None else None,
                "margem": _dinheiro(margem) if margem is not None else None,
                "margem_pct": float(round(margem / receita, 4))
                if margem is not None and receita
                else None,
            }
        )
    return linhas


def _rateio(dados: DadosFinanceiros) -> dict[str, Any]:
    ref, mes = dados.referencia, inicio_do_mes(dados.referencia)

    def pagos(titulos: Sequence[Titulo]) -> list[Titulo]:
        return [t for t in titulos if not t.cancelada and t.pagamento and mes <= t.pagamento <= ref]

    def por_centro(titulos: Sequence[Titulo]) -> dict[str, Decimal]:
        totais: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for t in titulos:
            if not t.rateio:
                totais[SEM_CENTRO] += t.valor_liquidado
                continue
            for centro, valor in t.rateio:
                totais[centro] += valor
        return totais

    recebido, pago = por_centro(pagos(dados.recebidos_mes)), por_centro(pagos(dados.pagos_mes))

    def nome(centro: str) -> str:
        if centro == SEM_CENTRO:
            return SEM_CENTRO
        return dados.nomes_centros.get(centro) or f"Centro de custo {centro}"

    centros = sorted(set(recebido) | set(pago), key=lambda c: (c == SEM_CENTRO, nome(c)))
    classificacoes: dict[str, list[Titulo]] = defaultdict(list)
    for t in pagos(dados.pagos_mes):
        classificacoes[" ".join(t.classificacao.split()) or SEM_CLASSIFICACAO].append(t)
    ordem = sorted(classificacoes.items(), key=lambda par: -_soma(par[1]))
    principais = ordem[:MAXIMO_CLASSIFICACOES]
    demais = [t for _, titulos in ordem[MAXIMO_CLASSIFICACOES:] for t in titulos]
    despesas = [
        {"classificacao": c, "valor": _dinheiro(_soma(ts)), "titulos": len(ts)}
        for c, ts in principais
    ]
    if demais:
        despesas.append(
            {
                "classificacao": "Demais classificações",
                "valor": _dinheiro(_soma(demais)),
                "titulos": len(demais),
            }
        )
    return {
        "centros": [
            {
                "centro": nome(c),
                "recebido": _dinheiro(recebido.get(c, ZERO)),
                "pago": _dinheiro(pago.get(c, ZERO)),
            }
            for c in centros
        ],
        "despesas_por_classificacao": despesas,
    }


# Blocos que costumam repetir de um dia para o outro: o e-mail avisa "sem alteração".
BLOCOS_COMPARADOS: dict[str, tuple[str, ...]] = {
    "inadimplencia": ("inadimplencia", "valor"),
    "mrr": ("recorrente", "mrr"),
    "contratos_ativos": ("recorrente", "contratos_ativos"),
    "contratos_a_vencer": ("contratos_a_vencer",),
    "margem": ("margem",),
    "rateio": ("rateio",),
}


def _valor(dados: Mapping[str, Any], caminho: Sequence[str]) -> Any:
    atual: Any = dados
    for chave in caminho:
        if not isinstance(atual, Mapping) or chave not in atual:
            return None
        atual = atual[chave]
    return atual


def sem_alteracao(atual: Mapping[str, Any], anterior: Mapping[str, Any] | None) -> list[str]:
    if not anterior:
        return []
    return [
        nome
        for nome, caminho in BLOCOS_COMPARADOS.items()
        if _valor(anterior, caminho) is not None
        and _valor(atual, caminho) == _valor(anterior, caminho)
    ]


def calcular_financeiro(
    dados: DadosFinanceiros,
    gerado_em: datetime,
    anterior: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resumo financeiro do dia em JSON (o e-mail e o admin só exibem)."""
    recorrente, mensal_por_cliente = _recorrente(dados)
    resumo: dict[str, Any] = {
        "tipo": "financeiro",
        "versao_regra": VERSAO_REGRA_FINANCEIRO,
        "referencia": dados.referencia.isoformat(),
        "hoje": dados.hoje.isoformat(),
        "gerado_em": gerado_em.isoformat(),
        "caixa": _caixa(dados),
        "inadimplencia": _inadimplencia(dados),
        "projecao": _projecao(dados),
        "faturamento": _faturamento(dados),
        "recorrente": recorrente,
        "contratos_a_vencer": _contratos_a_vencer(dados, mensal_por_cliente),
        "margem": _margem(dados),
        "rateio": _rateio(dados),
    }
    resumo["sem_alteracao"] = sem_alteracao(resumo, anterior)
    return resumo
