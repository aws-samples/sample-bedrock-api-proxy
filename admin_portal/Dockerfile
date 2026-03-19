# Multi-stage Dockerfile for Admin Portal
# Builds React frontend and packages with FastAPI backend
#
# Build context should be the project root directory:
#   docker build -f admin_portal/Dockerfile -t admin-portal .

# Stage 1: Build Frontend
FROM node:20-slim AS frontend-builder

WORKDIR /build

# Copy frontend package files
COPY admin_portal/frontend/package.json admin_portal/frontend/package-lock.json ./

# Install dependencies
RUN npm ci

# Copy frontend source
COPY admin_portal/frontend/ ./

# Build frontend for production with /admin/ base path
ENV VITE_BASE_PATH=/admin/
RUN npm run build

# Stage 2: Python Dependencies
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /build

# Copy the main project's dependency files (admin_portal uses same deps)
COPY pyproject.toml uv.lock README.md ./

# Copy the main app module (needed for DynamoDB client imports)
COPY app ./app

# Copy admin_portal backend
COPY admin_portal/backend ./admin_portal/backend

# Install dependencies
RUN uv sync --frozen --no-dev

# Stage 3: Final Runtime
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install runtime system dependencies
RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash appuser

# Copy virtual environment from builder
COPY --from=builder /build/.venv /app/.venv

# Copy project files (needed for package resolution)
COPY --from=builder /build/pyproject.toml /build/uv.lock /build/README.md /app/

# Copy main app module (needed for DynamoDB client imports)
COPY --from=builder /build/app /app/app

# Copy admin_portal backend
COPY --from=builder /build/admin_portal/backend /app/admin_portal/backend

# Copy built frontend from frontend-builder
COPY --from=frontend-builder /build/dist /app/admin_portal/frontend/dist

# Set ownership
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

# Expose port
EXPOSE 8005

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8005/health || exit 1

# Run admin portal
CMD ["python", "-m", "uvicorn", "admin_portal.backend.main:app", "--host", "0.0.0.0", "--port", "8005"]
