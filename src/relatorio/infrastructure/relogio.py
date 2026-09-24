from __future__ import annotations

from datetime import datetime

from relatorio.domain.entidades import FUSO_BRASILIA


class RelogioSistema:
    """Relógio real, sempre com fuso de Brasília (proibido ``datetime.now()`` sem fuso)."""

    def agora(self) -> datetime:
        return datetime.now(tz=FUSO_BRASILIA)
