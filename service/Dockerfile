# syntax=docker/dockerfile:1

# --- builder: resolve + install deps and build the venv with uv -----------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- runtime: non-root, no build toolchain, no writable root FS -----------
FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
VOLUME ["/data", "/backup"]

# Intended to run with `read_only: true` at the compose/run level (see
# deploy/docker-compose.yml) — nothing in this image writes outside /data or
# /backup. `python -m traintracker.gateway` (2a's one-shot auth smoke check)
# and `python -m traintracker.history` (2e's nightly sync/retention job)
# remain available for manual/cron invocation via
# `docker compose run --entrypoint ...`.
ENTRYPOINT ["python", "-m", "traintracker.poller"]
