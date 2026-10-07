FROM node:22.21.1-bookworm-slim@sha256:25b3eb23a00590b7499f2a2ce939322727fcce1b15fdd69754fcd09536a3ae2c AS pi-build
WORKDIR /opt/pi
COPY pi/package.json pi/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && test -x node_modules/.bin/pi

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/pi/node_modules/.bin:${PATH} \
    RESEARCH_AGENT_INTERNAL_API_URL=http://127.0.0.1:8000 \
    RESEARCH_AGENT_PI_SESSION_DIR=/data/pi-sessions
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid 10001 agent && useradd --uid 10001 --gid 10001 --home-dir /home/agent --create-home agent && \
    mkdir -p /data /workspace && chown agent:agent /data /workspace
COPY pyproject.toml ./
RUN python -c "import subprocess,sys,tomllib; dependencies=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',*dependencies])"
COPY app ./app
COPY pskit_compute ./pskit_compute
COPY --from=pi-build /usr/local/bin/node /usr/local/bin/node
COPY --from=pi-build /opt/pi/node_modules /app/pi/node_modules
COPY pi/package.json pi/extension.js pi/system-prompt.md ./pi/
COPY skills ./skills
COPY scripts/af3_callback_proxy.py ./scripts/af3_callback_proxy.py
COPY scripts/compute_receiver.py ./scripts/compute_receiver.py
COPY scripts/mcp_compute_receiver.py ./scripts/mcp_compute_receiver.py
COPY scripts/backfill_compute_artifacts.py ./scripts/backfill_compute_artifacts.py
COPY scripts/agent_data_migrate.py ./scripts/agent_data_migrate.py
RUN install -d -o agent -g agent -m 0750 /var/lib/pskit-mcp
USER agent
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
