import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st
from pandas.errors import EmptyDataError

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

DATA_DIR = ROOT / "data" / "registry"
DATA_DIR.mkdir(parents=True, exist_ok=True)
REG_PATH = DATA_DIR / "master_bank_registry.csv"

COLUMNS = [
    "IRIS_ID",
    "edit_Name",
    "HPSCReg_Name",
    "status",
    "Reason_ForHold",
    "Banking",
    "Morph",
    "Flourescence",
    "Myco",
    "Cellpellet_Max",
    "SNP",
    "STR",
    "Cellpellet_PCR",
    "verification",
    "g_Banding",
    "Pluri_FACS",
    "PluriIF_Staining",
    "Trilineage",
    "COA",
    "CTS",
    "Responsible_Person",
    "MB_type",
    "Bemerkung",
    "LN2_Seedbank",
    "Masterbank_freezing_Date",
    "Bank_size",
    "LN2_Masterbank",
]

SUMMARY_FIELDS = [
    ("Morph", "Morphology"),
    ("Myco", "Mycotest"),
    ("verification", "Editing verification"),
    ("SNP", "SNP / Karyotyping"),
    ("STR", "STR profiling"),
]

ATTACHMENT_FIELDS = SUMMARY_FIELDS

FALLBACK_UPLOAD_DIR = DATA_DIR / "uploads"
FALLBACK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
FALLBACK_SNAPSHOT_DIR = DATA_DIR / "snapshots"
FALLBACK_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
FALLBACK_REPORT_DIR = DATA_DIR / "reports"
FALLBACK_REPORT_DIR.mkdir(parents=True, exist_ok=True)


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def normalize_value(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "item"


def ensure_registry_file() -> None:
    if not REG_PATH.exists() or REG_PATH.stat().st_size == 0:
        pd.DataFrame(columns=COLUMNS).to_csv(REG_PATH, index=False)


ensure_registry_file()


def evaluate_master_bank_readiness(snapshot_payload: dict) -> dict:
    issues: list[str] = []
    warnings: list[str] = []

    entries = snapshot_payload.get("entries") or []
    if not entries:
        issues.append("No master bank entries captured in snapshot.")
        return {"ready": False, "issues": issues, "warnings": warnings}

    required_fields = ["IRIS_ID", "HPSCReg_Name", "status", "Responsible_Person", "Masterbank_freezing_Date"]

    for entry in entries:
        label = entry.get("label", "Entry")
        data = entry.get("data", {})
        attachments = entry.get("attachments", {})

        for field in required_fields:
            if not (data.get(field) or "").strip():
                issues.append(f"{label}: '{field}' is required.")

        for field_key, field_label in SUMMARY_FIELDS:
            value = (data.get(field_key) or "").strip()
            if value and field_key not in attachments:
                warnings.append(f"{label}: attach evidence for '{field_label}'.")

        status = (data.get("status") or "").strip().lower()
        if status and status not in {"released", "ready", "approved"}:
            warnings.append(f"{label}: status '{data.get('status', '')}' is not marked as released/ready.")

    return {"ready": not issues, "issues": issues, "warnings": warnings}

init_page("Master Bank & QC Registry")
st.title("Step 7 - Master Bank & QC Registry")
st.caption("Review and maintain master bank entries; anchor finalised records on-chain.")

selected_project = use_project("project")
if selected_project:
    upload_dir = project_subdir(selected_project, "uploads", "master_bank_registry")
    snapshot_dir = project_subdir(selected_project, "snapshots", "master_bank_registry")
    report_dir = project_subdir(selected_project, "reports", "master_bank_registry")
else:
    upload_dir = FALLBACK_UPLOAD_DIR
    snapshot_dir = FALLBACK_SNAPSHOT_DIR
    report_dir = FALLBACK_REPORT_DIR


def save_registry_upload(upload, label: str, field_label: str) -> Optional[str]:
    if not upload:
        return None
    path = save_uploaded_file(
        selected_project,
        upload,
        upload_dir,
        category="uploads",
        context={"step": "master_bank_registry", "label": label, "field": field_label},
    )
    return path.as_posix()


try:
    df = pd.read_csv(REG_PATH, dtype=str)
except EmptyDataError:
    df = pd.DataFrame(columns=COLUMNS)
    df.to_csv(REG_PATH, index=False)

df = df.fillna("")

col_person, col_search = st.columns(2)
with col_person:
    person_filter = st.text_input("Responsible contains")
with col_search:
    global_search = st.text_input("Global search (IRIS_ID / edit / name)")

filtered = df.copy()
if person_filter:
    filtered = filtered[filtered["Responsible_Person"].str.contains(person_filter, case=False, na=False)]
if global_search:
    mask = False
    for key in ["IRIS_ID", "edit_Name", "HPSCReg_Name"]:
        mask = mask | filtered[key].str.contains(global_search, case=False, na=False)
    filtered = filtered[mask]

st.markdown("### Registry editor")

MAX_BANKS = 2

if filtered.empty:
    st.info("No rows match the current filters. Provide details for MB1 and MB2 manually.")
    working = pd.DataFrame([{col: "" for col in COLUMNS} for _ in range(MAX_BANKS)])
    working_indices: List[Optional[int]] = []
else:
    working = filtered.copy().fillna("")
    if len(working) > MAX_BANKS:
        st.warning("More than two entries match the filters; showing the first two (MB1 / MB2).")
        working = working.head(MAX_BANKS)
    working_indices = working.index.tolist()

if len(working) < MAX_BANKS:
    padding = pd.DataFrame([{col: "" for col in COLUMNS} for _ in range(MAX_BANKS - len(working))])
    working = pd.concat([working, padding], ignore_index=True)
    working_indices.extend([None] * (MAX_BANKS - len(working_indices)))

key_column = "IRIS_ID" if working["IRIS_ID"].astype(str).str.strip().ne("").any() else "HPSCReg_Name"
if key_column not in working.columns:
    key_column = "IRIS_ID"

actual_labels: List[str] = []
for idx in range(MAX_BANKS):
    raw_value = working.loc[idx, key_column] if key_column in working.columns else ""
    actual_labels.append(str(raw_value).strip() or f"Entry_{idx + 1}")

display_labels = [f"MB{i+1}" for i in range(MAX_BANKS)]

vertical_editor = pd.DataFrame({"Field": COLUMNS})
for idx, display_label in enumerate(display_labels):
    row = working.loc[idx, COLUMNS] if idx < len(working) else pd.Series({col: "" for col in COLUMNS})
    vertical_editor[display_label] = [row.get(col, "") for col in COLUMNS]

editor_result = st.data_editor(
    vertical_editor,
    column_config={
        "Field": st.column_config.Column("Field", disabled=True),
    },
    key="registry_editor_two_columns",
    use_container_width=True,
    num_rows="dynamic",
    hide_index=True,
)

table_result = editor_result.set_index("Field")
edited_entries: List[Dict[str, str]] = []
for display_label in display_labels:
    entry = {
        field: normalize_value(table_result.at[field, display_label] if display_label in table_result.columns else "")
        for field in COLUMNS
    }
    edited_entries.append(entry)

uploads_state = st.session_state.setdefault("mb_registry_uploads", {})
attachments_result: Dict[str, Dict[str, str]] = {}
for display_label in display_labels:
    attachments_result[display_label] = uploads_state.get(display_label, {}).copy()

if st.button("Save changes", disabled=len(edited_entries) == 0, key="registry_save_changes"):
    updated_df = df.copy()
    for idx, entry in enumerate(edited_entries):
        assignment = {col: normalize_value(entry.get(col, "")) for col in updated_df.columns}
        has_content = any(value.strip() for value in assignment.values())
        if not has_content:
            continue
        target_index = working_indices[idx] if idx < len(working_indices) else None
        if target_index is not None and target_index in updated_df.index:
            for col, value in assignment.items():
                updated_df.at[target_index, col] = value
        else:
            updated_df = pd.concat([updated_df, pd.DataFrame([assignment])], ignore_index=True)
    updated_df = updated_df.fillna("")
    updated_df.to_csv(REG_PATH, index=False)
    df = updated_df
    st.success("Registry saved.")

st.markdown("### Summary")
if not edited_entries:
    st.info("No registry rows to summarise yet.")
else:
    summary_payload = {"Metric": [label for _, label in SUMMARY_FIELDS]}
    for display_label, entry in zip(display_labels, edited_entries):
        values = []
        for field_key, field_label in SUMMARY_FIELDS:
            value = entry.get(field_key, "").strip()
            values.append(value or "-")
        summary_payload[display_label] = values
    summary_df = pd.DataFrame(summary_payload)
    st.write(summary_df.to_html(escape=False, index=False), unsafe_allow_html=True)

if display_labels:
    st.markdown("### Upload evidence")
for display_label, entry_data in zip(display_labels, edited_entries):
    st.subheader(display_label)
    for field_key, field_label in ATTACHMENT_FIELDS:
        uploader_key = f"upload_{slugify(display_label)}_{slugify(field_label)}"
        uploaded_file = st.file_uploader(f"{field_label} data ({display_label})", key=uploader_key)
        if uploaded_file:
            saved_path = save_registry_upload(uploaded_file, display_label, field_label)
            if saved_path:
                attachments_result[display_label][field_key] = saved_path
                uploads_state.setdefault(display_label, {})[field_key] = saved_path
                st.success(f"Stored file at {saved_path}")
        existing = attachments_result[display_label].get(field_key)
        if existing:
            preview_file(
                Path(existing),
                label=Path(existing).name,
                key_prefix=f"{slugify(display_label)}_{field_key}",
            )

    seq_uploader_key = f"upload_{slugify(display_label)}_sequencing"
    seq_files = st.file_uploader(
        f"Sequencing data ({display_label})",
        type=["ab1", "abi", "fastq", "fq", "gz", "zip"],
        accept_multiple_files=True,
        key=seq_uploader_key,
        help="Upload raw traces or processed sequencing archives used during release verification.",
    )
    if seq_files:
        seq_list = attachments_result[display_label].setdefault("sequencing_data", [])
        state_list = uploads_state.setdefault(display_label, {}).setdefault("sequencing_data", [])
        for seq_file in seq_files:
            saved_path = save_registry_upload(seq_file, display_label, "Sequencing data")
            if saved_path:
                seq_list.append(saved_path)
                state_list.append(saved_path)
                st.success(f"Stored sequencing file at {saved_path}")

    existing_seq = attachments_result[display_label].get("sequencing_data", [])
    if existing_seq:
        st.caption("Sequencing data")
        for idx, seq_path in enumerate(existing_seq):
            preview_file(
                Path(seq_path),
                label=Path(seq_path).name,
                key_prefix=f"{slugify(display_label)}_seq_{idx}",
            )

    uploads_state[display_label] = attachments_result.get(display_label, {})

st.markdown("### Snapshot & hash")
snapshot_state = st.session_state.get("mb_registry_snapshot")
if snapshot_state:
    st.info(f"Latest snapshot: {snapshot_state['path']} (SHA-256: {snapshot_state['digest']})")

if st.button("Save snapshot & compute hash", disabled=len(edited_entries) == 0, key="registry_snapshot"):
    timestamp_unix = int(time.time())
    snapshot_payload = {
        "timestamp_unix": timestamp_unix,
        "entries": [],
    }
    for display_label, entry in zip(display_labels, edited_entries):
        snapshot_payload["entries"].append(
            {
                "label": display_label,
                "data": entry,
                "attachments": attachments_result.get(display_label, {}),
            }
        )
    json_payload = json.dumps(snapshot_payload, indent=2)
    digest = sha256_text(json_payload)
    snapshot_path = snapshot_dir / f"master_bank_snapshot_{timestamp_unix}_{digest[:12]}.json"
    snapshot_path.write_text(json_payload, encoding="utf-8")
    st.session_state["mb_registry_snapshot"] = {
        "digest": digest,
        "path": snapshot_path.as_posix(),
        "payload": snapshot_payload,
    }
    readiness = evaluate_master_bank_readiness(snapshot_payload)
    st.session_state["mb_registry_readiness"] = readiness
    st.success("Snapshot saved.")
    st.code(f"SHA-256: {digest}", language="text")
    st.caption(f"Saved: {snapshot_path}")
    if selected_project:
        register_file(
            selected_project,
            "snapshots",
            {
                "step": "master_bank_registry",
                "path": str(snapshot_path),
                "digest": digest,
                "timestamp": timestamp_unix,
            },
        )
        append_audit(
            selected_project,
            {
                "ts": int(time.time()),
                "step": "master_bank_registry",
                "action": "snapshot_saved",
                "snapshot_path": str(snapshot_path),
            },
        )

    if readiness["ready"]:
        st.success("Readiness check passed: registry entries complete.")
    else:
        st.warning("Resolve the following before anchoring:")
        for issue in readiness["issues"]:
            st.write(f"- {issue}")
    for warning in readiness["warnings"]:
        st.caption(f"Warning: {warning}")

snapshot_state = st.session_state.get("mb_registry_snapshot")

export_col_csv, export_col_xlsx = st.columns(2)
with export_col_csv:
    st.download_button(
        "Download CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="master_bank_registry.csv",
        mime="text/csv",
    )
with export_col_xlsx:
    export_path = DATA_DIR / "registry_export.xlsx"
    with pd.ExcelWriter(export_path, engine="xlsxwriter") as buffer:
        df.to_excel(buffer, index=False, sheet_name="Registry")
    st.download_button(
        "Download Excel",
        data=export_path.read_bytes(),
        file_name="master_bank_registry.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    export_path.unlink(missing_ok=True)

if snapshot_state:
    st.markdown("### Snapshot PDF")
    image_paths = []
    for label_dict in attachments_result.values():
        for path_str in label_dict.values():
            p = Path(path_str)
            if p.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                image_paths.append(p)
    pdf_timestamp = snapshot_state["payload"].get("timestamp_unix", int(time.time()))
    pdf_filename = build_step_filename(selected_project, "master-bank", pdf_timestamp)
    pdf_path = report_dir / pdf_filename
    snapshot_to_pdf(
        snapshot_state["payload"],
        pdf_path,
        "Master Bank Registry Snapshot",
        image_paths=image_paths or None,
    )
    with pdf_path.open("rb") as pdf_file:
        st.download_button(
            "Download snapshot as PDF",
            pdf_file,
            file_name=pdf_path.name,
            key="mb_pdf_download",
        )
    st.caption(f"PDF saved: {pdf_path}")
    if selected_project:
        pdf_digest = sha256_bytes(pdf_path.read_bytes())
        register_file(
            selected_project,
            "reports",
            {
                "step": "master_bank_registry",
                "path": str(pdf_path),
                "timestamp": pdf_timestamp,
                "digest": pdf_digest,
                "type": "pdf",
            },
        )

st.markdown("### Anchor snapshot on-chain")
if not snapshot_state:
    st.info("Create a snapshot before anchoring on-chain.")
else:
    snapshot_path = Path(snapshot_state["path"])
    readiness_state = st.session_state.get("mb_registry_readiness")
    if not readiness_state:
        readiness_state = evaluate_master_bank_readiness(snapshot_state["payload"])
        st.session_state["mb_registry_readiness"] = readiness_state

    st.caption(f"Snapshot to anchor: {snapshot_path}")
    if readiness_state:
        if readiness_state["ready"]:
            st.success("Ready to anchor: registry readiness checks passed.")
        else:
            st.error("Registry readiness checks failed:")
            for issue in readiness_state["issues"]:
                st.write(f"- {issue}")
            if readiness_state["warnings"]:
                st.caption("Warnings:")
                for warning in readiness_state["warnings"]:
                    st.caption(f"Warning: {warning}")
    else:
        st.info("Reload snapshot to evaluate readiness.")

    anchor_disabled = not readiness_state or not readiness_state["ready"]

    if st.button("Anchor latest snapshot", key="anchor_master_bank", disabled=anchor_disabled):
        digest = snapshot_state["digest"]
        metadata_uri = snapshot_path.resolve().as_uri()
        res = send_log_tx(hex_digest=digest, step="Master Bank Registry", metadata_uri=metadata_uri)
        st.success("Anchored on-chain.")
        st.write(f"Tx: {res['tx_hash']}")
        st.json(res["receipt"])
        if selected_project:
            register_chain_tx(
                selected_project,
                {
                    "step": "master_bank_registry",
                    "tx_hash": res["tx_hash"],
                    "digest": digest,
                    "timestamp": int(time.time()),
                    "metadata_uri": metadata_uri,
                },
            )
            append_audit(
                selected_project,
                {
                    "ts": int(time.time()),
                    "step": "master_bank_registry",
                    "action": "anchored",
                    "tx_hash": res["tx_hash"],
                },
            )
