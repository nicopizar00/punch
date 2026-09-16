FROM node:lts-alpine AS builder
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src/tests/ ./src/tests/
RUN npm run build

FROM grafana/k6:0.55.0
COPY --from=builder /build/dist/ /scripts/
COPY --chmod=755 scripts/k6-wrapper.sh /scripts/k6-wrapper.sh
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
