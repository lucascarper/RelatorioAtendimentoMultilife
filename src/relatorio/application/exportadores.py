"""Exportadores por período: o relatório de cada dia e os dados fonte usados no cálculo.

* **Atendimento:** resumos diários e agendas (relatório); agendamentos e mudanças de status
  gravados pelo coletor (fonte). Só lê o banco.
* **Financeiro:** resumos diários (relatório); títulos recebidos, pagos e faturados no
  período, relidos do SGG (fonte).
* **SESMT:** resumos diários (relatório); empresas, contratos, documentos e eventos do
  eSocial relidos do SGG (fonte). A coleta é longa: só das 20h às 5h.
* **Atendimentos por médico:** contagens por dia e médico (relatório); exames clínicos dos
  médicos escolhidos com o nome do trabalhador lido do SGG na hora, nunca gravado (fonte).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any

from relatorio.application.coleta import ResolvedorAgendas
from relatorio.application.configuracao import fim_do_dia, inicio_do_dia
from relatorio.application.exportacao import Exportador, Progresso
from relatorio.application.metricas import ObterMetricasPeriodo
from relatorio.application.modelos import ResumoRegistro
from relatorio.application.ports import (
    ErroIntegracao,
    ExamesGateway,
    FabricaUoW,
    FinanceiroGateway,
    Relogio,
    SggGateway,
)
from relatorio.application.sesmt import ConsolidarSesmt
from relatorio.domain.comparativo import comparar
from relatorio.domain.entidades import FUSO_BRASILIA, Agenda, Snapshot
from relatorio.domain.exames import TIPOS_EXAME
from relatorio.domain.exportacao import (
    ATENDIMENTO,
    FINANCEIRO,
    MEDICOS,
    SESMT,
    Aba,
    Coluna,
    Planilha,
    Valor,
)
from relatorio.domain.financeiro import Titulo
from relatorio.domain.metricas import AgendamentoDoDia
from relatorio.domain.resumo import ResumoDiario

DIAS_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
ENVIO = {"pendente": "Pendente", "enviando": "Enviando", "enviado": "Enviado", "falha": "Falha"}


def _dia(d: date) -> str:
    return DIAS_SEMANA[d.weekday()]


def _num(valor: Any) -> float | int | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, Decimal):
        return float(valor)
    return valor if isinstance(valor, int | float) else None


def _sim(valor: bool) -> str:
    return "Sim" if valor else "Não"


def _faltando(inicio: date, fim: date, presentes: Iterable[date]) -> list[date]:
    tem = set(presentes)
    return [
        inicio + timedelta(days=n)
        for n in range((fim - inicio).days + 1)
        if inicio + timedelta(days=n) not in tem
    ]


def _nota_faltando(faltando: list[date], o_que: str) -> str | None:
    if not faltando:
        return None
    datas = ", ".join(f"{d:%d/%m}" for d in faltando[:15])
    resto = f" e mais {len(faltando) - 15}" if len(faltando) > 15 else ""
    return f"{len(faltando)} dia(s) sem {o_que}: {datas}{resto}."


# --------------------------------------------------------------------------- atendimento


def agendas_das_opcoes(opcoes: Iterable[str]) -> frozenset[int]:
    """Agendas escolhidas no pedido ("a12,a13"); vazio = sem escolha (as do relatório)."""
    return frozenset(int(o[1:]) for o in opcoes if o[:1] == "a" and o[1:].isdigit())


class ExportarAtendimento:
    def __init__(
        self,
        uow: FabricaUoW,
        sgg: SggGateway | None = None,
        metricas: ObterMetricasPeriodo | None = None,
    ) -> None:
        self._uow = uow
        self._sgg = sgg
        self._metricas = metricas

    def _escolha(self, opcoes: frozenset[str]) -> tuple[frozenset[int] | None, bool]:
        """(agendas escolhidas, se os indicadores precisam ser recalculados).

        Sem escolha no pedido: ``(None, False)``. Escolha igual à do próprio relatório: os
        resumos gravados (iguais aos do e-mail) servem, mas a fonte só traz essas agendas."""
        escolhidas = agendas_das_opcoes(opcoes)
        if not escolhidas or self._metricas is None:
            return None, False
        filtro = self._metricas.configuracao().filtro
        with self._uow() as uow:
            padrao = filtro.agendas_marcadas(uow.agendas.listar())
        return escolhidas, escolhidas != padrao

    @staticmethod
    def _filtrar_fonte(
        snapshots: list[Snapshot],
        agendas: Mapping[int, Agenda],
        escolhidas: frozenset[int],
        recalcular: bool,
        metricas: ObterMetricasPeriodo,
    ) -> list[Snapshot]:
        """Só os agendamentos das agendas escolhidas. Com a seleção do relatório, vale o
        mesmo filtro do e-mail (inclusive agendamentos que ele conta sem agenda resolvida)."""
        resolvedor = ResolvedorAgendas(list(agendas.values()))

        def agenda_de(s: Snapshot) -> int | None:
            if s.id_agenda is not None:
                return s.id_agenda
            return resolvedor.resolver(s.agenda_nome, s.id_unidade_atendimento)

        if recalcular:
            return [s for s in snapshots if agenda_de(s) in escolhidas]
        filtro = metricas.configuracao().filtro
        return [
            s
            for s in snapshots
            if filtro.inclui(
                AgendamentoDoDia(
                    s.id_agendamento,
                    agenda_de(s),
                    s.agenda_nome,
                    s.id_unidade_atendimento,
                    s.data_agendamento,
                    s.hora_agendamento,
                    s.situacao_atual,
                    (),
                ),
                agendas,
            )
        ]

    @staticmethod
    def _recalcular(
        metricas: ObterMetricasPeriodo, registro: ResumoRegistro, escolhidas: frozenset[int]
    ) -> Mapping[str, Any]:
        """Os indicadores do dia só com as agendas escolhidas (mesmas regras do e-mail)."""
        alertas = registro.metricas.get("alertas", {})
        resultado = metricas.executar(
            inicio_do_dia(registro.data),
            fim_do_dia(registro.data),
            faltas_confirmadas=alertas.get("faltas_confirmadas_na_agenda", {}).get("ids", []),
            verificacao_faltas_indisponivel=bool(alertas.get("verificacao_faltas_indisponivel")),
            agendas_escolhidas=escolhidas,
        )
        resumo = ResumoDiario(
            data_referencia=registro.data,
            unidades=resultado.unidades,
            gerado_em=registro.gerado_em,
            metricas=resultado.metricas,
            comparativo=comparar(resultado.metricas.kpis, None, registro.data - timedelta(days=7)),
        )
        return resumo.para_json()

    def gerar(
        self, inicio: date, fim: date, progresso: Progresso, opcoes: frozenset[str]
    ) -> Planilha:
        planilha = Planilha(ATENDIMENTO, "Relatório de Atendimentos", inicio, fim)
        escolhidas, recalcular = self._escolha(opcoes)
        progresso(10, "Lendo os resumos diários")
        with self._uow() as uow:
            resumos = uow.resumos.listar_periodo(inicio, fim)
        resumo = Aba(
            "Resumo diário",
            "Indicadores de cada dia, recalculados só com as agendas escolhidas"
            if recalcular
            else "Indicadores de cada dia (os mesmos do e-mail diário)",
            (
                Coluna("Data", "data", 12),
                Coluna("Dia", largura=6),
                Coluna("Agendados", "inteiro", 11),
                Coluna("Atendimentos", "inteiro", 13),
                Coluna("Faltas", "inteiro", 8),
                Coluna("Taxa de faltas", "percentual", 12),
                Coluna("Cancelados", "inteiro", 11),
                Coluna("Sem baixa", "inteiro", 10),
                Coluna("Tempo de Espera - Recepção", "duracao", 16),
                Coluna("Tempo de Atendimento - Recepção", "duracao", 18),
                Coluna("Tempo de Espera - Consultório", "duracao", 17),
                Coluna("Tempo de Consulta", "duracao", 14),
                Coluna("Tempo Médio Total de Permanência", "duracao", 19),
                Coluna("E-mail", largura=10),
            ),
        )
        agendas = Aba(
            "Por agenda",
            "Atendimentos e tempo de atendimento por agenda, em cada dia",
            (
                Coluna("Data", "data", 12),
                Coluna("Agenda", largura=30),
                Coluna("Consultório", largura=20),
                Coluna("Guichê", largura=8),
                Coluna("Agendados", "inteiro", 11),
                Coluna("Atendimentos", "inteiro", 13),
                Coluna("Média", "duracao", 10),
                Coluna("Mediana", "duracao", 10),
                Coluna("Maior", "duracao", 10),
            ),
        )
        for posicao, registro in enumerate(resumos):
            if not recalcular or escolhidas is None or self._metricas is None:
                m: Mapping[str, Any] = registro.metricas
            else:
                progresso(10 + 25 * posicao // max(len(resumos), 1), "Recalculando os indicadores")
                m = self._recalcular(self._metricas, registro, escolhidas)
            k: Mapping[str, Any] = m.get("kpis", {})
            resumo.linhas.append(
                (
                    registro.data,
                    _dia(registro.data),
                    _num(k.get("agendados")),
                    _num(k.get("atendimentos")),
                    _num(k.get("faltas")),
                    _num(k.get("taxa_faltas")),
                    _num(k.get("cancelados")),
                    _num(k.get("sem_baixa")),
                    _num(k.get("espera_recepcao_s")),
                    _num(k.get("tma_guiches_s")),
                    _num(k.get("espera_consultorio_s")),
                    _num(k.get("tma_consultorios_s", k.get("tma_s"))),
                    _num(k.get("permanencia_total_s")),
                    ENVIO.get(str(registro.status_envio), str(registro.status_envio))
                    + (" (e-mail com a seleção original)" if recalcular else ""),
                )
            )
            for a in m.get("agendas", []):
                agendas.linhas.append(
                    (
                        registro.data,
                        a.get("agenda", ""),
                        a.get("consultorio") or "",
                        _sim(bool(a.get("guiche"))),
                        _num(a.get("agendados")),
                        _num(a.get("atendimentos")),
                        _num(a.get("media_s")),
                        _num(a.get("mediana_s")),
                        _num(a.get("maximo_s")),
                    )
                )

        progresso(35, "Lendo os agendamentos do período")
        with self._uow() as uow:
            snapshots = uow.snapshots.listar_por_data(inicio, fim)
            agendas_cadastro = {a.id_agenda: a for a in uow.agendas.listar()}
        if escolhidas is not None and self._metricas is not None:
            snapshots = self._filtrar_fonte(
                snapshots, agendas_cadastro, escolhidas, recalcular, self._metricas
            )
        progresso(55, "Lendo as mudanças de status")
        with self._uow() as uow:
            eventos = uow.eventos.listar_por_agendamentos([s.id_agendamento for s in snapshots])
        tipos, dias_sem_tipo = self._tipos_no_sgg(
            {s.data_agendamento for s in snapshots}, progresso
        )
        fonte_agendamentos = Aba(
            "Fonte - Agendamentos",
            "Agendamentos do período (último estado conhecido no SGG)",
            (
                Coluna("ID agendamento", "inteiro", 14),
                Coluna("Data", "data", 12),
                Coluna("Hora", largura=8),
                Coluna("Agenda", largura=30),
                Coluna("Tipo de exame", largura=22),
                Coluna("Consultório", largura=18),
                Coluna("Guichê", largura=8),
                Coluna("ID agenda", "inteiro", 10),
                Coluna("Unidade", "inteiro", 9),
                Coluna("Situação", largura=15),
                Coluna("Última edição no SGG", "datahora", 18),
            ),
            fonte=True,
            nota="Sem dados de pacientes (LGPD): só o código do agendamento.",
        )
        fonte_eventos = Aba(
            "Fonte - Mudanças de status",
            "Cada mudança de situação observada, usada para medir os tempos",
            (
                Coluna("ID agendamento", "inteiro", 14),
                Coluna("Data", "data", 12),
                Coluna("Agenda", largura=30),
                Coluna("De", largura=15),
                Coluna("Para", largura=15),
                Coluna("Ocorrido em", "datahora", 17),
                Coluna("Observado em", "datahora", 17),
                Coluna("Origem", largura=16),
            ),
            fonte=True,
        )
        for s in sorted(
            snapshots, key=lambda x: (x.data_agendamento, x.hora_agendamento or time.min)
        ):
            agenda = agendas_cadastro.get(s.id_agenda) if s.id_agenda is not None else None
            fonte_agendamentos.linhas.append(
                (
                    s.id_agendamento,
                    s.data_agendamento,
                    f"{s.hora_agendamento:%H:%M}" if s.hora_agendamento else "",
                    s.agenda_nome,
                    tipos.get(s.id_agendamento, ""),
                    (agenda.sala or "") if agenda else "",
                    _sim(bool(agenda and agenda.guiche)),
                    s.id_agenda,
                    s.id_unidade_atendimento,
                    str(s.situacao_atual),
                    s.data_hora_edicao.astimezone(FUSO_BRASILIA) if s.data_hora_edicao else None,
                )
            )
            for e in eventos.get(s.id_agendamento, []):
                fonte_eventos.linhas.append(
                    (
                        s.id_agendamento,
                        s.data_agendamento,
                        s.agenda_nome,
                        str(e.status_anterior) if e.status_anterior else "",
                        str(e.status_novo),
                        e.ocorrido_em.astimezone(FUSO_BRASILIA),
                        e.observado_em.astimezone(FUSO_BRASILIA),
                        str(e.origem),
                    )
                )
        planilha.abas = [resumo, agendas, fonte_agendamentos, fonte_eventos]
        if escolhidas is not None:
            nomes = sorted(
                (
                    f"{agendas_cadastro[i].consultorio} (#{i})"
                    if i in agendas_cadastro
                    else f"Agenda #{i}"
                )
                for i in escolhidas
            )
            planilha.observacoes.append(
                f"Agendas escolhidas ({len(nomes)}): {', '.join(nomes)}. "
                + (
                    "Indicadores recalculados só com elas; podem diferir do e-mail diário."
                    if recalcular
                    else "É a mesma seleção do e-mail diário."
                )
            )
            if recalcular:
                planilha.observacoes.append(
                    "Faltas confirmadas na agenda do SGG só foram conferidas para as agendas do "
                    "e-mail: em agendas fora dele, agendamentos sem baixa não viram falta."
                )
        nota = _nota_faltando(_faltando(inicio, fim, (r.data for r in resumos)), "resumo")
        if nota:
            planilha.observacoes.append(nota + " Dias sem expediente não têm resumo.")
        if dias_sem_tipo:
            dias_txt = ", ".join(f"{d:%d/%m}" for d in dias_sem_tipo)
            planilha.observacoes.append(
                f"O SGG não respondeu nos dias {dias_txt}: o tipo de exame ficou em branco."
            )
        progresso(95, "Organizando as abas")
        return planilha

    def _tipos_no_sgg(
        self, dias_com_agendamento: set[date], progresso: Progresso
    ) -> tuple[dict[int, str], list[date]]:
        """Tipo de exame de cada agendamento, lido do SGG na hora (não é gravado)."""
        if self._sgg is None:
            return {}, []
        tipos: dict[int, str] = {}
        falhas: list[date] = []
        dias_lidos = sorted(dias_com_agendamento)
        for posicao, dia in enumerate(dias_lidos):
            progresso(
                60 + 30 * posicao // max(len(dias_lidos), 1),
                f"Lendo o tipo de exame no SGG ({dia:%d/%m})",
            )
            try:
                tipos.update({a.id_agendamento: a.tipo for a in self._sgg.agendamentos_do_dia(dia)})
            except ErroIntegracao:
                falhas.append(dia)
        return tipos, falhas


# --------------------------------------------------------------------------- financeiro


def _aba_titulos(nome: str, titulo: str, rotulo_pessoa: str, titulos: list[Titulo]) -> Aba:
    aba = Aba(
        nome,
        titulo,
        (
            Coluna("ID", "inteiro", 10),
            Coluna(rotulo_pessoa, largura=36),
            Coluna("Classificação", largura=22),
            Coluna("Emissão", "data", 12),
            Coluna("Vencimento", "data", 12),
            Coluna("Pagamento", "data", 12),
            Coluna("Valor", "moeda", 14),
            Coluna("Valor cobrado", "moeda", 14),
            Coluna("Situação", largura=20),
            Coluna("Cancelada", largura=10),
        ),
        fonte=True,
        nota="Relido do SGG no momento da exportação.",
    )
    for t in sorted(titulos, key=lambda x: (x.pagamento or x.emissao or date.min, x.id)):
        aba.linhas.append(
            (
                t.id,
                t.nome,
                t.classificacao,
                t.emissao,
                t.vencimento,
                t.pagamento,
                _num(t.valor),
                _num(t.valor_cobrado),
                t.situacao,
                _sim(t.cancelada),
            )
        )
    return aba


class ExportarFinanceiro:
    def __init__(self, uow: FabricaUoW, sgg: FinanceiroGateway) -> None:
        self._uow = uow
        self._sgg = sgg

    def gerar(
        self, inicio: date, fim: date, progresso: Progresso, opcoes: frozenset[str]
    ) -> Planilha:
        planilha = Planilha(FINANCEIRO, "Relatório Financeiro", inicio, fim)
        progresso(8, "Lendo os resumos diários")
        with self._uow() as uow:
            resumos = uow.resumos_financeiros.listar_periodo(inicio, fim)
        resumo = Aba(
            "Resumo diário",
            "Caixa e faturamento de cada dia (os mesmos do e-mail diário)",
            (
                Coluna("Data", "data", 12),
                Coluna("Dia", largura=6),
                Coluna("Recebido no dia", "moeda", 15),
                Coluna("Pago no dia", "moeda", 15),
                Coluna("Saldo do dia", "moeda", 15),
                Coluna("Títulos recebidos", "inteiro", 11),
                Coluna("Títulos pagos", "inteiro", 11),
                Coluna("Recebido no mês", "moeda", 16),
                Coluna("Pago no mês", "moeda", 16),
                Coluna("Saldo do mês", "moeda", 16),
                Coluna("Faturado no dia", "moeda", 15),
                Coluna("Faturado no mês", "moeda", 16),
                Coluna("E-mail", largura=10),
            ),
        )
        for registro in resumos:
            caixa: Mapping[str, Any] = registro.metricas.get("caixa", {})
            fat: Mapping[str, Any] = registro.metricas.get("faturamento", {})
            resumo.linhas.append(
                (
                    registro.data,
                    _dia(registro.data),
                    _num(caixa.get("recebido_dia")),
                    _num(caixa.get("pago_dia")),
                    _num(caixa.get("saldo_dia")),
                    _num(caixa.get("titulos_recebidos_dia")),
                    _num(caixa.get("titulos_pagos_dia")),
                    _num(caixa.get("recebido_mes")),
                    _num(caixa.get("pago_mes")),
                    _num(caixa.get("saldo_mes")),
                    _num(fat.get("dia")),
                    _num(fat.get("mes")),
                    ENVIO.get(str(registro.status_envio), str(registro.status_envio)),
                )
            )
        progresso(20, "Relendo no SGG os títulos recebidos")
        recebidos = self._sgg.receber_pagos(inicio, fim)
        progresso(45, "Relendo no SGG os títulos pagos")
        pagos = self._sgg.pagar_pagos(inicio, fim)
        progresso(70, "Relendo no SGG o faturamento emitido")
        emitidos = self._sgg.receber_emitidos(inicio, fim)
        itens = Aba(
            "Fonte - Itens faturados",
            "Serviços de cada conta emitida no período (faturamento simplificado do SGG)",
            (
                Coluna("ID título", "inteiro", 10),
                Coluna("Cliente", largura=36),
                Coluna("Emissão", "data", 12),
                Coluna("Serviço", largura=36),
                Coluna("Qtde", "inteiro", 7),
                Coluna("Valor", "moeda", 14),
            ),
            fonte=True,
        )
        for t in emitidos:
            for item in t.itens:
                itens.linhas.append(
                    (t.id, t.nome, t.emissao, item.servico, item.qtde, _num(item.valor))
                )
        planilha.abas = [
            resumo,
            _aba_titulos(
                "Fonte - Recebimentos", "Contas a receber pagas no período", "Cliente", recebidos
            ),
            _aba_titulos(
                "Fonte - Pagamentos", "Contas a pagar pagas no período", "Fornecedor", pagos
            ),
            _aba_titulos(
                "Fonte - Faturamento", "Contas a receber emitidas no período", "Cliente", emitidos
            ),
            itens,
        ]
        nota = _nota_faltando(_faltando(inicio, fim, (r.data for r in resumos)), "resumo")
        if nota:
            planilha.observacoes.append(nota)
        planilha.observacoes.append(
            "As abas de fonte foram relidas do SGG na exportação: títulos alterados depois do "
            "envio do e-mail aparecem com a situação atual."
        )
        progresso(95, "Organizando as abas")
        return planilha


# --------------------------------------------------------------------------- SESMT


class ExportarSesmt:
    def __init__(self, uow: FabricaUoW, coleta: ConsolidarSesmt, relogio: Relogio) -> None:
        self._uow = uow
        self._coleta = coleta
        self._relogio = relogio

    def gerar(
        self, inicio: date, fim: date, progresso: Progresso, opcoes: frozenset[str]
    ) -> Planilha:
        planilha = Planilha(SESMT, "Relatório de Gestão SESMT", inicio, fim)
        progresso(2, "Lendo os resumos diários")
        with self._uow() as uow:
            resumos = uow.resumos_sesmt.listar_periodo(inicio, fim)
        resumo = Aba(
            "Resumo diário",
            "Pendências e envios ao eSocial de cada dia (os mesmos do e-mail diário)",
            (
                Coluna("Data", "data", 12),
                Coluna("Dia", largura=6),
                Coluna("A vencer (30 dias)", "inteiro", 14),
                Coluna("Vencidos", "inteiro", 10),
                Coluna("Eventos eSocial", "inteiro", 13),
                Coluna("Com recibo", "inteiro", 11),
                Coluna("Sem recibo", "inteiro", 11),
                Coluna("E-mail", largura=10),
            ),
        )
        for registro in resumos:
            m = registro.metricas
            esocial: Mapping[str, Any] = m.get("esocial", {})
            resumo.linhas.append(
                (
                    registro.data,
                    _dia(registro.data),
                    _num(m.get("a_vencer", {}).get("total")),
                    _num(m.get("vencidos", {}).get("total")),
                    _num(esocial.get("total")),
                    _num(esocial.get("com_recibo")),
                    _num(esocial.get("sem_recibo")),
                    ENVIO.get(str(registro.status_envio), str(registro.status_envio)),
                )
            )

        faixas = {
            "empresas": (3, 3),
            "contratos": (6, 6),
            "documentos": (6, 55),
            "esocial": (55, 93),
        }
        rotulos = {
            "empresas": "Relendo as empresas no SGG",
            "contratos": "Relendo os contratos no SGG",
            "documentos": "Relendo programas e laudos, empresa por empresa",
            "esocial": "Relendo os eventos do eSocial, empresa por empresa",
        }

        def andamento(etapa: str, feitas: int, total: int) -> None:
            de, ate = faixas[etapa]
            valor = de + (ate - de) * feitas // max(total, 1)
            texto = rotulos[etapa] + (f" ({feitas} de {total})" if total > 1 else "")
            progresso(valor, texto)

        hoje = self._relogio.agora().astimezone(FUSO_BRASILIA).date()
        dados = self._coleta.carregar(fim, max(hoje, fim + timedelta(days=1)), andamento)
        empresas = dados.empresas

        def nome(id_empresa: int | None) -> str:
            empresa = empresas.get(id_empresa) if id_empresa is not None else None
            return empresa.nome if empresa else (f"Empresa #{id_empresa}" if id_empresa else "")

        aba_empresas = Aba(
            "Fonte - Empresas",
            "Empresas do SGG",
            (
                Coluna("ID", "inteiro", 9),
                Coluna("Empresa", largura=40),
                Coluna("CNPJ", largura=20),
                Coluna("Grupo", largura=22),
                Coluna("eSocial habilitado", largura=12),
            ),
            fonte=True,
        )
        for emp in sorted(empresas.values(), key=lambda x: x.nome.casefold()):
            grupo = dados.nomes_grupos.get(emp.id_grupo, emp.id_grupo)
            aba_empresas.linhas.append(
                (emp.id, emp.nome, emp.cnpj, grupo, _sim(emp.esocial_habilitado))
            )
        aba_contratos = Aba(
            "Fonte - Contratos",
            "Contratos em andamento e vencidos",
            (
                Coluna("ID", "inteiro", 9),
                Coluna("Código", largura=14),
                Coluna("Empresa", largura=40),
                Coluna("Situação", largura=14),
                Coluna("Vencimento", "data", 12),
                Coluna("Contrato corrente", largura=10),
            ),
            fonte=True,
        )
        for c in sorted(dados.contratos, key=lambda x: (x.vencimento or date.max, x.id)):
            aba_contratos.linhas.append(
                (c.id, c.codigo, nome(c.id_cliente), c.situacao, c.vencimento, _sim(c.ultimo))
            )
        aba_documentos = Aba(
            "Fonte - Documentos",
            "Programas e laudos (PGR, PCMSO, LTCAT) das empresas com contrato em andamento",
            (
                Coluna("Empresa", largura=40),
                Coluna("Tipo", largura=9),
                Coluna("Código", largura=10),
                Coluna("Emissão", "data", 12),
                Coluna("Vencimento", "data", 12),
                Coluna("Situação", largura=14),
            ),
            fonte=True,
        )
        for d in sorted(dados.documentos, key=lambda x: (nome(x.id_empresa).casefold(), x.tipo)):
            aba_documentos.linhas.append(
                (nome(d.id_empresa), d.tipo, d.codigo, d.emissao, d.vencimento, d.situacao)
            )
        aba_eventos = Aba(
            "Fonte - Eventos eSocial",
            "Eventos gerados no período, das empresas com o eSocial habilitado",
            (
                Coluna("Código", largura=10),
                Coluna("Evento", largura=9),
                Coluna("Empresa", largura=40),
                Coluna("Funcionário", largura=16),
                Coluna("Data de geração", "data", 12),
                Coluna("Gerado em", "datahora", 17),
                Coluna("Prazo", "data", 12),
                Coluna("Recibo", largura=26),
                Coluna("Protocolo", largura=30),
            ),
            fonte=True,
            nota="Funcionário pelo código do SGG, sem nome nem CPF (LGPD).",
        )
        no_periodo = [
            ev for ev in dados.eventos if ev.data_geracao and inicio <= ev.data_geracao <= fim
        ]
        for ev in sorted(no_periodo, key=lambda x: (x.data_geracao or date.min, x.codigo)):
            aba_eventos.linhas.append(
                (
                    ev.codigo,
                    ev.tipo,
                    nome(ev.id_empresa),
                    f"Funcionário #{ev.id_funcionario}" if ev.id_funcionario else "",
                    ev.data_geracao,
                    ev.gerado_em.astimezone(FUSO_BRASILIA) if ev.gerado_em else None,
                    ev.prazo,
                    ev.recibo,
                    ev.protocolo,
                )
            )
        planilha.abas = [resumo, aba_empresas, aba_contratos, aba_documentos, aba_eventos]
        planilha.observacoes.append(
            "Empresas, contratos e documentos refletem a situação no SGG no momento da "
            "exportação; os eventos do eSocial são os gerados no período."
        )
        if dados.empresas_sem_consulta:
            planilha.observacoes.append(
                f"{dados.empresas_sem_consulta} empresa(s) não responderam no SGG "
                "e ficaram de fora."
            )
        nota = _nota_faltando(_faltando(inicio, fim, (r.data for r in resumos)), "resumo")
        if nota:
            planilha.observacoes.append(nota)
        progresso(95, "Organizando as abas")
        return planilha


# --------------------------------------------------------------------------- médicos


class ExportarMedicos:
    def __init__(self, uow: FabricaUoW, sgg: ExamesGateway) -> None:
        self._uow = uow
        self._sgg = sgg

    def gerar(
        self, inicio: date, fim: date, progresso: Progresso, opcoes: frozenset[str]
    ) -> Planilha:
        planilha = Planilha(MEDICOS, "Atendimentos por médico", inicio, fim)
        progresso(5, "Lendo os exames gravados")
        with self._uow() as uow:
            coletas = {c.data for c in uow.exames.coletas_recentes(400) if inicio <= c.data <= fim}
            exames = [e for d in sorted(coletas) for e in uow.exames.do_dia(d)]
        tipos = list(TIPOS_EXAME) + sorted({e.tipo for e in exames} - set(TIPOS_EXAME))
        por_dia = Aba(
            "Por dia e médico",
            "Exames clínicos por dia, médico e tipo (os mesmos do e-mail diário)",
            (
                Coluna("Data", "data", 12),
                Coluna("Dia", largura=6),
                Coluna("Médico", largura=30),
                Coluna("CRM", largura=13),
                Coluna("Total", "inteiro", 8),
                *(Coluna(t, "inteiro", max(10, len(t) + 2)) for t in tipos),
            ),
        )
        totais = Aba(
            "Totais do período",
            "Exames clínicos de cada médico no período",
            (
                Coluna("Médico", largura=30),
                Coluna("CRM", largura=13),
                Coluna("Total", "inteiro", 8),
                *(Coluna(t, "inteiro", max(10, len(t) + 2)) for t in tipos),
            ),
        )
        contagem: dict[tuple[date, str], Counter[str]] = {}
        nomes_medicos: dict[str, str] = {}
        for e in exames:
            contagem.setdefault((e.data, e.crm), Counter())[e.tipo] += 1
            nomes_medicos[e.crm] = e.medico
        no_periodo: dict[str, Counter[str]] = {}
        for (dia, crm), cont in sorted(
            contagem.items(), key=lambda i: (i[0][0], nomes_medicos[i[0][1]])
        ):
            por_dia.linhas.append(
                (
                    dia,
                    _dia(dia),
                    nomes_medicos[crm],
                    crm,
                    sum(cont.values()),
                    *(cont[t] for t in tipos),
                )
            )
            no_periodo.setdefault(crm, Counter()).update(cont)
        for crm, cont in sorted(no_periodo.items(), key=lambda i: -sum(i[1].values())):
            totais.linhas.append(
                (nomes_medicos[crm], crm, sum(cont.values()), *(cont[t] for t in tipos))
            )

        # Nomes dos trabalhadores: lidos agora, um dia por vez, e nunca gravados (LGPD).
        nomes: dict[int, str] = {}
        sem_nome: list[date] = []
        dias_com_exame = sorted({e.data for e in exames})
        for posicao, dia in enumerate(dias_com_exame, start=1):
            progresso(
                10 + 80 * posicao // max(len(dias_com_exame), 1),
                f"Lendo os nomes no SGG ({dia:%d/%m})",
            )
            try:
                nomes.update({x.id: x.funcionario for x in self._sgg.exames_clinicos(dia)})
            except ErroIntegracao:
                sem_nome.append(dia)
        fonte = Aba(
            "Fonte - Exames clínicos",
            "Exames clínicos dos médicos escolhidos em Configurações",
            (
                Coluna("Data", "data", 12),
                Coluna("Empresa", largura=40),
                Coluna("Funcionário", largura=36),
                Coluna("Médico", largura=30),
                Coluna("CRM", largura=13),
                Coluna("Tipo exame", largura=20),
                Coluna("ID exame", "inteiro", 10),
                Coluna("Código do funcionário", "inteiro", 12),
            ),
            fonte=True,
            nota=(
                "Contém nomes de trabalhadores (dados pessoais de saúde): "
                "uso interno, não encaminhe."
            ),
        )
        for e in sorted(
            exames, key=lambda x: (x.data, x.medico.casefold(), x.empresa.casefold(), x.id)
        ):
            linha: tuple[Valor, ...] = (
                e.data,
                e.empresa,
                nomes.get(e.id) or f"Funcionário #{e.id_funcionario}",
                e.medico,
                e.crm,
                e.tipo,
                e.id,
                e.id_funcionario,
            )
            fonte.linhas.append(linha)
        planilha.abas = [por_dia, totais, fonte]
        planilha.observacoes.append(
            "Só os médicos que estavam escolhidos em Configurações quando cada dia foi processado."
        )
        nota = _nota_faltando(_faltando(inicio, fim, coletas), "processamento dos exames")
        if nota:
            planilha.observacoes.append(nota + " Use Processar uma data e exporte de novo.")
        if sem_nome:
            dias_txt = ", ".join(f"{d:%d/%m}" for d in sem_nome)
            planilha.observacoes.append(
                f"O SGG não respondeu nos dias {dias_txt}: o funcionário aparece pelo código."
            )
        progresso(95, "Organizando as abas")
        return planilha


def montar_exportadores(
    uow: FabricaUoW,
    relogio: Relogio,
    financeiro: FinanceiroGateway | None,
    coleta_sesmt: ConsolidarSesmt | None,
    exames: ExamesGateway | None,
    sgg: SggGateway | None = None,
    metricas: ObterMetricasPeriodo | None = None,
) -> dict[str, Exportador]:
    exportadores: dict[str, Exportador] = {ATENDIMENTO: ExportarAtendimento(uow, sgg, metricas)}
    if financeiro is not None:
        exportadores[FINANCEIRO] = ExportarFinanceiro(uow, financeiro)
    if coleta_sesmt is not None:
        exportadores[SESMT] = ExportarSesmt(uow, coleta_sesmt, relogio)
    if exames is not None:
        exportadores[MEDICOS] = ExportarMedicos(uow, exames)
    return exportadores
