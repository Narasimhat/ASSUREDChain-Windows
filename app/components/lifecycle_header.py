"""FDA-grade 6-stage lifecycle header and progress breadcrumb for ASSUREDChain."""

from __future__ import annotations

from typing import Optional
import streamlit as st

from app.components.project_state import load_project_meta

STAGES = [
    (1, "1. Request & TPP", "pages/1_Project_Request.py"),
    (2, "2. CRISPR Design", "pages/2_CRISPR_Design.py"),
    (3, "3. Transfection & Bulk QC", "pages/3_Transfection_Bulk_QC.py"),
    (4, "4. Single-Cell Cloning & Screening", "pages/4_Single_Cell_Cloning_and_Screening.py"),
    (5, "5. Dual-Clone QC", "pages/5_Dual_Clone_QC.py"),
    (6, "6. Master Bank & CoA", "pages/6_Master_Bank_and_CoA.py"),
]


def is_stage_completed(meta: dict, stage_num: int) -> bool:
    """Determine whether a given pipeline stage has been completed."""
    if not meta:
        return False
    if stage_num == 1:
        return bool(meta.get("stage_1_locked_at") or meta.get("target_gene"))
    elif stage_num == 2:
        return bool(meta.get("design_locked_at") or meta.get("design_hash"))
    elif stage_num == 3:
        return bool(meta.get("stage3_bulk_qc_hash") or meta.get("transfection") or meta.get("bulk_qc_hash"))
    elif stage_num == 4:
        return bool(meta.get("cloning_locked_at") or meta.get("cloning_hash") or meta.get("screening_hash"))
    elif stage_num == 5:
        return bool(meta.get("stage5_qc_hash") or meta.get("dual_clone_qc"))
    elif stage_num == 6:
        return bool(meta.get("final_release_hash") or meta.get("coa_hash"))
    return False


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

    completed_count = sum(1 for s in range(1, 7) if is_stage_completed(meta, s))
    pct = int((completed_count / 6.0) * 100)

    # Compact header banner
    col_proj, col_tpp, col_deliv = st.columns([1.4, 1.4, 1.2])
    with col_proj:
        st.markdown(f"**Project:** `{proj_display}` &nbsp;|&nbsp; **Target:** `{gene}` ({edit_type})")
    with col_tpp:
        st.markdown(f"**Parent Line:** `{cell_line}` &nbsp;|&nbsp; **Progress:** `{completed_count}/6` ({pct}%)")
    with col_deliv:
        badge_bg = "#ecfdf3" if completed_count == 6 else "#eff8ff"
        badge_fg = "#027a48" if completed_count == 6 else "#175cd3"
        badge_border = "#a6f4c5" if completed_count == 6 else "#b2ddff"
        st.markdown(
            f"<div style='text-align: right;'><span style='background-color:{badge_bg}; color:{badge_fg}; padding:3px 8px; border-radius:12px; font-size:0.8rem; font-weight:600; border:1px solid {badge_border};'>🎯 Deliverable: 2 Clones</span></div>",
            unsafe_allow_html=True,
        )

    # 6-Stage Stepper
    cols = st.columns(6)
    for idx, (num, label, page_link) in enumerate(STAGES):
        with cols[idx]:
            is_active = (current_stage == num)
            completed = is_stage_completed(meta, num)

            if is_active:
                bg = "#027a48"
                fg = "#ffffff"
                border = "#054f31"
                weight = "700"
                icon = "✓ " if completed else "▶ "
                shadow = "0 2px 4px rgba(2,122,72,0.25)"
            elif completed:
                bg = "#ecfdf3"
                fg = "#027a48"
                border = "#a6f4c5"
                weight = "600"
                icon = "✓ "
                shadow = "none"
            else:
                bg = "#f8f9fa"
                fg = "#667085"
                border = "#e4e7ec"
                weight = "500"
                icon = "○ "
                shadow = "none"

            st.markdown(
                f"""
                <div style="background-color:{bg}; color:{fg}; border:1px solid {border}; border-radius:6px; padding:6px 3px; text-align:center; font-size:0.75rem; font-weight:{weight}; line-height:1.2; box-shadow:{shadow}; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="{label}">
                    {icon}{label}
                </div>
                """,
                unsafe_allow_html=True,
            )

    if subtitle:
        st.caption(subtitle)
    st.divider()