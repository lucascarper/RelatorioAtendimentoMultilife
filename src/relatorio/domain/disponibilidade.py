"""Janelas em que a coleta ficou indisponível (alerta do e-mail).

Um intervalo sem nenhum ciclo de coleta bem-sucedido maior que a tolerância é uma
falha — seja porque a API respondeu com erro, seja porque o worker estava parado.
Ex.: "coleta indisponível das 10:12 às 10:40".
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from itertools import pairwise

from relatorio.domain.metricas import FalhaColeta

TOLERANCIA_PADRAO = timedelta(minutes=5)
INTERVALO_CICLO = timedelta(minutes=1)


def janelas_sem_coleta(
    sucessos: Iterable[datetime],
    inicio: datetime,
    fim: datetime,
    tolerancia: timedelta = TOLERANCIA_PADRAO,
    intervalo: timedelta = INTERVALO_CICLO,
) -> tuple[FalhaColeta, ...]:
    """Lacunas maiores que a tolerância entre ciclos bem-sucedidos.

    A indisponibilidade começa no ciclo seguinte ao último sucesso (o sucesso das 10:11
    cobriu as 10:11; a primeira falta de dados é às 10:12).
    """
    if fim <= inicio:
        return ()
    pontos = sorted(t for t in sucessos if inicio <= t <= fim)
    marcos = [inicio, *pontos, fim]
    falhas = []
    for anterior, seguinte in pairwise(marcos):
        if seguinte - anterior > tolerancia:
            comeco = anterior + intervalo if anterior in pontos else anterior
            falhas.append(FalhaColeta(inicio=comeco, fim=seguinte))
    return tuple(falhas)
