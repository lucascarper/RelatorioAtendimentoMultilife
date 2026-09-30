"""Relatório de gestão do SESMT: contratos e documentos a vencer, vencidos e envios ao eSocial.

Funções puras sobre o que já foi lido do SGG (empresas, contratos, programas/laudos e
eventos do eSocial). As regras:

* **Documento corrente**: programas e laudos (PGR, PCMSO, LTCAT…) são emitidos de novo a
  cada renovação e o SGG guarda todo o histórico. Para cada empresa e tipo vale o de
  **maior vencimento**; os anteriores foram substituídos e não geram pendência.
* **Contrato corrente**: só o contrato marcado como o último do cliente (``ultimo``).
  Contratos antigos já substituídos ou encerrados não entram.
* **A vencer**: vence de hoje até hoje + 30 dias. **Vencendo** é o que vence em até 7 dias.
* **Vencido**: o vencimento já passou. Contratos vencidos há mais de 90 dias são de
  clientes que saíram e não pedem ação; só entram no total, não na lista.
* **eSocial**: eventos gerados na data de referência. Com recibo = transmitido; sem
  recibo = ainda não transmitido ou rejeitado (a API não distingue os dois).

LGPD: nenhum nome de funcionário nem CPF. O evento traz só o código do funcionário e o
XML (com CPF) é descartado na leitura.

Mudou alguma regra? Suba ``VERSAO_REGRA_SESMT`` e reprocesse os dias afetados.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

VERSAO_REGRA_SESMT = "1.0.0"

TIPOS_DOCUMENTO = ("PGR", "PCMSO", "LTCAT")
JANELA_A_VENCER_DIAS = 30
DIAS_VENCENDO = 7
JANELA_CONTRATO_VENCIDO_DIAS = 90
MAX_LINHAS_PENDENCIAS = 40
MAX_LINHAS_ESOCIAL = 40
MAX_LINHAS_SEM_RECIBO = 30

STATUS_A_VENCER = "A vencer"
STATUS_VENCENDO = "Vencendo"
STATUS_VENCIDO = "Vencido"
CATEGORIA_PROGRAMA = "programa"
CATEGORIA_CONTRATO = "contrato"


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
class ContratoSesmt:
    id: int
    id_cliente: int | None
    vencimento: date | None
    situacao: str
    ultimo: bool
    codigo: str = ""


@dataclass(frozen=True, slots=True)
class EventoEsocial:
    codigo: str
    tipo: str
    id_empresa: int
    id_funcionario: int | None
    data_geracao: date | None
    gerado_em: datetime | None = None
    prazo: date | None = None
    recibo: str = ""
    protocolo: str = ""

    @property
    def transmitido(self) -> bool:
        return bool(self.recibo)


@dataclass(frozen=True, slots=True)
class DadosSesmt:
    """Tudo o que o cálculo precisa, já lido do SGG."""

    referencia: date  # dia dos eventos do eSocial (ontem, no envio da manhã)
    hoje: date  # dia da coleta: base dos vencimentos
    empresas: Mapping[int, EmpresaSesmt]
    contratos: Sequence[ContratoSesmt]
    documentos: Sequence[DocumentoSst]
    eventos: Sequence[EventoEsocial]
    nomes_grupos: Mapping[str, str] = field(default_factory=dict)
    empresas_sem_consulta: int = 0  # empresas cuja consulta falhou (relatório parcial)


# --------------------------------------------------------------------------- pendências


def _nome_grupo(empresa: EmpresaSesmt | None, nomes: Mapping[str, str]) -> str:
    if empresa is None or not empresa.id_grupo:
        return "Sem grupo"
    return nomes.get(empresa.id_grupo) or f"Grupo {empresa.id_grupo}"


def _nome_empresa(id_empresa: int | None, empresas: Mapping[int, EmpresaSesmt]) -> str:
    empresa = empresas.get(id_empresa) if id_empresa is not None else None
    if empresa is not None and empresa.nome:
        return empresa.nome
    return f"Empresa #{id_empresa}" if id_empresa is not None else "Empresa sem cadastro"


def _status(dias: int) -> str:
    if dias < 0:
        return STATUS_VENCIDO
    return STATUS_VENCENDO if dias <= DIAS_VENCENDO else STATUS_A_VENCER


@dataclass(frozen=True, slots=True)
class _Pendencia:
    categoria: str
    tipo: str
    grupo: str
    identificacao: str
    empresa: str
    id_empresa: int | None
    vencimento: date
    dias: int  # negativo quando vencido

    @property
    def status(self) -> str:
        return _status(self.dias)

    def como_dict(self) -> dict[str, Any]:
        return {
            "categoria": self.categoria,
            "tipo": self.tipo,
            "grupo": self.grupo,
            "identificacao": self.identificacao,
            "empresa": self.empresa,
            "id_empresa": self.id_empresa,
            "vencimento": self.vencimento.isoformat(),
            "dias": self.dias,
            "status": self.status,
        }


def _pendencia(
    categoria: str,
    tipo: str,
    rotulo: str,
    id_empresa: int | None,
    vencimento: date,
    dados: DadosSesmt,
) -> _Pendencia:
    empresa = dados.empresas.get(id_empresa) if id_empresa is not None else None
    nome = _nome_empresa(id_empresa, dados.empresas)
    return _Pendencia(
        categoria=categoria,
        tipo=tipo,
        grupo=_nome_grupo(empresa, dados.nomes_grupos),
        identificacao=f"{rotulo} - {nome}",
        empresa=nome,
        id_empresa=id_empresa,
        vencimento=vencimento,
        dias=(vencimento - dados.hoje).days,
    )


def documentos_correntes(documentos: Sequence[DocumentoSst]) -> list[DocumentoSst]:
    """O documento de maior vencimento de cada empresa e tipo (os demais foram renovados)."""
    correntes: dict[tuple[int, str], DocumentoSst] = {}
    for doc in documentos:
        if doc.vencimento is None:
            continue
        chave = (doc.id_empresa, doc.tipo)
        atual = correntes.get(chave)
        if atual is None or atual.vencimento is None or doc.vencimento > atual.vencimento:
            correntes[chave] = doc
    return list(correntes.values())


def _pendencias(dados: DadosSesmt) -> list[_Pendencia]:
    itens = [
        _pendencia(CATEGORIA_PROGRAMA, doc.tipo, doc.tipo, doc.id_empresa, doc.vencimento, dados)
        for doc in documentos_correntes(dados.documentos)
        if doc.vencimento is not None
    ]
    for contrato in dados.contratos:
        if contrato.ultimo and contrato.vencimento is not None:
            rotulo = f"CT-{contrato.codigo or contrato.id}"
            itens.append(
                _pendencia(
                    CATEGORIA_CONTRATO,
                    "Contrato",
                    rotulo,
                    contrato.id_cliente,
                    contrato.vencimento,
                    dados,
                )
            )
    return itens


def _contagem(itens: Sequence[_Pendencia]) -> dict[str, int]:
    por_categoria = Counter(i.categoria for i in itens)
    return {
        "documentos": por_categoria[CATEGORIA_PROGRAMA],
        "contratos": por_categoria[CATEGORIA_CONTRATO],
    }


def _a_vencer(pendencias: Sequence[_Pendencia]) -> dict[str, Any]:
    itens = sorted(
        (i for i in pendencias if 0 <= i.dias <= JANELA_A_VENCER_DIAS),
        key=lambda i: (i.dias, i.empresa),
    )
    return {
        "janela_dias": JANELA_A_VENCER_DIAS,
        "total": len(itens),
        **_contagem(itens),
        "vencendo": sum(1 for i in itens if i.status == STATUS_VENCENDO),
        "itens": [i.como_dict() for i in itens[:MAX_LINHAS_PENDENCIAS]],
        "ocultos": max(0, len(itens) - MAX_LINHAS_PENDENCIAS),
    }


def _vencidos(pendencias: Sequence[_Pendencia]) -> dict[str, Any]:
    vencidos = [i for i in pendencias if i.dias < 0]
    # Contrato vencido há muito tempo é cliente que saiu: fica só na contagem de antigos.
    recentes = [
        i
        for i in vencidos
        if i.categoria != CATEGORIA_CONTRATO or -i.dias <= JANELA_CONTRATO_VENCIDO_DIAS
    ]
    itens = sorted(recentes, key=lambda i: (i.dias, i.empresa))  # o mais atrasado primeiro
    return {
        "total": len(itens),
        **_contagem(itens),
        "contratos_antigos": len(vencidos) - len(recentes),
        "janela_contrato_dias": JANELA_CONTRATO_VENCIDO_DIAS,
        "itens": [i.como_dict() for i in itens[:MAX_LINHAS_PENDENCIAS]],
        "ocultos": max(0, len(itens) - MAX_LINHAS_PENDENCIAS),
    }


# --------------------------------------------------------------------------- eSocial


def _linha_evento(evento: EventoEsocial, empresas: Mapping[int, EmpresaSesmt]) -> dict[str, Any]:
    empresa = empresas.get(evento.id_empresa)
    return {
        "codigo": evento.codigo,
        "horario": evento.gerado_em.strftime("%H:%M:%S") if evento.gerado_em else None,
        "evento": evento.tipo,
        "funcionario": evento.id_funcionario,
        "empresa": _nome_empresa(evento.id_empresa, empresas),
        "cnpj": empresa.cnpj if empresa else "",
        "recibo": evento.recibo or None,
        "protocolo": evento.protocolo or None,
    }


def _ordem_evento(evento: EventoEsocial) -> tuple[str, str]:
    """Todos são do mesmo dia: ordena pela hora de geração e, se faltar, pelo código."""
    hora = evento.gerado_em.strftime("%H:%M:%S") if evento.gerado_em else ""
    return (hora, evento.codigo.zfill(12))


def _esocial(dados: DadosSesmt) -> dict[str, Any]:
    do_dia = sorted(
        (e for e in dados.eventos if e.data_geracao == dados.referencia), key=_ordem_evento
    )
    transmitidos = [e for e in do_dia if e.transmitido]
    sem_recibo = [e for e in do_dia if not e.transmitido]
    return {
        "referencia": dados.referencia.isoformat(),
        "total": len(do_dia),
        "com_recibo": len(transmitidos),
        "sem_recibo": len(sem_recibo),
        "por_evento": dict(sorted(Counter(e.tipo for e in do_dia).items())),
        "empresas": len({e.id_empresa for e in do_dia}),
        "eventos": [_linha_evento(e, dados.empresas) for e in transmitidos[:MAX_LINHAS_ESOCIAL]],
        "eventos_ocultos": max(0, len(transmitidos) - MAX_LINHAS_ESOCIAL),
        "pendentes": [_linha_evento(e, dados.empresas) for e in sem_recibo[:MAX_LINHAS_SEM_RECIBO]],
        "pendentes_ocultos": max(0, len(sem_recibo) - MAX_LINHAS_SEM_RECIBO),
    }


# --------------------------------------------------------------------------- resumo


def calcular_sesmt(dados: DadosSesmt, gerado_em: datetime) -> dict[str, Any]:
    """Resumo do dia em JSON puro (é o que vai para ``resumo_sesmt.metricas``)."""
    pendencias = _pendencias(dados)
    return {
        "versao_regra": VERSAO_REGRA_SESMT,
        "referencia": dados.referencia.isoformat(),
        "hoje": dados.hoje.isoformat(),
        "gerado_em": gerado_em.isoformat(),
        "a_vencer": _a_vencer(pendencias),
        "vencidos": _vencidos(pendencias),
        "esocial": _esocial(dados),
        "empresas_sem_consulta": dados.empresas_sem_consulta,
    }
