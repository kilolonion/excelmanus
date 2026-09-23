FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EXCELMANUS_DEPLOY_MODE=server \
    EXCELMANUS_WEB_WORKERS=2 \
    EXCELMANUS_WORKSPACE_ROOT=/var/lib/excelmanus/workspace \
    EXCELMANUS_HOME=/var/lib/excelmanus/home

WORKDIR /opt/excelmanus
COPY pyproject.toml uv.lock README.md ./
RUN pip install --no-cache-dir uv \
 && uv sync --frozen --no-dev --extra web --no-install-project
COPY excelmanus ./excelmanus
COPY deploy ./deploy
RUN uv sync --frozen --no-dev --extra web

RUN mkdir -p /var/lib/excelmanus/home /var/lib/excelmanus/workspace \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin excelmanus \
 && chown -R excelmanus:excelmanus /var/lib/excelmanus /opt/excelmanus
USER excelmanus
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2)"
CMD [".venv/bin/python", "-m", "excelmanus.api", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
