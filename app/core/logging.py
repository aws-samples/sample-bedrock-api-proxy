"""
Structured logging configuration.

Provides structured logging with context and correlation IDs for tracing.
"""
import logging
import sys
from typing import Any, Dict

from app.core.config import settings


class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record with structured information.

        Args:
            record: Log record

        Returns:
            Formatted log string
        """
        # Base log data
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields if present
        if hasattr(record, "extra"):
            log_data.update(record.extra)

        # Add request context if present
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id

        if hasattr(record, "api_key"):
            # Mask API key for security
            api_key = record.api_key
            log_data["api_key"] = f"{api_key[:7]}...{api_key[-4:]}" if api_key else None

        if hasattr(record, "user_id"):
            log_data["user_id"] = record.user_id

        # Format as key=value pairs
        formatted_parts = []
        for key, value in log_data.items():
            if isinstance(value, str) and " " in value:
                formatted_parts.append(f'{key}="{value}"')
            else:
                formatted_parts.append(f"{key}={value}")

        return " ".join(formatted_parts)


def setup_logging():
    """Configure application logging."""
    # Get log level from settings
    log_level = getattr(logging, settings.log_level.upper())

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    # Set formatter
    formatter = StructuredFormatter(
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(handler)

    # Configure specific loggers
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)

    # Log startup message
    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging configured: level={settings.log_level}, environment={settings.environment}"
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get logger instance with name.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class LoggerAdapter(logging.LoggerAdapter):
    """Logger adapter for adding context to log messages."""

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        """
        Process log message and add context.

        Args:
            msg: Log message
            kwargs: Keyword arguments

        Returns:
            Tuple of (message, kwargs)
        """
        # Add extra context from adapter
        if "extra" not in kwargs:
            kwargs["extra"] = {}

        kwargs["extra"].update(self.extra)

        return msg, kwargs


def get_logger_with_context(name: str, **context) -> LoggerAdapter:
    """
    Get logger with context.

    Args:
        name: Logger name
        **context: Context to add to all log messages

    Returns:
        LoggerAdapter instance
    """
    logger = get_logger(name)
    return LoggerAdapter(logger, context)
