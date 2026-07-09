"""Central logging facade used by QSign services."""

import json
import logging
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class LoggingService:
    """Small facade that keeps the standard logger out of other services."""

    DEFAULT_MAX_LOG_BYTES = 2_000_000
    DEFAULT_LOG_BACKUP_COUNT = 5

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    @classmethod
    def create(
        cls,
        name: str,
        level: int = logging.INFO,
        log_directory: str | Path | None = None,
        console: bool | None = None,
    ) -> "LoggingService":
        """Create a logger with a safe default console configuration."""
        logger = logging.getLogger(name)
        logger.setLevel(level)
        if console is None:
            console = name != "qsign"
        if console and not cls._has_console_handler(logger):
            handler = logging.StreamHandler()
            handler.setFormatter(_QSignLogFormatter())
            logger.addHandler(handler)
        if log_directory is None and name == "qsign":
            log_directory = "logs"
        if log_directory is not None:
            cls._ensure_file_handler(logger, Path(log_directory))
        logger.propagate = False
        return cls(logger)

    def debug(self, message: str, **context: Any) -> None:
        self._logger.debug(message, extra=self._extra(context))

    def info(self, message: str, **context: Any) -> None:
        self._logger.info(message, extra=self._extra(context))

    def warning(self, message: str, **context: Any) -> None:
        self._logger.warning(message, extra=self._extra(context))

    def error(self, message: str, **context: Any) -> None:
        self._logger.error(message, extra=self._extra(context))

    def exception(self, message: str, **context: Any) -> None:
        self._logger.exception(message, extra=self._extra(context))

    @staticmethod
    def _extra(context: Mapping[str, Any]) -> dict[str, Any] | None:
        return {"qsign_context": dict(context)} if context else None

    @staticmethod
    def _has_console_handler(logger: logging.Logger) -> bool:
        return any(type(handler) is logging.StreamHandler for handler in logger.handlers)

    @staticmethod
    def _ensure_file_handler(logger: logging.Logger, log_directory: Path) -> None:
        log_path = log_directory / "qsign.log"
        resolved_log_path = log_path.resolve()
        for handler in logger.handlers:
            if (
                isinstance(handler, RotatingFileHandler)
                and Path(handler.baseFilename) == resolved_log_path
            ):
                return
        log_directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            resolved_log_path,
            maxBytes=LoggingService.DEFAULT_MAX_LOG_BYTES,
            backupCount=LoggingService.DEFAULT_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(_QSignLogFormatter())
        logger.addHandler(file_handler)


class _QSignLogFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        context = getattr(record, "qsign_context", None)
        if not context:
            return message
        return f"{message} | {json.dumps(context, ensure_ascii=True, default=str)}"
