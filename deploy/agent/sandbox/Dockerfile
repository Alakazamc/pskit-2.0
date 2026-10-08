ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/pskit \
    PATH=/usr/local/bin:/usr/bin:/bin

RUN groupadd --gid 10001 pskit \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash pskit \
    && install -d -o 10001 -g 10001 -m 0700 /workspace /home/pskit \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY --chown=10001:10001 entrypoint.sh /opt/pskit-sandbox/entrypoint.sh
RUN chmod 0555 /opt/pskit-sandbox/entrypoint.sh

USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["/opt/pskit-sandbox/entrypoint.sh"]
