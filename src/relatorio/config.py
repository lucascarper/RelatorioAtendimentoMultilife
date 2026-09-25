"""Configuração por ambiente (12-factor), tipada e validada com pydantic-settings.

Segredos (chave do SGG, senha SMTP, hash do admin) só existem em variáveis de ambiente
e ficam em ``SecretStr``: não aparecem em ``repr``, logs nem mensagens de erro.
"""

from __future__ import annotations

from datetime import time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from relatorio.application.configuracao import ConfiguracaoRelatorio, JanelaColeta
from relatorio.domain.turnos import ConfiguracaoTurnos

RAIZ_PROJETO = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    tz: str = "America/Sao_Paulo"

    # Banco
    database_url: str = "postgresql+psycopg://relatorio:relatorio@localhost:5432/relatorio"

    # SGG
    sgg_api_key: SecretStr = SecretStr("")
    sgg_base_url: str = "https://app.sgg.net.br/api/v3/"
    sgg_max_rpm: int = Field(default=40, ge=1, le=60)
    sgg_timeout_s: float = 15.0

    # E-mail (KingHost via SMTP)
    email_backend: Literal["smtp", "arquivo"] = "smtp"
    email_saida_dir: Path = Path("saida_emails")
    smtp_host: str = "mail.kinghost.net"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_ssl: bool | None = None  # None: SSL direto na 465, STARTTLS nas demais
    smtp_timeout_s: float = 30.0
    email_from: str = "relatorios@multilife.com.br"
    email_from_nome: str = "MultiLife · Relatórios"
    email_alerta_tecnico: str = "tecnologia@multilife.com.br"
    # Staging: se preenchido, TODO e-mail vai só para estes endereços (vírgula).
    email_destinatarios_override: str = ""

    # Admin
    admin_user: str = "admin"
    admin_password_hash: SecretStr = SecretStr("")
    secret_key: SecretStr = SecretStr("")
    admin_url: str = ""

    # Coleta e regras (padrões; o admin pode sobrescrever as regras de negócio)
    coletor_habilitado: bool = True
    coleta_inicio: time = time(6, 0)
    coleta_fim: time = time(18, 0)
    # Segundos entre ciclos de coleta (1 requisição por ciclo): divisor de 60, de 5 a 60.
    coleta_intervalo_s: int = 5
    turno_manha_inicio: time = time(6, 0)
    turno_tarde_inicio: time = time(13, 0)
    turno_tarde_fim: time = time(18, 0)
    atipico_min_minutos: int = 1
    atipico_max_minutos: int = 180

    templates_dir: Path = RAIZ_PROJETO / "templates"
    static_dir: Path = RAIZ_PROJETO / "static"

    @field_validator("database_url")
    @classmethod
    def _driver_psycopg(cls, valor: str) -> str:
        """A Railway entrega ``postgresql://``; o SQLAlchemy precisa do driver psycopg 3."""
        for prefixo in ("postgres://", "postgresql://"):
            if valor.startswith(prefixo):
                return "postgresql+psycopg://" + valor.removeprefix(prefixo)
        return valor

    @field_validator("coleta_intervalo_s")
    @classmethod
    def _intervalo_coleta(cls, valor: int) -> int:
        if not 5 <= valor <= 60 or 60 % valor:
            raise ValueError("COLETA_INTERVALO_S deve dividir 60 e ficar entre 5 e 60")
        return valor

    @property
    def smtp_usa_ssl(self) -> bool:
        return self.smtp_ssl if self.smtp_ssl is not None else self.smtp_port == 465

    @property
    def destinatarios_override(self) -> tuple[str, ...]:
        return tuple(e.strip() for e in self.email_destinatarios_override.split(",") if e.strip())

    @property
    def configuracao_padrao(self) -> ConfiguracaoRelatorio:
        return ConfiguracaoRelatorio(
            turnos=ConfiguracaoTurnos(
                manha_inicio=self.turno_manha_inicio,
                tarde_inicio=self.turno_tarde_inicio,
                tarde_fim=self.turno_tarde_fim,
            ),
            atipico_min_minutos=self.atipico_min_minutos,
            atipico_max_minutos=self.atipico_max_minutos,
            email_alerta_tecnico=self.email_alerta_tecnico,
        )

    @property
    def janela_coleta(self) -> JanelaColeta:
        return JanelaColeta(
            inicio=self.coleta_inicio,
            fim=self.coleta_fim,
            intervalo=timedelta(seconds=self.coleta_intervalo_s),
        )

    def pendencias(self, servico: Literal["web", "worker"]) -> list[str]:
        """Variáveis obrigatórias ausentes para o serviço (checadas no arranque)."""
        faltando: list[str] = []
        if servico == "worker":
            if self.coletor_habilitado and not self.sgg_api_key.get_secret_value():
                faltando.append("SGG_API_KEY")
            if self.email_backend == "smtp":
                if not self.smtp_user:
                    faltando.append("SMTP_USER")
                if not self.smtp_password.get_secret_value():
                    faltando.append("SMTP_PASSWORD")
        if servico == "web":
            if not self.admin_password_hash.get_secret_value():
                faltando.append("ADMIN_PASSWORD_HASH")
            if not self.secret_key.get_secret_value():
                faltando.append("SECRET_KEY")
        return faltando


@lru_cache(maxsize=1)
def obter_settings() -> Settings:
    return Settings()
