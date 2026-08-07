from __future__ import annotations

import logging
import os


_APP_HANDLER_MARKER = "_orlik_application_handler"
_APPLICATION_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_application_logging() -> logging.Logger:
    """Configure terminal logging for application modules without changing Uvicorn."""
    app_logger = logging.getLogger("app")
    level = _application_log_level()
    handler = _application_handler(app_logger)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_APPLICATION_LOG_FORMAT))
    app_logger.setLevel(level)
    app_logger.propagate = False
    return app_logger


def _application_log_level() -> int:
    configured = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    level = logging.getLevelName(configured)
    return level if isinstance(level, int) else logging.INFO


def _application_handler(app_logger: logging.Logger) -> logging.Handler:
    for handler in app_logger.handlers:
        if getattr(handler, _APP_HANDLER_MARKER, False):
            return handler
    handler = logging.StreamHandler()
    setattr(handler, _APP_HANDLER_MARKER, True)
    app_logger.addHandler(handler)
    return handler
