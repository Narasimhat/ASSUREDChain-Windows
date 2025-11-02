"""Shared layout helpers for Streamlit pages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import streamlit as st


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGO_CANDIDATES = [
    ROOT / "assets" / "assured_logo1.png",
    ROOT / "assets" / "assured_logo.png",
]
DEFAULT_ICON = "🧬"


@dataclass(frozen=True)
class LogoAsset:
    """Metadata about the logo asset used across pages."""

    path: Path

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def icon(self) -> str:
        return str(self.path) if self.exists else DEFAULT_ICON


def _default_logo_path() -> Path:
    for candidate in DEFAULT_LOGO_CANDIDATES:
        if candidate.exists():
            return candidate
    return DEFAULT_LOGO_CANDIDATES[-1]


def resolve_logo(path: Optional[Path] = None) -> LogoAsset:
    """Return information about the logo asset, falling back to user-supplied assets."""
    if path is None:
        return LogoAsset(_default_logo_path())
    return LogoAsset(Path(path))


def configure_page(title: str, *, layout: str = "wide", logo_path: Optional[Path] = None) -> LogoAsset:
    """Set Streamlit page config using a shared logo/icon and return the asset metadata."""
    logo = resolve_logo(logo_path)
    st.set_page_config(page_title=title, page_icon=logo.icon, layout=layout)
    return logo


def render_page_logo(
    *,
    logo_path: Optional[Path] = None,
    show_banner: bool = True,
    banner_width: int = 260,
) -> None:
    """Render the shared logo in the app header and optionally in the page body."""
    logo = resolve_logo(logo_path)
    if logo.exists:
        logo_fn = getattr(st, "logo", None)
        if callable(logo_fn):
            logo_fn(str(logo.path))
        if show_banner:
            st.image(str(logo.path), width=banner_width)
            st.divider()
    elif show_banner:
        st.markdown(
            "<div style='font-size:26px;font-weight:700;letter-spacing:0.08em;'>ASSUREDChain</div>",
            unsafe_allow_html=True,
        )
        st.divider()


def init_page(
    title: str,
    *,
    layout: str = "wide",
    logo_path: Optional[Path] = None,
    show_banner: bool = True,
    banner_width: int = 260,
) -> LogoAsset:
    """Convenience helper to configure the page and immediately render the shared logo."""
    logo = configure_page(title, layout=layout, logo_path=logo_path)
    render_page_logo(logo_path=logo.path, show_banner=show_banner, banner_width=banner_width)
    return logo
