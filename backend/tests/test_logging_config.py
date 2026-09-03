from __future__ import annotations

import io
import logging
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from app.logging_config import _APP_HANDLER_MARKER, configure_application_logging


class ApplicationLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app_logger = logging.getLogger("app")
        self.original_handlers = list(self.app_logger.handlers)
        self.original_level = self.app_logger.level
        self.original_propagate = self.app_logger.propagate
        self._remove_application_handlers()

    def tearDown(self) -> None:
        self._remove_application_handlers()
        self.app_logger.handlers[:] = self.original_handlers
        self.app_logger.setLevel(self.original_level)
        self.app_logger.propagate = self.original_propagate

    def test_default_level_is_info(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            logger = configure_application_logging()

        self.assertEqual(logger.level, logging.INFO)

    def test_debug_override_is_supported(self) -> None:
        with patch.dict(os.environ, {"APP_LOG_LEVEL": "DEBUG"}, clear=True):
            logger = configure_application_logging()

        self.assertEqual(logger.level, logging.DEBUG)

    def test_invalid_override_falls_back_to_info(self) -> None:
        with patch.dict(os.environ, {"APP_LOG_LEVEL": "not-a-level"}, clear=True):
            logger = configure_application_logging()

        self.assertEqual(logger.level, logging.INFO)

    def test_configuration_is_idempotent(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            configure_application_logging()
            configure_application_logging()

        self.assertEqual(len(self._application_handlers()), 1)

    def test_child_logger_emits_info_to_application_handler(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch("sys.stderr", output):
            configure_application_logging()
            logging.getLogger("app.services.example").info("reviewed render visible")

        self.assertIn("INFO app.services.example: reviewed render visible", output.getvalue())

    def test_uvicorn_logger_configuration_is_unchanged(self) -> None:
        uvicorn_logger = logging.getLogger("uvicorn.error")
        original_handlers = list(uvicorn_logger.handlers)
        original_level = uvicorn_logger.level
        original_propagate = uvicorn_logger.propagate

        configure_application_logging()

        self.assertEqual(uvicorn_logger.handlers, original_handlers)
        self.assertEqual(uvicorn_logger.level, original_level)
        self.assertEqual(uvicorn_logger.propagate, original_propagate)

    def test_local_backend_script_has_info_defaults(self) -> None:
        script = (
            Path(__file__).resolve().parents[2] / "scripts" / "dev-backend-local.sh"
        ).read_text(encoding="utf-8")

        self.assertIn('export APP_LOG_LEVEL="${APP_LOG_LEVEL:-INFO}"', script)
        self.assertIn('export UVICORN_LOG_LEVEL="${UVICORN_LOG_LEVEL:-info}"', script)
        self.assertIn('--log-level "${UVICORN_LOG_LEVEL}"', script)
        self.assertIn('--reload-dir "$ROOT_DIR/backend/app"', script)

    def _application_handlers(self) -> list[logging.Handler]:
        return [
            handler
            for handler in self.app_logger.handlers
            if getattr(handler, _APP_HANDLER_MARKER, False)
        ]

    def _remove_application_handlers(self) -> None:
        for handler in self._application_handlers():
            self.app_logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
