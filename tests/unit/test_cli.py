"""Linha de comando (comandos que não precisam de banco)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import bcrypt
import pytest

from relatorio.config import obter_settings
from relatorio.interfaces import cli

VARIAVEIS = {
    "SGG_API_KEY": "chave",
    "SMTP_USER": "relatorios@multilife.com.br",
    "SMTP_PASSWORD": "senha",
    "ADMIN_PASSWORD_HASH": "hash",
    "SECRET_KEY": "segredo",
}


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)  # sem .env local
    for nome in [*VARIAVEIS, "APP_ENV"]:
        monkeypatch.delenv(nome, raising=False)
    obter_settings.cache_clear()
    yield
    obter_settings.cache_clear()


def test_previa_simulada_sem_banco(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    saida = tmp_path / "previa.html"
    assert cli.main(["previa", "--simulado", "--data", "2026-09-23", "--saida", str(saida)]) == 0
    html = saida.read_text(encoding="utf-8")
    assert "Prévia com dados fictícios" in html
    assert "data:image/png;base64," in html
    assert saida.with_suffix(".txt").exists()
    assert "Resumo de Atendimentos — 23/09/2026 (quarta-feira)" in capsys.readouterr().out


def test_verificar_config(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cli.main(["verificar-config"]) == 1
    for nome, valor in VARIAVEIS.items():
        monkeypatch.setenv(nome, valor)
    obter_settings.cache_clear()
    assert cli.main(["verificar-config"]) == 0


def test_gerar_hash_senha(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    respostas = iter(["curta", "senha-bem-longa-1", "outra-senha-longa", "senha-bem-longa-1"] * 2)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt="": next(respostas))
    assert cli.main(["gerar-hash-senha"]) == 1  # curta demais
    assert cli.main(["gerar-hash-senha"]) == 1  # não confere
    respostas = iter(["senha-bem-longa-1", "senha-bem-longa-1"])
    assert cli.main(["gerar-hash-senha"]) == 0
    linha = capsys.readouterr().out.strip().splitlines()[-1]
    assert linha.startswith("ADMIN_PASSWORD_HASH=$2b$12$")
    assert bcrypt.checkpw(b"senha-bem-longa-1", linha.split("=", 1)[1].encode())


def test_demo_recusado_fora_de_desenvolvimento(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    assert cli.main(["demo"]) == 2
    assert "Recusado em staging" in capsys.readouterr().out


def test_data_invalida() -> None:
    with pytest.raises(SystemExit):
        cli.main(["reprocessar", "--data", "23/09/2026"])
