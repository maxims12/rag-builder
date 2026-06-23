# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Keep Python lean and predictable inside the container
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Minimal system deps. (Playwright/crawl4ai system libs are added in Phase 5.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager) from the official static image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies first for better layer caching. The package itself is
# installed in non-editable mode after sources are copied.
COPY pyproject.toml ./
COPY README.md ./
# uv.lock may not exist yet during early scaffold; copy if present.
COPY uv.loc[k] ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev || true

# Copy application source
COPY . .

# Resolve/install the project itself (no-op friendly during scaffold)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev || true

EXPOSE 8000

# Run the FastAPI app. (app.main:app is implemented in Phase 1.)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
