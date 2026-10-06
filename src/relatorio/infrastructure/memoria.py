"""Repositórios em memória (demonstração, prévia do e-mail e testes — sem banco).

Operam direto sobre ``BancoEmMemoria``: não há rollback, o que basta para os casos de
uso de demonstração. Em produção vale sempre o PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from types import TracebackType
from typing import Any, Self

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
from relatorio.domain.entidades import FUSO_BRASILIA, Agenda, Evento, Snapshot
from relatorio.domain.exames import ExameClinico, Medico


@dataclass
class BancoEmMemoria:
    agendas: dict[int, Agenda] = field(default_factory=dict)
    snapshots: dict[int, Snapshot] = field(default_factory=dict)
    eventos: list[Evento] = field(default_factory=list)
    resumos: dict[date, ResumoRegistro] = field(default_factory=dict)
    destinatarios: dict[int, Destinatario] = field(default_factory=dict)
    resumos_financeiros: dict[date, ResumoRegistro] = field(default_factory=dict)
    destinatarios_financeiro: dict[int, Destinatario] = field(default_factory=dict)
    resumos_sesmt: dict[date, ResumoRegistro] = field(default_factory=dict)
    destinatarios_sesmt: dict[int, Destinatario] = field(default_factory=dict)
    usuarios: dict[int, UsuarioSistema] = field(default_factory=dict)
    medicos: dict[str, Medico] = field(default_factory=dict)
    exames: dict[int, ExameClinico] = field(default_factory=dict)
    coletas_exames: dict[date, ColetaExames] = field(default_factory=dict)
    configuracoes: dict[str, str] = field(default_factory=dict)
    execucoes: dict[int, ExecucaoJob] = field(default_factory=dict)
    cursores: dict[str, datetime] = field(default_factory=dict)
    commits: int = 0


class _Agendas:
    def __init__(self, banco: BancoEmMemoria) -> None:
        self.b = banco

    def listar(self) -> list[Agenda]:
        return sorted(self.b.agendas.values(), key=lambda a: a.id_agenda)

    def sincronizar(self, agendas: Sequence[Agenda]) -> tuple[int, int, int]:
        novas = atualizadas = 0
        recebidas = {a.id_agenda for a in agendas}
        for agenda in agendas:
            existente = self.b.agendas.get(agenda.id_agenda)
            if existente is None:
                novas += 1
                self.b.agendas[agenda.id_agenda] = agenda
            else:
                atualizadas += 1
                self.b.agendas[agenda.id_agenda] = replace(
                    agenda, incluir_relatorio=existente.incluir_relatorio, guiche=existente.guiche
                )
        desativadas = 0
        for id_agenda, agenda in list(self.b.agendas.items()):
            if id_agenda not in recebidas and agenda.ativa:
                self.b.agendas[id_agenda] = replace(agenda, ativa=False)
                desativadas += 1
        return novas, atualizadas, desativadas

    def definir_inclusao(self, id_agenda: int, incluir: bool) -> None:
        self.b.agendas[id_agenda] = replace(self.b.agendas[id_agenda], incluir_relatorio=incluir)

    def definir_guiche(self, id_agenda: int, guiche: bool) -> None:
        self.b.agendas[id_agenda] = replace(self.b.agendas[id_agenda], guiche=guiche)


class _Snapshots:
    def __init__(self, banco: BancoEmMemoria) -> None:
        self.b = banco

    def obter_varios(self, ids: Collection[int]) -> dict[int, Snapshot]:
        return {i: self.b.snapshots[i] for i in ids if i in self.b.snapshots}

    def salvar(self, snapshot: Snapshot) -> None:
        self.b.snapshots[snapshot.id_agendamento] = snapshot

    def listar_por_data(self, inicio: date, fim: date) -> list[Snapshot]:
        return [s for s in self.b.snapshots.values() if inicio <= s.data_agendamento <= fim]

    def apagar_anteriores_a(self, limite: date) -> int:
        antigos = [k for k, s in self.b.snapshots.items() if s.data_agendamento < limite]
        for k in antigos:
            del self.b.snapshots[k]
        return len(antigos)


class _Eventos:
    def __init__(self, banco: BancoEmMemoria) -> None:
        self.b = banco

    def inserir(self, evento: Evento) -> bool:
        chave = (evento.id_agendamento, evento.status_novo, evento.ocorrido_em)
        if any((e.id_agendamento, e.status_novo, e.ocorrido_em) == chave for e in self.b.eventos):
            return False
        self.b.eventos.append(evento)
        return True

    def listar_por_agendamentos(self, ids: Collection[int]) -> dict[int, list[Evento]]:
        resultado: dict[int, list[Evento]] = {}
        for e in sorted(self.b.eventos, key=lambda e: e.ocorrido_em):
            if e.id_agendamento in ids:
                resultado.setdefault(e.id_agendamento, []).append(e)
        return resultado

    def apagar_anteriores_a(self, limite: datetime) -> int:
        antes = len(self.b.eventos)
        self.b.eventos = [e for e in self.b.eventos if e.ocorrido_em >= limite]
        return antes - len(self.b.eventos)


class _Resumos:
    def __init__(self, resumos: dict[date, ResumoRegistro]) -> None:
        self.r = resumos

    def obter(self, dia: date) -> ResumoRegistro | None:
        return self.r.get(dia)

    def salvar_metricas(
        self, dia: date, metricas: Mapping[str, Any], versao_regra: str, gerado_em: datetime
    ) -> None:
        atual = self.r.get(dia)
        self.r[dia] = ResumoRegistro(
            data=dia,
            metricas=dict(metricas),
            versao_regra=versao_regra,
            gerado_em=gerado_em,
            status_envio=atual.status_envio if atual else StatusEnvio.PENDENTE,
            enviado_em=atual.enviado_em if atual else None,
        )

    def reservar_envio(self, dia: date, forcar: bool = False) -> bool:
        atual = self.r.get(dia)
        if atual is None:
            return False
        livre = atual.status_envio in (StatusEnvio.PENDENTE, StatusEnvio.FALHA)
        if not (livre or forcar):
            return False
        self.r[dia] = replace(atual, status_envio=StatusEnvio.ENVIANDO)
        return True

    def marcar_enviado(self, dia: date, quando: datetime) -> None:
        self.r[dia] = replace(self.r[dia], status_envio=StatusEnvio.ENVIADO, enviado_em=quando)

    def marcar_falha_envio(self, dia: date) -> None:
        self.r[dia] = replace(self.r[dia], status_envio=StatusEnvio.FALHA)

    def listar_recentes(self, limite: int = 30) -> list[ResumoRegistro]:
        return sorted(self.r.values(), key=lambda r: r.data, reverse=True)[:limite]

    def apagar_anteriores_a(self, limite: date) -> int:
        antigos = [d for d in self.r if d < limite]
        for d in antigos:
            del self.r[d]
        return len(antigos)


class _Destinatarios:
    def __init__(self, destinatarios: dict[int, Destinatario], aceita_anexo: bool = False) -> None:
        self.d = destinatarios
        self.aceita_anexo = aceita_anexo

    def listar(self, apenas_ativos: bool = False) -> list[Destinatario]:
        return [d for d in self.d.values() if d.ativo or not apenas_ativos]

    def adicionar(self, email: str, nome: str | None) -> Destinatario:
        novo = Destinatario(
            id=len(self.d) + 1,
            email=email,
            nome=nome,
            ativo=True,
            criado_em=datetime.now(tz=FUSO_BRASILIA),
        )
        self.d[novo.id] = novo
        return novo

    def definir_ativo(self, id_destinatario: int, ativo: bool) -> None:
        self.d[id_destinatario] = replace(self.d[id_destinatario], ativo=ativo)
        if not ativo and self.aceita_anexo:
            self.definir_anexo(id_destinatario, False)

    def definir_anexo(self, id_destinatario: int, recebe: bool) -> None:
        if not self.aceita_anexo:  # mesma regra do repositório SQL
            raise ValueError("Só a lista do relatório de atendimentos recebe anexo.")
        self.d[id_destinatario] = replace(self.d[id_destinatario], recebe_anexo=recebe)


class _Usuarios:
    def __init__(self, usuarios: dict[int, UsuarioSistema]) -> None:
        self.u = usuarios

    def listar(self) -> list[UsuarioSistema]:
        return sorted(self.u.values(), key=lambda x: x.nome.lower())

    def obter(self, id_usuario: int) -> UsuarioSistema | None:
        return self.u.get(id_usuario)

    def obter_por_usuario(self, usuario: str) -> UsuarioSistema | None:
        alvo = usuario.strip().lower()
        return next((x for x in self.u.values() if x.usuario == alvo), None)

    def criar(
        self, nome: str, usuario: str, senha_hash: str, permissoes: Sequence[str]
    ) -> UsuarioSistema:
        if self.obter_por_usuario(usuario) is not None:
            raise UsuarioDuplicado(usuario)
        novo = UsuarioSistema(
            id=max(self.u, default=0) + 1,
            nome=nome,
            usuario=usuario.strip().lower(),
            senha_hash=senha_hash,
            permissoes=tuple(permissoes),
            criado_em=datetime.now(tz=FUSO_BRASILIA),
        )
        self.u[novo.id] = novo
        return novo

    def atualizar(
        self,
        id_usuario: int,
        nome: str,
        usuario: str,
        permissoes: Sequence[str],
        senha_hash: str | None = None,
    ) -> None:
        outro = self.obter_por_usuario(usuario)
        if outro is not None and outro.id != id_usuario:
            raise UsuarioDuplicado(usuario)
        atual = self.u[id_usuario]
        self.u[id_usuario] = replace(
            atual,
            nome=nome,
            usuario=usuario.strip().lower(),
            permissoes=tuple(permissoes),
            senha_hash=atual.senha_hash if senha_hash is None else senha_hash,
        )

    def excluir(self, id_usuario: int) -> bool:
        return self.u.pop(id_usuario, None) is not None


class _Medicos:
    def __init__(self, banco: BancoEmMemoria) -> None:
        self.b = banco

    def listar(self) -> list[Medico]:
        return sorted(self.b.medicos.values(), key=lambda m: (not m.selecionado, m.nome.lower()))

    def selecionados(self) -> list[Medico]:
        return sorted(
            (m for m in self.b.medicos.values() if m.selecionado), key=lambda m: m.nome.lower()
        )

    def registrar_vistos(self, medicos: Sequence[Medico], dia: date) -> None:
        for medico in medicos:
            atual = self.b.medicos.get(medico.crm)
            visto = max(dia, atual.visto_em) if atual and atual.visto_em else dia
            self.b.medicos[medico.crm] = Medico(
                medico.crm, medico.nome, bool(atual and atual.selecionado), visto
            )

    def adicionar(self, medico: Medico) -> None:
        atual = self.b.medicos.get(medico.crm)
        self.b.medicos[medico.crm] = replace(atual or medico, nome=medico.nome, selecionado=True)

    def definir_selecionado(self, crm: str, selecionado: bool) -> None:
        if crm in self.b.medicos:
            self.b.medicos[crm] = replace(self.b.medicos[crm], selecionado=selecionado)


class _Exames:
    def __init__(self, banco: BancoEmMemoria) -> None:
        self.b = banco

    def substituir_dia(self, dia: date, exames: Sequence[ExameClinico]) -> None:
        for id_exame in [i for i, e in self.b.exames.items() if e.data == dia]:
            del self.b.exames[id_exame]
        for exame in exames:
            # Como no banco: o nome do trabalhador nunca é guardado.
            self.b.exames.setdefault(exame.id, replace(exame, funcionario=""))

    def do_dia(self, dia: date) -> list[ExameClinico]:
        return sorted((e for e in self.b.exames.values() if e.data == dia), key=lambda e: e.id)

    def nomes_empresas(self, ids: Collection[int]) -> dict[int, str]:
        nomes: dict[int, str] = {}
        for exame in sorted(self.b.exames.values(), key=lambda e: e.data):
            if exame.id_empresa in ids:
                nomes[exame.id_empresa] = exame.empresa
        return nomes

    def registrar_coleta(self, coleta: ColetaExames) -> None:
        self.b.coletas_exames[coleta.data] = coleta

    def coleta(self, dia: date) -> ColetaExames | None:
        return self.b.coletas_exames.get(dia)

    def coletas_recentes(self, limite: int = 14) -> list[ColetaExames]:
        return sorted(self.b.coletas_exames.values(), key=lambda c: c.data, reverse=True)[:limite]

    def apagar_anteriores_a(self, limite: date) -> int:
        antigos = [i for i, e in self.b.exames.items() if e.data < limite]
        for id_exame in antigos:
            del self.b.exames[id_exame]
        for dia in [d for d in self.b.coletas_exames if d < limite]:
            del self.b.coletas_exames[dia]
        return len(antigos)


class _Configuracoes:
    def __init__(self, banco: BancoEmMemoria) -> None:
        self.b = banco

    def obter_todas(self) -> dict[str, str]:
        return dict(self.b.configuracoes)

    def definir(self, chave: str, valor: str) -> None:
        self.b.configuracoes[chave] = valor

    def remover(self, chave: str) -> None:
        self.b.configuracoes.pop(chave, None)


class _Execucoes:
    def __init__(self, banco: BancoEmMemoria) -> None:
        self.b = banco

    def iniciar(self, job: str, inicio: datetime, detalhe: Mapping[str, Any]) -> int:
        novo_id = len(self.b.execucoes) + 1
        self.b.execucoes[novo_id] = ExecucaoJob(
            id=novo_id,
            job=job,
            inicio=inicio,
            fim=None,
            status=StatusJob.EXECUTANDO,
            detalhe=dict(detalhe),
        )
        return novo_id

    def finalizar(
        self, id_execucao: int, fim: datetime, status: StatusJob, detalhe: Mapping[str, Any]
    ) -> None:
        atual = self.b.execucoes[id_execucao]
        self.b.execucoes[id_execucao] = replace(
            atual, fim=fim, status=status, detalhe={**atual.detalhe, **detalhe}
        )

    def _do_job(self, job: str) -> list[ExecucaoJob]:
        return sorted(
            (e for e in self.b.execucoes.values() if e.job == job),
            key=lambda e: (e.inicio, e.id),
            reverse=True,
        )

    def recentes_por_job(self, por_job: int = 7) -> dict[str, list[ExecucaoJob]]:
        jobs = {e.job for e in self.b.execucoes.values()}
        return {job: self._do_job(job)[:por_job] for job in sorted(jobs)}

    def ultima(self, job: str, status: StatusJob | None = None) -> ExecucaoJob | None:
        return next((e for e in self._do_job(job) if status is None or e.status is status), None)

    def inicios_com_sucesso(self, job: str, de: datetime, ate: datetime) -> list[datetime]:
        return sorted(
            e.inicio
            for e in self._do_job(job)
            if e.status is StatusJob.SUCESSO and de <= e.inicio <= ate
        )

    def houve_sucesso(self, job: str, referencia: str) -> bool:
        return any(
            e.status is StatusJob.SUCESSO and e.detalhe.get("referencia") == referencia
            for e in self._do_job(job)
        )

    def falhas_consecutivas(self, job: str) -> int:
        total = 0
        for e in self._do_job(job):
            if e.status is StatusJob.FALHA:
                total += 1
            elif e.status is StatusJob.SUCESSO:
                break
        return total

    def compactar_sucessos(self, job: str, anteriores_a: datetime) -> int:
        vistos: set[datetime] = set()
        removidos = []
        for chave, e in sorted(self.b.execucoes.items(), key=lambda i: (i[1].inicio, i[0])):
            if e.job != job or e.status is not StatusJob.SUCESSO or e.inicio >= anteriores_a:
                continue
            minuto = e.inicio.replace(second=0, microsecond=0)
            if minuto in vistos:
                removidos.append(chave)
            vistos.add(minuto)
        for chave in removidos:
            del self.b.execucoes[chave]
        return len(removidos)

    def apagar_anteriores_a(self, limite: datetime) -> int:
        antigos = [k for k, e in self.b.execucoes.items() if e.inicio < limite]
        for k in antigos:
            del self.b.execucoes[k]
        return len(antigos)


class _Cursores:
    def __init__(self, banco: BancoEmMemoria) -> None:
        self.b = banco

    def obter(self, nome: str) -> datetime | None:
        return self.b.cursores.get(nome)

    def salvar(self, nome: str, valor: datetime) -> None:
        self.b.cursores[nome] = valor


class UoWEmMemoria:
    """Opera direto no banco em memória (sem rollback — suficiente para os casos de uso)."""

    def __init__(self, banco: BancoEmMemoria) -> None:
        self.banco = banco
        self.agendas = _Agendas(banco)
        self.snapshots = _Snapshots(banco)
        self.eventos = _Eventos(banco)
        self.resumos = _Resumos(banco.resumos)
        self.destinatarios = _Destinatarios(banco.destinatarios, aceita_anexo=True)
        self.resumos_financeiros = _Resumos(banco.resumos_financeiros)
        self.destinatarios_financeiro = _Destinatarios(banco.destinatarios_financeiro)
        self.resumos_sesmt = _Resumos(banco.resumos_sesmt)
        self.destinatarios_sesmt = _Destinatarios(banco.destinatarios_sesmt)
        self.usuarios = _Usuarios(banco.usuarios)
        self.medicos = _Medicos(banco)
        self.exames = _Exames(banco)
        self.configuracoes = _Configuracoes(banco)
        self.execucoes = _Execucoes(banco)
        self.cursores = _Cursores(banco)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        erro: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        self.banco.commits += 1
