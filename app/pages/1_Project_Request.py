"""Stage 1: Project Request & Target Product Profile (TPP) - FDA Cell Line Delivery Record."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.components.file_utils import build_step_filename, save_uploaded_file, snapshot_to_pdf
from app.components.layout import init_page
from app.components.lifecycle_header import render_lifecycle_header
from app.components.project_state import (
    ensure_dirs,
    load_project_meta,
    normalize_path_for_storage,
    project_subdir,
    register_file,
    update_project_meta,
    use_project,
)
from app.components.web3_client import send_log_tx


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return date.today()


def main() -> None:
    init_page("Stage 1 - Project Request & TPP")
    selected_project = use_project("Project")

    render_lifecycle_header(
        current_stage=1,
        selected_project=selected_project,
        subtitle="Stage 1: Define project requirements, target edit, and acceptance criteria for two delivered clones.",
    )

    if not selected_project:
        st.info("👈 Please select or create a project in the sidebar to begin.")
        st.stop()

    meta = load_project_meta(selected_project)
    reports_dir = project_subdir(selected_project, "reports", "charter")
    snapshots_dir = project_subdir(selected_project, "snapshots", "charter")

    st.subheader("📋 Project Request & Target Product Profile")

    with st.form("project_request_form"):
        # Section 1: Gene Target & Modification
        col_gene, col_edit, col_mut = st.columns(3)
        with col_gene:
            target_gene = st.text_input(
                "Target Gene Symbol *",
                value=meta.get("target_gene") or meta.get("gene") or "APOE",
                help="Official HGNC gene symbol (e.g. APOE, TP53, SORCS1).",
            )
        with col_edit:
            edit_program = st.selectbox(
                "Edit Modality *",
                [
                    "SNP Knock-in (ssODN)",
                    "Gene Knockout (Indel / Deletion)",
                    "Terminal Tag (FLAG, HA, GFP)",
                    "Base Editing (ABE/CBE)",
                    "Prime Editing",
                ],
                index=0,
                help="CRISPR genome editing mechanism.",
            )
        with col_mut:
            mutation_label = st.text_input(
                "Desired Edit / Mutation *",
                value=meta.get("mutation_label") or meta.get("mutation") or "R176C",
                help="Specific amino acid/nucleotide change or knockout intent (e.g. R176C, Exon 4 KO).",
            )

        # Section 2: Parental Cell Line & Origin
        col_cell, col_passage, col_str = st.columns(3)
        with col_cell:
            cell_line = st.text_input(
                "Parental Cell Line *",
                value=meta.get("cell_line") or "BIHi005-A",
                help="Certified master or donor line name (e.g. BIHi005-A, H9, K562).",
            )
        with col_passage:
            parental_passage = st.number_input(
                "Parental Passage Number",
                min_value=1,
                max_value=100,
                value=int(meta.get("parental_passage") or 14),
            )
        with col_str:
            parental_str_status = st.selectbox(
                "Parental STR Authentication",
                ["Verified (100% Profile on Record)", "Pending Testing", "External Repository Certificate"],
                index=0,
            )

        # Section 3: Deliverable Specifications (FDA 2-Clone Standard)
        st.markdown("##### 🎯 FDA Release Specifications: Two Independent Validated Clones")
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            st.text_input("Primary Target Clone Name", value=meta.get("clone_a_id") or f"{cell_line}-{target_gene}-C1")
        with col_c2:
            st.text_input("Backup Target Clone Name", value=meta.get("clone_b_id") or f"{cell_line}-{target_gene}-C2")

        col_qc1, col_qc2, col_qc3 = st.columns(3)
        with col_qc1:
            req_ontarget = st.checkbox("Bi-allelic On-Target Sequence Confirmation", value=True, disabled=True)
            req_karyo = st.checkbox("Normal Diploid Karyotype (G-Banding/SNP)", value=True)
        with col_qc2:
            req_myco = st.checkbox("Sterility & Mycoplasma Clearance (Pass)", value=True, disabled=True)
            req_str = st.checkbox("STR Identity Match (100% to Parent)", value=True, disabled=True)
        with col_qc3:
            req_pluri = st.checkbox("Pluripotency / Viability > 80%", value=True)
            req_offtarget = st.checkbox("Top Predicted Off-Targets Verified Clean", value=True)

        # Section 4: Operational & Sponsor Details
        col_owner, col_due, col_gxp = st.columns(3)
        with col_owner:
            owner = st.text_input("Principal Investigator / Lead", value=meta.get("owner") or "Narasimha Telugu")
        with col_due:
            target_milestone = st.date_input("Target Release Date", value=parse_date(meta.get("target_milestone")))
        with col_gxp:
            compliance_tier = st.selectbox(
                "Compliance Tier",
                ["GLP / GMP-Compatible Research", "IND-Enabling Phase 1 Spec", "Standard R&D"],
                index=0,
            )

        objective = st.text_area(
            "Project Scope & Clinical / Research Hypothesis",
            value=meta.get("objective") or f"Derive two independent validated clonal lines carrying {target_gene} {mutation_label} with comprehensive QC.",
            height=70,
        )

        submit = st.form_submit_button("💾 Lock Project Request & Advance to Stage 2", type="primary")

    if submit:
        timestamp = int(time.time())
        updated_meta = {
            "target_gene": target_gene.strip(),
            "gene": target_gene.strip(),
            "edit_program": edit_program,
            "edit_type": edit_program,
            "mutation_label": mutation_label.strip(),
            "mutation": mutation_label.strip(),
            "cell_line": cell_line.strip(),
            "parental_passage": int(parental_passage),
            "parental_str_status": parental_str_status,
            "clone_a_id": f"{cell_line}-{target_gene}-C1",
            "clone_b_id": f"{cell_line}-{target_gene}-C2",
            "owner": owner.strip(),
            "target_milestone": target_milestone.isoformat(),
            "compliance_tier": compliance_tier,
            "objective": objective.strip(),
            "stage_1_locked_at": timestamp,
            "active_stage": 2,
        }
        update_project_meta(selected_project, updated_meta)

        # Save machine-readable snapshot
        snap_file = snapshots_dir / f"{selected_project}_stage1_request_{timestamp}.json"
        snap_file.write_text(json.dumps(updated_meta, indent=2), encoding="utf-8")
        register_file(
            selected_project,
            "snapshots",
            snap_file,
            sha256=sha256_bytes(snap_file.read_bytes()),
            size=snap_file.stat().st_size,
            mime="application/json",
            context={"step": "charter", "stage": 1},
        )

        st.success(f"Project Request locked! Target: {target_gene} {mutation_label} in {cell_line}. Proceeding to Stage 2.")
        st.page_link("pages/2_CRISPR_Design.py", label="👉 Open Stage 2: CRISPR Design Studio", icon="🧬")


if __name__ == "__main__":
    main()