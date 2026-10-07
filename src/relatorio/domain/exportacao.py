"""Exportação de relatórios por período: regras e o formato neutro da planilha.

Cada relatório exporta duas partes na mesma planilha: as abas do **relatório** (o que foi
calculado e enviado por e-mail, dia a dia) e as abas da **fonte** (os dados que entraram no
cálculo). O formato aqui é neutro (abas, colunas e linhas); a infraestrutura escreve o .xlsx.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from relatorio.domain.entidades import FUSO_BRASILIA

ATENDIMENTO = "atendimento"
FINANCEIRO = "financeiro"
SESMT = "sesmt"
MEDICOS = "medicos"
TIPOS = (ATENDIMENTO, FINANCEIRO, SESMT, MEDICOS)
ROTULOS = {
    ATENDIMENTO: "Atendimento",
    FINANCEIRO: "Financeiro",
    SESMT: "SESMT",
    MEDICOS: "Atendimentos por médico",
}
MAX_DIAS = 31
# A exportação do SESMT relê o SGG empresa por empresa (mais de mil consultas): só fora do
# expediente, quando a cota da API é maior e o coletor do painel não está rodando.
SESMT_INICIO_HORA = 20
SESMT_FIM_HORA = 5

# "duracao" recebe segundos e aparece como h:mm:ss; "percentual" recebe a fração (0,25 = 25%).
Formato = Literal[
    "texto", "inteiro", "decimal", "moeda", "percentual", "data", "datahora", "duracao"
]


EXPORTAR = "exportar"
PROCESSAR = "processar"
ACOES = (EXPORTAR, PROCESSAR)
# Os relatórios financeiro e do SESMT são do dia anterior: o dia de hoje ainda não fechou.
SO_ATE_ONTEM = (FINANCEIRO, SESMT)


def validar_periodo(
    inicio: date, fim: date, hoje: date, tipo: str = "", acao: str = EXPORTAR
) -> None:
    """Lança ``ValueError`` com a mensagem para a tela se o período não pode ser pedido."""
    if fim < inicio:
        raise ValueError("A data final deve ser igual ou posterior à data inicial.")
    if fim > hoje:
        raise ValueError("A data final não pode ser no futuro.")
    if acao == PROCESSAR and tipo in SO_ATE_ONTEM and fim >= hoje:
        raise ValueError("Este relatório é do dia anterior: escolha a data final até ontem.")
    if (fim - inicio).days + 1 > MAX_DIAS:
        raise ValueError(f"O período pode ter no máximo {MAX_DIAS} dias.")


def sesmt_liberado(agora: datetime) -> bool:
    hora = agora.astimezone(FUSO_BRASILIA).hour
    return hora >= SESMT_INICIO_HORA or hora < SESMT_FIM_HORA


def dias(inicio: date, fim: date) -> list[date]:
    return [inicio + timedelta(days=n) for n in range((fim - inicio).days + 1)]


@dataclass(frozen=True, slots=True)
class Coluna:
    nome: str
    formato: Formato = "texto"
    largura: int = 16


Valor = str | int | float | date | datetime | None


@dataclass(slots=True)
class Aba:
    nome: str  # nome curto da aba (até 31 caracteres)
    titulo: str  # primeira linha da aba
    colunas: tuple[Coluna, ...]
    linhas: list[tuple[Valor, ...]] = field(default_factory=list)
    fonte: bool = False  # aba de dados fonte (as do relatório vêm antes)
    nota: str = ""


@dataclass(slots=True)
class Planilha:
    tipo: str
    titulo: str
    inicio: date
    fim: date
    abas: list[Aba] = field(default_factory=list)
    observacoes: list[str] = field(default_factory=list)
