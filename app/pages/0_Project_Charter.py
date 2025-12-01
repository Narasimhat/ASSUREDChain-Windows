import hashlib
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.components.file_utils import build_step_filename, save_uploaded_file, snapshot_to_pdf
from app.components.layout import init_page
from app.components.project_state import (
    load_manifest,
    load_project_meta,
    project_subdir,
    register_file,
    update_project_meta,
    use_project,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return date.today()


def _render_charter_summary(meta: Dict[str, object]) -> Dict[str, object]:
    return {
        "objective": meta.get("objective", ""),
        "scope": meta.get("scope", ""),
        "success_criteria": meta.get("success_criteria", ""),
        "edit_program": meta.get("edit_program", ""),
        "cell_line": meta.get("cell_line", ""),
        "compliance_scope": meta.get("compliance_scope", ""),
        "target_milestone": meta.get("target_milestone", ""),
        "owner": meta.get("owner", ""),
        "disease_relevance": meta.get("disease_relevance", ""),
    }


def main() -> None:
    init_page("Project Charter")
    selected_project = use_project("Project")
    st.title("Project Charter")
    st.caption("Define the project objective, scope, and success criteria before entering the ASSURED workflow.")

    if not selected_project:
        st.info("Select or create a project in the sidebar to edit its charter.")
        st.stop()

    meta = load_project_meta(selected_project)
    charter_dir = project_subdir(selected_project, "uploads", "charter")
    reports_dir = project_subdir(selected_project, "reports", "charter")

    st.subheader("Charter details")
    default_objective = meta.get("objective", "")
    default_scope = meta.get("scope", "")
    default_success = meta.get("success_criteria", "")
    default_program = meta.get("edit_program", "SNP Knock-in")
    default_cell_line = meta.get("cell_line", meta.get("meta_cell_line", ""))
    default_compliance = meta.get("compliance_scope", "Research only")
    default_owner = meta.get("owner", "")
    default_milestone = _parse_date(meta.get("target_milestone"))
    default_disease = meta.get("disease_relevance", "")

    with st.form("project_charter_form"):
        objective = st.text_area(
            "Objective / hypothesis",
            value=default_objective,
            placeholder="Summarise the scientific or operational goal (e.g., Validate SORCS1 SNP knock-in for QC panel).",
        )
        col1, col2 = st.columns(2)
        with col1:
            edit_program = st.selectbox(
                "Edit program",
                [
                    "SNP Knock-in",
                    "Base Editing",
                    "Prime Editing",
                    "Knockout / Frameshift",
                    "Gene Tagging",
                    "Other",
                ],
                index=0 if default_program not in {None, ""} else 0,
            )
            cell_line = st.text_input("Primary cell line", value=default_cell_line)
            owner = st.text_input("Project owner / lead scientist", value=default_owner)
            disease_relevance = st.text_input("Disease relevance", value=default_disease, placeholder="e.g., Alzheimer's disease, Cancer")
        with col2:
            compliance_scope = st.selectbox(
                "Compliance scope",
                [
                    "Research only",
                    "Preclinical (GLP)",
                    "Clinical (GMP)",
                    "Regulatory (LAGESO)",
                    "Other",
                ],
                index=0,
            )
            target_milestone = st.date_input("Target milestone date", value=default_milestone)
        scope = st.text_area(
            "Scope & constraints",
            value=default_scope,
            placeholder="Highlight experimental scope, key variables, known risks, and dependencies.",
        )
        success = st.text_area(
            "Success criteria",
            value=default_success,
            placeholder="Define measurable outcomes (e.g., >=70% edit rate across ≥3 clones, mycoplasma negative).",
        )

        submit_charter = st.form_submit_button("Save charter")

    if submit_charter:
        updates = {
            "objective": objective.strip(),
            "scope": scope.strip(),
            "success_criteria": success.strip(),
            "edit_program": edit_program,
            "cell_line": cell_line.strip(),
            "compliance_scope": compliance_scope,
            "owner": owner.strip(),
            "target_milestone": target_milestone.isoformat(),
            "disease_relevance": disease_relevance.strip(),
        }
        update_project_meta(selected_project, updates)
        meta = load_project_meta(selected_project)
        
        # Create charter snapshot as JSON
        charter_snapshots_dir = project_subdir(selected_project, "snapshots", "charter")
        timestamp = int(time.time())
        snapshot_payload = {
            "project_id": selected_project,
            "timestamp_unix": timestamp,
            **updates
        }
        snapshot_filename = f"{selected_project}_{timestamp}_{_sha256_bytes(json.dumps(snapshot_payload).encode())[:12]}.json"
        snapshot_path = charter_snapshots_dir / snapshot_filename
        snapshot_path.write_text(json.dumps(snapshot_payload, indent=2), encoding="utf-8")
        
        register_file(
            selected_project,
            "snapshots",
            {
                "step": "charter",
                "path": str(snapshot_path),
                "timestamp": timestamp,
                "digest": _sha256_bytes(snapshot_path.read_bytes()),
            },
        )
        
        st.success("Project charter updated and snapshot saved.")

    st.subheader("Charter summary")
    summary_payload = _render_charter_summary(meta)
    st.json(summary_payload, expanded=True)

    st.subheader("Attachments")
    charter_uploads = st.file_uploader(
        "Upload charter documents (protocols, approvals, slides)",
        accept_multiple_files=True,
        key="charter_uploads",
    )
    if charter_uploads:
        for upload in charter_uploads:
            saved_path = save_uploaded_file(
                selected_project,
                upload,
                charter_dir,
                category="uploads",
                context={"step": "charter"},
            )
            register_file(
                selected_project,
                "uploads",
                {
                    "step": "charter",
                    "filename": upload.name,
                    "path": str(saved_path),
                    "timestamp": int(time.time()),
                    "type": Path(upload.name).suffix.lower().lstrip("."),
                },
            )
            st.success(f"Uploaded {upload.name}")

    manifest = load_manifest(selected_project)
    charter_files = [
        entry
        for entry in manifest.get("files", {}).get("uploads", [])
        if isinstance(entry, dict) and entry.get("step") == "charter"
    ]
    if charter_files:
        st.caption("Existing charter attachments:")
        for entry in charter_files:
            st.write(f"- {entry.get('filename')} ({entry.get('path')})")

    st.subheader("Export charter")
    export_payload = {
        "project_id": selected_project,
        "generated_at": int(time.time()),
        "charter": summary_payload,
        "attachments": [entry.get("filename") for entry in charter_files],
    }
    if st.button("Generate charter PDF"):
        pdf_filename = build_step_filename(selected_project, "charter", export_payload["generated_at"])
        pdf_path = reports_dir / pdf_filename
        snapshot_to_pdf(export_payload, pdf_path, f"Project Charter - {selected_project}")
        register_file(
            selected_project,
            "reports",
            {
                "step": "charter",
                "path": str(pdf_path),
                "timestamp": export_payload["generated_at"],
                "digest": _sha256_bytes(pdf_path.read_bytes()),
                "type": "pdf",
            },
        )
        with pdf_path.open("rb") as handle:
            st.download_button(
                "Download charter PDF",
                handle,
                file_name=pdf_path.name,
                key="charter_pdf_download",
            )
        st.success(f"Charter PDF saved: {pdf_path}")


if __name__ == "__main__":
    main()
