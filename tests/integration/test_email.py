"""Renderização (Jinja2 + premailer) e envio (SMTP/arquivo) dos e-mails."""

from __future__ import annotations

import smtplib
from datetime import date
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from relatorio.application.modelos import ConteudoEmail, ImagemInline
from relatorio.domain.comparativo import comparar
from relatorio.domain.metricas import RegrasMetricas, calcular_metricas
from relatorio.domain.resumo import ResumoDiario
from relatorio.infrastructure.email.apresentacao import (
    duracao,
    montar_apresentacao,
    numero,
    percentual,
)
from relatorio.infrastructure.email.envio import (
    EnviadorArquivo,
    EnviadorSmtp,
    ErroEmail,
    montar_mensagem,
)
from relatorio.infrastructure.email.renderizador import LOGO_CID, RenderizadorJinja
from relatorio.infrastructure.sgg.simulado import gerar_dia
from relatorio.interfaces.demo import gerar_previa, html_para_navegador, volume_do_dia
from tests.fabricas import agenda, atendimento, hora

RAIZ = Path(__file__).resolve().parents[2]
DIA = date(2026, 9, 23)


@pytest.fixture(scope="module")
def previa() -> tuple[dict[str, Any], ConteudoEmail]:
    return gerar_previa(DIA, RAIZ / "templates", RAIZ / "static", "https://admin.exemplo")


@pytest.fixture
def renderizador() -> RenderizadorJinja:
    return RenderizadorJinja(RAIZ / "templates", RAIZ / "static", "https://admin.exemplo")


class TestFormatacao:
    @pytest.mark.parametrize(
        ("segundos", "texto"),
        [(None, "—"), (45, "45 s"), (600, "10 min"), (629, "10 min"), (3900, "1 h 05 min")],
    )
    def test_duracao(self, segundos: float | None, texto: str) -> None:
        assert duracao(segundos) == texto

    def test_numero_e_percentual(self) -> None:
        assert numero(1234) == "1.234"
        assert numero(None) == "—"
        assert percentual(0.0948) == "9,5%"
        assert percentual(None) == "—"


class TestRelatorio:
    def test_estrutura_do_email(self, previa: tuple[dict[str, Any], ConteudoEmail]) -> None:
        metricas, conteudo = previa
        assert conteudo.assunto == "Resumo de Atendimentos — 23/09/2026 (quarta-feira)"
        html = conteudo.html
        assert f'src="cid:{LOGO_CID}"' in html
        for secao in (
            "Por turno",
            "TMA por consultório",
            "Tempo de atendimento por agenda",
            "Comparativo com a semana anterior",
            "Alertas",
            "Regra de cálculo v1.2.0",
            "Prévia com dados fictícios",
            "https://admin.exemplo",
        ):
            assert secao in html, secao
        # premailer: estilos da <style> copiados para os elementos (Outlook/Gmail)
        assert 'class="num"' in html
        assert "font-variant-numeric:tabular-nums" in html.replace(" ", "")
        assert "@media screen and (max-width: 520px)" in html  # media queries mantidas
        assert conteudo.imagens[0].cid == LOGO_CID
        assert conteudo.imagens[0].conteudo.startswith(b"\x89PNG")
        # Tudo do JSON, nada calculado no template
        assert f">{metricas['kpis']['atendimentos']}<" in html
        assert "Coleta indisponível das 10:12 às 10:40 (28 min)" in html

    def test_versao_texto(self, previa: tuple[dict[str, Any], ConteudoEmail]) -> None:
        _, conteudo = previa
        texto = conteudo.texto
        assert texto.startswith("Resumo de Atendimentos — 23/09/2026")
        for trecho in ("INDICADORES DO DIA", "POR TURNO", "TMA POR CONSULTÓRIO", "ALERTAS", "LGPD"):
            assert trecho in texto
        assert "<" not in texto

    def test_sem_movimento(self, renderizador: RenderizadorJinja) -> None:
        metricas = {
            "assunto": "Resumo de Atendimentos — 27/09/2026 (domingo)",
            "data_referencia": "2026-09-27",
            "dia_semana": "domingo",
            "sem_movimento": True,
            "unidades": ["Todas as unidades"],
            "kpis": {},
            "alertas": {},
            "versao_regra": "1.0.0",
            "gerado_em": "2026-09-27T23:00:00-03:00",
        }
        conteudo = renderizador.relatorio(metricas)
        assert "Sem movimento em 27/09/2026" in conteudo.html
        assert "TMA por consultório" not in conteudo.html
        assert "Tudo certo" in conteudo.html

    def test_resumo_de_versao_antiga_nao_quebra(self, renderizador: RenderizadorJinja) -> None:
        minimo = {
            "assunto": "Resumo",
            "data_referencia": "2026-09-23",
            "kpis": {"atendimentos": 5, "agendados": 5, "faltas": 0},
        }
        conteudo = renderizador.relatorio(minimo)
        assert "sem base de comparação" in conteudo.html

    def test_manchete_e_alertas(self, previa: tuple[dict[str, Any], ConteudoEmail]) -> None:
        metricas, _ = previa
        apresentacao = montar_apresentacao(metricas)
        assert apresentacao.manchete.startswith(f"{metricas['kpis']['atendimentos']} atendimentos")
        assert apresentacao.quantidade_alertas >= 3
        assert apresentacao.preheader.endswith("alerta(s) para revisar.")
        assert apresentacao.kpis[1].delta is not None
        assert apresentacao.kpis[1].delta.texto.endswith("p.p.")
        severidades = [a.severidade for a in apresentacao.alertas]
        assert severidades[0] == "critico"  # falha de coleta primeiro

    def test_html_para_navegador(self, previa: tuple[dict[str, Any], ConteudoEmail]) -> None:
        _, conteudo = previa
        html = html_para_navegador(conteudo)
        assert "cid:" not in html
        assert "data:image/png;base64," in html


def test_simulador_dia_sem_expediente_e_dia_fraco() -> None:
    domingo, sabado = date(2026, 9, 27), date(2026, 9, 26)
    assert volume_do_dia(domingo, 120, ultimo=True) == 0
    assert gerar_dia(domingo, volume=0) == []
    assert volume_do_dia(sabado, 120, ultimo=True) == 48
    assert len(gerar_dia(sabado, volume=10)) == 10


def test_alerta(renderizador: RenderizadorJinja) -> None:
    conteudo = renderizador.alerta("Coleta falhando", "Falhou 10 vezes.", {"Último erro": "S000"})
    assert conteudo.assunto == "[Alerta] Relatório de Atendimentos — Coleta falhando"
    assert "Falhou 10 vezes." in conteudo.html
    assert "Último erro: S000" in conteudo.texto


CONTEUDO = ConteudoEmail(
    assunto="Resumo",
    html="<p>Olá</p><img src='cid:logo'>",
    texto="Olá",
    imagens=(ImagemInline("logo", b"\x89PNG-fake"),),
)


def test_mensagem_multipart_com_logo_inline() -> None:
    mensagem = montar_mensagem(
        "relatorios@multilife.com.br", "MultiLife", ["a@x.com", "b@x.com"], CONTEUDO
    )
    assert mensagem["To"] == "a@x.com, b@x.com"
    assert "MultiLife" in mensagem["From"]
    assert mensagem["Message-ID"].endswith("@multilife.com.br>")
    reaberta = message_from_bytes(bytes(mensagem))
    tipos = [p.get_content_type() for p in reaberta.walk()]
    assert tipos == [
        "multipart/alternative",
        "text/plain",
        "multipart/related",
        "text/html",
        "image/png",
    ]
    imagem = next(p for p in reaberta.walk() if p.get_content_type() == "image/png")
    assert imagem["Content-ID"] == "<logo>"


class SmtpFalso:
    instancias: list[SmtpFalso] = []  # noqa: RUF012

    def __init__(self, host: str, porta: int, **kwargs: Any) -> None:
        self.host, self.porta, self.kwargs = host, porta, kwargs
        self.chamadas: list[str] = []
        self.enviadas: list[EmailMessage] = []
        SmtpFalso.instancias.append(self)

    def __enter__(self) -> SmtpFalso:
        return self

    def __exit__(self, *args: object) -> None:
        self.chamadas.append("quit")

    def ehlo(self) -> None:
        self.chamadas.append("ehlo")

    def starttls(self, context: object) -> None:
        self.chamadas.append("starttls")

    def login(self, usuario: str, senha: str) -> None:
        if senha == "errada":
            raise smtplib.SMTPAuthenticationError(535, b"auth failed")
        self.chamadas.append(f"login:{usuario}")

    def send_message(self, mensagem: EmailMessage) -> None:
        self.enviadas.append(mensagem)


@pytest.fixture
def smtp_falso(monkeypatch: pytest.MonkeyPatch) -> type[SmtpFalso]:
    SmtpFalso.instancias = []
    monkeypatch.setattr(smtplib, "SMTP", SmtpFalso)
    monkeypatch.setattr(smtplib, "SMTP_SSL", SmtpFalso)
    return SmtpFalso


def enviador(porta: int, ssl: bool, senha: str = "segredo") -> EnviadorSmtp:
    return EnviadorSmtp(
        "mail.kinghost.net",
        porta,
        "relatorios@multilife.com.br",
        senha,
        "relatorios@multilife.com.br",
        "MultiLife",
        usar_ssl=ssl,
    )


def test_smtp_starttls(smtp_falso: type[SmtpFalso]) -> None:
    enviador(587, ssl=False).enviar(["a@x.com"], CONTEUDO)
    [smtp] = smtp_falso.instancias
    assert (smtp.host, smtp.porta) == ("mail.kinghost.net", 587)
    assert smtp.chamadas == [
        "ehlo",
        "starttls",
        "ehlo",
        "login:relatorios@multilife.com.br",
        "quit",
    ]
    assert smtp.enviadas[0]["Subject"] == "Resumo"


def test_smtp_ssl_direto(smtp_falso: type[SmtpFalso]) -> None:
    enviador(465, ssl=True).enviar(["a@x.com"], CONTEUDO)
    [smtp] = smtp_falso.instancias
    assert "context" in smtp.kwargs
    assert "starttls" not in smtp.chamadas


def test_smtp_erro_vira_erro_de_integracao_sem_senha(smtp_falso: type[SmtpFalso]) -> None:
    with pytest.raises(ErroEmail) as erro:
        enviador(587, ssl=False, senha="errada").enviar(["a@x.com"], CONTEUDO)
    assert "mail.kinghost.net:587" in str(erro.value)
    assert "errada" not in str(erro.value)


def test_enviador_arquivo(tmp_path: Path) -> None:
    EnviadorArquivo(tmp_path, "relatorios@multilife.com.br", "MultiLife").enviar(
        ["a@x.com"], CONTEUDO
    )
    assert len(list(tmp_path.glob("*.eml"))) == 1
    [html] = tmp_path.glob("*.html")
    assert html.read_text(encoding="utf-8") == CONTEUDO.html


class TestTurnosEGuiches:
    """Blocos por turno (consultórios e agendas) e guichês separados na análise por turno."""

    def resumo(self, guiche: bool) -> dict[str, Any]:
        agendas = {
            10: agenda(10, "Clínico", sala="Sala 01"),
            50: agenda(50, "Recepção", sala="Guichê 1", guiche=guiche),
        }
        m = calcular_metricas(
            [
                atendimento(1, hora_agendada="08:00", chamada="08:00", fim="08:15"),
                atendimento(
                    2, hora_agendada="14:00", chegada="13:50", chamada="14:00", fim="14:30"
                ),
                atendimento(
                    3,
                    id_agenda=50,
                    hora_agendada="07:30",
                    chegada="07:25",
                    chamada="07:30",
                    fim="07:35",
                ),
            ],
            agendas,
            RegrasMetricas(),
            hora("00:00"),
            hora("23:59:59"),
        )
        return ResumoDiario(
            DIA, ("Todas as unidades",), hora("23:00"), m, comparar(m.kpis, None, DIA)
        ).para_json()

    def test_guiches_separados_so_quando_ha_guiche(self) -> None:
        assert [g["titulo"] for g in montar_apresentacao(self.resumo(False)).grupos_turno] == [""]
        a = montar_apresentacao(self.resumo(True))
        assert [g["titulo"] for g in a.grupos_turno] == ["Consultórios e demais agendas", "Guichês"]

    def test_blocos_por_turno_com_escala_comum(self, renderizador: RenderizadorJinja) -> None:
        a = montar_apresentacao(self.resumo(True))
        manha, tarde = a.consultorios_turnos
        assert (manha["rotulo"], tarde["rotulo"]) == ("Manhã", "Tarde")
        assert [(c["consultorio"], c["tma"]) for c in manha["linhas"]] == [
            ("Sala 01", "15 min"),
            ("Guichê 1", "5 min"),
        ]
        assert [(c["consultorio"], c["tma"], c["barra"]) for c in tarde["linhas"]] == [
            ("Sala 01", "30 min", 100)
        ]
        assert manha["linhas"][0]["barra"] == 50  # mesma escala nos dois turnos
        assert [b["rotulo"] for b in a.agendas_turnos] == ["Manhã", "Tarde"]
        conteudo = renderizador.relatorio(self.resumo(True))
        assert "Guichês" in conteudo.html and "(guichê)" in conteudo.html
        assert "TMA POR CONSULTÓRIO — TARDE" in conteudo.texto

    def test_resumo_antigo_usa_celulas_de_turno(self) -> None:
        antigo = self.resumo(False)
        for chave in ("por_turno_grupos", "consultorios_por_turno", "agendas_por_turno"):
            del antigo[chave]
        a = montar_apresentacao(antigo)
        assert [len(b["linhas"]) for b in a.consultorios_turnos] == [2, 1]
        assert [b["rotulo"] for b in a.agendas_turnos] == ["Dia"]


class TestConsultoriosERecepcao:
    """Com guichês, KPIs, manchete e comparativo separam consultórios e recepção."""

    def test_cartoes_em_dois_grupos(self) -> None:
        a = montar_apresentacao(TestTurnosEGuiches().resumo(True))
        assert [g["titulo"] for g in a.grupos_kpis] == ["Consultórios", "Recepção (guichês)"]
        consultorios, recepcao = (g["cartoes"] for g in a.grupos_kpis)
        assert [c.rotulo for c in consultorios] == [
            "Atendimentos nos consultórios",
            "Espera no consultório",
            "TMA dos consultórios",
            "Faltas",
        ]
        assert [(c.rotulo, c.valor) for c in recepcao] == [
            ("Atendimentos nos guichês", "1"),
            ("Espera na recepção", "5 min"),
            ("TMA dos guichês", "5 min"),
        ]
        assert a.manchete.startswith("2 atendimentos nos consultórios e 1 nos guichês")
        assert "Espera média de 5 min na recepção e 10 min no consultório." in a.manchete

    def test_sem_guiche_fica_como_antes(self) -> None:
        a = montar_apresentacao(TestTurnosEGuiches().resumo(False))
        assert [g["titulo"] for g in a.grupos_kpis] == [""]
        assert a.grupos_kpis[0]["cartoes"][0].rotulo == "Atendimentos realizados"
        # Indicadores de guichê sem dado nos dois dias não entram no comparativo.
        assert all("guich" not in linha["rotulo"] for linha in a.comparativo)

    def test_email_com_grupos(self, renderizador: RenderizadorJinja) -> None:
        conteudo = renderizador.relatorio(TestTurnosEGuiches().resumo(True))
        assert "Recepção (guichês)" in conteudo.html
        assert "Espera na recepção" in conteudo.html
        assert "RECEPÇÃO (GUICHÊS)" in conteudo.texto
