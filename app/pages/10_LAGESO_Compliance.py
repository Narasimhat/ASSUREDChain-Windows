import json
import sys
import time
from pathlib import Path
from typing import Dict, List

import streamlit as st

from app.components.layout import init_page

try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.components.autofill import d
from app.components.file_utils import build_step_filename, snapshot_to_pdf
from app.components.project_state import (
    ensure_dirs,
    load_manifest,
    project_subdir,
    register_chain_tx,
    register_file,
    update_project_meta,
    use_project,
)
from app.components.web3_client import send_log_tx


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


COLUMN_HEADERS = [
    "Lfd.Nr.",
    "Parental cell line",
    "Cell line name",
    "Link Form Z",
    "IRIS ID",
    "Gene symbol (NCBI ID)",
    "crRNA/sgRNA sequence",
    "Donor/Vector (ssODN seq / Addgene cat. number)",
    "Modification Type",
    "Modification description",
    "Zygosity",
    "Date of Registration",
    "RG",
    "Disease",
]


def evaluate_readiness(rows: List[Dict[str, str]]) -> Dict[str, List[str] | bool]:
    issues: List[str] = []
    warnings: List[str] = []
    required = [
        "Lfd.Nr.",
        "Parental cell line",
        "Cell line name",
        "IRIS ID",
        "Gene symbol (NCBI ID)",
        "crRNA/sgRNA sequence",
        "Modification Type",
        "Modification description",
        "Date of Registration",
    ]
    for idx, row in enumerate(rows, start=1):
        for key in required:
            if not (row.get(key) or "").strip():
                issues.append(f"Row {idx}: '{key}' is required.")
        if not (row.get("Donor/Vector (ssODN seq / Addgene cat. number)") or "").strip():
            warnings.append(f"Row {idx}: Donor/Vector information missing.")
        if not (row.get("RG") or "").strip():
            warnings.append(f"Row {idx}: Risk group (RG) not specified.")
    return {"ready": not issues, "issues": issues, "warnings": warnings}

init_page("Step 10 - LAGESO Compliance Manager")
st.title("Step 10 - LAGESO Compliance Manager")
st.caption("Edit compliance entries, save snapshots, and generate the official Excel workbook.")

project_id = use_project("Project")
if not project_id:
    st.warning("Select a project from the sidebar to continue.")
    st.stop()

manifest = load_manifest(project_id)
meta = manifest.get("meta", {})
compliance = meta.get("compliance", {})

project_root = ensure_dirs(project_id)
snapshots_root = project_root / "snapshots"
reports_dir = project_subdir(project_id, "reports", "lageso")
snapshot_dir = project_subdir(project_id, "snapshots", "lageso")

templates_dir = ROOT / "data" / "templates"
available_templates = sorted(templates_dir.glob("*.xlsm"))
saved_template = Path(compliance.get("excel_template_path", "")) if compliance.get("excel_template_path") else None
if available_templates:
    template_labels = [str(p) for p in available_templates]
    default_index = 0
    if saved_template and str(saved_template) in template_labels:
        default_index = template_labels.index(str(saved_template))
    template_choice = st.selectbox("Excel template (.xlsm)", template_labels, index=default_index)
else:
    template_choice = st.text_input(
        "Excel template (.xlsm)",
        str(saved_template or (templates_dir / "13_15 SD Proj 1-3.xlsm")),
    )

template_path = Path(template_choice)
method = st.selectbox("Sheet / method", ["TALENs", "CRISPR plasmids", "CRISPR RNPs"], index=2)
row_defaults = compliance.get("excel_rows", {})
start_row = st.number_input(
    "Start row to write (1-indexed)",
    min_value=1,
    value=row_defaults.get(method, {}).get("start_row", 5),
    step=1,
)


def latest_json(folder: Path) -> Dict:
    if not folder.exists():
        return {}
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for file in files:
        try:
            return json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


design = latest_json(snapshots_root / "design")
assessment = latest_json(snapshots_root / "assessment")
screening = latest_json(snapshots_root / "screening")
master_bank = latest_json(snapshots_root / "master_bank_registry")

design_guides = design.get("selected_guides") or []
design_primers = design.get("primer_pairs") or []
design_donor = design.get("donor") or {}

mb_entries = master_bank.get("entries") or []
mb_data = mb_entries[0].get("data", {}) if mb_entries else {}

cell_line_name = meta.get("cell_line") or mb_data.get("HPSCReg_Name") or compliance.get("cell_line", "")
operator_name = (
    compliance.get("responsible_person") or meta.get("owner") or meta.get("contacts", {}).get("operator", "")
)
bank_identifier = (
    mb_data.get("IRIS_ID")
    or mb_data.get("HPSCReg_Name")
    or compliance.get("bank_id", "")
)

values_preview = {
    "ProjectID": project_id,
    "CellLine": cell_line_name,
    "Gene": meta.get("gene") or design.get("mutation", {}).get("gene", ""),
    "Edit": design.get("mutation", {}).get("edit_intent", ""),
    "Operator": operator_name,
    "Date": time.strftime("%Y-%m-%d"),
    "gRNA1": d(design_guides, 0, "sequence", default=""),
    "gRNA2": d(design_guides, 1, "sequence", default=""),
    "PrimerFw": d(design_primers, 0, "forward", default=""),
    "PrimerRv": d(design_primers, 0, "reverse", default=""),
    "Amplicon_bp": d(design_primers, 0, "expected_amplicon_bp", default=""),
    "DonorType": design_donor.get("donor_type") or design_donor.get("type", ""),
    "DonorSeq": design_donor.get("sequence", ""),
    "AssayTool": assessment.get("tool_used") or "",
    "TotalIndelPct": assessment.get("total_indel_pct")
    or d(screening, "clones", 0, "sanger", "total_indel_pct", default=""),
    "Positives": ", ".join(screening.get("positives", []))
    if isinstance(screening.get("positives"), list)
    else "",
    "BankID": bank_identifier,
    "Passage": mb_data.get("Bank_size") or compliance.get("passage", ""),
}

st.markdown("### Current values derived from snapshots")
st.json(values_preview)

default_row = {
    "Lfd.Nr.": compliance.get("sequential_number", "1"),
    "Parental cell line": compliance.get("parental_cell_line", meta.get("parental_line", "")),
    "Cell line name": compliance.get("cell_line", cell_line_name),
    "Link Form Z": compliance.get("link_form_z", ""),
    "IRIS ID": compliance.get("bank_id", bank_identifier),
    "Gene symbol (NCBI ID)": compliance.get("gene_symbol_ncbi", meta.get("gene", "")),
    "crRNA/sgRNA sequence": values_preview["gRNA1"],
    "Donor/Vector (ssODN seq / Addgene cat. number)": compliance.get("donor_vector", values_preview["DonorSeq"]),
    "Modification Type": values_preview["Edit"],
    "Modification description": compliance.get("modification_description", ""),
    "Zygosity": compliance.get("zygosity", ""),
    "Date of Registration": compliance.get("registration_date", time.strftime("%Y-%m-%d")),
    "RG": compliance.get("risk_classification", ""),
    "Disease": compliance.get("disease", meta.get("disease", "")),
}

existing_rows = row_defaults.get(method, {}).get("rows") or []
if not existing_rows:
    existing_rows = [default_row]

st.markdown("### Compliance table")
table_editor = st.data_editor(
    existing_rows,
    column_config={col: st.column_config.TextColumn() for col in COLUMN_HEADERS},
    num_rows="dynamic",
    use_container_width=True,
    key=f"lageso_table_{method}",
)
table_records = table_editor.to_dict("records") if hasattr(table_editor, "to_dict") else list(table_editor)

readiness = evaluate_readiness(table_records or [default_row])
st.markdown("Readiness status:")
st.write(readiness)


def save_compliance_snapshot(rows: List[Dict[str, str]]):
    timestamp = int(time.time())
    payload = {
        "project_id": project_id,
        "timestamp_unix": timestamp,
        "method": method,
        "start_row": start_row,
        "rows": rows or [default_row],
    }
    payload_bytes = json.dumps(payload, indent=2).encode("utf-8")
    digest = sha256_bytes(payload_bytes)
    outfile = snapshot_dir / f"{project_id}_lageso_compliance_{timestamp}_{digest[:12]}.json"
    outfile.write_bytes(payload_bytes)

    st.session_state["lageso_compliance_snapshot"] = {
        "digest": digest,
        "outfile": str(outfile),
        "metadata_uri": outfile.resolve().as_uri(),
        "payload": payload,
    }
    st.session_state["lageso_compliance_readiness"] = readiness

    register_file(
        project_id,
        "snapshots",
        {
            "step": "lageso_compliance",
            "path": str(outfile),
            "digest": digest,
            "timestamp": timestamp,
        },
    )
    pdf_filename = build_step_filename(project_id, "lageso-compliance", timestamp)
    pdf_path = reports_dir / pdf_filename
    snapshot_to_pdf(payload, pdf_path, "LAGESO Compliance Snapshot")
    register_file(
        project_id,
        "reports",
        {
            "step": "lageso_compliance",
            "path": str(pdf_path),
            "timestamp": timestamp,
            "digest": sha256_bytes(pdf_path.read_bytes()),
            "type": "pdf",
        },
    )
    st.success("Snapshot saved and PDF generated.")
    with pdf_path.open("rb") as handle:
        st.download_button("Download compliance snapshot (PDF)", handle, file_name=pdf_path.name)

    update_project_meta(
        project_id,
        {
            "compliance": {
                "excel_template_path": str(template_path),
                "last_excel_method": method,
                "excel_rows": {
                    **row_defaults,
                    method: {
                        "start_row": start_row,
                        "rows": rows,
                    },
                },
                "sequential_number": rows[0].get("Lfd.Nr.", ""),
                "parental_cell_line": rows[0].get("Parental cell line", ""),
                "cell_line": rows[0].get("Cell line name", ""),
                "link_form_z": rows[0].get("Link Form Z", ""),
                "bank_id": rows[0].get("IRIS ID", ""),
                "gene_symbol_ncbi": rows[0].get("Gene symbol (NCBI ID)", ""),
                "donor_vector": rows[0].get("Donor/Vector (ssODN seq / Addgene cat. number)", ""),
                "modification_description": rows[0].get("Modification description", ""),
                "zygosity": rows[0].get("Zygosity", ""),
                "registration_date": rows[0].get("Date of Registration", ""),
                "risk_classification": rows[0].get("RG", ""),
                "disease": rows[0].get("Disease", ""),
            }
        },
    )


def generate_excel(rows: List[Dict[str, str]]):
    if load_workbook is None:
        st.error("openpyxl is required (`pip install openpyxl`).")
        return
    if not template_path.exists():
        st.error(f"Template not found: {template_path}")
        return

    wb = load_workbook(template_path, keep_vba=True)
    sheet_map = {
        "TALENs": "TALENs",
        "CRISPR plasmids": "CRISPR_plasmids",
        "CRISPR RNPs": "CRISPR_RNPs",
    }
    sheet_name = sheet_map[method]
    if sheet_name not in wb.sheetnames:
        st.error(f"Sheet '{sheet_name}' not found in template.")
        return
    ws = wb[sheet_name]

    cell_map = {
        "TALENs": {
            "ProjectID": "B2",
            "CellLine": "B3",
            "Gene": "B4",
            "Edit": "B5",
            "Operator": "B6",
            "Date": "B7",
            "gRNA1": "B9",
            "gRNA2": "B10",
            "PrimerFw": "B12",
            "PrimerRv": "B13",
            "Amplicon_bp": "B14",
            "DonorType": "B16",
            "DonorSeq": "B17",
            "AssayTool": "E3",
            "TotalIndelPct": "E4",
            "Positives": "E6",
            "BankID": "E11",
            "Passage": "E12",
        },
        "CRISPR plasmids": {
            "ProjectID": "B2",
            "CellLine": "B3",
            "Gene": "B4",
            "Edit": "B5",
            "Operator": "B6",
            "Date": "B7",
            "gRNA1": "C9",
            "gRNA2": "C10",
            "PrimerFw": "C12",
            "PrimerRv": "C13",
            "Amplicon_bp": "C14",
            "DonorType": "C16",
            "DonorSeq": "C17",
            "AssayTool": "F3",
            "TotalIndelPct": "F4",
            "Positives": "F6",
            "BankID": "F11",
            "Passage": "F12",
        },
        "CRISPR RNPs": {
            "ProjectID": "B2",
            "CellLine": "B3",
            "Gene": "B4",
            "Edit": "B5",
            "Operator": "B6",
            "Date": "B7",
            "gRNA1": "D9",
            "gRNA2": "D10",
            "PrimerFw": "D12",
            "PrimerRv": "D13",
            "Amplicon_bp": "D14",
            "DonorType": "D16",
            "DonorSeq": "D17",
            "AssayTool": "G3",
            "TotalIndelPct": "G4",
            "Positives": "G6",
            "BankID": "G11",
            "Passage": "G12",
        },
    }

    for key, value in values_preview.items():
        dest = cell_map[method].get(key)
        if dest:
            ws[dest] = value

    for offset, row in enumerate(rows or [default_row]):
        for col_idx, header in enumerate(COLUMN_HEADERS, start=1):
            ws.cell(row=start_row + offset, column=col_idx, value=row.get(header, ""))

    timestamp = int(time.time())
    out_path = reports_dir / f"{project_id}_{method.replace(' ', '_')}_{timestamp}.xlsm"
    wb.save(out_path)
    register_file(
        project_id,
        "reports",
        {
            "step": "lageso_excel",
            "path": str(out_path),
            "timestamp": timestamp,
            "label": f"{method} Excel",
            "type": "xlsm",
        },
    )
    st.success(f"Excel workbook generated: {out_path.name}")
    with out_path.open("rb") as handle:
        st.download_button("Download populated .xlsm", handle, file_name=out_path.name)


col_save, col_excel, col_anchor = st.columns(3)

with col_save:
    if st.button("Save compliance snapshot"):
        save_compliance_snapshot(table_records)

with col_excel:
    if st.button("Generate Excel"):
        generate_excel(table_records)

with col_anchor:
    snapshot_state = st.session_state.get("lageso_compliance_snapshot")
    readiness_state = st.session_state.get("lageso_compliance_readiness", readiness)
    if snapshot_state:
        st.code(f"Hash: {snapshot_state['digest']}", language="text")
        if readiness_state["ready"]:
            st.success("Ready to anchor.")
        else:
            st.error("Resolve issues before anchoring:")
            for issue in readiness_state["issues"]:
                st.write(f"- {issue}")
            if readiness_state["warnings"]:
                st.caption("Warnings:")
                for warning in readiness_state["warnings"]:
                    st.caption(f"Warning: {warning}")
        disabled = not readiness_state["ready"]
        if st.button("Anchor snapshot", disabled=disabled):
            try:
                result = send_log_tx(
                    hex_digest=snapshot_state["digest"],
                    step="LAGESO Compliance",
                    metadata_uri=snapshot_state["metadata_uri"],
                )
            except Exception as exc:
                st.error(f"Anchoring failed: {exc}")
            else:
                st.success("Anchored on-chain.")
                st.write(f"Tx: {result['tx_hash']}")
                st.json(result["receipt"])
                register_chain_tx(
                    project_id,
                    {
                        "step": "lageso_compliance",
                        "tx_hash": result["tx_hash"],
                        "digest": snapshot_state["digest"],
                        "timestamp": int(time.time()),
                        "metadata_uri": snapshot_state["metadata_uri"],
                    },
                )
    else:
        st.info("Save a compliance snapshot to enable anchoring.")
