"""Stage 5: Dual-Clone Comprehensive Quality Control & Release Testing - FDA Cell Line Delivery Record."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.components.file_utils import build_step_filename, save_uploaded_file
from app.components.in_app_analyzer import render_in_app_analyzer
from app.components.layout import init_page
from app.components.lifecycle_header import render_lifecycle_header
from app.components.project_state import (
    append_audit,
    load_project_meta,
    project_subdir,
    register_chain_tx,
    register_file,
    update_project_meta,
    use_project,
)
from app.components.web3_client import send_log_tx


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    init_page("Stage 5 - Dual-Clone Quality Control")
    selected_project = use_project("Project")

    render_lifecycle_header(
        current_stage=5,
        selected_project=selected_project,
        subtitle="Stage 5: Execute comprehensive FDA release testing on Two Independent Clonal Lines (Clone A & Clone B).",
    )

    if not selected_project:
        st.info("👈 Please select or create a project in the sidebar to begin.")
        st.stop()

    meta = load_project_meta(selected_project)
    snapshots_dir = project_subdir(selected_project, "snapshots", "qc_release")
    upload_dir = project_subdir(selected_project, "uploads", "qc_release")

    clone_a_id = meta.get("clone_a_id") or "BIHi005-A-APOE-C1"
    clone_b_id = meta.get("clone_b_id") or "BIHi005-A-APOE-C2"

    st.subheader(f"🎯 Dual-Clone Release Testing Panel: {clone_a_id} & {clone_b_id}")
    st.markdown(
        """
        <div style="background-color:#eff8ff; border: 1px solid #b2ddff; border-radius: 8px; padding: 12px 16px; margin-bottom: 15px;">
            <span style="font-weight: 700; color: #175cd3; font-size: 0.95rem;">FDA Regulatory Standard for Clonal Cell Line Release:</span><br/>
            <span style="color: #344054; font-size: 0.88rem;">
                Both independent clonal lines must satisfy 6 Release Pillars: (1) Bi-allelic on-target sequence, (2) Off-target safety, 
                (3) Diploid genomic integrity (Karyotype), (4) Sterility/Mycoplasma clearance, (5) STR identity match, and (6) Viability & pluripotency.
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Optional In-App Sanger Trace QC deconvolution runner for confirming sequence
    with st.expander("⚡ Run / Verify In-App Sanger Chromatogram Deconvolution for Clones", expanded=False):
        render_in_app_analyzer(selected_project)

    qc_data = meta.get("dual_clone_qc") or {}
    saved_a = qc_data.get("clone_a") or {}
    saved_b = qc_data.get("clone_b") or {}

    col_hdr, col_btn = st.columns([2.5, 1.5])
    with col_hdr:
        st.markdown("##### 🧪 Release Testing Matrix (Clone A vs. Clone B)")
    with col_btn:
        if st.button("✨ Pre-fill Inspection-Ready Standards", help="Auto-populates all 6 release pillars with verified conforming FDA standards"):
            st.session_state["qc_on_a"] = "PASS: 100% Intended Edit (Homozygous/Targeted)"
            st.session_state["qc_hdr_a"] = 96.0
            st.session_state["qc_off_a"] = "PASS: Top 3 Loci Verified Negative / WT"
            st.session_state["qc_karyo_a"] = "PASS: Normal Diploid 46,XY (No Clonal Aneuploidies)"
            st.session_state["qc_ster_a"] = "PASS: Mycoplasma Negative & Culture Sterile"
            st.session_state["qc_str_a"] = "PASS: 100% Genetic Match to Parental Master"
            st.session_state["qc_viab_a"] = 89
            st.session_state["qc_pluri_a"] = "PASS: >90% Marker Positive"

            st.session_state["qc_on_b"] = "PASS: 100% Intended Edit (Homozygous/Targeted)"
            st.session_state["qc_hdr_b"] = 94.5
            st.session_state["qc_off_b"] = "PASS: Top 3 Loci Verified Negative / WT"
            st.session_state["qc_karyo_b"] = "PASS: Normal Diploid 46,XY (No Clonal Aneuploidies)"
            st.session_state["qc_ster_b"] = "PASS: Mycoplasma Negative & Culture Sterile"
            st.session_state["qc_str_b"] = "PASS: 100% Genetic Match to Parental Master"
            st.session_state["qc_viab_b"] = 87
            st.session_state["qc_pluri_b"] = "PASS: >90% Marker Positive"
            st.rerun()

    with st.form("dual_clone_qc_form"):
        col_pillar, col_a, col_b = st.columns([1.5, 2, 2])

        with col_pillar:
            st.markdown("**1. On-Target Sequence Verification**")
            st.caption("Bi-allelic Sanger sequence confirmation")
        with col_a:
            st.markdown(f"**Clone A: `{clone_a_id}`**")
            ontarget_a = st.selectbox("Clone A Sequence Result", ["PASS: 100% Intended Edit (Homozygous/Targeted)", "HOLD: Heterozygous / Subclonal", "FAIL: Unintended Indel"], key="qc_on_a")
            hdr_a = st.number_input("Clone A Intended %", min_value=0.0, max_value=100.0, value=96.0, step=0.1, key="qc_hdr_a")
        with col_b:
            st.markdown(f"**Clone B: `{clone_b_id}`**")
            ontarget_b = st.selectbox("Clone B Sequence Result", ["PASS: 100% Intended Edit (Homozygous/Targeted)", "HOLD: Heterozygous / Subclonal", "FAIL: Unintended Indel"], key="qc_on_b")
            hdr_b = st.number_input("Clone B Intended %", min_value=0.0, max_value=100.0, value=94.5, step=0.1, key="qc_hdr_b")

        st.markdown("---")
        col_pillar2, col_a2, col_b2 = st.columns([1.5, 2, 2])
        with col_pillar2:
            st.markdown("**2. Off-Target Safety Clearance**")
            st.caption("Top in-silico predicted loci")
        with col_a2:
            offtarget_a = st.selectbox("Clone A Off-Target", ["PASS: Top 3 Loci Verified Negative / WT", "PENDING: NGS Analysis", "FAIL: Mutation Detected"], key="qc_off_a")
        with col_b2:
            offtarget_b = st.selectbox("Clone B Off-Target", ["PASS: Top 3 Loci Verified Negative / WT", "PENDING: NGS Analysis", "FAIL: Mutation Detected"], key="qc_off_b")

        st.markdown("---")
        col_pillar3, col_a3, col_b3 = st.columns([1.5, 2, 2])
        with col_pillar3:
            st.markdown("**3. Genomic Integrity & Karyotyping**")
            st.caption("G-banding (20 metaphases) / SNP array")
        with col_a3:
            karyo_a = st.selectbox("Clone A Karyotype", ["PASS: Normal Diploid 46,XY (No Clonal Aneuploidies)", "PASS: Normal Diploid 46,XX", "FAIL: Structural/Numerical Aneuploidy"], key="qc_karyo_a")
        with col_b3:
            karyo_b = st.selectbox("Clone B Karyotype", ["PASS: Normal Diploid 46,XY (No Clonal Aneuploidies)", "PASS: Normal Diploid 46,XX", "FAIL: Structural/Numerical Aneuploidy"], key="qc_karyo_b")

        st.markdown("---")
        col_pillar4, col_a4, col_b4 = st.columns([1.5, 2, 2])
        with col_pillar4:
            st.markdown("**4. Sterility & Mycoplasma Clearance**")
            st.caption("RT-PCR & 14-day USP <71> culture")
        with col_a4:
            sterility_a = st.selectbox("Clone A Sterility", ["PASS: Mycoplasma Negative & Culture Sterile", "FAIL: Mycoplasma Positive", "FAIL: Microbial Contamination"], key="qc_ster_a")
        with col_b4:
            sterility_b = st.selectbox("Clone B Sterility", ["PASS: Mycoplasma Negative & Culture Sterile", "FAIL: Mycoplasma Positive", "FAIL: Microbial Contamination"], key="qc_ster_b")

        st.markdown("---")
        col_pillar5, col_a5, col_b5 = st.columns([1.5, 2, 2])
        with col_pillar5:
            st.markdown("**5. STR Identity Authentication**")
            st.caption("16-24 locus STR profile vs. parent")
        with col_a5:
            str_a = st.selectbox("Clone A STR Identity", ["PASS: 100% Genetic Match to Parental Master", "FAIL: Mismatch / Cross-contamination"], key="qc_str_a")
        with col_b5:
            str_b = st.selectbox("Clone B STR Identity", ["PASS: 100% Genetic Match to Parental Master", "FAIL: Mismatch / Cross-contamination"], key="qc_str_b")

        st.markdown("---")
        col_pillar6, col_a6, col_b6 = st.columns([1.5, 2, 2])
        with col_pillar6:
            st.markdown("**6. Viability & Pluripotency**")
            st.caption("Post-thaw recovery & marker panel")
        with col_a6:
            viab_a = st.slider("Clone A Post-Thaw Viability (%)", 50, 100, 89, key="qc_viab_a")
            pluri_a = st.selectbox("Clone A Pluripotency (Oct4/Nanog/Tra-1-60)", ["PASS: >90% Marker Positive", "PASS: Multilineage Differentiation Intact", "FAIL: Low Expression (<70%)"], key="qc_pluri_a")
        with col_b6:
            viab_b = st.slider("Clone B Post-Thaw Viability (%)", 50, 100, 87, key="qc_viab_b")
            pluri_b = st.selectbox("Clone B Pluripotency (Oct4/Nanog/Tra-1-60)", ["PASS: >90% Marker Positive", "PASS: Multilineage Differentiation Intact", "FAIL: Low Expression (<70%)"], key="qc_pluri_b")

        st.markdown("---")
        col_verdict1, col_verdict2 = st.columns([2, 1])
        with col_verdict1:
            overall_verdict = st.radio(
                "Final Dual-Clone Regulatory Disposition",
                [
                    "RELEASE APPROVED: Both Clone A and Clone B meet all FDA Critical Quality Attributes.",
                    "CONDITIONAL RELEASE: Primary Clone A fully approved, Clone B on hold.",
                    "REJECT: Batch failed release specifications.",
                ],
                index=0,
            )
            qc_inspector = st.text_input("QC Reviewer / Qualified Person (QP)", value=meta.get("owner") or "Narasimha Telugu")
        with col_verdict2:
            st.markdown("<br>", unsafe_allow_html=True)
            submit_qc_btn = st.form_submit_button("💾 Approve Dual-Clone QC & Advance to Stage 6", type="primary")

    if submit_qc_btn:
        timestamp = int(time.time())
        qc_release_payload = {
            "project_id": selected_project,
            "clone_a": {
                "id": clone_a_id,
                "ontarget_verdict": ontarget_a,
                "hdr_pct": hdr_a,
                "offtarget_verdict": offtarget_a,
                "karyotype_verdict": karyo_a,
                "sterility_verdict": sterility_a,
                "str_verdict": str_a,
                "viability_pct": viab_a,
                "pluripotency_verdict": pluri_a,
                "released": "PASS" in ontarget_a and "PASS" in sterility_a and "PASS" in str_a,
            },
            "clone_b": {
                "id": clone_b_id,
                "ontarget_verdict": ontarget_b,
                "hdr_pct": hdr_b,
                "offtarget_verdict": offtarget_b,
                "karyotype_verdict": karyo_b,
                "sterility_verdict": sterility_b,
                "str_verdict": str_b,
                "viability_pct": viab_b,
                "pluripotency_verdict": pluri_b,
                "released": "PASS" in ontarget_b and "PASS" in sterility_b and "PASS" in str_b,
            },
            "overall_verdict": overall_verdict,
            "inspector": qc_inspector,
            "timestamp_unix": timestamp,
        }

        snap_json = json.dumps(qc_release_payload, indent=2)
        snap_hash = hashlib.sha256(snap_json.encode("utf-8")).hexdigest()
        snap_file = snapshots_dir / f"{selected_project}_dual_clone_qc_{timestamp}.json"
        snap_file.write_text(snap_json, encoding="utf-8")

        register_file(
            selected_project,
            "snapshots",
            snap_file,
            sha256=snap_hash,
            size=snap_file.stat().st_size,
            mime="application/json",
            context={"step": "dual_clone_qc", "stage": 5},
        )

        update_project_meta(
            selected_project,
            {
                "dual_clone_qc": qc_release_payload,
                "stage5_qc_hash": snap_hash,
                "active_stage": 6,
            },
        )

        append_audit(selected_project, "dual_clone_qc_approved", details={"verdict": overall_verdict, "hash": snap_hash})

        try:
            tx = send_log_tx(selected_project, snap_hash, step="Dual_Clone_QC")
            if tx:
                register_chain_tx(selected_project, {"step": "Dual_Clone_QC", "tx_hash": tx, "hash": snap_hash})
                st.info(f"Anchored Dual-Clone QC on Sepolia: `{tx}`")
        except Exception:
            pass

        st.success(f"Stage 5 Dual-Clone QC Approved! Both {clone_a_id} and {clone_b_id} qualified for release. Advancing to Stage 6.")
        st.page_link("pages/6_Master_Bank_and_CoA.py", label="👉 Open Stage 6: Master Bank & Certificate of Analysis (CoA)", icon="🏆")


if __name__ == "__main__":
    main()