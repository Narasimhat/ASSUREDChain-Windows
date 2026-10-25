"""Stage 3: Transfection & Bulk In-Process Control (IPC) - FDA Cell Line Delivery Record."""

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
    init_page("Stage 3 - Transfection & Bulk QC")
    selected_project = use_project("Project")

    render_lifecycle_header(
        current_stage=3,
        selected_project=selected_project,
        subtitle="Stage 3: Document RNP transfection parameters and verify bulk pool editing efficiency before single-cell cloning.",
    )

    if not selected_project:
        st.info("👈 Please select or create a project in the sidebar to begin.")
        st.stop()

    meta = load_project_meta(selected_project)
    snapshots_dir = project_subdir(selected_project, "snapshots", "delivery")
    reports_dir = project_subdir(selected_project, "reports", "delivery")
    upload_dir = project_subdir(selected_project, "uploads", "delivery")

    st.subheader("⚡ 1. Delivery & Electroporation Parameters")
    with st.form("transfection_form"):
        col_dev, col_prog, col_cells = st.columns(3)
        with col_dev:
            device = st.selectbox(
                "Electroporation Platform",
                ["Neon Transfection System (Thermo)", "Lonza 4D-Nucleofector", "MaxCyte STX", "Chemical (Lipofectamine Stem)"],
                index=0,
            )
        with col_prog:
            program_code = st.text_input("Program / Voltage / Pulse", value="1150V, 30ms, 2 pulses")
        with col_cells:
            cell_count = st.number_input("Cell Count Transfected", min_value=10000, max_value=10000000, value=250000, step=10000)

        col_rnp, col_ssodn, col_viab = st.columns(3)
        with col_rnp:
            rnp_amount = st.text_input("Cas9 : sgRNA Molar Ratio", value="1:1.2 (20 pmol Cas9, 24 pmol sgRNA)")
        with col_ssodn:
            donor_amount = st.text_input("Donor Amount (ssODN / dsDNA)", value="50 pmol ssODN")
        with col_viab:
            post_viability = st.slider("Post-Delivery Viability (%)", min_value=10, max_value=100, value=78, step=1)

        col_op, col_lot = st.columns(2)
        with col_op:
            operator = st.text_input("Operator", value=meta.get("owner") or "Narasimha Telugu")
        with col_lot:
            reagent_lots = st.text_input("Reagent Lot Numbers", value="Alt-R Cas9 HiFi v3 #0039218")

        save_delivery_btn = st.form_submit_button("💾 Save Transfection Log")

    if save_delivery_btn:
        timestamp = int(time.time())
        delivery_payload = {
            "device": device,
            "program_code": program_code,
            "cell_count": int(cell_count),
            "rnp_amount": rnp_amount,
            "donor_amount": donor_amount,
            "post_viability_pct": post_viability,
            "operator": operator,
            "reagent_lots": reagent_lots,
            "timestamp_unix": timestamp,
        }
        update_project_meta(selected_project, {"transfection": delivery_payload})
        st.success("Transfection parameters saved to electronic batch record.")

    st.markdown("---")
    st.subheader("🔬 2. In-Process Control (IPC): 48h Bulk Pool Trace Deconvolution")
    st.caption("Verify that the bulk edited cell pool exhibits robust cutting / knock-in efficiency before committing cells to single-cell cloning.")

    # Render In-App Trace Analyzer for the Bulk Pool
    render_in_app_analyzer(selected_project)

    st.markdown("---")
    st.subheader("🏁 3. Bulk In-Process Gate Decision")

    qc_import = st.session_state.get("assured_qc_import") or {}
    total_indel = qc_import.get("total_indel_pct") or 0.0
    hdr_pct = qc_import.get("hdr_pct") or 0.0

    col_gate1, col_gate2 = st.columns([2, 1])
    with col_gate1:
        gate_decision = st.radio(
            "Bulk Editing Gate Verdict",
            [
                "PASS: Editing efficiency verified (>20%). Proceed to Single-Cell Cloning.",
                "HOLD: Low efficiency. Re-evaluate delivery conditions or guide design.",
                "FAIL: No edit detected in pool. Repeat electroporation.",
            ],
            index=0,
        )
        gate_notes = st.text_input("IPC Release Notes", value=f"Bulk Pool QC: Total Indels={total_indel}%, HDR={hdr_pct}%. Pool confirmed edited.")

    with col_gate2:
        st.markdown("<br>", unsafe_allow_html=True)
        advance_stage3 = st.button("🚀 Approve Bulk IPC & Advance to Stage 4", type="primary")

    if advance_stage3:
        timestamp = int(time.time())
        ipc_snapshot = {
            "project_id": selected_project,
            "gate_decision": gate_decision,
            "total_indel_pct": total_indel,
            "hdr_pct": hdr_pct,
            "gate_notes": gate_notes,
            "timestamp_unix": timestamp,
        }
        snap_json = json.dumps(ipc_snapshot, indent=2)
        snap_hash = hashlib.sha256(snap_json.encode("utf-8")).hexdigest()
        snap_file = snapshots_dir / f"{selected_project}_stage3_bulk_qc_{timestamp}.json"
        snap_file.write_text(snap_json, encoding="utf-8")

        register_file(
            selected_project,
            "snapshots",
            snap_file,
            sha256=snap_hash,
            size=snap_file.stat().st_size,
            mime="application/json",
            context={"step": "bulk_qc", "stage": 3},
        )

        update_project_meta(selected_project, {"stage3_bulk_qc_hash": snap_hash, "active_stage": 4})
        append_audit(selected_project, "bulk_qc_passed", details={"gate": gate_decision, "hash": snap_hash})

        try:
            tx = send_log_tx(selected_project, snap_hash, step="Bulk_QC")
            if tx:
                register_chain_tx(selected_project, {"step": "Bulk_QC", "tx_hash": tx, "hash": snap_hash})
                st.info(f"Anchored on Sepolia: `{tx}`")
        except Exception:
            pass

        st.success("Stage 3 Approved! Advancing to Stage 4 Single-Cell Cloning & Screening.")
        st.page_link("pages/4_Single_Cell_Cloning_and_Screening.py", label="👉 Open Stage 4: Single-Cell Cloning & Screening", icon="🧫")


if __name__ == "__main__":
    main()