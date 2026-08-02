ARG SUPERMEMORY_PLATFORM=linux/amd64

FROM --platform=$SUPERMEMORY_PLATFORM node:22-bookworm-slim@sha256:7af03b14a13c8cdd38e45058fd957bf00a72bbe17feac43b1c15a689c029c732 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src ./src
COPY tests ./tests
RUN npm test && npm run build

FROM --platform=$SUPERMEMORY_PLATFORM node:22-bookworm-slim@sha256:7af03b14a13c8cdd38e45058fd957bf00a72bbe17feac43b1c15a689c029c732 AS production-dependencies

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM --platform=$SUPERMEMORY_PLATFORM node:22-bookworm-slim@sha256:7af03b14a13c8cdd38e45058fd957bf00a72bbe17feac43b1c15a689c029c732

WORKDIR /app
COPY --from=build /app/dist/src ./dist/src
COPY --from=production-dependencies /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /app/node_modules/.bin/codex /usr/local/bin/codex \
    && mkdir -p /workspace /var/lib/codex \
    && chown node:node /workspace /var/lib/codex

ENV CODEX_HOME=/var/lib/codex

USER node
VOLUME ["/var/lib/codex"]
EXPOSE 8646
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
  CMD node -e "require('http').get('http://127.0.0.1:8646/healthz', r => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"
ENTRYPOINT ["node", "/app/dist/src/index.js"]
