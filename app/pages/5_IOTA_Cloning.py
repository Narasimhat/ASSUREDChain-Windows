import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import streamlit as st
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

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

FALLBACK_DATA_DIR = ROOT / "data" / "iota_cloning_logs"
FALLBACK_UPLOAD_DIR = FALLBACK_DATA_DIR / "attachments"
FALLBACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
FALLBACK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class StepEvidence(BaseModel):
    done: bool = False
    notes: Optional[str] = None
    file_path: Optional[str] = None


class IOTADevelopment(BaseModel):
    thaw_plate: StepEvidence
    plate_on_iota: StepEvidence
    extract_clones: StepEvidence
    isolate_dna: StepEvidence
    feed_plate1: StepEvidence
    feed_plate2: StepEvidence


class CloneVerification(BaseModel):
    pcr_seq: StepEvidence
    seq_analysis: StepEvidence
    split_positive: StepEvidence
    report: StepEvidence


class SeedbankProduction(BaseModel):
    split_positive: StepEvidence
    produce_seedbank: StepEvidence
    prepare_gdna: StepEvidence
    transfer_validation: StepEvidence


class KaryotypingAnalysis(BaseModel):
    submit_samples: StepEvidence
    analyze_and_establish_master: StepEvidence


class IOTACloningSnapshot(BaseModel):
    project_id: str
    cell_line: str
    operator: str
    date: str
    development: IOTADevelopment
    verification: CloneVerification
    seedbank: SeedbankProduction
    karyo: KaryotypingAnalysis
    notes: Optional[str] = None
    attachments: Dict[str, str] = {}
    timestamp_unix: int


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def evaluate_cloning_readiness(payload: dict) -> dict:
    issues: list[str] = []
    warnings: list[str] = []

    def check_steps(section: dict, required: dict):
        for key, label in required.items():
            step = (section or {}).get(key) or {}
            if not step.get("done"):
                issues.append(f"Mark '{label}' as done.")
            if step.get("done") and not step.get("file_path"):
                warnings.append(f"'{label}' marked done without attached evidence.")

    check_steps(
        payload.get("development"),
        {
            "thaw_plate": "Thaw plate",
            "plate_on_iota": "Plate on iOTA",
            "extract_clones": "Extract clones",
            "isolate_dna": "Isolate DNA",
        },
    )
    check_steps(
        payload.get("verification"),
        {
            "pcr_seq": "PCR / sequencing",
            "seq_analysis": "Sequence analysis",
            "split_positive": "Split positive clones",
            "report": "Prepare report",
        },
    )
    check_steps(
        payload.get("seedbank"),
        {
            "split_positive": "Split positive clones",
            "produce_seedbank": "Produce seed bank",
            "prepare_gdna": "Prepare gDNA",
            "transfer_validation": "Transfer validation",
        },
    )
    check_steps(
        payload.get("karyo"),
        {
            "submit_samples": "Submit samples for karyotyping",
            "analyze_and_establish_master": "Analyze and establish master bank",
        },
    )

    attachments_map = payload.get("attachments") or {}
    if not attachments_map:
        warnings.append("No cloning attachments uploaded (checklist, imaging, reports).")

    return {"ready": not issues, "issues": issues, "warnings": warnings}

init_page("Step 5 - IOTA Clone Development and Verification")
selected_project = use_project("project")
st.title("Step 5 - IOTA Clone Development and Verification")
st.caption("Checklist for isoCell/IOTA cloning workflow. Anchors on-chain as step 'Cloning'.")

attachments: Dict[str, str] = {}
saved_attachment_paths: list[Path] = []

if selected_project:
    snapshot_dir = project_subdir(selected_project, "snapshots", "cloning")
    upload_dir = project_subdir(selected_project, "uploads", "cloning")
    report_dir = project_subdir(selected_project, "reports", "cloning")
else:
    snapshot_dir = FALLBACK_DATA_DIR
    upload_dir = FALLBACK_UPLOAD_DIR
    report_dir = FALLBACK_DATA_DIR


def evidence_block(step_key: str, label: str, prefix: str) -> StepEvidence:
    with st.expander(label):
        done = st.checkbox("Done", key=f"{prefix}_{step_key}_done")
        notes = st.text_area("Notes", key=f"{prefix}_{step_key}_notes")
        upload = st.file_uploader("Evidence file (optional)", key=f"{prefix}_{step_key}_file")
        saved_path = None
        if upload:
            saved_path = save_uploaded_file(
                selected_project,
                upload,
                upload_dir,
                category="uploads",
                context={"step": "cloning", "task": f"{prefix}_{step_key}"},
            )
            attachments[f"{prefix}_{step_key}"] = str(saved_path)
            saved_attachment_paths.append(saved_path)
        return StepEvidence(done=done, notes=notes or None, file_path=str(saved_path) if saved_path else None)


with st.form("iota_cloning_form"):
    cols = st.columns(4)
    with cols[0]:
        project_id = st.text_input(
            "Project ID",
            value=selected_project or "SORCS1_KI_v1",
            help="Defaults to the project selected in the sidebar.",
        )
    with cols[1]:
        cell_line = st.text_input("Cell line", value="BIHi005-A-1X")
    with cols[2]:
        operator = st.text_input("Operator", value="Narasimha Telugu")
    with cols[3]:
        date = st.text_input("Date (YYYY-MM-DD)", value=time.strftime("%Y-%m-%d"))

    st.subheader("Development of Single-Cell Edited Clones (IOTA)")
    development = IOTADevelopment(
        thaw_plate=evidence_block("thaw_plate", "Thaw and plate cells for isoCell/IOTA cloning", "dev"),
        plate_on_iota=evidence_block("plate_on_iota", "Seed cells on IOTA plates", "dev"),
        extract_clones=evidence_block("extract_clones", "Extract clones into 2 x 48 x 96-well plates", "dev"),
        isolate_dna=evidence_block("isolate_dna", "Isolate DNA from one plate for verification", "dev"),
        feed_plate1=evidence_block("feed_plate1", "Feed and maintain second plate (days 1-3)", "dev"),
        feed_plate2=evidence_block("feed_plate2", "Feed and maintain second plate (days 4-7)", "dev"),
    )

    st.subheader("Clone Verification")
    verification = CloneVerification(
        pcr_seq=evidence_block("pcr_seq", "Perform PCR and sequencing", "ver"),
        seq_analysis=evidence_block("seq_analysis", "Analyse sequencing results", "ver"),
        split_positive=evidence_block("split_positive", "Split positive clones for expansion", "ver"),
        report=evidence_block("report", "Compile report for positive clones", "ver"),
    )

    st.subheader("Seedbank Production")
    seedbank = SeedbankProduction(
        split_positive=evidence_block("split_positive", "Split positive clones into expansion plates", "seed"),
        produce_seedbank=evidence_block("produce_seedbank", "Create seed bank from verified clones", "seed"),
        prepare_gdna=evidence_block("prepare_gdna", "Prepare cell pellets for genomic DNA", "seed"),
        transfer_validation=evidence_block("transfer_validation", "Transfer seed banks for validation", "seed"),
    )

    st.subheader("Karyotyping and Analysis")
    karyo = KaryotypingAnalysis(
        submit_samples=evidence_block("submit_samples", "Submit samples for karyotyping (e.g., Bonn)", "karyo"),
        analyze_and_establish_master=evidence_block(
            "analyze_and_establish_master", "Analyse karyotyping & establish master cell bank", "karyo"
        ),
    )

    notes = st.text_area("General notes")
    submitted = st.form_submit_button("Save snapshot and compute hash")

if submitted:
    try:
        snapshot = IOTACloningSnapshot(
            project_id=project_id,
            cell_line=cell_line,
            operator=operator,
            date=date,
            development=development,
            verification=verification,
            seedbank=seedbank,
            karyo=karyo,
            notes=notes or None,
            attachments=attachments,
            timestamp_unix=int(time.time()),
        )

        payload_dict = json.loads(snapshot.model_dump_json())
        payload = json.dumps(payload_dict, indent=2).encode("utf-8")
        digest = sha256_bytes(payload)
        outfile = snapshot_dir / f"{snapshot.project_id}_{snapshot.cell_line}_{snapshot.timestamp_unix}_{digest[:12]}.json"
        outfile.write_bytes(payload)

        st.session_state["iota_cloning_snapshot"] = {
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
                    "step": "cloning",
                    "path": str(outfile),
                    "digest": digest,
                    "timestamp": snapshot.timestamp_unix,
                },
            )
            append_audit(
                selected_project,
                {
                    "ts": int(time.time()),
                    "step": "cloning",
                    "action": "snapshot_saved",
                    "snapshot_path": str(outfile),
                },
            )

        st.success("IOTA cloning snapshot saved.")
        st.code(f"SHA-256: {digest}", language="text")
        st.caption(f"Saved: {outfile}")
        if saved_attachment_paths:
            st.caption("Attachment previews")
            for idx, path in enumerate(saved_attachment_paths):
                preview_file(path, label=path.name, key_prefix=f"cloning_attach_{idx}")
        st.info("Snapshot ready for on-chain anchoring with step label 'Cloning'.")

        readiness = evaluate_cloning_readiness(payload_dict)
        st.session_state["cloning_readiness"] = readiness
        if readiness["ready"]:
            st.success("Readiness check passed: cloning checklist complete.")
        else:
            st.warning("Resolve the following before anchoring:")
            for issue in readiness["issues"]:
                st.write(f"- {issue}")
        for warning in readiness["warnings"]:
            st.caption(f"Warning: {warning}")

        image_attachments = [p for p in saved_attachment_paths if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
        pdf_filename = build_step_filename(snapshot.project_id, "cloning", snapshot.timestamp_unix)
        pdf_path = report_dir / pdf_filename
        snapshot_to_pdf(
            payload_dict,
            pdf_path,
            f"IOTA Cloning Snapshot - {snapshot.project_id}",
            image_paths=image_attachments or None,
        )
        with pdf_path.open("rb") as pdf_file:
            st.download_button(
                "Download IOTA cloning snapshot as PDF",
                pdf_file,
                file_name=pdf_path.name,
                key="cloning_pdf_download",
            )
        st.caption(f"PDF saved: {pdf_path}")
        if selected_project:
            register_file(
                selected_project,
                "reports",
                {
                    "step": "cloning",
                    "path": str(pdf_path),
                    "timestamp": snapshot.timestamp_unix,
                    "digest": sha256_bytes(pdf_path.read_bytes()),
                    "type": "pdf",
                },
            )
    except ValidationError as exc:
        st.error(exc)

snapshot_state = st.session_state.get("iota_cloning_snapshot")
readiness_state = st.session_state.get("cloning_readiness")
if snapshot_state:
    st.divider()
    st.subheader("On-chain Anchoring")
    st.write("Anchor the IOTA cloning hash to the AssuredRegistry contract with step `Cloning`.")
    st.code(f"SHA-256: {snapshot_state['digest']}", language="text")
    st.caption(f"Snapshot: {snapshot_state['outfile']}")

    if readiness_state:
        if readiness_state["ready"]:
            st.success("Ready to anchor: Cloning readiness checks passed.")
        else:
            st.error("Cloning readiness checks failed:")
            for issue in readiness_state["issues"]:
                st.write(f"- {issue}")
            if readiness_state["warnings"]:
                st.caption("Warnings:")
                for warning in readiness_state["warnings"]:
                    st.caption(f"Warning: {warning}")
    else:
        st.info("Generate or reload a snapshot to evaluate readiness.")

    anchor_disabled = not readiness_state or not readiness_state["ready"]

    if st.button("Anchor cloning hash on-chain (Sepolia)", disabled=anchor_disabled):
        try:
            result = send_log_tx(
                hex_digest=snapshot_state["digest"],
                step="Cloning",
                metadata_uri=snapshot_state["metadata_uri"],
            )
            st.success("Anchored on-chain.")
            st.write(f"Tx: {result['tx_hash']}")
            st.json(result["receipt"])
            st.caption("Verify the transaction on Sepolia Etherscan.")

            entry_id = None
            logs = result["receipt"].get("logs", [])
            if logs:
                try:
                    entry_id = int(logs[0]["topics"][1].hex(), 16)
                except Exception:
                    entry_id = None

            chainproof = {
                "tx_hash": result["tx_hash"],
                "contract": os.getenv("CONTRACT_ADDRESS"),
                "chain_id": int(os.getenv("CHAIN_ID", "11155111")),
                "step": "Cloning",
                "content_hash": snapshot_state["digest"],
                "entry_id": entry_id,
            }
            proof_path = Path(snapshot_state["outfile"]).with_suffix(".chainproof.json")
            proof_path.write_text(json.dumps(chainproof, indent=2), encoding="utf-8")
            st.caption(f"Chain proof saved: {proof_path}")

            if selected_project:
                register_chain_tx(
                    selected_project,
                    {
                        "step": "cloning",
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
                        "step": "cloning",
                        "action": "anchored",
                        "tx_hash": result["tx_hash"],
                    },
                )
        except Exception as exc:
            st.error(f"On-chain anchoring failed: {exc}")
