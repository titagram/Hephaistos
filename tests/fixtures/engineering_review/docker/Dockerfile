FROM node:22-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 python3-pytest \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/hermes-review-dependencies
COPY package.json ./
RUN npm install --ignore-scripts --no-audit --no-fund \
    && touch /opt/hermes-review-dependencies/python-ready

WORKDIR /workspace
