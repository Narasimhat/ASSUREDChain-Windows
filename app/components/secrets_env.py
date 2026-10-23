"""Copy Streamlit Cloud secrets into os.environ for getenv-based modules."""

from __future__ import annotations

import os
from typing import Any, Mapping


def _flatten_secrets(mapping: Mapping[str, Any], prefix: str = "") -> None:
    for key, value in mapping.items():
        name = f"{prefix}{key}" if not prefix else f"{prefix}_{key}"
        if isinstance(value, Mapping):
            _flatten_secrets(value, name)
            continue
        if value is None:
            continue
        text = str(value)
        # Prefer explicit environment variables over secrets.toml duplicates.
        if name not in os.environ or not os.environ[name]:
            os.environ[name] = text
        # Also expose nested keys without section prefix when unambiguous
        # (e.g. [openai] OPENAI_API_KEY -> OPENAI_API_KEY already flat).
        if prefix and key not in os.environ:
            os.environ[key] = text


def apply_streamlit_secrets() -> None:
    """Best-effort sync of st.secrets into the process environment."""
    try:
        import streamlit as st
    except Exception:
        return
    try:
        secrets = st.secrets
    except Exception:
        return
    try:
        _flatten_secrets(dict(secrets))
    except Exception:
        # Secrets may be unavailable outside Streamlit Cloud / local secrets.toml
        return
