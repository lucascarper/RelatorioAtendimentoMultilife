"""Repositórios PostgreSQL (implementam as portas da aplicação)."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from types import TracebackType
from typing import Any, Self, cast

from sqlalchemy import CursorResult, Engine, Result, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from relatorio.application.modelos import (
    ColetaExames,
    Destinatario,
    ExecucaoJob,
    ResumoRegistro,
    StatusEnvio,
    StatusJob,
    UsuarioSistema,
)
from relatorio.application.ports import UsuarioDuplicado
from relatorio.domain.entidades import Agenda, Evento, OrigemEvento, Situacao, Snapshot
from relatorio.domain.exames import ExameClinico, Medico
from relatorio.infrastructure.db.modelos import (
    AgendamentoEventoModel,
    AgendamentoSnapshotModel,
    AgendaModel,
    ColetaExamesModel,
    ConfiguracaoModel,
    CursorColetaModel,
    DestinatarioFinanceiroModel,
    DestinatarioModel,
    DestinatarioSesmtModel,
    ExameClinicoModel,
    ExecucaoJobModel,
    MedicoRelatorioModel,
    ResumoDiarioModel,
    ResumoFinanceiroModel,
    ResumoSesmtModel,
    UsuarioModel,
)

# Os relatórios (atendimentos, financeiro e SESMT) têm tabelas de resumo e de destinatários
# com as mesmas colunas: os repositórios recebem o modelo.
ModeloResumo = type[ResumoDiarioModel] | type[ResumoFinanceiroModel] | type[ResumoSesmtModel]
ModeloDestinatario = (
    type[DestinatarioModel] | type[DestinatarioFinanceiroModel] | type[DestinatarioSesmtModel]
)

# ------------------------------------------------------------------ conversões


def _afetadas(resultado: Result[Any]) -> int:
    """Quantidade de linhas afetadas por um UPDATE/DELETE."""
    return int(cast(CursorResult[Any], resultado).rowcount or 0)


def _agenda(m: AgendaModel) -> Agenda:
    return Agenda(
        id_agenda=m.id_agenda,
        nome=m.nome,
        sala=m.sala,
        id_unidade_atendimento=m.id_unidade_atendimento,
        unidade_atendimento=m.unidade_atendimento,
        ativa=m.ativa,
        incluir_relatorio=m.incluir_relatorio,
        guiche=m.guiche,
    )


def _snapshot(m: AgendamentoSnapshotModel) -> Snapshot:
    return Snapshot(
        id_agendamento=m.id_agendamento,
        id_agenda=m.id_agenda,
        agenda_nome=m.agenda_nome,
        id_unidade_atendimento=m.id_unidade_atendimento,
        data_agendamento=m.data_agendamento,
        hora_agendamento=m.hora_agendamento,
        situacao_atual=Situacao(m.situacao_atual),
        data_hora_edicao=m.data_hora_edicao,
        atualizado_em=m.atualizado_em,
    )


def _evento(m: AgendamentoEventoModel) -> Evento:
    return Evento(
        id_agendamento=m.id_agendamento,
        status_anterior=Situacao(m.status_anterior) if m.status_anterior else None,
        status_novo=Situacao(m.status_novo),
        ocorrido_em=m.ocorrido_em,
        observado_em=m.observado_em,
        origem=OrigemEvento(m.origem),
    )


def _resumo(m: Any) -> ResumoRegistro:  # ResumoDiarioModel, …FinanceiroModel ou …SesmtModel
    return ResumoRegistro(
        data=m.data,
        metricas=dict(m.metricas),
        versao_regra=m.versao_regra,
        gerado_em=m.gerado_em,
        status_envio=StatusEnvio(m.status_envio),
        enviado_em=m.enviado_em,
    )


def _usuario(m: UsuarioModel) -> UsuarioSistema:
    return UsuarioSistema(
        id=m.id,
        nome=m.nome,
        usuario=m.usuario,
        senha_hash=m.senha_hash,
        permissoes=tuple(m.permissoes or ()),
        criado_em=m.criado_em,
    )


def _destinatario(m: Any) -> Destinatario:  # DestinatarioModel ou …FinanceiroModel
    return Destinatario(
        id=m.id,
        email=m.email,
        nome=m.nome,
        ativo=m.ativo,
        criado_em=m.criado_em,
        recebe_anexo=bool(getattr(m, "recebe_anexo", False)),  # só a lista de atendimentos tem
    )


def _execucao(m: ExecucaoJobModel) -> ExecucaoJob:
    return ExecucaoJob(
        id=m.id,
        job=m.job,
        inicio=m.inicio,
        fim=m.fim,
        status=StatusJob(m.status),
        detalhe=dict(m.detalhe or {}),
    )


# ------------------------------------------------------------------ repositórios


class AgendaRepositorioSql:
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def listar(self) -> list[Agenda]:
        modelos = self._s.scalars(select(AgendaModel).order_by(AgendaModel.nome)).all()
        return [_agenda(m) for m in modelos]

    def sincronizar(self, agendas: Sequence[Agenda]) -> tuple[int, int, int]:
        existentes = set(self._s.scalars(select(AgendaModel.id_agenda)).all())
        for agenda in agendas:
            comando = insert(AgendaModel).values(
                id_agenda=agenda.id_agenda,
                nome=agenda.nome,
                sala=agenda.sala,
                id_unidade_atendimento=agenda.id_unidade_atendimento,
                unidade_atendimento=agenda.unidade_atendimento,
                ativa=agenda.ativa,
                incluir_relatorio=agenda.incluir_relatorio,
                guiche=agenda.guiche,
            )
            # incluir_relatorio e guiche são decisões do admin: o sync nunca sobrescreve.
            self._s.execute(
                comando.on_conflict_do_update(
                    index_elements=[AgendaModel.id_agenda],
                    set_={
                        "nome": comando.excluded.nome,
                        "sala": comando.excluded.sala,
                        "id_unidade_atendimento": comando.excluded.id_unidade_atendimento,
                        "unidade_atendimento": comando.excluded.unidade_atendimento,
                        "ativa": comando.excluded.ativa,
                        "atualizado_em": func.now(),
                    },
                )
            )
        recebidas = {a.id_agenda for a in agendas}
        desativadas = _afetadas(
            self._s.execute(
                update(AgendaModel)
                .where(AgendaModel.id_agenda.not_in(recebidas), AgendaModel.ativa.is_(True))
                .values(ativa=False, atualizado_em=func.now())
            )
        )
        novas = len(recebidas - existentes)
        return novas, len(recebidas) - novas, desativadas

    def definir_inclusao(self, id_agenda: int, incluir: bool) -> None:
        self._s.execute(
            update(AgendaModel)
            .where(AgendaModel.id_agenda == id_agenda)
            .values(incluir_relatorio=incluir)
        )

    def definir_guiche(self, id_agenda: int, guiche: bool) -> None:
        self._s.execute(
            update(AgendaModel).where(AgendaModel.id_agenda == id_agenda).values(guiche=guiche)
        )


class SnapshotRepositorioSql:
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def obter_varios(self, ids: Collection[int]) -> dict[int, Snapshot]:
        if not ids:
            return {}
        modelos = self._s.scalars(
            select(AgendamentoSnapshotModel).where(
                AgendamentoSnapshotModel.id_agendamento.in_(list(ids))
            )
        ).all()
        return {m.id_agendamento: _snapshot(m) for m in modelos}

    def salvar(self, snapshot: Snapshot) -> None:
        valores = {
            "id_agendamento": snapshot.id_agendamento,
            "id_agenda": snapshot.id_agenda,
            "agenda_nome": snapshot.agenda_nome,
            "id_unidade_atendimento": snapshot.id_unidade_atendimento,
            "data_agendamento": snapshot.data_agendamento,
            "hora_agendamento": snapshot.hora_agendamento,
            "situacao_atual": snapshot.situacao_atual.value,
            "data_hora_edicao": snapshot.data_hora_edicao,
            "atualizado_em": snapshot.atualizado_em,
        }
        comando = insert(AgendamentoSnapshotModel).values(**valores)
        self._s.execute(
            comando.on_conflict_do_update(
                index_elements=[AgendamentoSnapshotModel.id_agendamento],
                set_={k: v for k, v in valores.items() if k != "id_agendamento"},
            )
        )

    def listar_por_data(self, inicio: date, fim: date) -> list[Snapshot]:
        modelos = self._s.scalars(
            select(AgendamentoSnapshotModel)
            .where(AgendamentoSnapshotModel.data_agendamento.between(inicio, fim))
            .order_by(AgendamentoSnapshotModel.id_agendamento)
        ).all()
        return [_snapshot(m) for m in modelos]

    def apagar_anteriores_a(self, limite: date) -> int:
        resultado = self._s.execute(
            delete(AgendamentoSnapshotModel).where(
                AgendamentoSnapshotModel.data_agendamento < limite
            )
        )
        return _afetadas(resultado)


class EventoRepositorioSql:
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def inserir(self, evento: Evento) -> bool:
        comando = (
            insert(AgendamentoEventoModel)
            .values(
                id_agendamento=evento.id_agendamento,
                status_anterior=evento.status_anterior.value if evento.status_anterior else None,
                status_novo=evento.status_novo.value,
                ocorrido_em=evento.ocorrido_em,
                observado_em=evento.observado_em,
                origem=evento.origem.value,
            )
            .on_conflict_do_nothing(constraint="uq_agendamento_evento_transicao")
            .returning(AgendamentoEventoModel.id)
        )
        return self._s.execute(comando).scalar_one_or_none() is not None

    def listar_por_agendamentos(self, ids: Collection[int]) -> dict[int, list[Evento]]:
        resultado: dict[int, list[Evento]] = {}
        lista = list(ids)
        for inicio in range(0, len(lista), 5000):
            modelos = self._s.scalars(
                select(AgendamentoEventoModel)
                .where(AgendamentoEventoModel.id_agendamento.in_(lista[inicio : inicio + 5000]))
                .order_by(AgendamentoEventoModel.ocorrido_em, AgendamentoEventoModel.id)
            ).all()
            for m in modelos:
                resultado.setdefault(m.id_agendamento, []).append(_evento(m))
        return resultado

    def apagar_anteriores_a(self, limite: datetime) -> int:
        resultado = self._s.execute(
            delete(AgendamentoEventoModel).where(AgendamentoEventoModel.ocorrido_em < limite)
        )
        return _afetadas(resultado)


class ResumoRepositorioSql:
    def __init__(self, sessao: Session, modelo: ModeloResumo = ResumoDiarioModel) -> None:
        self._s = sessao
        self._m = modelo

    def obter(self, dia: date) -> ResumoRegistro | None:
        modelo = self._s.get(self._m, dia)
        return _resumo(modelo) if modelo else None

    def salvar_metricas(
        self, dia: date, metricas: Mapping[str, Any], versao_regra: str, gerado_em: datetime
    ) -> None:
        comando = insert(self._m).values(
            data=dia,
            metricas=dict(metricas),
            versao_regra=versao_regra,
            gerado_em=gerado_em,
            status_envio=StatusEnvio.PENDENTE.value,
        )
        self._s.execute(
            comando.on_conflict_do_update(
                index_elements=[self._m.data],
                set_={
                    "metricas": comando.excluded.metricas,
                    "versao_regra": comando.excluded.versao_regra,
                    "gerado_em": comando.excluded.gerado_em,
                },
            )
        )

    def reservar_envio(self, dia: date, forcar: bool = False) -> bool:
        condicao = self._m.data == dia
        if not forcar:
            condicao = condicao & self._m.status_envio.in_(
                [StatusEnvio.PENDENTE.value, StatusEnvio.FALHA.value]
            )
        # UPDATE … WHERE status livre é atômico: dois envios concorrentes não passam os dois.
        linha = self._s.execute(
            update(self._m)
            .where(condicao)
            .values(status_envio=StatusEnvio.ENVIANDO.value)
            .returning(self._m.data)
        ).first()
        return linha is not None

    def marcar_enviado(self, dia: date, quando: datetime) -> None:
        self._s.execute(
            update(self._m)
            .where(self._m.data == dia)
            .values(status_envio=StatusEnvio.ENVIADO.value, enviado_em=quando)
        )

    def marcar_falha_envio(self, dia: date) -> None:
        self._s.execute(
            update(self._m).where(self._m.data == dia).values(status_envio=StatusEnvio.FALHA.value)
        )

    def listar_recentes(self, limite: int = 30) -> list[ResumoRegistro]:
        modelos = self._s.scalars(select(self._m).order_by(self._m.data.desc()).limit(limite)).all()
        return [_resumo(m) for m in modelos]

    def apagar_anteriores_a(self, limite: date) -> int:
        resultado = self._s.execute(delete(self._m).where(self._m.data < limite))
        return _afetadas(resultado)


class DestinatarioRepositorioSql:
    def __init__(
        self,
        sessao: Session,
        modelo: ModeloDestinatario = DestinatarioModel,
        restricao_email: str = "uq_destinatario_email",
    ) -> None:
        self._s = sessao
        self._m = modelo
        self._restricao = restricao_email
        # Só a lista do relatório de atendimentos tem a planilha nominal (LGPD).
        self.aceita_anexo = modelo is DestinatarioModel

    def listar(self, apenas_ativos: bool = False) -> list[Destinatario]:
        consulta = select(self._m).order_by(self._m.email)
        if apenas_ativos:
            consulta = consulta.where(self._m.ativo.is_(True))
        return [_destinatario(m) for m in self._s.scalars(consulta).all()]

    def adicionar(self, email: str, nome: str | None) -> Destinatario:
        comando = insert(self._m).values(email=email.strip().lower(), nome=nome)
        # Recadastrar um e-mail existente o reativa (e atualiza o nome, se informado).
        modelo_id = self._s.execute(
            comando.on_conflict_do_update(
                constraint=self._restricao,
                set_={
                    "ativo": True,
                    "nome": func.coalesce(comando.excluded.nome, self._m.nome),
                },
            ).returning(self._m.id)
        ).scalar_one()
        modelo = self._s.get(self._m, modelo_id, populate_existing=True)
        if modelo is None:  # pragma: no cover - acabou de ser inserido
            raise LookupError(email)
        return _destinatario(modelo)

    def definir_ativo(self, id_destinatario: int, ativo: bool) -> None:
        self._s.execute(update(self._m).where(self._m.id == id_destinatario).values(ativo=ativo))
        if not ativo and self.aceita_anexo:
            # Desativar revoga a planilha: reativar exige autorizar de novo.
            self.definir_anexo(id_destinatario, False)

    def definir_anexo(self, id_destinatario: int, recebe: bool) -> None:
        if not self.aceita_anexo:
            raise ValueError("Só a lista do relatório de atendimentos recebe anexo.")
        self._s.execute(
            update(DestinatarioModel)
            .where(DestinatarioModel.id == id_destinatario)
            .values(recebe_anexo=recebe)
        )


class UsuarioRepositorioSql:
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def listar(self) -> list[UsuarioSistema]:
        modelos = self._s.scalars(
            select(UsuarioModel).order_by(func.lower(UsuarioModel.nome))
        ).all()
        return [_usuario(m) for m in modelos]

    def obter(self, id_usuario: int) -> UsuarioSistema | None:
        modelo = self._s.get(UsuarioModel, id_usuario)
        return _usuario(modelo) if modelo else None

    def obter_por_usuario(self, usuario: str) -> UsuarioSistema | None:
        modelo = self._s.scalars(
            select(UsuarioModel).where(UsuarioModel.usuario == usuario.strip().lower())
        ).first()
        return _usuario(modelo) if modelo else None

    def criar(
        self, nome: str, usuario: str, senha_hash: str, permissoes: Sequence[str]
    ) -> UsuarioSistema:
        modelo = UsuarioModel(
            nome=nome,
            usuario=usuario.strip().lower(),
            senha_hash=senha_hash,
            permissoes=list(permissoes),
        )
        try:
            with self._s.begin_nested():  # savepoint: a falha não invalida a sessão
                self._s.add(modelo)
                self._s.flush()
        except IntegrityError as erro:
            raise UsuarioDuplicado(usuario) from erro
        self._s.refresh(modelo)
        return _usuario(modelo)

    def atualizar(
        self,
        id_usuario: int,
        nome: str,
        usuario: str,
        permissoes: Sequence[str],
        senha_hash: str | None = None,
    ) -> None:
        modelo = self._s.get(UsuarioModel, id_usuario)
        if modelo is None:
            raise LookupError(id_usuario)
        try:
            with self._s.begin_nested():
                modelo.nome = nome
                modelo.usuario = usuario.strip().lower()
                modelo.permissoes = list(permissoes)
                if senha_hash is not None:
                    modelo.senha_hash = senha_hash
                self._s.flush()
        except IntegrityError as erro:
            raise UsuarioDuplicado(usuario) from erro

    def excluir(self, id_usuario: int) -> bool:
        resultado = self._s.execute(delete(UsuarioModel).where(UsuarioModel.id == id_usuario))
        return _afetadas(resultado) > 0


def _medico(m: MedicoRelatorioModel) -> Medico:
    return Medico(crm=m.crm, nome=m.nome, selecionado=m.selecionado, visto_em=m.visto_em)


def _exame(m: ExameClinicoModel) -> ExameClinico:
    return ExameClinico(
        id=m.id,
        data=m.data,
        id_empresa=m.id_empresa,
        id_funcionario=m.id_funcionario,
        crm=m.crm,
        medico=m.medico,
        tipo=m.tipo,
        empresa=m.empresa,
    )


def _coleta(m: ColetaExamesModel) -> ColetaExames:
    return ColetaExames(
        data=m.data,
        processado_em=m.processado_em,
        clinicos=m.clinicos,
        selecionados=m.selecionados,
        medicos=tuple(m.medicos),
    )


class MedicoRepositorioSql:
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def listar(self) -> list[Medico]:
        consulta = select(MedicoRelatorioModel).order_by(
            MedicoRelatorioModel.selecionado.desc(), func.lower(MedicoRelatorioModel.nome)
        )
        return [_medico(m) for m in self._s.scalars(consulta).all()]

    def selecionados(self) -> list[Medico]:
        consulta = (
            select(MedicoRelatorioModel)
            .where(MedicoRelatorioModel.selecionado.is_(True))
            .order_by(func.lower(MedicoRelatorioModel.nome))
        )
        return [_medico(m) for m in self._s.scalars(consulta).all()]

    def registrar_vistos(self, medicos: Sequence[Medico], dia: date) -> None:
        for medico in medicos:
            comando = insert(MedicoRelatorioModel).values(
                crm=medico.crm, nome=medico.nome, visto_em=dia
            )
            self._s.execute(
                comando.on_conflict_do_update(
                    index_elements=[MedicoRelatorioModel.crm],
                    set_={
                        "nome": comando.excluded.nome,
                        "visto_em": func.greatest(
                            func.coalesce(MedicoRelatorioModel.visto_em, comando.excluded.visto_em),
                            comando.excluded.visto_em,
                        ),
                    },
                )
            )

    def adicionar(self, medico: Medico) -> None:
        comando = insert(MedicoRelatorioModel).values(
            crm=medico.crm, nome=medico.nome, selecionado=True
        )
        self._s.execute(
            comando.on_conflict_do_update(
                index_elements=[MedicoRelatorioModel.crm],
                set_={"nome": comando.excluded.nome, "selecionado": True},
            )
        )

    def definir_selecionado(self, crm: str, selecionado: bool) -> None:
        self._s.execute(
            update(MedicoRelatorioModel)
            .where(MedicoRelatorioModel.crm == crm)
            .values(selecionado=selecionado)
        )


class ExameRepositorioSql:
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def substituir_dia(self, dia: date, exames: Sequence[ExameClinico]) -> None:
        self._s.execute(delete(ExameClinicoModel).where(ExameClinicoModel.data == dia))
        if exames:
            self._s.execute(
                insert(ExameClinicoModel)
                .values(
                    [
                        {
                            "id": e.id,
                            "data": e.data,
                            "id_empresa": e.id_empresa,
                            "empresa": e.empresa[:200],
                            "id_funcionario": e.id_funcionario,
                            "crm": e.crm,
                            "medico": e.medico[:200],
                            "tipo": e.tipo[:60],
                        }
                        for e in exames
                    ]
                )
                # O SGG pode trocar a data de um exame já gravado em outro dia.
                .on_conflict_do_nothing(index_elements=[ExameClinicoModel.id])
            )

    def do_dia(self, dia: date) -> list[ExameClinico]:
        consulta = (
            select(ExameClinicoModel)
            .where(ExameClinicoModel.data == dia)
            .order_by(ExameClinicoModel.id)
        )
        return [_exame(m) for m in self._s.scalars(consulta).all()]

    def nomes_empresas(self, ids: Collection[int]) -> dict[int, str]:
        if not ids:
            return {}
        consulta = (
            select(ExameClinicoModel.id_empresa, ExameClinicoModel.empresa)
            .where(ExameClinicoModel.id_empresa.in_(list(ids)))
            .distinct(ExameClinicoModel.id_empresa)
            .order_by(ExameClinicoModel.id_empresa, ExameClinicoModel.data.desc())
        )
        return {id_empresa: nome for id_empresa, nome in self._s.execute(consulta).all()}

    def registrar_coleta(self, coleta: ColetaExames) -> None:
        valores = {
            "processado_em": coleta.processado_em,
            "clinicos": coleta.clinicos,
            "selecionados": coleta.selecionados,
            "medicos": list(coleta.medicos),
        }
        comando = insert(ColetaExamesModel).values(data=coleta.data, **valores)
        self._s.execute(
            comando.on_conflict_do_update(index_elements=[ColetaExamesModel.data], set_=valores)
        )

    def coleta(self, dia: date) -> ColetaExames | None:
        modelo = self._s.get(ColetaExamesModel, dia, populate_existing=True)
        return _coleta(modelo) if modelo else None

    def coletas_recentes(self, limite: int = 14) -> list[ColetaExames]:
        consulta = select(ColetaExamesModel).order_by(ColetaExamesModel.data.desc()).limit(limite)
        return [_coleta(m) for m in self._s.scalars(consulta).all()]

    def apagar_anteriores_a(self, limite: date) -> int:
        resultado = self._s.execute(
            delete(ExameClinicoModel).where(ExameClinicoModel.data < limite)
        )
        self._s.execute(delete(ColetaExamesModel).where(ColetaExamesModel.data < limite))
        return _afetadas(resultado)


class ConfiguracaoRepositorioSql:
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def obter_todas(self) -> dict[str, str]:
        return {m.chave: m.valor for m in self._s.scalars(select(ConfiguracaoModel)).all()}

    def definir(self, chave: str, valor: str) -> None:
        comando = insert(ConfiguracaoModel).values(chave=chave, valor=valor)
        self._s.execute(
            comando.on_conflict_do_update(
                index_elements=[ConfiguracaoModel.chave],
                set_={"valor": comando.excluded.valor, "atualizado_em": func.now()},
            )
        )

    def remover(self, chave: str) -> None:
        self._s.execute(delete(ConfiguracaoModel).where(ConfiguracaoModel.chave == chave))


class ExecucaoJobRepositorioSql:
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def iniciar(self, job: str, inicio: datetime, detalhe: Mapping[str, Any]) -> int:
        modelo = ExecucaoJobModel(
            job=job, inicio=inicio, status=StatusJob.EXECUTANDO.value, detalhe=dict(detalhe)
        )
        self._s.add(modelo)
        self._s.flush()
        return modelo.id

    def finalizar(
        self, id_execucao: int, fim: datetime, status: StatusJob, detalhe: Mapping[str, Any]
    ) -> None:
        modelo = self._s.get(ExecucaoJobModel, id_execucao)
        if modelo is None:
            return
        modelo.fim = fim
        modelo.status = status.value
        modelo.detalhe = {**(modelo.detalhe or {}), **detalhe}

    def recentes_por_job(self, por_job: int = 7) -> dict[str, list[ExecucaoJob]]:
        ordem = (
            func.row_number()
            .over(
                partition_by=ExecucaoJobModel.job,
                order_by=(ExecucaoJobModel.inicio.desc(), ExecucaoJobModel.id.desc()),
            )
            .label("ordem")
        )
        sub = select(ExecucaoJobModel.id, ordem).subquery()
        modelos = self._s.scalars(
            select(ExecucaoJobModel)
            .join(sub, sub.c.id == ExecucaoJobModel.id)
            .where(sub.c.ordem <= por_job)
            .order_by(
                ExecucaoJobModel.job, ExecucaoJobModel.inicio.desc(), ExecucaoJobModel.id.desc()
            )
        ).all()
        resultado: dict[str, list[ExecucaoJob]] = {}
        for m in modelos:
            resultado.setdefault(m.job, []).append(_execucao(m))
        return resultado

    def ultima(self, job: str, status: StatusJob | None = None) -> ExecucaoJob | None:
        consulta = select(ExecucaoJobModel).where(ExecucaoJobModel.job == job)
        if status is not None:
            consulta = consulta.where(ExecucaoJobModel.status == status.value)
        modelo = self._s.scalars(
            consulta.order_by(ExecucaoJobModel.inicio.desc(), ExecucaoJobModel.id.desc()).limit(1)
        ).first()
        return _execucao(modelo) if modelo else None

    def inicios_com_sucesso(self, job: str, de: datetime, ate: datetime) -> list[datetime]:
        return list(
            self._s.scalars(
                select(ExecucaoJobModel.inicio)
                .where(
                    ExecucaoJobModel.job == job,
                    ExecucaoJobModel.status == StatusJob.SUCESSO.value,
                    ExecucaoJobModel.inicio.between(de, ate),
                )
                .order_by(ExecucaoJobModel.inicio)
            ).all()
        )

    def houve_sucesso(self, job: str, referencia: str) -> bool:
        consulta = select(func.count()).where(
            ExecucaoJobModel.job == job,
            ExecucaoJobModel.status == StatusJob.SUCESSO.value,
            ExecucaoJobModel.detalhe["referencia"].astext == referencia,
        )
        return bool(self._s.scalar(consulta))

    def falhas_consecutivas(self, job: str) -> int:
        status = self._s.scalars(
            select(ExecucaoJobModel.status)
            .where(
                ExecucaoJobModel.job == job,
                ExecucaoJobModel.status.in_([StatusJob.SUCESSO.value, StatusJob.FALHA.value]),
            )
            .order_by(ExecucaoJobModel.inicio.desc(), ExecucaoJobModel.id.desc())
            .limit(200)
        ).all()
        total = 0
        for valor in status:
            if valor != StatusJob.FALHA.value:
                break
            total += 1
        return total

    def compactar_sucessos(self, job: str, anteriores_a: datetime) -> int:
        filtro = (
            ExecucaoJobModel.job == job,
            ExecucaoJobModel.status == StatusJob.SUCESSO.value,
            ExecucaoJobModel.inicio < anteriores_a,
        )
        primeiros_do_minuto = (
            select(func.min(ExecucaoJobModel.id))
            .where(*filtro)
            .group_by(func.date_trunc("minute", ExecucaoJobModel.inicio))
        )
        resultado = self._s.execute(
            delete(ExecucaoJobModel)
            .where(*filtro, ExecucaoJobModel.id.not_in(primeiros_do_minuto))
            .execution_options(synchronize_session=False)
        )
        return _afetadas(resultado)

    def apagar_anteriores_a(self, limite: datetime) -> int:
        resultado = self._s.execute(
            delete(ExecucaoJobModel).where(ExecucaoJobModel.inicio < limite)
        )
        return _afetadas(resultado)


class CursorRepositorioSql:
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def obter(self, nome: str) -> datetime | None:
        modelo = self._s.get(CursorColetaModel, nome)
        return modelo.valor if modelo else None

    def salvar(self, nome: str, valor: datetime) -> None:
        comando = insert(CursorColetaModel).values(nome=nome, valor=valor)
        self._s.execute(
            comando.on_conflict_do_update(
                index_elements=[CursorColetaModel.nome],
                set_={"valor": comando.excluded.valor, "atualizado_em": func.now()},
            )
        )


# ------------------------------------------------------------------ unidade de trabalho


class UnidadeDeTrabalhoSql:
    def __init__(self, fabrica_sessao: sessionmaker[Session]) -> None:
        self._fabrica = fabrica_sessao
        self._sessao: Session | None = None

    def __enter__(self) -> Self:
        sessao = self._fabrica()
        self._sessao = sessao
        self.agendas = AgendaRepositorioSql(sessao)
        self.snapshots = SnapshotRepositorioSql(sessao)
        self.eventos = EventoRepositorioSql(sessao)
        self.resumos = ResumoRepositorioSql(sessao)
        self.destinatarios = DestinatarioRepositorioSql(sessao)
        self.resumos_financeiros = ResumoRepositorioSql(sessao, ResumoFinanceiroModel)
        self.destinatarios_financeiro = DestinatarioRepositorioSql(
            sessao, DestinatarioFinanceiroModel, "uq_destinatario_financeiro_email"
        )
        self.resumos_sesmt = ResumoRepositorioSql(sessao, ResumoSesmtModel)
        self.destinatarios_sesmt = DestinatarioRepositorioSql(
            sessao, DestinatarioSesmtModel, "uq_destinatario_sesmt_email"
        )
        self.usuarios = UsuarioRepositorioSql(sessao)
        self.medicos = MedicoRepositorioSql(sessao)
        self.exames = ExameRepositorioSql(sessao)
        self.configuracoes = ConfiguracaoRepositorioSql(sessao)
        self.execucoes = ExecucaoJobRepositorioSql(sessao)
        self.cursores = CursorRepositorioSql(sessao)
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        erro: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._sessao is not None:
            self._sessao.rollback()  # sem efeito se já houve commit
            self._sessao.close()
            self._sessao = None

    def commit(self) -> None:
        if self._sessao is None:
            raise RuntimeError("Unidade de trabalho fora de contexto")
        self._sessao.commit()


def criar_fabrica_sessao(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
