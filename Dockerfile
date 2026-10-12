# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY contracts ./contracts
COPY orchestrator ./orchestrator
COPY worker ./worker
COPY cli ./cli
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install .

# ---- control plane: runs unprivileged
FROM base AS api
RUN useradd --system --uid 10001 orchestrator
USER orchestrator
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec python -m orchestrator.api"]

# ---- worker agent: needs access to a Docker daemon (socket or DOCKER_HOST); see docs/operations
FROM base AS worker
EXPOSE 9000
CMD ["python", "-m", "worker"]

# ---- CLI image (optional)
FROM base AS cli
ENTRYPOINT ["orchestrator"]
