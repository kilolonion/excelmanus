FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY excelmanus /app/excelmanus

RUN pip install --no-cache-dir ".[web]"

RUN mkdir -p /workspace
WORKDIR /workspace

EXPOSE 8000

CMD ["excelmanus-api"]
