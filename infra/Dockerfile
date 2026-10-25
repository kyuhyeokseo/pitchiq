# PitchIQ Defensive Intelligence: one container serving the built frontend and the FastAPI/WebSocket API
# (spec 19: one Azure Container App, one replica, one worker; sessions live in memory).
# Build from the repository root:  docker build -f infra/Dockerfile -t pitchiq .
# Built and smoke-tested by the CI workflow (.github/workflows/ci.yml); the development host has no Docker.

FROM node:20.18.1-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig.json ./
COPY frontend/src ./src
RUN npx tsc --noEmit && npx vite build

FROM python:3.12.7-slim-bookworm
# the commit this image was built from (CI passes it); /api/health reports it, so the live build can be identified
ARG GIT_SHA=dev
ENV PITCHIQ_GIT_SHA=$GIT_SHA
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    PUBLIC_DATA_DIR=/app/data/public \
    PITCHIQ_DOTENV=0 \
    LLM_MODE=offline \
    AUTH_MODE=managed_identity
WORKDIR /app
COPY pyproject.toml ./
COPY infra/constraints.txt ./infra/constraints.txt
COPY backend/app ./backend/app
# pinned runtime deps plus the Foundry agents, at the exact versions the tests passed with; the source tree on
# PYTHONPATH is what runs
RUN pip install --no-cache-dir -c infra/constraints.txt ".[foundry,mcp,telemetry]"
# public observations only: no generator code, no private truth (see .dockerignore)
COPY data/public ./data/public
COPY --from=web /web/dist ./frontend/dist
RUN useradd --uid 10001 --no-create-home pitchiq && chown -R pitchiq /app
USER pitchiq
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "uvicorn", "app.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
