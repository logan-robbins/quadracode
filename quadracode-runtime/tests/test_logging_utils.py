from __future__ import annotations

import logging

from quadracode_runtime.logging_utils import configure_logging


def _reset_root_logger() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.NOTSET)


def test_configure_logging_sets_level(monkeypatch) -> None:
    _reset_root_logger()
    monkeypatch.setenv("QUADRACODE_LOG_LEVEL", "debug")

    configure_logging(force=True)

    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_is_idempotent(monkeypatch) -> None:
    _reset_root_logger()
    monkeypatch.delenv("QUADRACODE_LOG_LEVEL", raising=False)

    configure_logging(force=True)
    first_count = len(logging.getLogger().handlers)

    configure_logging()
    second_count = len(logging.getLogger().handlers)

    assert first_count == second_count != 0

