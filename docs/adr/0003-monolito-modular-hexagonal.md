# ADR 0003 — Monólito modular com arquitetura hexagonal

- **Status:** aceita
- **Data:** 2026-09-24

## Contexto

O escopo é pequeno (1 web + 1 worker + 1 PostgreSQL no plano Hobby/Pro da Railway), mas as regras de cálculo precisam ser confiáveis, versionadas e 100% testáveis.

## Decisão

Um único pacote Python, em camadas:

| Camada | Pode importar | Não importa |
| --- | --- | --- |
| `domain` | só a biblioteca padrão | httpx, sqlalchemy, fastapi |
| `application` | `domain` + portas (`Protocol`) | implementações concretas |
| `infrastructure` | bibliotecas externas | `interfaces` |
| `interfaces` | tudo, via `Container` | — |

A composição fica em dois pontos: `application/montagem.py` (casos de uso a partir das portas) e `infrastructure/container.py` (implementações reais). Os testes e a demonstração montam os mesmos casos de uso com adaptadores em memória e um SGG simulado.

## Consequências

- Cálculo testado sem rede nem banco (cobertura do domínio acima de 99%).
- O mesmo caminho de código atende produção, prévia, demonstração e teste ponta a ponta.
- Trocar o provedor de e-mail ou o cliente HTTP não mexe em nenhum caso de uso.
