import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Literal, Optional
import re

import streamlit as st
from pydantic import BaseModel, Field, ValidationError

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.components.file_utils import build_step_filename, preview_file, save_uploaded_file, snapshot_to_pdf
from app.components.layout import init_page
from app.components.project_state import (
    append_audit,
    project_subdir,
    register_chain_tx,
    register_file,
    use_project,
)
from app.components.web3_client import send_log_tx

FALLBACK_DATA_DIR = ROOT_DIR / "data" / "delivery_logs"
FALLBACK_UPLOAD_DIR = FALLBACK_DATA_DIR / "attachments"
FALLBACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
FALLBACK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class Component(BaseModel):
    name: str = Field(..., description="e.g., Cas9 NLS, sgRNA1, ssODN")
    component_type: Literal["Cas9", "sgRNA", "ssODN", "Buffer", "Additive", "Other"]
    concentration: Optional[float] = None
    concentration_units: Optional[Literal["uM", "mg/mL", "ng/uL", "X", "Other"]] = None
    volume_ul: Optional[float] = Field(default=None, description="Volume used (uL)")
    vendor: Optional[str] = None
    lot_number: Optional[str] = None


class DeliveryLog(BaseModel):
    project_id: str
    cell_line: str
    passage_number: Optional[str] = None
    delivery_goal: Literal["RNP", "Plasmid", "mRNA", "Other"] = "RNP"
    total_cells: int = Field(..., description="Total cells prepared for electroporation")
    viability_percent: Optional[float] = None

    electroporation_device: str = "Neon Transfection System"
    neon_tip_volume_ul: float = 10.0
    voltage_v: int = 1200
    pulse_width_ms: float = 30.0
    pulse_number: int = 1

    buffer_system: str = "Buffer R (Neon kit)"
    buffer_notes: Optional[str] = None

    rnp_components: list[Component]
    assembly_notes: Optional[str] = None
    incubation_time_minutes: Optional[float] = None

    post_pulse_recovery_medium: str = "Pre-warmed complete medium"
    post_pulse_recovery_volume_ml: float = 0.5
    recovery_temperature_c: float = 37.0
    recovery_duration_minutes: float = 10.0
    plating_format: str = "24-well plate"
    plating_density_cells_per_well: Optional[int] = None
    additional_notes: Optional[str] = None

    author: str
    timestamp_unix: int


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def evaluate_delivery_readiness(payload: dict, attachments_map: dict) -> dict:
    issues: list[str] = []
    warnings: list[str] = []

    components = payload.get("rnp_components") or []
    if not components:
        issues.append("List the RNP/mix components (Cas9, sgRNA, donor, buffers).")
    else:
        component_types = {comp.get("component_type") for comp in components}
        if payload.get("delivery_goal") == "RNP":
            if "Cas9" not in component_types:
                issues.append("Add a Cas9 component for RNP delivery.")
            if "sgRNA" not in component_types:
                issues.append("Add at least one sgRNA component for RNP delivery.")
        for comp in components:
            name = comp.get("name") or comp.get("component_type") or "component"
            lot_number = (comp.get("lot_number") or "").strip()
            if lot_number:
                if not re.fullmatch(r"[A-Za-z0-9._-]{4,}", lot_number):
                    warnings.append(f"{name}: lot '{lot_number}' contains unsupported characters.")
            else:
                warnings.append(f"{name}: lot number missing.")
            if comp.get("concentration") is None:
                warnings.append(f"{name}: concentration not recorded.")

    if payload.get("total_cells", 0) <= 0:
        issues.append("Total cells must be greater than zero.")
    if payload.get("viability_percent") is not None and payload["viability_percent"] < 50:
        warnings.append("Viability is below 50%; confirm this is expected.")

    attachments_present = bool(attachments_map)
    if not attachments_present:
        warnings.append("No delivery attachments provided (Neon export, plate images, etc.).")

    return {"ready": not issues, "issues": issues, "warnings": warnings}

init_page("Step 2 - Delivery Logger")
selected_project = use_project("project")
st.title("Step 2 - Delivery Logger")
st.caption(
    "Capture Neon electroporation metadata (1200 V / 30 ms / 1 pulse) to mirror the Delivery section of the ASSURED protocol."
)

attachments: dict[str, str] = {}
saved_attachment_paths: list[Path] = []

if selected_project:
    snapshot_dir = project_subdir(selected_project, "snapshots", "delivery")
    upload_dir = project_subdir(selected_project, "uploads", "delivery")
    report_dir = project_subdir(selected_project, "reports", "delivery")
else:
    snapshot_dir = FALLBACK_DATA_DIR
    upload_dir = FALLBACK_UPLOAD_DIR
    report_dir = FALLBACK_DATA_DIR


with st.form("delivery_form"):
    project_id = st.text_input(
        "Project ID",
        value=selected_project or "SORCS1_KI_v1",
        help="Defaults to the project selected in the sidebar.",
    )
    cell_line = st.text_input("Cell line", value="hiPSC clone 7")
    passage_number = st.text_input("Passage number", value="P28")
    delivery_goal = st.selectbox(
        "Delivery modality", options=["RNP", "Plasmid", "mRNA", "Other"], index=0
    )
    total_cells = int(
        st.number_input("Total cells prepared", value=250000, min_value=1000, step=10000)
    )
    viability_percent = st.number_input(
        "Viability (%)", min_value=0.0, max_value=100.0, value=92.0
    )

    st.subheader("Neon Parameters")
    electroporation_device = st.text_input(
        "Electroporation device", value="Neon Transfection System"
    )
    neon_tip_volume_ul = st.number_input(
        "Neon tip volume (uL)", min_value=1.0, max_value=100.0, value=10.0
    )
    voltage_v = int(
        st.number_input("Voltage (V)", min_value=100.0, max_value=3000.0, value=1200.0)
    )
    pulse_width_ms = st.number_input(
        "Pulse width (ms)", min_value=1.0, max_value=100.0, value=30.0
    )
    pulse_number = int(
        st.number_input("Number of pulses", min_value=1.0, max_value=5.0, value=1.0)
    )

    buffer_system = st.text_input("Buffer system", value="Buffer R (Neon kit)")
    buffer_notes = st.text_area("Buffer notes", value="Prepared fresh, kept on ice.")

    st.subheader("RNP / Delivery Mix")
    components: list[Component] = []
    default_components = [
        ("Cas9 Nuclease", "Cas9"),
        ("sgRNA 1", "sgRNA"),
        ("ssODN HDR donor", "ssODN"),
    ]
    for i in range(3):
        label, default_type = default_components[i]
        with st.expander(f"Component {i + 1}: {label}"):
            components.append(
                Component(
                    name=st.text_input(
                        f"Component {i + 1} name", value=label, key=f"comp_name_{i}"
                    ),
                    component_type=st.selectbox(
                        "Component type",
                        options=["Cas9", "sgRNA", "ssODN", "Buffer", "Additive", "Other"],
                        index=["Cas9", "sgRNA", "ssODN"].index(default_type),
                        key=f"comp_type_{i}",
                    ),
                    concentration=st.number_input(
                        "Concentration",
                        min_value=0.0,
                        value=0.0,
                        step=0.1,
                        key=f"comp_conc_{i}",
                    )
                    or None,
                    concentration_units=st.selectbox(
                        "Concentration units",
                        options=["", "uM", "mg/mL", "ng/uL", "X", "Other"],
                        index=0,
                        key=f"comp_unit_{i}",
                    )
                    or None,
                    volume_ul=st.number_input(
                        "Volume (uL)", min_value=0.0, value=5.0, step=0.5, key=f"comp_vol_{i}"
                    )
                    or None,
                    vendor=st.text_input("Vendor", key=f"comp_vendor_{i}") or None,
                    lot_number=st.text_input("Lot number", key=f"comp_lot_{i}") or None,
                )
            )

    assembly_notes = st.text_area(
        "Assembly notes",
        value="Assemble RNP in PBS + 1 mM MgCl2; incubate 10 min at room temperature.",
    )
    incubation_time_minutes = st.number_input(
        "Incubation prior to electroporation (min)", min_value=0.0, value=10.0
    )

    st.subheader("Post-pulse Recovery")
    post_pulse_recovery_medium = st.text_input(
        "Recovery medium", value="mTeSR + 10 uM ROCK inhibitor"
    )
    post_pulse_recovery_volume_ml = st.number_input(
        "Recovery medium volume (mL)", min_value=0.1, max_value=5.0, value=0.5
    )
    recovery_temperature_c = st.number_input(
        "Recovery temperature (°C)", min_value=4.0, max_value=45.0, value=37.0
    )
    recovery_duration_minutes = st.number_input(
        "Recovery duration (min)", min_value=1.0, max_value=120.0, value=10.0
    )
    plating_format = st.text_input("Plating format", value="24-well plate")
    plating_density_cells_per_well = int(
        st.number_input(
            "Plating density (cells per well)",
            min_value=0.0,
            value=100000.0,
            step=50000.0,
        )
    )

    additional_notes = st.text_area(
        "Additional notes",
        value="Deliver 1200 V / 30 ms / 1 pulse per protocol. Immediately transfer to recovery medium.",
    )

    uploads = st.file_uploader(
        "Attach delivery artifacts (protocol PDFs, device readouts, screenshots)",
        accept_multiple_files=True,
    )
    if uploads:
        for idx, upload in enumerate(uploads):
            saved_path = save_uploaded_file(
                selected_project,
                upload,
                upload_dir,
                category="uploads",
                context={"step": "delivery"},
            )
            attachments[upload.name] = str(saved_path)
            saved_attachment_paths.append(saved_path)

    author = st.text_input("Author", value="Narasimha Telugu")

    submitted = st.form_submit_button("Save delivery snapshot & compute hash")

if submitted:
    try:
        log = DeliveryLog(
            project_id=project_id,
            cell_line=cell_line,
            passage_number=passage_number or None,
            delivery_goal=delivery_goal,
            total_cells=total_cells,
            viability_percent=viability_percent,
            electroporation_device=electroporation_device,
            neon_tip_volume_ul=neon_tip_volume_ul,
            voltage_v=voltage_v,
            pulse_width_ms=pulse_width_ms,
            pulse_number=pulse_number,
            buffer_system=buffer_system,
            buffer_notes=buffer_notes or None,
            rnp_components=components,
            assembly_notes=assembly_notes or None,
            incubation_time_minutes=incubation_time_minutes,
            post_pulse_recovery_medium=post_pulse_recovery_medium,
            post_pulse_recovery_volume_ml=post_pulse_recovery_volume_ml,
            recovery_temperature_c=recovery_temperature_c,
            recovery_duration_minutes=recovery_duration_minutes,
            plating_format=plating_format,
            plating_density_cells_per_well=plating_density_cells_per_well,
            additional_notes=additional_notes or None,
            author=author,
            timestamp_unix=int(time.time()),
        )
        payload_dict = json.loads(log.model_dump_json())
        payload_dict["attachments"] = attachments.copy()
        payload_bytes = json.dumps(payload_dict, indent=2).encode("utf-8")
        digest = sha256_bytes(payload_bytes)
        outfile = snapshot_dir / f"{log.project_id}_{log.timestamp_unix}_{digest[:12]}.json"
        outfile.write_bytes(payload_bytes)

        st.session_state["delivery_snapshot"] = {
            "digest": digest,
            "outfile": str(outfile),
            "metadata_uri": outfile.resolve().as_uri(),
            "payload": payload_dict,
            "project_id": project_id,
        }

        if selected_project:
            register_file(
                selected_project,
                "snapshots",
                {
                    "step": "delivery",
                    "path": str(outfile),
                    "digest": digest,
                    "timestamp": log.timestamp_unix,
                },
            )
            append_audit(
                selected_project,
                {
                    "ts": int(time.time()),
                    "step": "delivery",
                    "action": "snapshot_saved",
                    "snapshot_path": str(outfile),
                },
            )

        st.success("Delivery snapshot saved.")
        st.code(f"SHA-256: {digest}", language="text")
        st.caption(f"Saved: {outfile}")
        if saved_attachment_paths:
            st.caption("Attachment previews")
            for idx, path in enumerate(saved_attachment_paths):
                preview_file(path, label=path.name, key_prefix=f"delivery_attach_{idx}")
        st.info("Snapshot ready for on-chain anchoring with step label 'Delivery'.")

        readiness = evaluate_delivery_readiness(payload_dict, attachments)
        st.session_state["delivery_readiness"] = readiness
        if readiness["ready"]:
            st.success("Readiness check passed: Delivery step is complete.")
        else:
            st.warning("Resolve the following before anchoring:")
            for issue in readiness["issues"]:
                st.write(f"- {issue}")
        for warning in readiness["warnings"]:
            st.caption(f"⚠️ {warning}")

        image_attachments = [p for p in saved_attachment_paths if p.suffix.lower() in {'.png', '.jpg', '.jpeg'}]
        pdf_filename = build_step_filename(log.project_id, "delivery", log.timestamp_unix)
        pdf_path = report_dir / pdf_filename
        snapshot_to_pdf(
            payload_dict,
            pdf_path,
            f"Delivery Snapshot — {log.project_id}",
            image_paths=image_attachments or None,
        )
        with pdf_path.open("rb") as pdf_file:
            st.download_button(
                "Download delivery snapshot as PDF",
                pdf_file,
                file_name=pdf_path.name,
                key="delivery_pdf_download",
            )
        st.caption(f"PDF saved: {pdf_path}")
        if selected_project:
            register_file(
                selected_project,
                "reports",
                {
                    "step": "delivery",
                    "path": str(pdf_path),
                    "timestamp": log.timestamp_unix,
                    "digest": sha256_bytes(pdf_path.read_bytes()),
                    "type": "pdf",
                },
            )
    except ValidationError as exc:
        st.error(exc)

snapshot = st.session_state.get("delivery_snapshot")
readiness_state = st.session_state.get("delivery_readiness")
if snapshot:
    st.divider()
    st.subheader("On-chain Anchoring")
    st.write(
        "Anchor the delivery metadata hash to the AssuredRegistry contract with step `Delivery`."
    )
    st.code(f"SHA-256: {snapshot['digest']}", language="text")
    st.caption(f"Snapshot: {snapshot['outfile']}")

    if readiness_state:
        if readiness_state["ready"]:
            st.success("Ready to anchor: Delivery readiness checks passed.")
        else:
            st.error("Delivery readiness checks failed:")
            for issue in readiness_state["issues"]:
                st.write(f"- {issue}")
            if readiness_state["warnings"]:
                st.caption("Warnings:")
                for warning in readiness_state["warnings"]:
                    st.caption(f"⚠️ {warning}")
    else:
        st.info("Generate or reload a snapshot to evaluate readiness.")

    anchor_disabled = not readiness_state or not readiness_state["ready"]

    if st.button("Anchor delivery hash on-chain (Sepolia)", disabled=anchor_disabled):
        try:
            result = send_log_tx(
                hex_digest=snapshot["digest"],
                step="Delivery",
                metadata_uri=snapshot["metadata_uri"],
            )
            st.success("Delivery step anchored on-chain ✅")
            st.write(f"Tx: {result['tx_hash']}")
            st.json(result["receipt"])
            st.caption("Verify the transaction on Sepolia Etherscan.")

            entry_id = None
            log_topics = result["receipt"].get("logs", [])
            if log_topics:
                try:
                    entry_id = int(log_topics[0]["topics"][1].hex(), 16)
                except Exception:
                    entry_id = None

            chainproof = {
                "tx_hash": result["tx_hash"],
                "contract": os.getenv("CONTRACT_ADDRESS"),
                "chain_id": int(os.getenv("CHAIN_ID", "11155111")),
                "step": "Delivery",
                "content_hash": snapshot["digest"],
                "entry_id": entry_id,
            }
            proof_path = Path(snapshot["outfile"]).with_suffix(".chainproof.json")
            proof_path.write_text(json.dumps(chainproof, indent=2), encoding="utf-8")
            st.caption(f"Chain proof saved: {proof_path}")

            if selected_project:
                register_chain_tx(
                    selected_project,
                    {
                        "step": "delivery",
                        "tx_hash": result["tx_hash"],
                        "digest": snapshot["digest"],
                        "timestamp": int(time.time()),
                        "metadata_uri": snapshot["metadata_uri"],
                    },
                )
                append_audit(
                    selected_project,
                    {
                        "ts": int(time.time()),
                        "step": "delivery",
                        "action": "anchored",
                        "tx_hash": result["tx_hash"],
                    },
                )
        except Exception as exc:
            st.error(f"On-chain anchoring failed: {exc}")
