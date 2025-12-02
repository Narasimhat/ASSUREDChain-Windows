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
    ensure_local_path,
    load_project_meta,
    normalize_path_for_storage,
    project_subdir,
    register_chain_tx,
    register_file,
    update_project_meta,
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
project_meta = load_project_meta(selected_project) if selected_project else {}
st.title("Step 5 - IOTA Clone Development and Verification")
st.caption("Checklist for isoCell/IOTA cloning workflow. Anchors on-chain as step 'Cloning'.")

attachments: Dict[str, str] = {}
saved_attachment_paths: list[Path] = []

# Get defaults from project metadata
cloning_defaults = project_meta.get("cloning_defaults", {})
default_cell_line = cloning_defaults.get("cell_line") or project_meta.get("cell_line", "BIHi005-A-1X")
default_operator = cloning_defaults.get("operator", "Narasimha Telugu")
default_date = cloning_defaults.get("date", time.strftime("%Y-%m-%d"))
default_notes = cloning_defaults.get("notes", "")

if selected_project:
    snapshot_dir = project_subdir(selected_project, "snapshots", "cloning")
    upload_dir = project_subdir(selected_project, "uploads", "cloning")
    report_dir = project_subdir(selected_project, "reports", "cloning")
else:
    snapshot_dir = FALLBACK_DATA_DIR
    upload_dir = FALLBACK_UPLOAD_DIR
    report_dir = FALLBACK_DATA_DIR

# Fixed snapshot filename (no timestamp, will update same file)
fixed_snapshot_file = snapshot_dir / f"{selected_project or 'default'}_cloning_snapshot.json" if selected_project else snapshot_dir / "default_cloning_snapshot.json"

# Load existing snapshot if it exists
existing_snapshot_data = {}
local_snapshot_file = ensure_local_path(fixed_snapshot_file)
if local_snapshot_file.exists():
    try:
        existing_snapshot_data = json.loads(local_snapshot_file.read_text())
    except Exception:
        pass

# Get defaults - prioritize existing snapshot over project metadata defaults
cloning_defaults = project_meta.get("cloning_defaults", {})
default_cell_line = existing_snapshot_data.get("cell_line") or cloning_defaults.get("cell_line") or project_meta.get("cell_line", "BIHi005-A-1X")
default_operator = existing_snapshot_data.get("operator") or cloning_defaults.get("operator", "Narasimha Telugu")
default_date = existing_snapshot_data.get("date") or cloning_defaults.get("date", time.strftime("%Y-%m-%d"))
default_notes = existing_snapshot_data.get("notes") or cloning_defaults.get("notes", "")
PREFIX_TO_SECTION = {
    "dev": "development",
    "ver": "verification",
    "seed": "seedbank",
    "karyo": "karyo",
}
REQUIRED_SECTIONS = {
    "development": "Development",
    "verification": "Verification",
    "seedbank": "Seedbank",
    "karyo": "Karyotyping",
}


def evidence_block(step_key: str, label: str, prefix: str) -> StepEvidence:
    # Get previous values from existing snapshot using proper section mapping
    section_name = PREFIX_TO_SECTION.get(prefix, prefix)
    prev_section = existing_snapshot_data.get(section_name, {})
    if isinstance(prev_section, dict):
        prev_step = prev_section.get(step_key, {})
        if isinstance(prev_step, dict):
            prev_done = prev_step.get("done", False)
            prev_notes = prev_step.get("notes", "")
            prev_file_path = prev_step.get("file_path")
        else:
            prev_done = False
            prev_notes = ""
            prev_file_path = None
    else:
        prev_done = False
        prev_notes = ""
        prev_file_path = None
    
    with st.expander(label, expanded=prev_done):
        # Use unique key combining prefix, step, and file path to ensure freshness
        checkbox_key = f"{prefix}_{step_key}_done_{hash(str(fixed_snapshot_file))}"
        notes_key = f"{prefix}_{step_key}_notes_{hash(str(fixed_snapshot_file))}"
        file_key = f"{prefix}_{step_key}_file_{hash(str(fixed_snapshot_file))}"
        
        # Initialize checkbox state if not exists
        if checkbox_key not in st.session_state:
            st.session_state[checkbox_key] = prev_done
            
        done = st.checkbox("Done", key=checkbox_key)
        notes = st.text_area("Notes", value=prev_notes or "", key=notes_key)
        
        # Show previously uploaded file if exists
        if prev_file_path and Path(prev_file_path).exists():
            st.caption(f"📎 Existing file: {Path(prev_file_path).name}")
            
        upload = st.file_uploader("Evidence file (optional)", key=file_key)
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
            # Use new upload path
            return StepEvidence(done=done, notes=notes or None, file_path=str(saved_path))
        else:
            # Keep existing file path if no new upload
            if prev_file_path:
                attachments[f"{prefix}_{step_key}"] = prev_file_path
                if Path(prev_file_path).exists():
                    saved_attachment_paths.append(Path(prev_file_path))
            return StepEvidence(done=done, notes=notes or None, file_path=prev_file_path)


def save_section_data(section_name: str, section_data: dict):
    """Save a specific section to the snapshot file"""
    try:
        # Get local path for file operations
        local_file = ensure_local_path(fixed_snapshot_file)
        
        # Load existing snapshot or create new one
        if local_file.exists():
            full_data = json.loads(local_file.read_text())
        else:
            full_data = {
                "project_id": selected_project or "SORCS1_KI_v1",
                "cell_line": default_cell_line,
                "operator": default_operator,
                "date": default_date,
                "notes": None,
                "attachments": {},
                "timestamp_unix": int(time.time()),
            }
        
        # Update the specific section
        full_data[section_name] = section_data
        full_data["timestamp_unix"] = int(time.time())
        
        # Update attachments dictionary from the section data
        if "attachments" not in full_data:
            full_data["attachments"] = {}
        
        # Extract file paths from section data and add to attachments
        for step_key, step_data in section_data.items():
            if isinstance(step_data, dict) and step_data.get("file_path"):
                attachment_key = f"{section_name[:4]}_{step_key}"  # e.g., "deve_thaw_plate"
                full_data["attachments"][attachment_key] = step_data["file_path"]
        
        # Save to file
        payload = json.dumps(full_data, indent=2).encode("utf-8")
        local_file.write_bytes(payload)
        
        st.success(f"✅ {section_name.title()} section saved!")
        st.caption(f"Saved to: {local_file}")
        return True
    except Exception as e:
        st.error(f"Failed to save: {str(e)}")
        return False


# Display project information (not form fields, just display)
st.subheader("Project Information")
cols = st.columns(4)
with cols[0]:
    st.metric("Project ID", selected_project or "SORCS1_KI_v1")
with cols[1]:
    st.metric("Cell line", default_cell_line)
with cols[2]:
    st.metric("Operator", default_operator)
with cols[3]:
    st.metric("Date", default_date)

st.divider()

# Section 1: Development
st.subheader("📝 Part 1: Development of Single-Cell Edited Clones (IOTA)")
with st.form("development_form"):
    development = IOTADevelopment(
        thaw_plate=evidence_block("thaw_plate", "Thaw and plate cells for isoCell/IOTA cloning", "dev"),
        plate_on_iota=evidence_block("plate_on_iota", "Seed cells on IOTA plates", "dev"),
        extract_clones=evidence_block("extract_clones", "Extract clones into 2 x 48 x 96-well plates", "dev"),
        isolate_dna=evidence_block("isolate_dna", "Isolate DNA from one plate for verification", "dev"),
        feed_plate1=evidence_block("feed_plate1", "Feed and maintain second plate (days 1-3)", "dev"),
        feed_plate2=evidence_block("feed_plate2", "Feed and maintain second plate (days 4-7)", "dev"),
    )
    save_dev = st.form_submit_button("💾 Save Development Section")

if save_dev:
    development_dict = json.loads(development.model_dump_json())
    save_section_data("development", development_dict)
    st.rerun()

st.divider()

# Section 2: Verification
st.subheader("🔬 Part 2: Clone Verification")
with st.form("verification_form"):
    verification = CloneVerification(
        pcr_seq=evidence_block("pcr_seq", "Perform PCR and sequencing", "ver"),
        seq_analysis=evidence_block("seq_analysis", "Analyse sequencing results", "ver"),
        split_positive=evidence_block("split_positive", "Split positive clones for expansion", "ver"),
        report=evidence_block("report", "Compile report for positive clones", "ver"),
    )
    save_ver = st.form_submit_button("💾 Save Verification Section")

if save_ver:
    verification_dict = json.loads(verification.model_dump_json())
    save_section_data("verification", verification_dict)
    st.rerun()

st.divider()

# Section 3: Seedbank
st.subheader("🌱 Part 3: Seedbank Production")
with st.form("seedbank_form"):
    seedbank = SeedbankProduction(
        split_positive=evidence_block("split_positive", "Split positive clones into expansion plates", "seed"),
        produce_seedbank=evidence_block("produce_seedbank", "Create seed bank from verified clones", "seed"),
        prepare_gdna=evidence_block("prepare_gdna", "Prepare cell pellets for genomic DNA", "seed"),
        transfer_validation=evidence_block("transfer_validation", "Transfer seed banks for validation", "seed"),
    )
    save_seed = st.form_submit_button("💾 Save Seedbank Section")

if save_seed:
    seedbank_dict = json.loads(seedbank.model_dump_json())
    save_section_data("seedbank", seedbank_dict)
    st.rerun()

st.divider()

# Section 4: Karyotyping
st.subheader("🧬 Part 4: Karyotyping and Analysis")
with st.form("karyo_form"):
    karyo = KaryotypingAnalysis(
        submit_samples=evidence_block("submit_samples", "Submit samples for karyotyping (e.g., Bonn)", "karyo"),
        analyze_and_establish_master=evidence_block(
            "analyze_and_establish_master", "Analyse karyotyping & establish master cell bank", "karyo"
        ),
    )
    save_karyo = st.form_submit_button("💾 Save Karyotyping Section")

if save_karyo:
    karyo_dict = json.loads(karyo.model_dump_json())
    save_section_data("karyo", karyo_dict)
    st.rerun()

st.divider()

# Final section: Generate full snapshot with hash and PDF
st.subheader("📋 Final Snapshot & On-chain Anchoring")
st.write("Once all sections are complete, generate the final snapshot with hash computation and PDF report.")

with st.form("final_snapshot_form"):
    notes = st.text_area("General notes", value=default_notes)
    submitted = st.form_submit_button("📋 Generate Final Snapshot & Compute Hash", help="Creates PDF and prepares for blockchain")

if submitted:
    # Load the complete saved data
    local_snapshot_file = ensure_local_path(fixed_snapshot_file)
    if not local_snapshot_file.exists():
        st.error("Please complete and save all sections before generating final snapshot.")
    else:
        try:
            full_data = json.loads(local_snapshot_file.read_text())

            # Ensure every section has been saved at least once
            missing_sections = [
                label
                for key, label in REQUIRED_SECTIONS.items()
                if not isinstance(full_data.get(key), dict) or not full_data.get(key)
            ]
            if missing_sections:
                st.error(f"Save these sections before generating the final snapshot: {', '.join(missing_sections)}.")
                st.stop()
            
            # Update notes
            full_data["notes"] = notes or None
            full_data["timestamp_unix"] = int(time.time())
            
            # Reconstruct the snapshot object for validation
            snapshot = IOTACloningSnapshot(
                project_id=full_data.get("project_id"),
                cell_line=full_data.get("cell_line"),
                operator=full_data.get("operator"),
                date=full_data.get("date"),
                development=IOTADevelopment(**full_data.get("development", {})),
                verification=CloneVerification(**full_data.get("verification", {})),
                seedbank=SeedbankProduction(**full_data.get("seedbank", {})),
                karyo=KaryotypingAnalysis(**full_data.get("karyo", {})),
                notes=full_data.get("notes"),
                attachments=full_data.get("attachments", {}),
                timestamp_unix=full_data.get("timestamp_unix"),
            )
            
            payload_dict = json.loads(snapshot.model_dump_json())
            payload = json.dumps(payload_dict, indent=2).encode("utf-8")
            
            # Save with updated notes
            fixed_snapshot_file = ensure_local_path(fixed_snapshot_file)
            fixed_snapshot_file.write_bytes(payload)
            
            digest = sha256_bytes(payload)
            outfile = fixed_snapshot_file
            
            # Full snapshot processing with hash and PDF
            st.session_state["iota_cloning_snapshot"] = {
                "digest": digest,
                "outfile": str(outfile),
                "metadata_uri": outfile.resolve().as_uri(),
                "payload": payload_dict,
                "project_id": full_data.get("project_id"),
            }

            if selected_project:
                register_file(
                    selected_project,
                    "snapshots",
                    {
                        "step": "cloning",
                        "path": normalize_path_for_storage(outfile),
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

            # Fixed PDF filename (updates same file)
            pdf_filename = f"{snapshot.project_id}_cloning_report.pdf"
            pdf_path = report_dir / pdf_filename
            
            # Get saved attachment paths from existing data
            saved_attachment_paths = []
            for key, path_str in full_data.get("attachments", {}).items():
                if path_str:
                    path_obj = Path(path_str)
                    if path_obj.exists():
                        saved_attachment_paths.append(path_obj)
            
            image_attachments = [p for p in saved_attachment_paths if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
            snapshot_to_pdf(
                payload_dict,
                pdf_path,
                f"IOTA Cloning Snapshot - {snapshot.project_id}",
                image_paths=image_attachments or None,
            )
            pdf_data = pdf_path.read_bytes()
            st.download_button(
                "Download IOTA cloning snapshot as PDF",
                pdf_data,
                file_name=pdf_path.name,
                mime="application/pdf",
                key="cloning_pdf_download",
            )
            st.caption(f"PDF saved: {pdf_path}")
            if selected_project:
                # Check if this PDF path is already registered to avoid duplicates
                from app.components.project_state import load_manifest
                current_manifest = load_manifest(selected_project)
                existing_reports = current_manifest.get("files", {}).get("reports", [])
                pdf_path_str = str(pdf_path)
                already_registered = any(
                    isinstance(e, dict) and e.get("path") == pdf_path_str and e.get("step") == "cloning"
                    for e in existing_reports
                )
                if not already_registered:
                    register_file(
                        selected_project,
                        "reports",
                        {
                            "step": "cloning",
                            "path": pdf_path_str,
                            "timestamp": snapshot.timestamp_unix,
                            "digest": sha256_bytes(pdf_path.read_bytes()),
                            "type": "pdf",
                        },
                    )
                # Save form defaults for next time
                defaults_payload = {
                    "cell_line": snapshot.cell_line,
                    "operator": snapshot.operator,
                    "date": snapshot.date,
                    "notes": snapshot.notes or "",
                }
                update_project_meta(selected_project, {"cloning_defaults": defaults_payload})
            st.info("Snapshot ready for on-chain anchoring with step label 'Cloning'.")
        except ValidationError as exc:
            st.error(exc)
        except Exception as e:
            st.error(f"Error generating snapshot: {str(e)}")

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
            proof_path = ensure_local_path(Path(snapshot_state["outfile"]).with_suffix(".chainproof.json"))
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
