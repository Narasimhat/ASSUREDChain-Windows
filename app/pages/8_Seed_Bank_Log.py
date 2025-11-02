import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Literal

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

FALLBACK_DATA_DIR = ROOT_DIR / "data" / "seed_bank_logs"
FALLBACK_UPLOAD_DIR = FALLBACK_DATA_DIR / "attachments"
FALLBACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
FALLBACK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class FrozenClone(BaseModel):
    clone_id: str
    source_colony: Optional[str] = None
    vial_count: int = 1
    cells_per_vial: Optional[float] = Field(default=None, description="Approximate cells per vial (x10^6)")
    cryomedia: str = "90% FBS + 10% DMSO"
    freezing_method: Literal["Controlled-rate", "Mr. Frosty", "Isopropanol bath", "Other"] = "Controlled-rate"
    storage_location: str = "LN2 Rack B / Box 12"
    viability_post_thaw_percent: Optional[float] = None
    dna_pellet_saved: bool = True
    dna_pellet_location: Optional[str] = None
    notes: Optional[str] = None


class SeedBankLog(BaseModel):
    project_id: str
    seed_bank_batch_id: str
    freeze_date: str
    operator: str
    cryopreservation_medium: str
    controlled_rate_program: Optional[str] = None
    total_clones_frozen: int
    clones: List[FrozenClone]
    dna_pellet_overview: Optional[str] = None
    preliminary_qc: Optional[str] = None
    downstream_qc_plan: Optional[str] = None
    notes: Optional[str] = None
    attachments: dict = {}
    author: str
    timestamp_unix: int


def sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def evaluate_seed_bank_readiness(payload: dict) -> dict:
    issues: list[str] = []
    warnings: list[str] = []

    if not (payload.get("project_id") and payload.get("seed_bank_batch_id") and payload.get("freeze_date")):
        issues.append("Project ID, seed bank batch ID, and freeze date are required.")

    clones = payload.get("clones") or []
    if not clones:
        issues.append("Add at least one clone to the seed bank.")
    else:
        for clone in clones:
            cid = clone.get("clone_id") or "Clone"
            if not clone.get("storage_location"):
                issues.append(f"{cid}: storage location is required.")
            if clone.get("vial_count", 0) <= 0:
                issues.append(f"{cid}: vial count must be greater than zero.")
            if clone.get("cells_per_vial") in (None, 0):
                warnings.append(f"{cid}: cells per vial not recorded.")
            if clone.get("dna_pellet_saved") and not clone.get("dna_pellet_location"):
                warnings.append(f"{cid}: DNA pellet saved but location not recorded.")

    attachments_map = payload.get("attachments") or {}
    if not attachments_map:
        warnings.append("No seed bank attachments uploaded (QC reports, cryofreezing logs, etc.).")

    if not payload.get("downstream_qc_plan"):
        warnings.append("Downstream QC plan not specified.")

    return {"ready": not issues, "issues": issues, "warnings": warnings}


init_page("Step 8 - Seed Bank Logger")
selected_project = use_project("project")
st.title("Step 8 - Seed Bank Logger")
st.caption("Freeze positive clones, record cryovial metadata, and track DNA pellets for downstream QC.")

attachments: dict[str, str] = {}
saved_attachment_paths: List[Path] = []

if selected_project:
    snapshot_dir = project_subdir(selected_project, "snapshots", "seed_bank")
    upload_dir = project_subdir(selected_project, "uploads", "seed_bank")
    report_dir = project_subdir(selected_project, "reports", "seed_bank")
else:
    snapshot_dir = FALLBACK_DATA_DIR
    upload_dir = FALLBACK_UPLOAD_DIR
    report_dir = FALLBACK_DATA_DIR


with st.form("seed_bank_form"):
    project_id = st.text_input(
        "Project ID",
        value=selected_project or "SORCS1_KI_v1",
        help="Defaults to the project selected in the sidebar.",
    )
    seed_bank_batch_id = st.text_input("Seed bank batch ID", value="SeedBank_2025-11-05")
    freeze_date = st.text_input("Freeze date", value=time.strftime("%Y-%m-%d"))
    operator = st.text_input("Operator", value="Narasimha Telugu")
    cryomedia = st.text_input("Cryopreservation medium", value="90% FBS + 10% DMSO + 10 uM Y-27632")
    controlled_rate_program = st.text_input(
        "Controlled-rate program (optional)", value="1degC/min to -80degC, hold, then transfer to LN2"
    )

    st.subheader("Clones prepared for seed bank")
    default_clones = [
        ("Clone_A", "C1", 2, 1.0, "LN2 Rack B / Box 12 / Vials A1-A2"),
        ("Clone_B", "C3", 2, 1.0, "LN2 Rack B / Box 12 / Vials B1-B2"),
        ("Clone_C", "C6", 2, 1.0, "LN2 Rack B / Box 12 / Vials C1-C2"),
    ]
    clone_rows: List[FrozenClone] = []
    clone_count = st.number_input("Number of clones", min_value=1, max_value=48, value=len(default_clones))
    for idx in range(int(clone_count)):
        defaults = default_clones[idx] if idx < len(default_clones) else (f"Clone_{idx+1}", "", 2, 1.0, "")
        clone_id_default, colony_default, vials_default, cells_default, location_default = defaults
        with st.expander(f"Clone {idx + 1}"):
            clone_id = st.text_input("Clone ID", value=clone_id_default, key=f"seed_clone_id_{idx}")
            source_colony = st.text_input("Source colony", value=colony_default, key=f"seed_source_{idx}")
            vial_count = int(
                st.number_input(
                    "Vial count",
                    min_value=1.0,
                    value=float(vials_default),
                    key=f"seed_vials_{idx}",
                )
            )
            cells_per_vial = st.number_input(
                "Cells per vial (x10^6)",
                min_value=0.0,
                value=cells_default,
                key=f"seed_cells_{idx}",
            )
            cryomed = st.text_input(
                "Cryomedia", value="90% FBS + 10% DMSO", key=f"seed_media_{idx}"
            )
            freezing_method = st.selectbox(
                "Freezing method",
                options=["Controlled-rate", "Mr. Frosty", "Isopropanol bath", "Other"],
                index=0,
                key=f"seed_method_{idx}",
            )
            storage_loc = st.text_input(
                "Storage location", value=location_default, key=f"seed_storage_{idx}"
            )
            viability = st.number_input(
                "Post-thaw viability (%)",
                min_value=0.0,
                max_value=100.0,
                value=95.0,
                key=f"seed_viability_{idx}",
            )
            dna_saved = st.checkbox(
                "DNA pellet saved for QC", value=True, key=f"seed_dna_checkbox_{idx}"
            )
            dna_loc = st.text_input(
                "DNA pellet location",
                value=f"-80degC rack C / box {idx + 5}",
                key=f"seed_dna_loc_{idx}",
            )
            notes = st.text_area(
                "Notes",
                value="Pellet reserved for SNP karyotyping.",
                key=f"seed_notes_{idx}",
            )

            evidence = st.file_uploader(
                "Evidence file (optional)",
                key=f"seed_evidence_{idx}",
            )
            evidence_path = None
            if evidence:
                evidence_path = save_uploaded_file(
                    selected_project,
                    evidence,
                    upload_dir,
                    category="uploads",
                    context={"step": "seed_bank", "clone": clone_id},
                )
                attachments[f"clone_{clone_id}_evidence"] = str(evidence_path)
                saved_attachment_paths.append(evidence_path)

            clone_rows.append(
                FrozenClone(
                    clone_id=clone_id,
                    source_colony=source_colony or None,
                    vial_count=vial_count,
                    cells_per_vial=cells_per_vial or None,
                    cryomedia=cryomed,
                    freezing_method=freezing_method,
                    storage_location=storage_loc,
                    viability_post_thaw_percent=viability or None,
                    dna_pellet_saved=dna_saved,
                    dna_pellet_location=dna_loc or None,
                    notes=notes or None,
                )
            )

    total_clones_frozen = sum(clone.vial_count for clone in clone_rows)
    st.caption(f"Total vials prepared: {total_clones_frozen}")

    dna_pellet_overview = st.text_area(
        "DNA pellet overview",
        value="Pellets generated from top clones for SNP karyotyping and off-target panel.",
    )
    preliminary_qc = st.text_area(
        "Preliminary QC results",
        value="Bulk SNP array pending; qPCR confirms single copy KI for Clone_A and Clone_B.",
    )
    downstream_qc_plan = st.text_area(
        "Downstream QC plan",
        value="Send DNA pellets to Cytogenetics core for SNP karyotyping; schedule mycoplasma testing pre-expansion.",
    )
    notes = st.text_area(
        "General notes",
        value="All vials transferred to LN2 within 2 h of controlled-rate run completion.",
    )

    extra_uploads = st.file_uploader(
        "Attach supporting files (freeze logs, plate maps, QC reports)",
        accept_multiple_files=True,
        key="seed_extra_uploads",
    )
    if extra_uploads:
        for upload in extra_uploads:
            saved_path = save_uploaded_file(
                selected_project,
                upload,
                upload_dir,
                category="uploads",
                context={"step": "seed_bank"},
            )
            attachments[upload.name] = str(saved_path)
            saved_attachment_paths.append(saved_path)

    author = st.text_input("Author", value="Narasimha Telugu")

    submitted = st.form_submit_button("Save seed bank snapshot & compute hash")

if submitted:
    try:
        log = SeedBankLog(
            project_id=project_id,
            seed_bank_batch_id=seed_bank_batch_id,
            freeze_date=freeze_date,
            operator=operator,
            cryopreservation_medium=cryomedia,
            controlled_rate_program=controlled_rate_program or None,
            total_clones_frozen=total_clones_frozen,
            clones=clone_rows,
            dna_pellet_overview=dna_pellet_overview or None,
            preliminary_qc=preliminary_qc or None,
            downstream_qc_plan=downstream_qc_plan or None,
            notes=notes or None,
            attachments=attachments,
            author=author,
            timestamp_unix=int(time.time()),
        )
        payload_dict = json.loads(log.model_dump_json())
        payload_bytes = json.dumps(payload_dict, indent=2).encode("utf-8")
        digest = sha256_bytes(payload_bytes)
        outfile = snapshot_dir / f"{log.project_id}_{log.seed_bank_batch_id}_{log.timestamp_unix}_{digest[:12]}.json"
        outfile.write_bytes(payload_bytes)

        st.session_state["seed_bank_snapshot"] = {
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
                    "step": "seed_bank",
                    "path": str(outfile),
                    "digest": digest,
                    "timestamp": log.timestamp_unix,
                },
            )
            append_audit(
                selected_project,
                {
                    "ts": int(time.time()),
                    "step": "seed_bank",
                    "action": "snapshot_saved",
                    "snapshot_path": str(outfile),
                },
            )

        st.success("Seed bank snapshot saved.")
        st.code(f"SHA-256: {digest}", language="text")
        st.caption(f"Saved: {outfile}")
        if saved_attachment_paths:
            st.caption("Attachment previews")
            for idx, path in enumerate(saved_attachment_paths):
                preview_file(path, label=path.name, key_prefix=f"seedbank_attach_{idx}")
        st.info("Snapshot ready for on-chain anchoring with step label 'Seed Bank'.")

        readiness = evaluate_seed_bank_readiness(payload_dict)
        st.session_state["seed_bank_readiness"] = readiness
        if readiness["ready"]:
            st.success("Readiness check passed: seed bank entries complete.")
        else:
            st.warning("Resolve the following before anchoring:")
            for issue in readiness["issues"]:
                st.write(f"- {issue}")
        for warning in readiness["warnings"]:
            st.caption(f"Warning: {warning}")

        image_attachments = [p for p in saved_attachment_paths if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
        pdf_filename = build_step_filename(log.project_id, "seed-bank", log.timestamp_unix)
        pdf_path = report_dir / pdf_filename
        snapshot_to_pdf(
            payload_dict,
            pdf_path,
            f"Seed Bank Snapshot - {log.project_id}",
            image_paths=image_attachments or None,
        )
        with pdf_path.open("rb") as pdf_file:
            st.download_button(
                "Download seed bank snapshot as PDF",
                pdf_file,
                file_name=pdf_path.name,
                key="seedbank_pdf_download",
            )
        st.caption(f"PDF saved: {pdf_path}")
        if selected_project:
            register_file(
                selected_project,
                "reports",
                {
                    "step": "seed_bank",
                    "path": str(pdf_path),
                    "timestamp": log.timestamp_unix,
                    "digest": sha256_bytes(pdf_path.read_bytes()),
                    "type": "pdf",
                },
            )
    except ValidationError as exc:
        st.error(exc)

snapshot = st.session_state.get("seed_bank_snapshot")
readiness_state = st.session_state.get("seed_bank_readiness")
if snapshot:
    st.divider()
    st.subheader("On-chain Anchoring")
    st.write("Anchor the seed bank metadata hash to the AssuredRegistry contract with step `Seed Bank`.")
    st.code(f"SHA-256: {snapshot['digest']}", language="text")
    st.caption(f"Snapshot: {snapshot['outfile']}")

    if readiness_state is None:
        readiness_state = evaluate_seed_bank_readiness(snapshot["payload"])
        st.session_state["seed_bank_readiness"] = readiness_state

    if readiness_state:
        if readiness_state["ready"]:
            st.success("Ready to anchor: seed bank readiness checks passed.")
        else:
            st.error("Seed bank readiness checks failed:")
            for issue in readiness_state["issues"]:
                st.write(f"- {issue}")
            if readiness_state["warnings"]:
                st.caption("Warnings:")
                for warning in readiness_state["warnings"]:
                    st.caption(f"Warning: {warning}")

    anchor_disabled = not readiness_state or not readiness_state["ready"]

    if st.button("Anchor seed bank hash on-chain (Sepolia)", disabled=anchor_disabled):
        try:
            result = send_log_tx(
                hex_digest=snapshot["digest"],
                step="Seed Bank",
                metadata_uri=snapshot["metadata_uri"],
            )
            st.success("Seed bank step anchored on-chain.")
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
                "step": "Seed Bank",
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
                        "step": "seed_bank",
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
                        "step": "seed_bank",
                        "action": "anchored",
                        "tx_hash": result["tx_hash"],
                    },
                )
        except Exception as exc:
            st.error(f"On-chain anchoring failed: {exc}")
