"""Limitador de requisições por minuto (RNF01: orçamento de ``SGG_MAX_RPM``, padrão 40).

A API do SGG aceita 60 req/min entre 05:00 e 20:00. O orçamento fica abaixo disso para
sobrar folga à reconciliação e a retentativas.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

JANELA_SEGUNDOS = 60.0


class LimitadorTaxa:
    def __init__(
        self,
        max_por_minuto: int,
        relogio: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_por_minuto < 1:
            raise ValueError("max_por_minuto deve ser ≥ 1")
        self._max = max_por_minuto
        self._relogio = relogio
        self._dormir = dormir
        self._janela: deque[float] = deque()
        self._trava = threading.Lock()

    def aguardar(self) -> None:
        """Bloqueia até haver vaga na janela deslizante de 60 s."""
        with self._trava:
            while True:
                agora = self._relogio()
                while self._janela and agora - self._janela[0] >= JANELA_SEGUNDOS:
                    self._janela.popleft()
                if len(self._janela) < self._max:
                    self._janela.append(agora)
                    return
                self._dormir(JANELA_SEGUNDOS - (agora - self._janela[0]))
