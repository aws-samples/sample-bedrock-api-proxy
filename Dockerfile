# Multi-stage Dockerfile for Anthropic-Bedrock API Proxy
# Using official uv image for faster dependency management

# Stage 1: Builder
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# Set working directory
WORKDIR /build

# Copy dependency files and source code (needed for package build)
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
COPY main.py ./

# Install dependencies using uv
# uv sync will create a .venv directory with all dependencies
RUN uv sync --frozen --no-dev

# Stage 2: Runtime
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Set working directory
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

# Copy project files (needed for uv run)
COPY pyproject.toml uv.lock README.md /app/

# Copy application code
COPY app /app/app
COPY main.py /app/

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
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application directly from the virtual environment
# Note: Using python -m uvicorn instead of uv run to avoid rebuild issues
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
