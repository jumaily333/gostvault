# =============================================================================
# GhostVault Intelligence System — Production Dockerfile
# Multi-stage build: builder → runtime
# =============================================================================

# ── Stage 1: dependency builder ────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies into a prefix for copy
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime image ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL maintainer="GhostVault Intelligence System"
LABEL version="1.0.0"

# Non-root user for security
RUN groupadd -r ghostvault && useradd -r -g ghostvault -d /app -s /sbin/nologin ghostvault

# Runtime system deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=ghostvault:ghostvault app/ ./app/
COPY --chown=ghostvault:ghostvault alembic/ ./alembic/
COPY --chown=ghostvault:ghostvault alembic.ini .

# Create log directory
RUN mkdir -p /app/logs && chown -R ghostvault:ghostvault /app/logs

USER ghostvault

EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run with uvicorn — use gunicorn in production for process management
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--loop", "uvloop", \
     "--http", "httptools", \
     "--log-level", "warning", \
     "--access-log"]
