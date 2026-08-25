ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.11-slim

FROM ${NODE_IMAGE} AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS runtime

ARG PSKIT_VERSION=0.3.0
ARG APP_UID=10001
ARG APP_GID=10001

LABEL org.opencontainers.image.title="PSKit 2.0" \
      org.opencontainers.image.description="Full-stack BioAI Agent workbench" \
      org.opencontainers.image.version="${PSKIT_VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend \
    PSKIT_ALLOW_LEGACY_PORT_10706=true \
    BIND_HOST=0.0.0.0 \
    BIND_PORT=10706 \
    DATABASE_URL=sqlite:////app/backend/data/pskit2.sqlite3 \
    DATA_DIR=/app/backend/data \
    ARTIFACT_DIR=/app/backend/data/artifacts \
    KNOWLEDGE_DIR=/app/knowledge \
    FRONTEND_DIST_DIR=/app/frontend/dist

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" pskit \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin pskit

COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/requirements.lock /app/backend/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /app/backend/requirements.lock

COPY backend/alembic.ini /app/backend/alembic.ini
COPY backend/alembic/ /app/backend/alembic/
COPY backend/app/ /app/backend/app/
RUN python -m pip install --no-cache-dir --no-build-isolation --no-deps /app/backend \
    && python -m pip check

COPY knowledge/ /app/knowledge/
COPY scripts/migrate_db.py /app/scripts/migrate_db.py
COPY scripts/build_rag_index.py /app/scripts/build_rag_index.py
COPY --from=frontend-builder /app/frontend/dist/ /app/frontend/dist/

RUN mkdir -p /app/backend/data/artifacts /app/backend/data/qdrant-local \
    && chown -R pskit:pskit /app

USER pskit
WORKDIR /app/backend

EXPOSE 10706
VOLUME ["/app/backend/data"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl --fail --silent --show-error http://127.0.0.1:10706/api/ready >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10706"]
