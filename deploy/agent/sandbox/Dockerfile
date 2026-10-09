ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}
ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/pskit \
    PATH=/usr/local/bin:/usr/bin:/bin

RUN sed -i \
      -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
      -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap util-linux \
    && test -x /usr/local/bin/python3 \
    && { test -x /usr/bin/python3 || ln -s /usr/local/bin/python3 /usr/bin/python3; } \
    && printf 'pskit:x:10001:\n' >> /etc/group \
    && printf 'pskit:x:10001:10001:PSKit workspace:/home/pskit:/bin/sh\n' >> /etc/passwd \
    && install -d -o 10001 -g 10001 -m 0700 /workspace /home/pskit \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY --chown=10001:10001 entrypoint.sh isolation.toml /opt/pskit-sandbox/
RUN chmod 0555 /opt/pskit-sandbox/entrypoint.sh \
    && chmod 0444 /opt/pskit-sandbox/isolation.toml

USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["/opt/pskit-sandbox/entrypoint.sh"]
