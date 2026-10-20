ARG PYTHON_VERSION=3.13
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS base

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1

# --- Build stage: compilers here, discarded before the final image ---
FROM base AS build

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    python3-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies before source, so a code change does not re-resolve them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked

# The module-level command discovers installed livekit-plugins-* packages.
RUN uv run --module livekit.agents download-files

COPY . .

# --- Final stage: no build tools ---
FROM base

# Non-privileged runtime user.
# https://docs.docker.com/build/building/best-practices/#user
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/app" \
    --shell "/sbin/nologin" \
    --uid "${UID}" \
    appuser

WORKDIR /app

COPY --from=build --chown=appuser:appuser /app /app

USER appuser

# "start" connects the worker to LiveKit and waits for jobs.
CMD ["uv", "run", "src/agent.py", "start"]
