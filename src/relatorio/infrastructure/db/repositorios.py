"""Repositórios PostgreSQL (implementam as portas da aplicação)."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from types import TracebackType
from typing import Any, Self, cast

from sqlalchemy import CursorResult, Engine, Result, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from relatorio.application.modelos import (
    Destinatario,
    ExecucaoJob,
    ResumoRegistro,
    StatusEnvio,
    StatusJob,
)
from relatorio.domain.entidades import Agenda, Evento, OrigemEvento, Situacao, Snapshot
from relatorio.infrastructure.db.modelos import (
    AgendamentoEventoModel,
    AgendamentoSnapshotModel,
    AgendaModel,
    ConfiguracaoModel,
    CursorColetaModel,
    DestinatarioModel,
    ExecucaoJobModel,
    ResumoDiarioModel,
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


def _resumo(m: ResumoDiarioModel) -> ResumoRegistro:
    return ResumoRegistro(
        data=m.data,
        metricas=dict(m.metricas),
        versao_regra=m.versao_regra,
        gerado_em=m.gerado_em,
        status_envio=StatusEnvio(m.status_envio),
        enviado_em=m.enviado_em,
    )


def _destinatario(m: DestinatarioModel) -> Destinatario:
    return Destinatario(id=m.id, email=m.email, nome=m.nome, ativo=m.ativo, criado_em=m.criado_em)


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
            )
            # incluir_relatorio é decisão do admin: o sync nunca sobrescreve.
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
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def obter(self, dia: date) -> ResumoRegistro | None:
        modelo = self._s.get(ResumoDiarioModel, dia)
        return _resumo(modelo) if modelo else None

    def salvar_metricas(
        self, dia: date, metricas: Mapping[str, Any], versao_regra: str, gerado_em: datetime
    ) -> None:
        comando = insert(ResumoDiarioModel).values(
            data=dia,
            metricas=dict(metricas),
            versao_regra=versao_regra,
            gerado_em=gerado_em,
            status_envio=StatusEnvio.PENDENTE.value,
        )
        self._s.execute(
            comando.on_conflict_do_update(
                index_elements=[ResumoDiarioModel.data],
                set_={
                    "metricas": comando.excluded.metricas,
                    "versao_regra": comando.excluded.versao_regra,
                    "gerado_em": comando.excluded.gerado_em,
                },
            )
        )

    def reservar_envio(self, dia: date, forcar: bool = False) -> bool:
        condicao = ResumoDiarioModel.data == dia
        if not forcar:
            condicao = condicao & ResumoDiarioModel.status_envio.in_(
                [StatusEnvio.PENDENTE.value, StatusEnvio.FALHA.value]
            )
        # UPDATE … WHERE status livre é atômico: dois envios concorrentes não passam os dois.
        linha = self._s.execute(
            update(ResumoDiarioModel)
            .where(condicao)
            .values(status_envio=StatusEnvio.ENVIANDO.value)
            .returning(ResumoDiarioModel.data)
        ).first()
        return linha is not None

    def marcar_enviado(self, dia: date, quando: datetime) -> None:
        self._s.execute(
            update(ResumoDiarioModel)
            .where(ResumoDiarioModel.data == dia)
            .values(status_envio=StatusEnvio.ENVIADO.value, enviado_em=quando)
        )

    def marcar_falha_envio(self, dia: date) -> None:
        self._s.execute(
            update(ResumoDiarioModel)
            .where(ResumoDiarioModel.data == dia)
            .values(status_envio=StatusEnvio.FALHA.value)
        )

    def listar_recentes(self, limite: int = 30) -> list[ResumoRegistro]:
        modelos = self._s.scalars(
            select(ResumoDiarioModel).order_by(ResumoDiarioModel.data.desc()).limit(limite)
        ).all()
        return [_resumo(m) for m in modelos]

    def apagar_anteriores_a(self, limite: date) -> int:
        resultado = self._s.execute(
            delete(ResumoDiarioModel).where(ResumoDiarioModel.data < limite)
        )
        return _afetadas(resultado)


class DestinatarioRepositorioSql:
    def __init__(self, sessao: Session) -> None:
        self._s = sessao

    def listar(self, apenas_ativos: bool = False) -> list[Destinatario]:
        consulta = select(DestinatarioModel).order_by(DestinatarioModel.email)
        if apenas_ativos:
            consulta = consulta.where(DestinatarioModel.ativo.is_(True))
        return [_destinatario(m) for m in self._s.scalars(consulta).all()]

    def adicionar(self, email: str, nome: str | None) -> Destinatario:
        comando = insert(DestinatarioModel).values(email=email.strip().lower(), nome=nome)
        # Recadastrar um e-mail existente o reativa (e atualiza o nome, se informado).
        modelo_id = self._s.execute(
            comando.on_conflict_do_update(
                constraint="uq_destinatario_email",
                set_={
                    "ativo": True,
                    "nome": func.coalesce(comando.excluded.nome, DestinatarioModel.nome),
                },
            ).returning(DestinatarioModel.id)
        ).scalar_one()
        modelo = self._s.get(DestinatarioModel, modelo_id, populate_existing=True)
        if modelo is None:  # pragma: no cover - acabou de ser inserido
            raise LookupError(email)
        return _destinatario(modelo)

    def definir_ativo(self, id_destinatario: int, ativo: bool) -> None:
        self._s.execute(
            update(DestinatarioModel)
            .where(DestinatarioModel.id == id_destinatario)
            .values(ativo=ativo)
        )


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
