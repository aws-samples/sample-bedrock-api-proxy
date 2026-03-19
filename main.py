"""
Main entry point for the Anthropic-Bedrock API Proxy service.

This module provides a simple entry point that can be used for running
the application with uvicorn.

Usage:
    python main.py
    or
    uvicorn main:app --reload
"""
from app.main import app

if __name__ == "__main__":
    import uvicorn
    from app.core.config import settings

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        workers=settings.workers if not settings.reload else 1,
        log_level=settings.log_level.lower(),
    )
