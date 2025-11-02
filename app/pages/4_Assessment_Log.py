import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

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

FALLBACK_DATA_DIR = ROOT / "data" / "assessment_logs"
FALLBACK_UPLOAD_DIR = FALLBACK_DATA_DIR / "attachments"
FALLBACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
FALLBACK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class IndelItem(BaseModel):
    size_bp: int
    frequency_pct: float


class PCRBands(BaseModel):
    wt_bp: Optional[int] = None
    edited_bp: Optional[int] = None
    observed_bands_bp: list[int] = []


class AssessmentSnapshot(BaseModel):
    project_id: str
    cell_line: str
    assay_type: str = Field(..., description="Sanger-Indel or PCR-Genotyping")
    tool_used: Optional[str] = None
    total_indel_pct: Optional[float] = None
    ki_pct: Optional[float] = None
    top_indels: list[IndelItem] = []
    decision: str = Field(..., description="proceed_to_cloning / repeat_edit / archive")
    pcr: Optional[PCRBands] = None
    notes: Optional[str] = None
    author: str
    timestamp_unix: int
    files: dict[str, str] = {}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def evaluate_assessment_readiness(payload: dict, attachments_map: dict[str, str]) -> dict:
    issues: list[str] = []
    warnings: list[str] = []

    assay_type = payload.get("assay_type")

    if assay_type == "Sanger-Indel":
        total_indel = payload.get("total_indel_pct")
        ki_pct = payload.get("ki_pct")
        top_indels = payload.get("top_indels") or []
        if total_indel is None and ki_pct is None and not top_indels:
            issues.append("Provide at least one Sanger readout (total indel %, KI %, or indel table).")
        if "tool_result" not in attachments_map:
            warnings.append("No tool output attached (CSV/JSON/PDF screenshot).")
    elif assay_type == "PCR-Genotyping":
        pcr = payload.get("pcr") or {}
        observed = pcr.get("observed_bands_bp") or []
        if not observed:
            issues.append("Record observed PCR bands to document screening outcome.")
        if pcr.get("wt_bp") is None and pcr.get("edited_bp") is None:
            warnings.append("Expected PCR band sizes not provided.")
        if "gel_image" not in attachments_map:
            warnings.append("Gel/band image not attached.")

    if not attachments_map:
        warnings.append("No assessment attachments uploaded.")

    decision = payload.get("decision")
    if not decision:
        issues.append("Assessment decision is required.")

    return {"ready": not issues, "issues": issues, "warnings": warnings}

init_page("Step 4 - Assessment (Sanger / PCR)")
selected_project = use_project("project")
st.title("Step 4 - Assessment (Sanger / PCR)")
st.caption(
    "Upload Sanger results (TIDE / ICE / DECODR / SeqScreener / TIDER) or log PCR genotyping. "
    "Then anchor on-chain as step 'Assessment'."
)

with st.expander("Which tool should I pick?"):
    st.markdown(
        "- **DECODR**: accuracy on complex/large indels.\n"
        "- **TIDE / SeqScreener**: best for low-edit samples (<10%).\n"
        "- **TIDER**: knock-in % quantification (tag insertions).\n"
        "- **ICE/TIDE** windows are fixed; DECODR supports broad indel spectra.\n"
        "_Tip_: avoid PeakTrace base-calling for these analyses."
    )

st.markdown(
    """
    **Sequencing & analysis resources**  
    • [Synthego ICE](https://ice.synthego.com/) – rapid indel quantification with batch export  
    • [TIDE / TIDER](https://tide.deskgen.com/) – Sanger-based indel and knock-in analysis  
    • [DECODR](https://decodr.org/) – handles complex edit mixtures and large indels  
    • [SeqScreener GeneEdit](https://www.thermofisher.com/de/de/home/life-science/sequencing/sanger-sequencing/applications/crispr-talen-genome-editing-sanger-sequencing/seqscreener-gene-edit-confirmation-app.html) – Thermo Fisher confirmation app for CRISPR edits
    """
)

attachments: dict[str, str] = {}
saved_attachment_paths: list[Path] = []

if selected_project:
    snapshot_dir = project_subdir(selected_project, "snapshots", "assessment")
    upload_dir = project_subdir(selected_project, "uploads", "assessment")
    report_dir = project_subdir(selected_project, "reports", "assessment")
else:
    snapshot_dir = FALLBACK_DATA_DIR
    upload_dir = FALLBACK_UPLOAD_DIR
    report_dir = FALLBACK_DATA_DIR


with st.form("assessment_form"):
    project_id = st.text_input(
        "Project ID",
        value=selected_project or "SORCS1_KI_v1",
        help="Defaults to the project selected in the sidebar.",
    )
    cell_line = st.text_input("Cell line / clone (if known)", value="BIHi005-A-1X (bulk)")
    assay_type = st.selectbox("Assessment type", ["Sanger-Indel", "PCR-Genotyping"])

    tool_used = None
    total_indel_pct = None
    ki_pct = None
    top_indels: list[IndelItem] = []
    pcr_bands: Optional[PCRBands] = None

    if assay_type == "Sanger-Indel":
        tool_used = st.selectbox("Tool used", ["DECODR", "ICE", "TIDE", "SeqScreener", "TIDER"])
        st.markdown("**Provide results** (upload export and/or enter values)")

        res_file = st.file_uploader(
            "Tool output (CSV/JSON/PDF/screenshot allowed)",
            type=["csv", "json", "pdf", "png", "jpg", "jpeg"],
        )
        if res_file is not None:
            saved_path = save_uploaded_file(
                selected_project,
                res_file,
                upload_dir,
                category="uploads",
                context={"step": "assessment", "kind": "tool_result"},
            )
            attachments["tool_result"] = str(saved_path)
            saved_attachment_paths.append(saved_path)

        total_indel_pct = st.number_input(
            "Total indel % (from tool)",
            min_value=0.0,
            max_value=100.0,
            step=0.1,
            value=0.0,
        )

        if tool_used == "TIDER":
            ki_pct = st.number_input(
                "Knock-in % (from TIDER)", min_value=0.0, max_value=100.0, step=0.1
            )

        st.markdown("Top indels (optional)")
        for idx in range(3):
            size_value = st.number_input(
                f"Indel {idx + 1} size (bp, negative=deletion, positive=insertion)",
                value=0,
                key=f"indel_size_{idx}",
            )
            freq_value = st.number_input(
                f"Indel {idx + 1} frequency (%)",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                key=f"indel_freq_{idx}",
            )
            if freq_value > 0:
                top_indels.append(IndelItem(size_bp=int(size_value), frequency_pct=float(freq_value)))

    else:
        st.markdown("**PCR genotyping**")
        wt_bp = st.number_input("Expected WT band (bp)", min_value=0, value=0)
        edited_bp = st.number_input("Expected edited band (bp)", min_value=0, value=0)
        observed_bands = st.text_input("Observed bands (comma-separated bp)", value="")
        observed_list = [
            int(item.strip()) for item in observed_bands.split(",") if item.strip().isdigit()
        ]

        gel = st.file_uploader("Gel image (PNG/JPG)", type=["png", "jpg", "jpeg"])
        if gel is not None:
            saved_path = save_uploaded_file(
                selected_project,
                gel,
                upload_dir,
                category="uploads",
                context={"step": "assessment", "kind": "gel_image"},
            )
            attachments["gel_image"] = str(saved_path)
            saved_attachment_paths.append(saved_path)

        pcr_bands = PCRBands(
            wt_bp=wt_bp or None,
            edited_bp=edited_bp or None,
            observed_bands_bp=observed_list,
        )
        tool_used = "PCR-Genotyping"

    sequencing_files = st.file_uploader(
        "Sequencing data (AB1/FASTQ/ZIP)",
        type=["ab1", "abi", "fastq", "fq", "gz", "zip"],
        accept_multiple_files=True,
        key="assessment_sequencing_files",
        help="Upload raw traces or processed sequencing archives associated with this assessment.",
    )
    if sequencing_files:
        existing = sum(1 for key in attachments if key.startswith("sequencing_data"))
        for idx, seq_file in enumerate(sequencing_files, start=1):
            saved_path = save_uploaded_file(
                selected_project,
                seq_file,
                upload_dir,
                category="uploads",
                context={"step": "assessment", "kind": "sequencing_data"},
            )
            attachments[f"sequencing_data_{existing + idx}"] = str(saved_path)
            saved_attachment_paths.append(saved_path)

    decision = st.selectbox("Decision", ["proceed_to_cloning", "repeat_edit", "archive"])
    notes = st.text_area("Notes")
    author = st.text_input("Author", value="Narasimha Telugu")

    submitted = st.form_submit_button("Save snapshot & compute hash")

if submitted:
    try:
        snapshot = AssessmentSnapshot(
            project_id=project_id,
            cell_line=cell_line,
            assay_type=assay_type,
            tool_used=tool_used,
            total_indel_pct=total_indel_pct,
            ki_pct=ki_pct,
            top_indels=top_indels,
            decision=decision,
            pcr=pcr_bands if assay_type == "PCR-Genotyping" else None,
            notes=notes or None,
            author=author,
            timestamp_unix=int(time.time()),
            files=attachments,
        )
        payload_dict = json.loads(snapshot.model_dump_json())
        payload = json.dumps(payload_dict, indent=2).encode("utf-8")
        digest = sha256_bytes(payload)
        outfile = snapshot_dir / f"{snapshot.project_id}_{snapshot.timestamp_unix}_{digest[:12]}.json"
        outfile.write_bytes(payload)

        st.session_state["assessment_snapshot"] = {
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
                    "step": "assessment",
                    "path": str(outfile),
                    "digest": digest,
                    "timestamp": snapshot.timestamp_unix,
                },
            )
            append_audit(
                selected_project,
                {
                    "ts": int(time.time()),
                    "step": "assessment",
                    "action": "snapshot_saved",
                    "snapshot_path": str(outfile),
                },
            )

        st.success("Assessment snapshot saved.")
        st.code(f"SHA-256: {digest}", language="text")
        st.caption(f"Saved: {outfile}")
        if saved_attachment_paths:
            st.caption("Attachment previews")
            for idx, path in enumerate(saved_attachment_paths):
                preview_file(path, label=path.name, key_prefix=f"assessment_attach_{idx}")
        st.info("Snapshot ready for on-chain anchoring with step label 'Assessment'.")

        readiness = evaluate_assessment_readiness(payload_dict, attachments)
        st.session_state["assessment_readiness"] = readiness
        if readiness["ready"]:
            st.success("Readiness check passed: Assessment step is complete.")
        else:
            st.warning("Resolve the following before anchoring:")
            for issue in readiness["issues"]:
                st.write(f"- {issue}")
        for warning in readiness["warnings"]:
            st.caption(f"Warning: {warning}")

        image_attachments = [p for p in saved_attachment_paths if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
        pdf_filename = build_step_filename(snapshot.project_id, "assessment", snapshot.timestamp_unix)
        pdf_path = report_dir / pdf_filename
        snapshot_to_pdf(
            payload_dict,
            pdf_path,
            f"Assessment Snapshot - {snapshot.project_id}",
            image_paths=image_attachments or None,
        )
        with pdf_path.open("rb") as pdf_file:
            st.download_button(
                "Download assessment snapshot as PDF",
                pdf_file,
                file_name=pdf_path.name,
                key="assessment_pdf_download",
            )
        st.caption(f"PDF saved: {pdf_path}")
        if selected_project:
            register_file(
                selected_project,
                "reports",
                {
                    "step": "assessment",
                    "path": str(pdf_path),
                    "timestamp": snapshot.timestamp_unix,
                    "digest": sha256_bytes(pdf_path.read_bytes()),
                    "type": "pdf",
                },
            )
    except ValidationError as exc:
        st.error(exc)

snapshot_state = st.session_state.get("assessment_snapshot")
readiness_state = st.session_state.get("assessment_readiness")
if snapshot_state:
    st.divider()
    st.subheader("On-chain Anchoring")
    st.write("Anchor the assessment hash to the AssuredRegistry contract with step `Assessment`.")
    st.code(f"SHA-256: {snapshot_state['digest']}", language="text")
    st.caption(f"Snapshot: {snapshot_state['outfile']}")

    if readiness_state:
        if readiness_state["ready"]:
            st.success("Ready to anchor: Assessment readiness checks passed.")
        else:
            st.error("Assessment readiness checks failed:")
            for issue in readiness_state["issues"]:
                st.write(f"- {issue}")
            if readiness_state["warnings"]:
                st.caption("Warnings:")
                for warning in readiness_state["warnings"]:
                    st.caption(f"Warning: {warning}")
    else:
        st.info("Generate or reload a snapshot to evaluate readiness.")

    anchor_disabled = not readiness_state or not readiness_state["ready"]

    if st.button("Anchor hash on-chain (Sepolia)", disabled=anchor_disabled):
        try:
            result = send_log_tx(
                hex_digest=snapshot_state["digest"],
                step="Assessment",
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
                "step": "Assessment",
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
                        "step": "assessment",
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
                        "step": "assessment",
                        "action": "anchored",
                        "tx_hash": result["tx_hash"],
                    },
                )
        except Exception as exc:
            st.error(f"On-chain anchoring failed: {exc}")
