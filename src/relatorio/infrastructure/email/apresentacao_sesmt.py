"""View-model do e-mail do SESMT: só formatação (datas, textos, ações e cores).

Recebe o JSON de ``resumo_sesmt.metricas`` (``domain.sesmt``) e devolve o que o template
exibe. Nenhuma regra de cálculo fica aqui nem no template.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from relatorio.domain.resumo import dia_semana
from relatorio.domain.sesmt import DIAS_VENCENDO

AZUL = "#164F95"
VERMELHO = "#D0080F"
VERDE = "#1E7A3A"
NEUTRO = "#1B2533"
MUDO = "#6B7280"
NEGATIVO = "#A3060B"
ALERTA = "#B45309"  # laranja escuro: contraste 5,0:1 no branco
ATENCAO = "#7A4D00"

COR_STATUS = {"Vencido": NEGATIVO, "Vencendo": ALERTA, "A vencer": ATENCAO}
TIPO_CATEGORIA = {"programa": "Programas/SST", "contrato": "Contrato"}
ACAO = {
    "PGR": "Agendar visita técnica",
    "PCMSO": "Iniciar renovação/análise",
    "LTCAT": "Iniciar renovação/análise",
    "Contrato": "Enviar renovação do contrato",
}
ACAO_PADRAO = "Iniciar renovação/análise"

DEFINICOES = (
    (
        "A vencer",
        "contratos e documentos (PGR, PCMSO, LTCAT) que vencem nos próximos 30 dias;"
        ' "Vencendo" é o que vence em até 7 dias.',
    ),
    (
        "Documento",
        "vale o de maior vencimento de cada empresa e tipo (os anteriores foram renovados)."
        " Só entram empresas com contrato em andamento.",
    ),
    (
        "Contrato",
        "só o contrato corrente de cada cliente. Vencidos há mais de 90 dias são de clientes"
        " que saíram e ficam só na contagem.",
    ),
    (
        "eSocial",
        "eventos gerados na data de referência. Sem recibo = ainda não transmitido ou"
        " rejeitado (a API do SGG não informa qual dos dois).",
    ),
    ("Funcionário", "código no SGG. O relatório não traz nome nem CPF do trabalhador."),
)


def _data(texto: str | None) -> date | None:
    return date.fromisoformat(texto[:10]) if texto else None


def _data_br(texto: str | None) -> str:
    dia = _data(texto)
    return f"{dia:%d/%m/%Y}" if dia else "-"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _dias(n: int) -> str:
    return _plural(n, "dia", "dias")


@dataclass(frozen=True, slots=True)
class Celula:
    texto: str
    cor: str = ""
    negrito: bool = False
    nota: str = ""  # linha menor abaixo do texto
    quebra: bool = False  # códigos longos sem espaço (recibo) quebram em qualquer ponto


@dataclass(frozen=True, slots=True)
class Tabela:
    cabecalho: tuple[str, ...]
    linhas: tuple[tuple[Celula, ...], ...]
    titulo: str = ""
    vazio: str = "Nada a mostrar."
    rodape: str = ""  # "e mais N itens"
    ocultar_no_celular: tuple[int, ...] = ()  # índices das colunas
    numericas: tuple[int, ...] = ()  # colunas que não quebram linha


@dataclass(frozen=True, slots=True)
class Cartao:
    rotulo: str
    valor: str
    contexto: str = ""
    cor: str = NEUTRO


@dataclass(frozen=True, slots=True)
class Secao:
    numero: int
    titulo: str
    subtitulo: str
    cor: str  # cor da barra ao lado do título
    tabelas: tuple[Tabela, ...]
    cartoes: tuple[Cartao, ...] = ()


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


def _rodape_ocultos(ocultos: int, o_que: str) -> str:
    return f"e mais {ocultos} {o_que} não listados" if ocultos else ""


def _celulas_pendencia(item: Mapping[str, Any]) -> tuple[Celula, Celula, Celula, Celula, Celula]:
    status = str(item["status"])
    return (
        Celula(TIPO_CATEGORIA.get(str(item["categoria"]), str(item["categoria"]))),
        Celula(str(item["grupo"])),
        Celula(str(item["identificacao"])),
        Celula(_data_br(item["vencimento"])),
        Celula(status, COR_STATUS.get(status, NEUTRO), negrito=True),
    )


def _secao_a_vencer(m: Mapping[str, Any]) -> Secao:
    bloco = m["a_vencer"]
    linhas = tuple(
        (*_celulas_pendencia(i), Celula(ACAO.get(str(i["tipo"]), ACAO_PADRAO)))
        for i in bloco["itens"]
    )
    return Secao(
        numero=1,
        titulo=f"Contratos e documentos a vencer (próximos {bloco['janela_dias']} dias)",
        subtitulo=(
            f"{_plural(bloco['documentos'], 'documento', 'documentos')} e"
            f" {_plural(bloco['contratos'], 'contrato', 'contratos')};"
            f" {bloco['vencendo']} {'vence' if bloco['vencendo'] == 1 else 'vencem'}"
            f" em até {DIAS_VENCENDO} dias."
        ),
        cor=AZUL,
        tabelas=(
            Tabela(
                cabecalho=(
                    "Tipo",
                    "Grupo",
                    "Identificação / Empresa",
                    "Vencimento",
                    "Status",
                    "Ação recomendada",
                ),
                linhas=linhas,
                vazio="Nenhum contrato ou documento vence nos próximos 30 dias.",
                rodape=_rodape_ocultos(bloco["ocultos"], "itens"),
                ocultar_no_celular=(0, 1, 5),
                numericas=(3,),
            ),
        ),
    )


def _secao_vencidos(m: Mapping[str, Any]) -> Secao:
    bloco = m["vencidos"]
    linhas = tuple(
        (*_celulas_pendencia(i), Celula(_dias(-int(i["dias"])), NEGATIVO, negrito=True))
        for i in bloco["itens"]
    )
    antigos = int(bloco["contratos_antigos"])
    subtitulo = (
        f"{_plural(bloco['documentos'], 'documento', 'documentos')} e"
        f" {_plural(bloco['contratos'], 'contrato', 'contratos')} vencidos."
    )
    if antigos:
        subtitulo += (
            f" Não listados: {_plural(antigos, 'contrato', 'contratos')}"
            f" {'vencido' if antigos == 1 else 'vencidos'} há mais de"
            f" {bloco['janela_contrato_dias']} dias."
        )
    return Secao(
        numero=2,
        titulo="Contratos e documentos vencidos (atenção crítica)",
        subtitulo=subtitulo,
        cor=VERMELHO,
        tabelas=(
            Tabela(
                cabecalho=(
                    "Tipo",
                    "Grupo",
                    "Identificação / Empresa",
                    "Vencimento",
                    "Status",
                    "Atraso",
                ),
                linhas=linhas,
                vazio="Nenhum contrato ou documento vencido.",
                rodape=_rodape_ocultos(bloco["ocultos"], "itens"),
                ocultar_no_celular=(0, 1),
                numericas=(3, 5),
            ),
        ),
    )


def _empresa(evento: Mapping[str, Any]) -> Celula:
    return Celula(str(evento["empresa"]), nota=str(evento.get("cnpj") or ""))


def _funcionario(evento: Mapping[str, Any]) -> str:
    codigo = evento.get("funcionario")
    return f"Funcionário #{codigo}" if codigo else "-"


def _tabela_esocial(
    eventos: Sequence[Mapping[str, Any]], titulo: str, ultima: str, vazio: str, rodape: str
) -> Tabela:
    linhas = tuple(
        (
            Celula(str(e.get("horario") or "-")),
            Celula(str(e["evento"]), negrito=True),
            Celula(_funcionario(e)),
            _empresa(e),
            Celula(
                str(e.get("recibo") or "Sem recibo"),
                "" if e.get("recibo") else NEGATIVO,
                nota=str(e.get("protocolo") or ""),
                quebra=True,
            ),
        )
        for e in eventos
    )
    return Tabela(
        titulo=titulo,
        cabecalho=("Horário", "Evento", "Colaborador", "Empresa / CNPJ", ultima),
        linhas=linhas,
        vazio=vazio,
        rodape=rodape,
        ocultar_no_celular=(0, 2),
        numericas=(0,),
    )


def _secao_esocial(m: Mapping[str, Any]) -> Secao:
    bloco = m["esocial"]
    ref = _data(bloco["referencia"])
    dia = f"{ref:%d/%m/%Y}" if ref else "-"
    total, com, sem = int(bloco["total"]), int(bloco["com_recibo"]), int(bloco["sem_recibo"])
    por_evento = " · ".join(f"{tipo}: {n}" for tipo, n in bloco["por_evento"].items())
    empresas = _plural(bloco["empresas"], "empresa", "empresas")
    cartoes = (
        Cartao(
            "Total de eventos gerados",
            str(total),
            " · ".join(filter(None, (empresas, por_evento))),
            AZUL,
        ),
        Cartao("Transmitidos (com recibo)", str(com), "recibo de entrega recebido", VERDE),
        Cartao(
            "Sem recibo",
            str(sem),
            "ainda não transmitidos ou rejeitados",
            NEGATIVO if sem else MUDO,
        ),
    )
    tabelas = []
    if bloco["pendentes"]:
        tabelas.append(
            _tabela_esocial(
                bloco["pendentes"],
                "Eventos sem recibo (conferir no SGG)",
                "Situação",
                "",
                _rodape_ocultos(bloco["pendentes_ocultos"], "eventos"),
            )
        )
    tabelas.append(
        _tabela_esocial(
            bloco["eventos"],
            "Eventos transmitidos" if bloco["pendentes"] else "",
            "Recibo de entrega / protocolo",
            f"Nenhum evento do eSocial foi gerado em {dia}.",
            _rodape_ocultos(bloco["eventos_ocultos"], "eventos"),
        )
    )
    return Secao(
        numero=3,
        titulo=f"Envios para o eSocial (referência: {dia})",
        subtitulo=f"Eventos gerados em {dia}, de todas as empresas com o eSocial habilitado.",
        cor=VERDE,
        cartoes=cartoes,
        tabelas=tuple(tabelas),
    )


# --------------------------------------------------------------------------- montagem


def _manchete(m: Mapping[str, Any]) -> str:
    a_vencer, vencidos, esocial = m["a_vencer"], m["vencidos"], m["esocial"]
    ref = _data(esocial["referencia"])
    dia = f"{ref:%d/%m}" if ref else "-"
    docs = _plural(a_vencer["documentos"], "documento", "documentos")
    contratos = _plural(a_vencer["contratos"], "contrato", "contratos")
    eventos = _plural(esocial["total"], "evento", "eventos")
    return (
        f"Vencem nos próximos {a_vencer['janela_dias']} dias: {a_vencer['total']}"
        f" ({docs} e {contratos}). Já vencidos: {vencidos['total']}."
        f" Em {dia}, {eventos} do eSocial"
        f" ({esocial['com_recibo']} com recibo e {esocial['sem_recibo']} sem)."
    )


def montar_apresentacao_sesmt(m: Mapping[str, Any], admin_url: str = "") -> ApresentacaoSesmt:
    hoje = date.fromisoformat(m["hoje"])
    gerado = datetime.fromisoformat(m["gerado_em"])
    manchete = _manchete(m)
    notas = []
    if m.get("empresas_sem_consulta"):
        n = int(m["empresas_sem_consulta"])
        sujeito = "empresa não pôde" if n == 1 else "empresas não puderam"
        notas.append(
            f"Atenção: {n} {sujeito} ser consultada nesta coleta;"
            " os números podem estar incompletos."
        )
    return ApresentacaoSesmt(
        assunto=f"Relatório de Gestão SESMT — {hoje:%d/%m/%Y} ({dia_semana(hoje)})",
        preheader=manchete,
        titulo_data=f"{dia_semana(hoje).capitalize()}, {hoje:%d/%m/%Y}",
        manchete=manchete,
        secoes=(_secao_a_vencer(m), _secao_vencidos(m), _secao_esocial(m)),
        notas=tuple(notas),
        gerado_em=f"{gerado:%d/%m/%Y} às {gerado:%H:%M}",
        versao_regra=str(m.get("versao_regra", "")),
        admin_url=admin_url,
        amostra=bool(m.get("amostra")),
        definicoes=DEFINICOES,
    )
