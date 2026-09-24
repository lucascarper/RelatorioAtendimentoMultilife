"""Linha de comando: ``relatorio <comando>`` ou ``python -m relatorio.interfaces.cli``.

Exemplos:
    relatorio reprocessar --data 2026-09-23 --enviar
    relatorio enviar --data 2026-09-23 --forcar
    relatorio previa --data 2026-09-23 --saida previa.html
    relatorio previa --simulado --saida exemplo.html
    relatorio demo --dias 14
    relatorio spike-sgg --data 2026-09-23
    relatorio gerar-hash-senha
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from relatorio.config import Settings, obter_settings
from relatorio.domain.entidades import FUSO_BRASILIA
from relatorio.infrastructure.container import Container
from relatorio.infrastructure.logs import configurar_logs
from relatorio.interfaces.demo import (
    gerar_previa,
    html_para_navegador,
    montar_ambiente,
    simular_dias,
)
from relatorio.interfaces.spike import executar_spike
from relatorio.interfaces.web.seguranca import gerar_hash_senha


def _data(texto: str) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError as erro:
        raise argparse.ArgumentTypeError("use o formato AAAA-MM-DD") from erro


def _imprimir(dados: Any) -> None:
    print(json.dumps(dados, ensure_ascii=False, indent=2, default=str))


def _container(settings: Settings) -> Container:
    return Container(settings)


def _ontem() -> date:
    return datetime.now(tz=FUSO_BRASILIA).date() - timedelta(days=1)


def cmd_reprocessar(args: argparse.Namespace, settings: Settings) -> int:
    container = _container(settings)
    _imprimir(
        container.casos.reprocessar.executar(
            args.data, reconciliar=args.reconciliar, enviar=args.enviar
        )
    )
    return 0


def cmd_consolidar(args: argparse.Namespace, settings: Settings) -> int:
    _imprimir(_container(settings).casos.consolidar.executar(args.data))
    return 0


def cmd_enviar(args: argparse.Namespace, settings: Settings) -> int:
    _imprimir(_container(settings).casos.enviar.executar(args.data, forcar=args.forcar))
    return 0


def cmd_coletar(_args: argparse.Namespace, settings: Settings) -> int:
    _imprimir(_container(settings).casos.coletar.executar())
    return 0


def cmd_reconciliar(args: argparse.Namespace, settings: Settings) -> int:
    _imprimir(_container(settings).casos.reconciliar.executar(args.data))
    return 0


def cmd_sincronizar(_args: argparse.Namespace, settings: Settings) -> int:
    _imprimir(_container(settings).casos.sincronizar.executar())
    return 0


def cmd_previa(args: argparse.Namespace, settings: Settings) -> int:
    if args.simulado:
        _, conteudo = gerar_previa(
            args.data, settings.templates_dir, settings.static_dir, settings.admin_url
        )
    else:
        container = _container(settings)
        with container.uow() as uow:
            registro = uow.resumos.obter(args.data)
        if registro is None:
            print(f"Não há resumo de {args.data:%d/%m/%Y}. Rode `relatorio consolidar`.")
            return 1
        conteudo = container.renderizador.relatorio(registro.metricas)
    args.saida.write_text(html_para_navegador(conteudo), encoding="utf-8")
    args.saida.with_suffix(".txt").write_text(conteudo.texto, encoding="utf-8")
    print(f"{conteudo.assunto}\nPrévia gravada em {args.saida} (e versão texto .txt).")
    return 0


def cmd_demo(args: argparse.Namespace, settings: Settings) -> int:
    """Popula o banco local com dias simulados (dados fictícios) para testar o admin."""
    if settings.app_env != "development" and not args.forcar:
        # IDs fictícios poderiam colidir com IDs reais do SGG num banco de homologação.
        print(f"Recusado em {settings.app_env}: use só em banco local (ou --forcar).")
        return 2
    container = _container(settings)
    fim = args.ate or _ontem()
    dias = [fim - timedelta(days=n) for n in reversed(range(args.dias))]
    ambiente = montar_ambiente(dias, container.uow, container.renderizador, engine=container.engine)
    simular_dias(dias, ambiente.casos, ambiente.executor, ambiente.relogio)
    print(f"{len(dias)} dia(s) simulado(s): {dias[0]:%d/%m/%Y} a {dias[-1]:%d/%m/%Y}.")
    return 0


def cmd_gerar_hash(_args: argparse.Namespace, _settings: Settings) -> int:
    senha = getpass.getpass("Nova senha do admin: ")
    if len(senha) < 12:
        print("Use pelo menos 12 caracteres.")
        return 1
    if senha != getpass.getpass("Repita a senha: "):
        print("As senhas não conferem.")
        return 1
    print("Defina na Railway:\nADMIN_PASSWORD_HASH=" + gerar_hash_senha(senha))
    return 0


def cmd_spike(args: argparse.Namespace, settings: Settings) -> int:
    container = _container(settings)
    agora = container.relogio.agora()
    _imprimir(executar_spike(container.sgg, args.data, agora))
    return 0


def cmd_verificar(_args: argparse.Namespace, settings: Settings) -> int:
    resultado = {"web": settings.pendencias("web"), "worker": settings.pendencias("worker")}
    _imprimir({"ambiente": settings.app_env, "variaveis_faltando": resultado})
    return 1 if any(resultado.values()) else 0


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relatorio", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("reprocessar", help="recalcula (e opcionalmente reenvia) uma data")
    p.add_argument("--data", type=_data, required=True)
    p.add_argument("--reconciliar", action="store_true", help="relê o dia no SGG antes")
    p.add_argument("--enviar", action="store_true", help="reenvia o e-mail (forçado)")
    p.set_defaults(funcao=cmd_reprocessar)

    p = sub.add_parser("consolidar", help="consolida uma data em resumo_diario")
    p.add_argument("--data", type=_data, default=None)
    p.set_defaults(funcao=cmd_consolidar)

    p = sub.add_parser("enviar", help="envia o e-mail de uma data")
    p.add_argument("--data", type=_data, default=None)
    p.add_argument("--forcar", action="store_true", help="envia mesmo se já enviado")
    p.set_defaults(funcao=cmd_enviar)

    p = sub.add_parser("coletar", help="roda um ciclo de coleta agora")
    p.set_defaults(funcao=cmd_coletar)

    p = sub.add_parser("reconciliar", help="varredura completa de um dia no SGG")
    p.add_argument("--data", type=_data, default=None)
    p.set_defaults(funcao=cmd_reconciliar)

    p = sub.add_parser("sincronizar-agendas", help="atualiza o cadastro de agendas")
    p.set_defaults(funcao=cmd_sincronizar)

    p = sub.add_parser("previa", help="grava o HTML do e-mail para abrir no navegador")
    p.add_argument("--data", type=_data, default=None)
    p.add_argument("--saida", type=Path, default=Path("previa-email.html"))
    p.add_argument("--simulado", action="store_true", help="usa um dia fictício (sem banco)")
    p.set_defaults(funcao=cmd_previa)

    p = sub.add_parser("demo", help="popula o banco com dias simulados (dados fictícios)")
    p.add_argument("--dias", type=int, default=8)
    p.add_argument("--ate", type=_data, default=None)
    p.add_argument("--forcar", action="store_true", help="permite fora de development")
    p.set_defaults(funcao=cmd_demo)

    p = sub.add_parser("gerar-hash-senha", help="gera o ADMIN_PASSWORD_HASH (bcrypt)")
    p.set_defaults(funcao=cmd_gerar_hash)

    p = sub.add_parser("spike-sgg", help="valida a API real do SGG (só leitura, agregados)")
    p.add_argument("--data", type=_data, default=None)
    p.set_defaults(funcao=cmd_spike)

    p = sub.add_parser("verificar-config", help="lista variáveis de ambiente faltando")
    p.set_defaults(funcao=cmd_verificar)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = criar_parser().parse_args(argv)
    if getattr(args, "data", "sem") is None:
        args.data = _ontem()
    settings = obter_settings()
    configurar_logs("WARNING" if args.comando == "demo" else settings.log_level)
    codigo: int = args.funcao(args, settings)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
