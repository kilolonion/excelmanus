FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY excelmanus /app/excelmanus

RUN pip install --no-cache-dir ".[all]"

RUN addgroup --system --gid 1001 appuser && \
    adduser --system --uid 1001 --ingroup appuser appuser

RUN mkdir -p /workspace && chown appuser:appuser /workspace
WORKDIR /workspace

USER appuser

EXPOSE 8000

ENV EXCELMANUS_CHANNELS=telegram

CMD ["excelmanus-api"]
