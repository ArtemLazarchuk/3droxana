"""Пакет бізнес-логіки асистента (важкі залежності підвантажуються лише за потреби)."""

from __future__ import annotations

from typing import Any

__all__ = ["ChatServiceError", "ChatSessionNotFound", "process_chat"]


_LAZY = {
    "ChatServiceError": ".chat",
    "ChatSessionNotFound": ".chat",
    "process_chat": ".chat",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        import importlib

        mod = importlib.import_module(_LAZY[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
