FROM python:3.10-slim

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY excelmanus /app/excelmanus

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["excelmanus-api"]
