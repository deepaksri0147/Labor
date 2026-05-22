import logging
import sys
from app.core.config import Config


class StructuredFormatter(logging.Formatter):
    """Log formatter that adds service context to every line."""

    FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    def __init__(self):
        super().__init__(fmt=self.FMT, datefmt="%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        record.name = record.name.removeprefix("app.")
        return super().format(record)


def setup_logging() -> None:
    """
    Configure root logger for the application.
    Call once at startup before any other imports log.
    """
    log_level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    root.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("kubernetes").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("opentelemetry").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        f"Logging initialised | level={Config.LOG_LEVEL} | service={Config.SERVICE_NAME}"
    )
