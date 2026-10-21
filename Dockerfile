FROM python:3.12-slim AS dependencies
COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python

FROM dependencies AS test
RUN uv sync --frozen --no-install-project --python /usr/local/bin/python
COPY backend ./backend
COPY tests ./tests
COPY eval ./eval
COPY docs/openapi ./docs/openapi
RUN uv run --no-sync ruff check backend tests eval \
 && uv run --no-sync ruff format --check backend tests eval \
 && uv run --no-sync mypy \
    && uv run --no-sync pytest -q \
    && uv run --no-sync python -m backend.openapi --check

FROM python:3.12-slim AS runtime
ARG SERVICE_NAME=cs
ENV SERVICE_NAME=${SERVICE_NAME} ENVIRONMENT=production DEV_AUTH_ENABLED=false \
    PORT=8080 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:${PATH}"
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --no-create-home app
WORKDIR /app
COPY --from=dependencies /app/.venv ./.venv
COPY backend ./backend
USER 10001:10001
CMD ["python", "-m", "backend"]
