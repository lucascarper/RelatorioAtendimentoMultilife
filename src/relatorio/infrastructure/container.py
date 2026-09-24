"""Raiz de composição: liga as portas às implementações reais a partir das Settings."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from relatorio.application.montagem import CasosDeUso, montar_casos_de_uso
from relatorio.application.ports import EnviadorEmail, FabricaUoW, UnidadeDeTrabalho
from relatorio.config import Settings
from relatorio.infrastructure.db.repositorios import UnidadeDeTrabalhoSql, criar_fabrica_sessao
from relatorio.infrastructure.email.envio import EnviadorArquivo, EnviadorSmtp
from relatorio.infrastructure.email.renderizador import RenderizadorJinja
from relatorio.infrastructure.jobs import ExecutorJobs
from relatorio.infrastructure.relogio import RelogioSistema
from relatorio.infrastructure.sgg.cliente import ClienteSgg


@dataclass
class Container:
    settings: Settings

    @cached_property
    def engine(self) -> Engine:
        return create_engine(
            self.settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            connect_args={"options": "-c timezone=America/Sao_Paulo"},
        )

    @cached_property
    def fabrica_sessao(self) -> sessionmaker[Session]:
        return criar_fabrica_sessao(self.engine)

    @property
    def uow(self) -> FabricaUoW:
        fabrica = self.fabrica_sessao

        def nova() -> UnidadeDeTrabalho:
            return UnidadeDeTrabalhoSql(fabrica)

        return nova

    @cached_property
    def relogio(self) -> RelogioSistema:
        return RelogioSistema()

    @cached_property
    def sgg(self) -> ClienteSgg:
        s = self.settings
        return ClienteSgg(
            s.sgg_base_url,
            s.sgg_api_key.get_secret_value(),
            max_rpm=s.sgg_max_rpm,
            timeout_s=s.sgg_timeout_s,
        )

    @cached_property
    def email(self) -> EnviadorEmail:
        s = self.settings
        if s.email_backend == "arquivo":
            return EnviadorArquivo(s.email_saida_dir, s.email_from, s.email_from_nome)
        return EnviadorSmtp(
            s.smtp_host,
            s.smtp_port,
            s.smtp_user,
            s.smtp_password.get_secret_value(),
            s.email_from,
            s.email_from_nome,
            usar_ssl=s.smtp_usa_ssl,
            timeout_s=s.smtp_timeout_s,
        )

    @cached_property
    def renderizador(self) -> RenderizadorJinja:
        s = self.settings
        return RenderizadorJinja(s.templates_dir, s.static_dir, s.admin_url)

    @cached_property
    def casos(self) -> CasosDeUso:
        s = self.settings
        return montar_casos_de_uso(
            uow=self.uow,
            relogio=self.relogio,
            sgg=self.sgg,
            email=self.email,
            renderizador=self.renderizador,
            configuracao_padrao=s.configuracao_padrao,
            janela=s.janela_coleta,
            coletor_habilitado=s.coletor_habilitado,
            destinatarios_override=s.destinatarios_override,
        )

    @cached_property
    def executor(self) -> ExecutorJobs:
        return ExecutorJobs(self.engine, self.uow, self.relogio)

    def fechar(self) -> None:
        if "sgg" in self.__dict__:
            self.sgg.fechar()
        if "engine" in self.__dict__:
            self.engine.dispose()
