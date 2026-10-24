"""Stage 4: Single-Cell Cloning & Monoclonality Assurance - FDA Cell Line Delivery Record."""

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
    init_page("Stage 4 - Single-Cell Cloning")
    selected_project = use_project("Project")

    render_lifecycle_header(
        current_stage=4,
        selected_project=selected_project,
        subtitle="Stage 4: Document single-cell deposition, monoclonality proof (FDA CQA), and colony expansion.",
    )

    if not selected_project:
        st.info("👈 Please select or create a project in the sidebar to begin.")
        st.stop()

    meta = load_project_meta(selected_project)
    snapshots_dir = project_subdir(selected_project, "snapshots", "cloning")
    upload_dir = project_subdir(selected_project, "uploads", "cloning")

    st.subheader("🔬 1. Monoclonality Assurance (FDA Critical Quality Attribute)")
    st.markdown(
        """
        <div style="background-color:#f8f9fa; border-left: 4px solid #039855; padding: 10px 14px; margin-bottom: 12px; font-size: 0.9rem;">
            <strong>Regulatory Standard:</strong> High probability of monoclonality is required to eliminate mosaicism. Document single-cell deposition method and optical/imaging evidence.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("monoclonality_form"):
        col_m1, col_m2, col_m3 = st.columns(3)
        with col_m1:
            isolation_method = st.selectbox(
                "Single-Cell Isolation Method *",
                [
                    "isoCell / iOTA Microfluidic Grid",
                    "FACS Single-Cell Index Sorting",
                    "CellenONE / Single-Cell Printer",
                    "Limiting Dilution (<0.5 cells/well)",
                ],
                index=0,
            )
        with col_m2:
            cloning_device = st.text_input("Instrument ID / Model", value="isoCell / iOTA #04")
        with col_m3:
            single_cell_media = st.selectbox("Cloning Media / Matrix", ["E8 + CloneR + Geltrex", "mTeSR Plus + RevitaCell", "Essential 8 Flex"], index=0)

        col_img1, col_img2 = st.columns(2)
        with col_img1:
            optical_proof = st.selectbox(
                "Optical Monoclonality Verification",
                [
                    "Verified: Day 0 Image Confirmed 1 Single Cell per Chamber",
                    "Verified: Automated Whole-Well Scan (Cell Metric)",
                    "Manual Microscopic Verification",
                ],
                index=0,
            )
        with col_img2:
            cloning_date = st.date_input("Single-Cell Deposition Date")

        st.markdown("##### 🧫 2. Expansion Tracking (96-Well to 24-Well)")
        col_p1, col_p2, col_p3 = st.columns(3)
        with col_p1:
            wells_seeded = st.number_input("Wells / Chambers Seeded", min_value=1, max_value=1000, value=96)
        with col_p2:
            colonies_emerged = st.number_input("Viable Single Colonies Emerged", min_value=1, max_value=500, value=34)
        with col_p3:
            cloning_efficiency = round((colonies_emerged / max(wells_seeded, 1)) * 100, 1)
            st.metric("Cloning Outgrowth Efficiency", f"{cloning_efficiency}%")

        st.markdown("##### 🎯 3. Selection of Two Primary Candidates for Stage 5 Release QC")
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            cand_a = st.text_input("Primary Candidate Clone A ID *", value=meta.get("clone_a_id") or "BIHi005-A-APOE-C1")
            cand_a_well = st.text_input("Clone A Origin Well / Grid", value="Plate 1 - Well B04")
        with col_c2:
            cand_b = st.text_input("Backup Candidate Clone B ID *", value=meta.get("clone_b_id") or "BIHi005-A-APOE-C2")
            cand_b_well = st.text_input("Clone B Origin Well / Grid", value="Plate 1 - Well E09")

        cloning_notes = st.text_area(
            "Monoclonality & Expansion Notes",
            value="Single-cell verified at Day 0. Healthy round ES/iPSC colony morphology observed at Day 7. Clones expanded into 24-well plates.",
            height=60,
        )

        col_sub1, col_sub2 = st.columns([1, 1])
        with col_sub1:
            save_cloning_btn = st.form_submit_button("💾 Lock Monoclonality & Advance to Stage 5", type="primary")
        with col_sub2:
            anchor_cloning = st.checkbox("Anchor Monoclonality Proof on Sepolia", value=True)

    if save_cloning_btn:
        timestamp = int(time.time())
        cloning_payload = {
            "project_id": selected_project,
            "isolation_method": isolation_method,
            "device": cloning_device,
            "optical_proof": optical_proof,
            "cloning_date": cloning_date.isoformat(),
            "wells_seeded": int(wells_seeded),
            "colonies_emerged": int(colonies_emerged),
            "cloning_efficiency_pct": cloning_efficiency,
            "clone_a": {"id": cand_a.strip(), "origin": cand_a_well.strip()},
            "clone_b": {"id": cand_b.strip(), "origin": cand_b_well.strip()},
            "notes": cloning_notes,
            "timestamp_unix": timestamp,
        }

        snap_json = json.dumps(cloning_payload, indent=2)
        snap_hash = hashlib.sha256(snap_json.encode("utf-8")).hexdigest()
        snap_file = snapshots_dir / f"{selected_project}_stage4_cloning_{timestamp}.json"
        snap_file.write_text(snap_json, encoding="utf-8")

        register_file(
            selected_project,
            "snapshots",
            snap_file,
            sha256=snap_hash,
            size=snap_file.stat().st_size,
            mime="application/json",
            context={"step": "cloning", "stage": 4},
        )

        update_project_meta(
            selected_project,
            {
                "clone_a_id": cand_a.strip(),
                "clone_b_id": cand_b.strip(),
                "cloning_locked_at": timestamp,
                "cloning_hash": snap_hash,
                "active_stage": 5,
            },
        )

        append_audit(selected_project, "monoclonality_locked", details={"clones": [cand_a, cand_b], "hash": snap_hash})

        if anchor_cloning:
            try:
                tx = send_log_tx(selected_project, snap_hash, step="Cloning")
                if tx:
                    register_chain_tx(selected_project, {"step": "Cloning", "tx_hash": tx, "hash": snap_hash})
                    st.info(f"Anchored Monoclonality Receipt on Sepolia: `{tx}`")
            except Exception:
                pass

        st.success(f"Stage 4 Monoclonality locked for {cand_a} and {cand_b}! Advancing to Stage 5 Dual-Clone QC.")
        st.page_link("pages/5_Dual_Clone_QC.py", label="👉 Open Stage 5: Dual-Clone QC Release Testing", icon="🧪")


if __name__ == "__main__":
    main()