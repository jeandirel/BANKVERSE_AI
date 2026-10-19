# syntax=docker/dockerfile:1.7
# BANKVERSE AI — web image (Next.js standalone output, non-root).
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
# The API origin used by the same-origin /api rewrite (internal service address in ECS / compose).
ARG BANKVERSE_API_URL=http://api:8000
ENV NEXT_TELEMETRY_DISABLED=1 BANKVERSE_API_URL=${BANKVERSE_API_URL}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S -g 10001 web && adduser -S -u 10001 -G web web
COPY --from=build --chown=web:web /app/.next/standalone ./
COPY --from=build --chown=web:web /app/.next/static ./.next/static
COPY --from=build --chown=web:web /app/public ./public
USER 10001:10001
EXPOSE 3000
CMD ["node", "server.js"]
