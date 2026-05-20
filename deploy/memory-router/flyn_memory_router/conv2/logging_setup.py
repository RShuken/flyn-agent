"""Structured JSON logging for conv-tier 2.0.

Configures stdlib logging to emit JSON-per-line with the canonical
fields: ts, level, trace_id, message_id, stage, outcome, duration_ms.

Call setup_logging() once at app startup. After that, any logger.info
with extra={"trace_id": ..., ...} flows through this formatter.

The formatter is non-intrusive: lines without trace_id (boot, config
loads, etc.) still get logged but with only the standard fields.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Render LogRecord as one JSON line per record.

    Extra fields passed via logger.info("msg", extra={...}) become
    top-level keys in the JSON. Standard fields (ts, level, message)
    are always present.
    """

    # Keys we always include
    _STANDARD = {"ts", "level", "logger", "message"}
    # LogRecord attributes that are auto-attached (don't emit as extras)
    _SKIP_ATTRS = {
        "name", "msg", "args", "created", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs",
        "message", "pathname", "process", "processName",
        "relativeCreated", "thread", "threadName", "taskName",
        "stack_info", "exc_info", "exc_text",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Pull arbitrary extra= fields out of the record
        for attr, value in record.__dict__.items():
            if attr in self._SKIP_ATTRS or attr in self._STANDARD:
                continue
            try:
                # Only include JSON-serializable extras
                json.dumps(value)
                payload[attr] = value
            except (TypeError, ValueError):
                payload[attr] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure stdlib root logger for JSON output to stdout.

    Idempotent — re-running just replaces the handler.
    """
    root = logging.getLogger()
    # Remove any existing handlers (re-run safety)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root.addHandler(handler)
    root.setLevel(level)
