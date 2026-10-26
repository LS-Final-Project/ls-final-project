FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci --prefix frontend --no-audit --no-fund
COPY docs/openapi ./docs/openapi
COPY frontend ./frontend
RUN npm test --prefix frontend && npm run build --prefix frontend

FROM node:24-bookworm-slim AS runtime
ENV NODE_ENV=production PORT=8080
WORKDIR /app
COPY --from=build --chown=node:node /app/frontend/dist ./dist
COPY --chown=node:node frontend/server.mjs frontend/evaluation-relay.mjs ./
USER node
CMD ["node", "server.mjs"]
