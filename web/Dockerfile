# NIXUS SQL React frontend (Phase 8.1) — multi-stage build.
# Stage 1 builds the Next.js standalone output; stage 2 runs it on a lean image.

FROM node:22-alpine AS builder
WORKDIR /app

# Install dependencies against the lockfile for reproducible builds.
COPY package.json package-lock.json* ./
RUN npm ci

# NEXT_PUBLIC_* values are inlined into the client bundle at BUILD time, so the
# API URL the BROWSER uses must be supplied here (not just at runtime). Compose
# passes this via build.args; the default is the host-reachable demo URL.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

# The API authenticates /api routes with X-API-Key. The browser cannot read the
# server's API_KEY, so the operator passes the same value as a build ARG (compose
# wires it from .env). Unset → no header is sent (requests will 401 — fail-closed).
ARG NEXT_PUBLIC_API_KEY=""
ENV NEXT_PUBLIC_API_KEY=${NEXT_PUBLIC_API_KEY}

COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Copy only the standalone server + static assets (output: "standalone").
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
