# syntax=docker/dockerfile:1.7
# Uma imagem para os dois serviços (web e worker); muda só o comando de início.
# Sem RUN --mount=type=cache: a Railway exige um id com o ID do serviço, e os dois
# serviços usam este mesmo arquivo. O cache de camadas do Docker já cobre as dependências.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# ---------------------------------------------------------------- dependências
FROM base AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------- execução
FROM base AS runtime
RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/saida_emails && chown app:app /app/saida_emails
COPY --from=build /app/.venv /app/.venv
COPY src ./src
COPY templates ./templates
COPY static ./static
COPY migrations ./migrations
COPY alembic.ini pyproject.toml ./
ENV PATH="/app/.venv/bin:$PATH" \
    TEMPLATES_DIR=/app/templates \
    STATIC_DIR=/app/static \
    EMAIL_SAIDA_DIR=/app/saida_emails
USER app
EXPOSE 8000

# Padrão: serviço web. O worker usa: python -m relatorio.infrastructure.scheduler
CMD ["sh", "-c", "exec uvicorn relatorio.interfaces.web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
