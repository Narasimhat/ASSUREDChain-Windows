from __future__ import annotations

from typing import Any


def d(data: Any, *keys: Any, default: Any = "") -> Any:
    """
    Safe nested getter: d(obj, "a", 0, "b", default="") → obj["a"][0]["b"] if present else default.
    Works with dict/list sequences and returns default when path missing.
    """
    current = data
    for key in keys:
        try:
            if isinstance(current, dict):
                current = current[key]
            elif isinstance(current, (list, tuple)) and isinstance(key, int):
                current = current[key]
            else:
                return default
        except (KeyError, IndexError, TypeError):
            return default
    return current if current not in (None, "") else default


def project_value(meta: dict, key: str, default: Any = "") -> Any:
    return meta.get(key, default)
