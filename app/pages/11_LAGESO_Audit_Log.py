import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

import streamlit as st

from app.components.layout import init_page

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.components.file_utils import build_step_filename, preview_file, save_uploaded_file, snapshot_to_pdf
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


def evaluate_readiness(payload: Dict, attachments: Dict[str, str]) -> Dict[str, List[str] | bool]:
    issues: List[str] = []
    warnings: List[str] = []

    required_fields = ["facility_name", "permit_number", "biosafety_level", "responsible_person", "review_date"]
    for field in required_fields:
        if not (payload.get(field) or "").strip():
            issues.append(f"{field.replace('_', ' ').title()} is required.")

    if not payload.get("project_contact_email"):
        warnings.append("Project contact email not provided.")
    if not payload.get("risk_classification"):
        warnings.append("Risk classification not specified.")

    if "risk_assessment" not in attachments:
        issues.append("Upload the risk assessment document.")
    if "sop" not in attachments:
        warnings.append("SOP/Work instruction not attached.")
    if payload.get("biosafety_level") in {"S2", "S3", "S4"} and "biosafety_certificate" not in attachments:
        warnings.append("Upload biosafety certificate for higher containment levels.")

    return {"ready": not issues, "issues": issues, "warnings": warnings}


init_page("Step 11 - LAGESO Audit Documentation")
selected_project = use_project("Project")
st.title("Step 11 - LAGESO Audit Documentation")
st.caption("Capture LAGESO-relevant compliance information, bundle evidence, and anchor the record on-chain.")

if not selected_project:
    st.warning("Select a project from the sidebar to begin.")
    st.stop()

project_meta = load_project_meta(selected_project)
compliance_defaults = project_meta.get("compliance", {})


snapshot_dir = project_subdir(selected_project, "snapshots", "lageso")
report_dir = project_subdir(selected_project, "reports", "lageso")
upload_dir = project_subdir(selected_project, "uploads", "lageso")


attachments: Dict[str, str] = {}
saved_attachment_paths: List[Path] = []


with st.form("lageso_audit_form"):
    project_name = st.text_input(
        "Project name/identifier",
        value=compliance_defaults.get("project_name") or selected_project,
    )
    facility_name = st.text_input("Facility / laboratory name", value=compliance_defaults.get("facility_name", ""))
    facility_address = st.text_area("Facility address", value=compliance_defaults.get("facility_address", ""))
    responsible_person = st.text_input(
        "Responsible person (PI / Lab lead)", value=compliance_defaults.get("responsible_person", "")
    )
    project_contact_email = st.text_input(
        "Project contact email", value=compliance_defaults.get("project_contact_email", "")
    )
    biosafety_options = ["S1", "S2", "S3", "S4"]
    default_bsl = biosafety_options.index(compliance_defaults.get("biosafety_level", "S1")) if compliance_defaults.get("biosafety_level", "S1") in biosafety_options else 0
    biosafety_level = st.selectbox("Biosafety level", biosafety_options, index=default_bsl)
    risk_classification = st.text_input(
        "Risk classification (per German genetic engineering law)",
        value=compliance_defaults.get("risk_classification", ""),
    )
    permit_number = st.text_input("Permit / notification number", value=compliance_defaults.get("permit_number", ""))
    review_date = st.text_input(
        "Review / submission date",
        value=compliance_defaults.get("review_date", time.strftime("%Y-%m-%d")),
    )
    inspector_notes = st.text_area("Inspector notes / remarks", value=compliance_defaults.get("inspector_notes", ""))

    st.markdown("### Uploaded evidence")
    col1, col2, col3 = st.columns(3)
    with col1:
        ra_file = st.file_uploader("Risk assessment (PDF)", type=["pdf"])
        if ra_file:
            path = save_uploaded_file(
                selected_project,
                ra_file,
                upload_dir,
                category="uploads",
                context={"step": "lageso", "type": "risk_assessment"},
            )
            attachments["risk_assessment"] = str(path)
            saved_attachment_paths.append(path)
    with col2:
        sop_file = st.file_uploader("SOP / WI (PDF)", type=["pdf"])
        if sop_file:
            path = save_uploaded_file(
                selected_project,
                sop_file,
                upload_dir,
                category="uploads",
                context={"step": "lageso", "type": "sop"},
            )
            attachments["sop"] = str(path)
            saved_attachment_paths.append(path)
    with col3:
        biosafety_file = st.file_uploader("Biosafety certificate", type=["pdf", "png", "jpg"])
        if biosafety_file:
            path = save_uploaded_file(
                selected_project,
                biosafety_file,
                upload_dir,
                category="uploads",
                context={"step": "lageso", "type": "biosafety_certificate"},
            )
            attachments["biosafety_certificate"] = str(path)
            saved_attachment_paths.append(path)

    st.markdown("### Activity summary")
    activities = st.multiselect(
        "Select applicable activities",
        [
            "Design / cloning",
            "Viral packaging",
            "Cell culture",
            "Animal work",
            "Human sample processing",
            "Data analysis / reporting",
        ],
        default=compliance_defaults.get("activities", []),
    )
    emergency_contacts = st.text_area(
        "Emergency contacts (safety officer, LAGESO liaison)",
        value=compliance_defaults.get("emergency_contacts", ""),
    )
    training_notes = st.text_area(
        "Training / qualification notes",
        value=compliance_defaults.get("training_notes", ""),
    )

    submitted = st.form_submit_button("Save LAGESO audit snapshot")


if submitted:
    payload = {
        "project_id": selected_project,
        "project_name": project_name,
        "facility_name": facility_name,
        "facility_address": facility_address,
        "responsible_person": responsible_person,
        "project_contact_email": project_contact_email,
        "biosafety_level": biosafety_level,
        "risk_classification": risk_classification,
        "permit_number": permit_number,
        "review_date": review_date,
        "inspector_notes": inspector_notes,
        "activities": activities,
        "emergency_contacts": emergency_contacts,
        "training_notes": training_notes,
        "attachments": attachments,
        "timestamp_unix": int(time.time()),
    }

    readiness = evaluate_readiness(payload, attachments)
    st.session_state["lageso_readiness"] = readiness

    payload_bytes = json.dumps(payload, indent=2).encode("utf-8")
    digest = sha256_bytes(payload_bytes)
    outfile = snapshot_dir / f"{selected_project}_lageso_{payload['timestamp_unix']}_{digest[:12]}.json"
    outfile.write_bytes(payload_bytes)

    st.session_state["lageso_snapshot"] = {
        "digest": digest,
        "outfile": str(outfile),
        "metadata_uri": outfile.resolve().as_uri(),
        "payload": payload,
    }

    update_project_meta(
        selected_project,
        {
            "compliance": {
                "project_name": project_name,
                "facility_name": facility_name,
                "facility_address": facility_address,
                "responsible_person": responsible_person,
                "project_contact_email": project_contact_email,
                "biosafety_level": biosafety_level,
                "risk_classification": risk_classification,
                "permit_number": permit_number,
                "review_date": review_date,
                "inspector_notes": inspector_notes,
                "activities": activities,
                "emergency_contacts": emergency_contacts,
                "training_notes": training_notes,
            }
        },
    )

    register_file(
        selected_project,
        "snapshots",
        {
            "step": "lageso_audit",
            "path": str(outfile),
            "digest": digest,
            "timestamp": payload["timestamp_unix"],
        },
    )
    append_audit(
        selected_project,
        {
            "ts": int(time.time()),
            "step": "lageso_audit",
            "action": "snapshot_saved",
            "snapshot_path": str(outfile),
        },
    )

    st.success("LAGESO audit snapshot saved.")
    st.code(f"SHA-256: {digest}", language="text")
    st.caption(f"Saved: {outfile}")

    if saved_attachment_paths:
        st.caption("Evidence previews")
        for idx, path in enumerate(saved_attachment_paths):
            preview_file(path, label=path.name, key_prefix=f"lageso_attach_{idx}")

    pdf_filename = build_step_filename(selected_project, "lageso-audit", payload["timestamp_unix"])
    pdf_path = report_dir / pdf_filename
    snapshot_to_pdf(payload, pdf_path, "LAGESO Audit Documentation")
    with pdf_path.open("rb") as handle:
        st.download_button("Download snapshot as PDF", handle, file_name=pdf_path.name)
    register_file(
        selected_project,
        "reports",
        {
            "step": "lageso_audit",
            "path": str(pdf_path),
            "digest": sha256_bytes(pdf_path.read_bytes()),
            "timestamp": payload["timestamp_unix"],
            "type": "pdf",
        },
    )


snapshot_state = st.session_state.get("lageso_snapshot")
readiness_state = st.session_state.get("lageso_readiness")
if snapshot_state:
    st.divider()
    st.subheader("On-chain Anchoring")
    st.code(f"SHA-256: {snapshot_state['digest']}", language="text")
    st.caption(f"Snapshot: {snapshot_state['outfile']}")

    if readiness_state:
        if readiness_state["ready"]:
            st.success("Ready to anchor: all mandatory documentation present.")
        else:
            st.error("Resolve the following issues before anchoring:")
            for issue in readiness_state["issues"]:
                st.write(f"- {issue}")
            if readiness_state["warnings"]:
                st.caption("Warnings:")
                for warning in readiness_state["warnings"]:
                    st.caption(f"Warning: {warning}")
    else:
        st.info("Submit the form to evaluate readiness.")

    anchor_disabled = not readiness_state or not readiness_state["ready"]

    if st.button("Anchor LAGESO snapshot on-chain", disabled=anchor_disabled):
        try:
            result = send_log_tx(
                hex_digest=snapshot_state["digest"],
                step="LAGESO Audit",
                metadata_uri=snapshot_state["metadata_uri"],
            )
        except Exception as exc:
            st.error(f"On-chain anchoring failed: {exc}")
        else:
            st.success("Anchored on-chain.")
            st.write(f"Tx: {result['tx_hash']}")
            st.json(result["receipt"])

            register_chain_tx(
                selected_project,
                {
                    "step": "lageso_audit",
                    "tx_hash": result["tx_hash"],
                    "digest": snapshot_state["digest"],
                    "timestamp": int(time.time()),
                    "metadata_uri": snapshot_state["metadata_uri"],
                },
            )
            append_audit(
                selected_project,
                {
                    "ts": int(time.time()),
                    "step": "lageso_audit",
                    "action": "anchored",
                    "tx_hash": result["tx_hash"],
                },
            )
