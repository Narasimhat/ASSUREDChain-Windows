"""FDA-grade 6-stage lifecycle header and progress breadcrumb for ASSUREDChain."""

from __future__ import annotations

from typing import Optional
import streamlit as st

from app.components.project_state import load_project_meta

STAGES = [
    (1, "1. Request & TPP", "pages/1_Project_Request.py"),
    (2, "2. CRISPR Design", "pages/2_CRISPR_Design.py"),
    (3, "3. Transfection & Bulk QC", "pages/3_Transfection_Bulk_QC.py"),
    (4, "4. Single-Cell Cloning", "pages/4_Single_Cell_Cloning.py"),
    (5, "5. Dual-Clone QC", "pages/5_Dual_Clone_QC.py"),
    (6, "6. Master Bank & CoA", "pages/6_Master_Bank_and_CoA.py"),
]


def render_lifecycle_header(
    current_stage: int | str,
    selected_project: Optional[str] = None,
    subtitle: Optional[str] = None,
) -> None:
    """Render a clean, compact FDA-grade batch record lifecycle breadcrumb."""
    meta = load_project_meta(selected_project) if selected_project else {}
    gene = meta.get("target_gene") or meta.get("gene") or "Target Gene"
    edit_type = meta.get("edit_program") or meta.get("edit_type") or "CRISPR Edit"
    cell_line = meta.get("cell_line") or "Primary / iPSC Line"
    proj_display = selected_project or "No Project Selected"

    # Compact header banner
    col_proj, col_tpp, col_deliv = st.columns([1.5, 1.5, 1])
    with col_proj:
        st.markdown(f"**Project:** `{proj_display}` &nbsp;|&nbsp; **Target:** `{gene}` ({edit_type})")
    with col_tpp:
        st.markdown(f"**Parent Line:** `{cell_line}`")
    with col_deliv:
        st.markdown(
            "<div style='text-align: right;'><span style='background-color:#eff8ff; color:#175cd3; padding:3px 8px; border-radius:12px; font-size:0.8rem; font-weight:600; border:1px solid #b2ddff;'>🎯 Deliverable: 2 Clones</span></div>",
            unsafe_allow_html=True,
        )

    # 6-Stage Stepper
    cols = st.columns(6)
    for idx, (num, label, page_link) in enumerate(STAGES):
        with cols[idx]:
            is_active = (current_stage == num)
            bg = "#039855" if is_active else "#f2f4f7"
            fg = "#ffffff" if is_active else "#344054"
            border = "#027a48" if is_active else "#d0d5dd"
            weight = "700" if is_active else "500"

            st.markdown(
                f"""
                <div style="background-color:{bg}; color:{fg}; border:1px solid {border}; border-radius:6px; padding:6px 4px; text-align:center; font-size:0.75rem; font-weight:{weight}; line-height:1.2; box-shadow: {'0 1px 2px rgba(0,0,0,0.05)' if is_active else 'none'};">
                    {label}
                </div>
                """,
                unsafe_allow_html=True,
            )

    if subtitle:
        st.caption(subtitle)
    st.divider()