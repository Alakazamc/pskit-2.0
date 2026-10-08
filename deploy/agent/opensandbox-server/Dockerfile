ARG OPENSANDBOX_SERVER_BASE
FROM ${OPENSANDBOX_SERVER_BASE}

COPY patch_server.py /tmp/pskit-patch-server.py
RUN python /tmp/pskit-patch-server.py /app/opensandbox_server \
    && rm -f /tmp/pskit-patch-server.py
