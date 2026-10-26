"""Page 7: ASSURED-Biomni Autonomous Co-Scientist Studio."""

from __future__ import annotations

import streamlit as st

from app.components.assistant_panel import render_assistant
from app.components.biomni_panel import render_biomni_coscientist
from app.components.layout import init_page
from app.components.project_state import ensure_project_selected


def main() -> None:
    init_page("ASSURED-Biomni Co-Scientist")
    selected_project = ensure_project_selected()
    render_assistant(selected_project, context="biomni")
    render_biomni_coscientist(selected_project)


if __name__ == "__main__":
    main()
