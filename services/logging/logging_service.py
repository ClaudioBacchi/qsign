"""Central logging facade used by QSign services."""

import logging
from collections.abc import Mapping
from typing import Any


class LoggingService:
    """Small facade that keeps the standard logger out of other services."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    @classmethod
    def create(cls, name: str, level: int = logging.INFO) -> "LoggingService":
        """Create a logger with a safe default console configuration."""
        logger = logging.getLogger(name)
        logger.setLevel(level)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
                )
            )
            logger.addHandler(handler)
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

