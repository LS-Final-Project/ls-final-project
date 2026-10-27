FROM python:3.12-slim AS dependencies
COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python

FROM dependencies AS test
ARG TEST_SERVICE=all
RUN uv sync --frozen --no-install-project --python /usr/local/bin/python
COPY backend ./backend
COPY tests ./tests
COPY eval ./eval
COPY docs/openapi ./docs/openapi
COPY frontend/src/mocks/generated ./frontend/src/mocks/generated
COPY scripts/check_backend.py ./scripts/check_backend.py
RUN uv run --no-sync python scripts/check_backend.py "$TEST_SERVICE"

FROM dependencies AS service-source
ARG SERVICE_NAME=cs
COPY backend ./backend
RUN python -c 'import os,shutil; service=os.environ["SERVICE_NAME"]; assert service in ("cs","tft","platform"); [shutil.rmtree("backend/api/"+other) for other in ("cs","tft","platform") if other != service]'

FROM python:3.12-slim AS runtime
ARG SERVICE_NAME=cs
ENV SERVICE_NAME=${SERVICE_NAME} ENVIRONMENT=production DEV_AUTH_ENABLED=false \
    PORT=8080 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:${PATH}"
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --no-create-home app
WORKDIR /app
COPY --from=dependencies /app/.venv ./.venv
COPY --from=service-source /app/backend ./backend
USER 10001:10001
CMD ["python", "-m", "backend"]

# 명시적으로 선택하는 합성 Job 이미지. production 잠금은 코드에서 유지한다.
FROM runtime AS evaluation-worker
COPY --chown=10001:10001 eval ./eval
ENTRYPOINT ["python", "-m", "backend.api.platform.evaluations.job_worker"]
CMD []

# target을 생략한 기존 서비스 빌드의 동작을 유지한다.
FROM runtime AS default-runtime
