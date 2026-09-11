FROM python:3.12-slim

# Pin uv to the version used to generate uv.lock (see local `uv --version`).
COPY --from=ghcr.io/astral-sh/uv:0.7.9 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# README.md is required by pyproject.toml [project].readme during the local build.
COPY pyproject.toml uv.lock README.md ./
COPY excelmanus /app/excelmanus

# `all` = cli + web + channels (telegram/qq/feishu) + analysis
RUN uv sync --frozen --no-dev --extra all

RUN addgroup --system --gid 1001 appuser && \
    adduser --system --uid 1001 --ingroup appuser appuser

RUN mkdir -p /workspace && chown -R appuser:appuser /workspace /app
WORKDIR /workspace

USER appuser

EXPOSE 8000

ENV EXCELMANUS_CHANNELS=telegram

CMD ["excelmanus-api"]
