import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import streamlit as st
from pydantic import BaseModel, Field, ValidationError

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

FALLBACK_DATA_DIR = ROOT / "data" / "screening_logs"
FALLBACK_UPLOAD_DIR = FALLBACK_DATA_DIR / "attachments"
FALLBACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
FALLBACK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class CloneResult(BaseModel):
    clone_id: str
    assay: str
    call: str
    pcr: dict = Field(default_factory=dict)
    sanger: dict = Field(default_factory=dict)
    comment: Optional[str] = None


class ScreeningSnapshot(BaseModel):
    project_id: str
    plate_id: str
    operator: str
    date: str
    clones: List[CloneResult]
    positives: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    attachments: dict = Field(default_factory=dict)
    timestamp_unix: int


def sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def evaluate_screening_readiness(payload: dict) -> dict:
    issues: list[str] = []
    warnings: list[str] = []

    clones = payload.get("clones") or []
    if not clones:
        issues.append("Log at least one clone screening result.")
    else:
        for clone in clones:
            cid = clone.get("clone_id") or "Clone"
            assay = clone.get("assay")
            call = (clone.get("call") or "").lower()
            if assay in {"PCR", "PCR+Sanger"}:
                pcr = clone.get("pcr") or {}
                observed = pcr.get("observed_bands_bp") or []
                if not observed:
                    issues.append(f"{cid}: record observed PCR bands.")
                gel_path = pcr.get("gel_image_path")
                if observed and not gel_path:
                    warnings.append(f"{cid}: add gel image for PCR confirmation.")
            if assay in {"Sanger", "PCR+Sanger"}:
                sanger = clone.get("sanger") or {}
                if not sanger.get("result_file"):
                    warnings.append(f"{cid}: attach Sanger trace/analysis output.")
            if call == "positive" and cid not in (payload.get("positives") or []):
                warnings.append(f"{cid} marked Positive but not included in positives list.")

    if not payload.get("positives"):
        warnings.append("Mark at least one clone as positive or note rationale in comments.")

    attachments_map = payload.get("attachments") or {}
    if not attachments_map:
        warnings.append("No supplemental attachments uploaded (plate map, summary, etc.).")

    return {"ready": not issues, "issues": issues, "warnings": warnings}

init_page("Step 6 - Screening Log")
selected_project = use_project("project")
st.title("Step 6 - Screening Log")
st.caption("Track PCR/Sanger screening results, mark positives, and anchor on-chain as step 'Screening'.")

attachments: dict[str, str] = {}
saved_attachment_paths: List[Path] = []

if selected_project:
    snapshot_dir = project_subdir(selected_project, "snapshots", "screening")
    upload_dir = project_subdir(selected_project, "uploads", "screening")
    report_dir = project_subdir(selected_project, "reports", "screening")
else:
    snapshot_dir = FALLBACK_DATA_DIR
    upload_dir = FALLBACK_UPLOAD_DIR
    report_dir = FALLBACK_DATA_DIR


with st.form("screening_form"):
    project_id = st.text_input(
        "Project ID",
        value=selected_project or "SORCS1_KI_v1",
        help="Defaults to the project selected in the sidebar.",
    )
    plate_id = st.text_input("Plate ID / Batch", value="Plate_A")
    operator = st.text_input("Operator", value="Narasimha Telugu")
    date = st.text_input("Date (YYYY-MM-DD)", value=time.strftime("%Y-%m-%d"))

    clone_count = st.number_input("Number of clones screened", min_value=1, max_value=96, value=4)
    clones: List[CloneResult] = []
    positives: List[str] = []

    for idx in range(int(clone_count)):
        with st.expander(f"Clone {idx + 1}"):
            clone_id = st.text_input("Clone ID", value=f"C{idx+1:02d}", key=f"clone_id_{idx}")
            assay = st.selectbox(
                "Assay",
                ["PCR", "Sanger", "PCR+Sanger"],
                index=0,
                key=f"assay_{idx}",
            )
            call = st.selectbox(
                "Call",
                ["Positive", "Mixed", "Negative", "Rerun"],
                index=2,
                key=f"call_{idx}",
            )
            comment = st.text_area("Comments", key=f"comment_{idx}")

            pcr_bands = {}
            if assay in ("PCR", "PCR+Sanger"):
                wt = st.text_input("PCR WT band", key=f"pcr_wt_{idx}")
                edited = st.text_input("PCR edited band", key=f"pcr_edit_{idx}")
                observed = st.text_input("Observed bands (comma-separated)", key=f"pcr_obs_{idx}")
                gel = st.file_uploader("PCR gel image (optional)", type=["png", "jpg", "jpeg"], key=f"gel_{idx}")
                gel_path = None
                if gel:
                    gel_path = save_uploaded_file(
                        selected_project,
                        gel,
                        upload_dir,
                        category="uploads",
                        context={"step": "screening", "kind": "gel_image", "clone": clone_id},
                    )
                    saved_attachment_paths.append(gel_path)
                pcr_bands = {
                    "wt_bp": wt or None,
                    "edited_bp": edited or None,
                    "observed_bands_bp": [v.strip() for v in observed.split(",") if v.strip()],
                    "gel_image_path": str(gel_path) if gel_path else None,
                }

            sanger_block = {}
            if assay in ("Sanger", "PCR+Sanger"):
                tool = st.text_input("Sanger tool", value="ICE", key=f"sanger_tool_{idx}")
                indel_pct = st.number_input(
                    "Total indel %",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.1,
                    value=0.0,
                    key=f"sanger_indel_{idx}",
                )
                ki_pct = st.number_input(
                    "Knock-in %",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.1,
                    value=0.0,
                    key=f"sanger_ki_{idx}",
                )
                sanger_file = st.file_uploader(
                    "Sanger screenshot / trace (optional)",
                    type=["png", "jpg", "jpeg", "ab1", "pdf"],
                    key=f"sanger_file_{idx}",
                )
                sanger_path = None
                if sanger_file:
                    sanger_path = save_uploaded_file(
                        selected_project,
                        sanger_file,
                        upload_dir,
                        category="uploads",
                        context={"step": "screening", "kind": "sanger_trace", "clone": clone_id},
                    )
                    saved_attachment_paths.append(sanger_path)
                sanger_block = {
                    "tool_used": tool,
                    "total_indel_pct": indel_pct,
                    "ki_pct": ki_pct,
                    "result_file": str(sanger_path) if sanger_path else None,
                }

            clones.append(
                CloneResult(
                    clone_id=clone_id,
                    assay=assay,
                    call=call,
                    pcr=pcr_bands,
                    sanger=sanger_block,
                    comment=comment or None,
                )
            )
            if call.lower() == "positive":
                positives.append(clone_id)

    notes = st.text_area("General notes")

    sequencing_uploads = st.file_uploader(
        "Sequencing data (AB1/FASTQ/ZIP)",
        type=["ab1", "abi", "fastq", "fq", "gz", "zip"],
        accept_multiple_files=True,
        key="screening_sequencing_uploads",
        help="Upload raw or processed sequencing files associated with clone confirmation.",
    )
    if sequencing_uploads:
        existing_seq = sum(1 for key in attachments if key.startswith("sequencing_data"))
        for idx, seq_file in enumerate(sequencing_uploads, start=1):
            saved_path = save_uploaded_file(
                selected_project,
                seq_file,
                upload_dir,
                category="uploads",
                context={"step": "screening", "kind": "sequencing_data"},
            )
            attachments[f"sequencing_data_{existing_seq + idx}"] = str(saved_path)
            saved_attachment_paths.append(saved_path)

    uploads = st.file_uploader(
        "Attach screening artifacts (plate map, summary XLSX, screenshots)",
        accept_multiple_files=True,
        key="screening_extra_uploads",
    )
    if uploads:
        for upload in uploads:
            saved_path = save_uploaded_file(
                selected_project,
                upload,
                upload_dir,
                category="uploads",
                context={"step": "screening"},
            )
            attachments[upload.name] = str(saved_path)
            saved_attachment_paths.append(saved_path)

    author = st.text_input("Author", value="Narasimha Telugu")

    submitted = st.form_submit_button("Save screening snapshot & compute hash")

if submitted:
    try:
        snapshot = ScreeningSnapshot(
            project_id=project_id,
            plate_id=plate_id,
            operator=operator,
            date=date,
            clones=clones,
            positives=positives,
            notes=notes or None,
            attachments=attachments,
            timestamp_unix=int(time.time()),
        )
        payload_dict = json.loads(snapshot.model_dump_json())
        payload = json.dumps(payload_dict, indent=2).encode("utf-8")
        digest = sha256_bytes(payload)
        outfile = snapshot_dir / f"{snapshot.project_id}_{snapshot.plate_id}_{snapshot.timestamp_unix}_{digest[:12]}.json"
        outfile.write_bytes(payload)

        st.session_state["screening_snapshot"] = {
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
                    "step": "screening",
                    "path": str(outfile),
                    "digest": digest,
                    "timestamp": snapshot.timestamp_unix,
                },
            )
            append_audit(
                selected_project,
                {
                    "ts": int(time.time()),
                    "step": "screening",
                    "action": "snapshot_saved",
                    "snapshot_path": str(outfile),
                },
            )

        st.success("Screening snapshot saved.")
        st.code(f"SHA-256: {digest}", language="text")
        st.caption(f"Saved: {outfile}")
        if saved_attachment_paths:
            st.caption("Attachment previews")
            for idx, path in enumerate(saved_attachment_paths):
                preview_file(path, label=path.name, key_prefix=f"screening_attach_{idx}")
        st.info("Snapshot ready for on-chain anchoring with step label 'Screening'.")

        readiness = evaluate_screening_readiness(payload_dict)
        st.session_state["screening_readiness"] = readiness
        if readiness["ready"]:
            st.success("Readiness check passed: screening data complete.")
        else:
            st.warning("Resolve the following before anchoring:")
            for issue in readiness["issues"]:
                st.write(f"- {issue}")
        for warning in readiness["warnings"]:
            st.caption(f"Warning: {warning}")

        image_attachments = [p for p in saved_attachment_paths if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
        pdf_filename = build_step_filename(snapshot.project_id, "screening", snapshot.timestamp_unix)
        pdf_path = report_dir / pdf_filename
        snapshot_to_pdf(
            payload_dict,
            pdf_path,
            f"Screening Snapshot - {snapshot.project_id}",
            image_paths=image_attachments or None,
        )
        with pdf_path.open("rb") as pdf_file:
            st.download_button(
                "Download screening snapshot as PDF",
                pdf_file,
                file_name=pdf_path.name,
                key="screening_pdf_download",
            )
        st.caption(f"PDF saved: {pdf_path}")
        if selected_project:
            register_file(
                selected_project,
                "reports",
                {
                    "step": "screening",
                    "path": str(pdf_path),
                    "timestamp": snapshot.timestamp_unix,
                    "digest": sha256_bytes(pdf_path.read_bytes()),
                    "type": "pdf",
                },
            )
    except ValidationError as exc:
        st.error(exc)

snapshot_state = st.session_state.get("screening_snapshot")
readiness_state = st.session_state.get("screening_readiness")
if snapshot_state:
    st.divider()
    st.subheader("On-chain Anchoring")
    st.write("Anchor the screening hash to the AssuredRegistry contract with step `Screening`.")
    st.code(f"SHA-256: {snapshot_state['digest']}", language="text")
    st.caption(f"Snapshot: {snapshot_state['outfile']}")

    if readiness_state:
        if readiness_state["ready"]:
            st.success("Ready to anchor: Screening readiness checks passed.")
        else:
            st.error("Screening readiness checks failed:")
            for issue in readiness_state["issues"]:
                st.write(f"- {issue}")
            if readiness_state["warnings"]:
                st.caption("Warnings:")
                for warning in readiness_state["warnings"]:
                    st.caption(f"Warning: {warning}")
    else:
        st.info("Generate or reload a snapshot to evaluate readiness.")

    anchor_disabled = not readiness_state or not readiness_state["ready"]

    if st.button("Anchor screening hash on-chain (Sepolia)", disabled=anchor_disabled):
        try:
            result = send_log_tx(
                hex_digest=snapshot_state["digest"],
                step="Screening",
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
                "step": "Screening",
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
                        "step": "screening",
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
                        "step": "screening",
                        "action": "anchored",
                        "tx_hash": result["tx_hash"],
                    },
                )
        except Exception as exc:
            st.error(f"On-chain anchoring failed: {exc}")
