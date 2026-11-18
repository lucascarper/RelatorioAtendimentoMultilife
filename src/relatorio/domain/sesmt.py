"""Relatório de gestão do SESMT: programas e laudos vencidos, os que vencem no mês que vem
e os envios ao eSocial do mês.

Funções puras sobre o que já foi lido do SGG (empresas, programas/laudos e eventos do
eSocial). As regras:

* **Documento corrente**: programas e laudos (PGR, PCMSO, LTCAT) são emitidos de novo a
  cada renovação e o SGG guarda todo o histórico. Para cada empresa e tipo vale o **mais
  recente** (maior emissão; empate ou emissão ausente, maior vencimento). Só o vencimento
  dele conta; os anteriores foram renovados. Entram **todas** as empresas do SGG.
* **Vencido**: o vencimento do documento corrente é anterior a ``hoje`` (o dia do e-mail).
* **Vence no mês que vem**: o vencimento cai no mês civil seguinte ao de ``hoje``.
* **eSocial**: eventos gerados do dia 1º do mês de ``referencia`` até ``referencia``.
  Enviado = gerado; rejeitado = sem recibo (a API não separa "rejeitado" de "ainda não
  transmitido": decisão do SESMT tratar os dois como rejeitados).

LGPD: nenhum nome de funcionário nem CPF. Os rejeitados saem agrupados por empresa.

Mudou alguma regra? Suba ``VERSAO_REGRA_SESMT`` e reprocesse os dias afetados.
"""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

VERSAO_REGRA_SESMT = "2.0.0"

TIPOS_DOCUMENTO = ("PGR", "PCMSO", "LTCAT")
MESES_HISTORICO_ESOCIAL = 12
SEM_GRUPO = "Sem grupo"


@dataclass(frozen=True, slots=True)
class EmpresaSesmt:
    id: int
    nome: str  # pessoa física vira "Empresa #id" (LGPD)
    cnpj: str  # só CNPJ válido (14 dígitos); vazio caso contrário
    id_grupo: str
    esocial_habilitado: bool


@dataclass(frozen=True, slots=True)
class DocumentoSst:
    id_empresa: int
    tipo: str
    vencimento: date | None
    emissao: date | None = None
    situacao: str = ""
    codigo: str = ""


@dataclass(frozen=True, slots=True)
class EventoEsocial:
    codigo: str
    tipo: str
    id_empresa: int
    data_geracao: date | None
    recibo: str = ""

    @property
    def rejeitado(self) -> bool:
        return not self.recibo


@dataclass(frozen=True, slots=True)
class DadosSesmt:
    """Tudo o que o cálculo precisa, já lido do SGG."""

    referencia: date  # último dia de eventos do eSocial (o dia da coleta)
    hoje: date  # dia do e-mail: base dos vencimentos
    empresas: Mapping[int, EmpresaSesmt]
    documentos: Sequence[DocumentoSst]
    eventos: Sequence[EventoEsocial]
    nomes_grupos: Mapping[str, str] = field(default_factory=dict)
    empresas_sem_consulta: int = 0  # empresas cuja consulta falhou (relatório parcial)


def da_versao_atual(metricas: Mapping[str, Any]) -> bool:
    """Resumos da regra 1.x (contratos e documentos a vencer) têm outro formato."""
    versao = str(metricas.get("versao_regra", ""))
    return versao.split(".", maxsplit=1)[0] == VERSAO_REGRA_SESMT.split(".", maxsplit=1)[0]


# --------------------------------------------------------------------------- datas


def _ultimo_dia(ano: int, mes: int) -> date:
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def mes_seguinte(dia: date) -> tuple[date, date]:
    inicio = _ultimo_dia(dia.year, dia.month) + timedelta(days=1)
    return inicio, _ultimo_dia(inicio.year, inicio.month)


def _meses_ate(dia: date, quantidade: int) -> list[date]:
    """O dia 1º dos ``quantidade`` meses que terminam no mês de ``dia``, do mais antigo."""
    meses = [dia.replace(day=1)]
    while len(meses) < quantidade:
        meses.append((meses[-1] - timedelta(days=1)).replace(day=1))
    return meses[::-1]


def mesmo_periodo_mes_anterior(fim: date) -> tuple[date, date]:
    """1º até o mesmo dia do mês anterior (31/03 → 01/02 a 28/02)."""
    inicio = (fim.replace(day=1) - timedelta(days=1)).replace(day=1)
    return inicio, min(inicio + timedelta(days=fim.day - 1), _ultimo_dia(inicio.year, inicio.month))


# --------------------------------------------------------------------------- nomes


def nome_grupo(empresa: EmpresaSesmt | None, nomes: Mapping[str, str]) -> str:
    if empresa is None or not empresa.id_grupo:
        return SEM_GRUPO
    return nomes.get(empresa.id_grupo) or f"Grupo {empresa.id_grupo}"


def _empresa(id_empresa: int, dados: DadosSesmt) -> dict[str, Any]:
    empresa = dados.empresas.get(id_empresa)
    return {
        "id_empresa": id_empresa,
        "empresa": empresa.nome if empresa else f"Empresa #{id_empresa}",
        "cnpj": empresa.cnpj if empresa else "",
        "grupo": nome_grupo(empresa, dados.nomes_grupos),
    }


# --------------------------------------------------------------------------- documentos


def documentos_correntes(documentos: Sequence[DocumentoSst]) -> list[DocumentoSst]:
    """O documento mais recente de cada empresa e tipo (os demais foram renovados).

    Um vencimento digitado errado num documento antigo não pode esconder nem criar pendência.
    """
    correntes: dict[tuple[int, str], DocumentoSst] = {}
    for doc in documentos:
        chave = (doc.id_empresa, doc.tipo)
        atual = correntes.get(chave)
        if atual is None or _recencia(doc) > _recencia(atual):
            correntes[chave] = doc
    return [d for d in correntes.values() if d.vencimento is not None]


def _recencia(doc: DocumentoSst) -> tuple[date, date]:
    return (doc.emissao or date.min, doc.vencimento or date.min)


def _vencidos(correntes: Sequence[DocumentoSst], dados: DadosSesmt) -> dict[str, Any]:
    vencidos = sorted(
        (d for d in correntes if d.vencimento is not None and d.vencimento < dados.hoje),
        key=lambda d: (d.vencimento, d.id_empresa, d.tipo),
    )
    por_grupo: defaultdict[str, Counter[str]] = defaultdict(Counter)
    itens = []
    for doc in vencidos:
        linha = _empresa(doc.id_empresa, dados)
        por_grupo[linha["grupo"]][doc.tipo] += 1
        itens.append({**linha, "tipo": doc.tipo, "vencimento": str(doc.vencimento)})
    return {
        "total": len(vencidos),
        "empresas": len({d.id_empresa for d in vencidos}),
        "por_tipo": {t: sum(1 for d in vencidos if d.tipo == t) for t in TIPOS_DOCUMENTO},
        "por_grupo": [
            {"grupo": g, "total": sum(c.values()), "por_tipo": {t: c[t] for t in TIPOS_DOCUMENTO}}
            for g, c in sorted(por_grupo.items(), key=lambda par: (-sum(par[1].values()), par[0]))
        ],
        "itens": itens,  # do mais atrasado ao menos (planilha)
    }


def _proximo_mes(correntes: Sequence[DocumentoSst], dados: DadosSesmt) -> dict[str, Any]:
    inicio, fim = mes_seguinte(dados.hoje)
    por_empresa: defaultdict[int, list[DocumentoSst]] = defaultdict(list)
    for doc in correntes:
        if doc.vencimento is not None and inicio <= doc.vencimento <= fim:
            por_empresa[doc.id_empresa].append(doc)
    empresas = []
    for id_empresa, docs in por_empresa.items():
        docs.sort(key=lambda d: (d.vencimento, d.tipo))
        empresas.append(
            {
                **_empresa(id_empresa, dados),
                "primeiro": str(docs[0].vencimento),
                "documentos": [{"tipo": d.tipo, "vencimento": str(d.vencimento)} for d in docs],
            }
        )
    empresas.sort(key=lambda e: (e["primeiro"], e["empresa"].casefold()))
    return {
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "total": sum(len(e["documentos"]) for e in empresas),
        "empresas_total": len(empresas),
        "empresas": empresas,
    }


# --------------------------------------------------------------------------- eSocial


def _contagem(eventos: Sequence[EventoEsocial], inicio: date, fim: date) -> tuple[int, int]:
    """(enviados, rejeitados) gerados de ``inicio`` a ``fim``."""
    do_periodo = [e for e in eventos if e.data_geracao and inicio <= e.data_geracao <= fim]
    return len(do_periodo), sum(1 for e in do_periodo if e.rejeitado)


def _esocial(dados: DadosSesmt) -> dict[str, Any]:
    inicio, fim = dados.referencia.replace(day=1), dados.referencia
    rejeitados = [
        e
        for e in dados.eventos
        if e.rejeitado and e.data_geracao and inicio <= e.data_geracao <= fim
    ]
    por_empresa: defaultdict[int, Counter[str]] = defaultdict(Counter)
    for evento in rejeitados:
        por_empresa[evento.id_empresa][evento.tipo] += 1
    enviados, _ = _contagem(dados.eventos, inicio, fim)
    antes_de, antes_ate = mesmo_periodo_mes_anterior(fim)
    enviados_antes, rejeitados_antes = _contagem(dados.eventos, antes_de, antes_ate)
    return {
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "enviados": enviados,
        "rejeitados": len(rejeitados),
        "anterior": {
            "inicio": antes_de.isoformat(),
            "fim": antes_ate.isoformat(),
            "enviados": enviados_antes,
            "rejeitados": rejeitados_antes,
        },
        "empresas_rejeitadas": len(por_empresa),
        "rejeitados_por_empresa": sorted(
            (
                {
                    **_empresa(id_empresa, dados),
                    "quantidade": sum(tipos.values()),
                    "tipos": dict(sorted(tipos.items())),
                }
                for id_empresa, tipos in por_empresa.items()
            ),
            key=lambda e: (-e["quantidade"], e["empresa"].casefold()),
        ),
    }


def _esocial_mensal(dados: DadosSesmt) -> list[dict[str, Any]]:
    """Enviados e rejeitados de cada um dos últimos 12 meses (o atual, até a referência)."""
    linhas = []
    for inicio in _meses_ate(dados.referencia, MESES_HISTORICO_ESOCIAL):
        fim = min(_ultimo_dia(inicio.year, inicio.month), dados.referencia)
        enviados, rejeitados = _contagem(dados.eventos, inicio, fim)
        linhas.append(
            {"mes": inicio.isoformat()[:7], "enviados": enviados, "rejeitados": rejeitados}
        )
    return linhas


# --------------------------------------------------------------------------- resumo


def calcular_sesmt(dados: DadosSesmt, gerado_em: datetime) -> dict[str, Any]:
    """Resumo do dia em JSON puro (é o que vai para ``resumo_sesmt.metricas``)."""
    correntes = documentos_correntes(dados.documentos)
    return {
        "versao_regra": VERSAO_REGRA_SESMT,
        "referencia": dados.referencia.isoformat(),
        "hoje": dados.hoje.isoformat(),
        "gerado_em": gerado_em.isoformat(),
        "empresas_consultadas": len(dados.empresas),
        "empresas_sem_consulta": dados.empresas_sem_consulta,
        "vencidos": _vencidos(correntes, dados),
        "proximo_mes": _proximo_mes(correntes, dados),
        "esocial": _esocial(dados),
        "esocial_mensal": _esocial_mensal(dados),
    }
