"""Tests for file-backed application logging."""

import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from services.logging.logging_service import LoggingService


class LoggingServiceTests(unittest.TestCase):
    def test_application_log_is_written_to_logs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger_name = "qsign.tests.file_logging"
            logger = LoggingService.create(
                logger_name,
                log_directory=directory,
            )
            try:
                logger.info("Signed PDF saved", destination="dist/signed/demo.pdf")

                content = (Path(directory) / "qsign.log").read_text(encoding="utf-8")
                self.assertIn("Signed PDF saved", content)
                self.assertIn('"destination": "dist/signed/demo.pdf"', content)
            finally:
                _close_logger_handlers(logger_name)

    def test_application_log_rotation_is_size_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger_name = "qsign.tests.rotation"
            LoggingService.create(logger_name, log_directory=directory)
            try:
                handlers = [
                    handler
                    for handler in logging.getLogger(logger_name).handlers
                    if isinstance(handler, RotatingFileHandler)
                ]

                self.assertEqual(len(handlers), 1)
                self.assertEqual(
                    handlers[0].maxBytes,
                    LoggingService.DEFAULT_MAX_LOG_BYTES,
                )
                self.assertEqual(
                    handlers[0].backupCount,
                    LoggingService.DEFAULT_LOG_BACKUP_COUNT,
                )
            finally:
                _close_logger_handlers(logger_name)

    def test_main_application_logger_writes_file_without_console_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger_name = "qsign"
            logger = LoggingService.create(logger_name, log_directory=directory)
            try:
                logger.info("Starting QSign desktop shell")

                handlers = logging.getLogger(logger_name).handlers
                console_handlers = [
                    handler
                    for handler in handlers
                    if type(handler) is logging.StreamHandler
                ]
                file_handlers = [
                    handler
                    for handler in handlers
                    if isinstance(handler, RotatingFileHandler)
                ]

                self.assertEqual(console_handlers, [])
                self.assertEqual(len(file_handlers), 1)
                content = (Path(directory) / "qsign.log").read_text(encoding="utf-8")
                self.assertIn("Starting QSign desktop shell", content)
            finally:
                _close_logger_handlers(logger_name)


def _close_logger_handlers(logger_name: str) -> None:
    logger = logging.getLogger(logger_name)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
