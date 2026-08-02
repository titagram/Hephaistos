FROM node:22-bookworm-slim@sha256:7af03b14a13c8cdd38e45058fd957bf00a72bbe17feac43b1c15a689c029c732 AS download

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://github.com/supermemoryai/supermemory/releases/download/server-v0.0.6/supermemory-server-linux-x64 \
      -o /tmp/supermemory-server \
    && echo 'bb1b7cee393818236873b8e2518a435e10d9195e27ea5608a3af48a733ef8ee8  /tmp/supermemory-server' | sha256sum -c -

FROM node:22-bookworm-slim@sha256:7af03b14a13c8cdd38e45058fd957bf00a72bbe17feac43b1c15a689c029c732

COPY --from=download --chmod=0755 /tmp/supermemory-server /usr/local/bin/supermemory-server

ENV PORT=6767 \
    SUPERMEMORY_DATA_DIR=/var/lib/supermemory
RUN mkdir -p /var/lib/supermemory && chown node:node /var/lib/supermemory
USER node
VOLUME ["/var/lib/supermemory"]
EXPOSE 6767
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
  CMD node -e "require('http').get('http://127.0.0.1:6767/',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"
ENTRYPOINT ["/usr/local/bin/supermemory-server"]
