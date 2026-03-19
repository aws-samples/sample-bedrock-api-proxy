# PTC Sandbox Docker Image with Data Science Packages
#
# This image extends the base Python image with commonly used
# data analysis and scientific computing libraries.
#
# Build: docker build -t ptc-sandbox:datascience .
# Usage: Set PTC_SANDBOX_IMAGE=ptc-sandbox:datascience in your .env

FROM python:3.11-slim

LABEL maintainer="Anthropic API Proxy"
LABEL description="PTC Sandbox with data science packages"

# Install system dependencies for scientific packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python data science packages
# Using --no-cache-dir to reduce image size
RUN pip install --no-cache-dir \
    # Core data analysis
    numpy>=1.24.0 \
    pandas>=2.0.0 \
    # Scientific computing
    scipy>=1.11.0 \
    # Data visualization (optional, useful for generating charts)
    matplotlib>=3.7.0 \
    # Statistics
    statsmodels>=0.14.0 \
    # Machine learning basics
    scikit-learn>=1.3.0 \
    # HTTP requests (useful for API calls if network enabled)
    requests>=2.31.0 \
    httpx>=0.24.0 \
    # JSON/data handling
    orjson>=3.9.0 \
    pydantic>=2.0.0 \
    # Date/time utilities
    python-dateutil>=2.8.0 \
    pytz>=2023.3

# Create non-root user for security
RUN useradd -m -s /bin/bash sandbox

# Create workspace directory with proper permissions for sandbox user
RUN mkdir -p /workspace && chown sandbox:sandbox /workspace

# Set working directory
WORKDIR /workspace

# Switch to non-root user
USER sandbox

# Default command (will be overridden by PTC runner)
CMD ["python", "--version"]
