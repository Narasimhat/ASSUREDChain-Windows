import hashlib
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

try:
    from docx import Document
except ImportError:
    Document = None

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.components.project_state import (
    ensure_dirs,
    load_manifest,
    project_subdir,
    register_file,
    update_project_meta,
    use_project,
)
from app.components.autofill import d
from app.components.file_utils import build_step_filename, snapshot_to_pdf
from app.components.web3_client import send_log_tx


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


COLUMNS = [
    "Lfd. Nr. in Anlage 13/15 Project S2",
    "Spender — Bezeichnung (Description)",
    "Spender — RG",
    "Empfänger — Bezeichnung (Description)",
    "Empfänger — RG",
    "Vektor — Bezeichnung (Description)",
    "übertragene Nukleinsäure vorhanden?",
    "übertragene Nukleinsäure — Bezeichnung (Description)",
    "Gefährdungspotential (Hazard potential)",
    "GVO — Bezeichnung (Description)",
    "GVO — RG",
    "GVO — erzeugt oder entsorgt am",
    "GVO — erhalten am",
]


def evaluate_readiness(rows: List[Dict[str, str]]) -> Dict[str, List[str] | bool]:
    issues: List[str] = []
    warnings: List[str] = []
    required = [
        "Lfd. Nr. in Anlage 13/15 Project S2",
        "Spender — Bezeichnung (Description)",
        "Empfänger — Bezeichnung (Description)",
        "Vektor — Bezeichnung (Description)",
        "übertragene Nukleinsäure — Bezeichnung (Description)",
        "GVO — Bezeichnung (Description)",
        "GVO — erzeugt oder entsorgt am",
    ]
    for idx, row in enumerate(rows, start=1):
        for field in required:
            if not (row.get(field) or "").strip():
                issues.append(f"Row {idx}: '{field}' is required.")
        if not (row.get("Spender — RG") or "").strip():
            warnings.append(f"Row {idx}: Spender RG missing.")
        if not (row.get("Empfänger — RG") or "").strip():
            warnings.append(f"Row {idx}: Empfänger RG missing.")
        if not (row.get("GVO — RG") or "").strip():
            warnings.append(f"Row {idx}: GVO RG missing.")
        if not (row.get("übertragene Nukleinsäure vorhanden?") or "").strip():
            warnings.append(f"Row {idx}: Please specify if nucleic acid is present.")
    return {"ready": not issues, "issues": issues, "warnings": warnings}

init_page("Step 12 - Form Z Manager")
st.title("Step 12 - Form Z Manager")
st.caption("Maintain Form Z entries, save snapshots, and generate Excel and Word versions.")

project_id = use_project("Project")
if not project_id:
    st.warning("Select a project from the sidebar.")
    st.stop()

manifest = load_manifest(project_id)
meta = manifest.get("meta", {})
compliance = meta.get("compliance", {})

project_root = ensure_dirs(project_id)
snapshots_root = project_root / "snapshots"
snapshot_dir = project_subdir(project_id, "snapshots", "form_z")
reports_dir = project_subdir(project_id, "reports", "form_z")

templates_dir = ROOT / "data" / "templates"
templates_dir.mkdir(parents=True, exist_ok=True)

# Template upload section
st.subheader("📤 Upload New Templates")
with st.expander("Upload templates to library"):
    col_excel, col_word = st.columns(2)
    with col_excel:
        st.caption("Upload Excel template (.xlsx, .xlsm)")
        excel_upload = st.file_uploader("Excel template", type=["xlsx", "xlsm"], key="excel_upload")
        if excel_upload:
            save_path = templates_dir / excel_upload.name
            with save_path.open("wb") as f:
                f.write(excel_upload.getbuffer())
            st.success(f"✅ Saved: {excel_upload.name}")
            st.rerun()
    
    with col_word:
        st.caption("Upload Word template (.docx)")
        word_upload = st.file_uploader("Word template", type=["docx"], key="word_upload")
        if word_upload:
            save_path = templates_dir / word_upload.name
            with save_path.open("wb") as f:
                f.write(word_upload.getbuffer())
            st.success(f"✅ Saved: {word_upload.name}")
            st.rerun()

st.subheader("📋 Select Templates")
available_excel_templates = sorted(list(templates_dir.glob("*.xlsm")) + list(templates_dir.glob("*.xlsx")))
available_word_templates = sorted(templates_dir.glob("*.docx"))

excel_template_saved = Path(compliance.get("form_z_template_path", "")) if compliance.get("form_z_template_path") else None
word_template_saved = Path(compliance.get("formblatt_z_template_path", "")) if compliance.get("formblatt_z_template_path") else None

if available_excel_templates:
    excel_choices = [str(p) for p in available_excel_templates]
    excel_index = 0
    if excel_template_saved and str(excel_template_saved) in excel_choices:
        excel_index = excel_choices.index(str(excel_template_saved))
    excel_template_path = Path(st.selectbox("Form Z Excel template (.xlsm)", excel_choices, index=excel_index))
else:
    excel_template_path = Path(
        st.text_input(
            "Form Z Excel template (.xlsm)",
            str(excel_template_saved or templates_dir / "Form_Z.xlsm"),
        )
    )

if available_word_templates:
    word_choices = [str(p) for p in available_word_templates]
    word_index = 0
    if word_template_saved and str(word_template_saved) in word_choices:
        word_index = word_choices.index(str(word_template_saved))
    word_template_path = Path(st.selectbox("Formblatt Z Word template (.docx)", word_choices, index=word_index))
else:
    word_template_path = Path(
        st.text_input(
            "Formblatt Z Word template (.docx)",
            str(word_template_saved or templates_dir / "formblatt_z_S1_projekt3.docx"),
        )
    )

start_row = st.number_input(
    "Start row (Excel sheet)",
    min_value=1,
    value=compliance.get("form_z_start_row", 6),
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


# Load data from various project steps
charter = latest_json(snapshots_root / "charter")
design = latest_json(snapshots_root / "design")
master_bank = latest_json(snapshots_root / "master_bank_registry")

# Extract charter information
primary_cell_line = charter.get("primary_cell_line", meta.get("parental_line", ""))
target_gene = charter.get("target_gene", meta.get("target_gene", ""))
gene_symbol = charter.get("gene_symbol", meta.get("gene_symbol", ""))
modification_type = charter.get("modification_type", meta.get("modification_type", ""))
modification_description = charter.get("modification_description", meta.get("modification_description", ""))

# Extract design information
design_guides = design.get("selected_guides") or []
sgrna_sequences = ", ".join([g.get("sequence", "") for g in design_guides if g.get("sequence")])
primary_guide = d(design_guides, 0, "sequence", default="")
donor_data = design.get("donor") or {}
donor_sequence = donor_data.get("sequence", "")
vector_name = design.get("vector_name", donor_data.get("name", ""))

# Extract master bank data - get all cell lines registered
mb_entries = master_bank.get("entries", [])
cell_line_names = []
gvo_dates = []
for entry in mb_entries:
    mb_data = entry.get("data", {})
    iris_id = mb_data.get("IRIS_ID", "")
    if iris_id:
        cell_line_names.append(iris_id)
    date_field = mb_data.get("Date_Registered") or mb_data.get("date_registered", "")
    if date_field:
        gvo_dates.append(str(date_field))

# Get first entry data for defaults
mb_data = mb_entries[0].get("data", {}) if mb_entries else {}
first_cell_line = cell_line_names[0] if cell_line_names else mb_data.get("IRIS_ID", "")
first_date = gvo_dates[0] if gvo_dates else ""

# Build default row with comprehensive auto-fill
default_row = {
    "Lfd. Nr. in Anlage 13/15 Project S2": compliance.get("form_z_number", "1"),
    "Spender — Bezeichnung (Description)": gene_symbol or target_gene,
    "Spender — RG": compliance.get("spender_rg", "1"),
    "Empfänger — Bezeichnung (Description)": primary_cell_line,
    "Empfänger — RG": compliance.get("empfaenger_rg", "1"),
    "Vektor — Bezeichnung (Description)": vector_name or donor_sequence[:50] if donor_sequence else "",
    "übertragene Nukleinsäure vorhanden?": "Ja" if donor_sequence else "Nein",
    "übertragene Nukleinsäure — Bezeichnung (Description)": donor_sequence,
    "Gefährdungspotential (Hazard potential)": compliance.get("hazard_potential", ""),
    "GVO — Bezeichnung (Description)": first_cell_line or project_id,
    "GVO — RG": compliance.get("gvo_rg", "1"),
    "GVO — erzeugt oder entsorgt am": first_date,
    "GVO — erhalten am": compliance.get("gvo_received_on", ""),
}

# Store auto-filled metadata in session for SD template export
if "form_z_autofill" not in st.session_state:
    st.session_state["form_z_autofill"] = {
        "parental_cell_line": primary_cell_line,
        "cell_line_names": cell_line_names,
        "gene_symbol": gene_symbol or target_gene,
        "sgrna_sequences": sgrna_sequences,
        "donor_sequence": donor_sequence,
        "vector_name": vector_name,
        "modification_type": modification_type,
        "modification_description": modification_description,
        "project_id": project_id,
    }

existing_rows = compliance.get("form_z_rows") or [default_row]

st.markdown("### Form Z entries")

# Show auto-fill info
with st.expander("📋 Auto-filled data from project"):
    col1, col2 = st.columns(2)
    with col1:
        st.caption("**From Project Charter:**")
        st.write(f"- Primary cell line: `{primary_cell_line or 'N/A'}`")
        st.write(f"- Target gene: `{target_gene or 'N/A'}`")
        st.write(f"- Gene symbol: `{gene_symbol or 'N/A'}`")
        st.write(f"- Modification type: `{modification_type or 'N/A'}`")
    with col2:
        st.caption("**From Design Log:**")
        st.write(f"- sgRNA sequences: `{sgrna_sequences or 'N/A'}`")
        st.write(f"- Donor/Vector: `{vector_name or 'N/A'}`")
        st.caption("**From Master Bank Registry:**")
        if cell_line_names:
            st.write(f"- Cell lines: `{', '.join(cell_line_names)}`")
        else:
            st.write("- No cell lines registered yet")

table_editor = st.data_editor(
    existing_rows,
    column_config={col: st.column_config.TextColumn() for col in COLUMNS},
    num_rows="dynamic",
    use_container_width=True,
    key="form_z_table",
)
table_rows = table_editor.to_dict("records") if hasattr(table_editor, "to_dict") else list(table_editor)

readiness = evaluate_readiness(table_rows or [default_row])


def save_form_z_snapshot(rows: List[Dict[str, str]]):
    timestamp = int(time.time())
    payload = {
        "project_id": project_id,
        "timestamp_unix": timestamp,
        "rows": rows or [default_row],
    }
    payload_bytes = json.dumps(payload, indent=2).encode("utf-8")
    digest = sha256_bytes(payload_bytes)
    outfile = snapshot_dir / f"{project_id}_form_z_{timestamp}_{digest[:12]}.json"
    outfile.write_bytes(payload_bytes)

    st.session_state["form_z_snapshot"] = {
        "digest": digest,
        "outfile": str(outfile),
        "metadata_uri": outfile.resolve().as_uri(),
        "payload": payload,
    }
    st.session_state["form_z_readiness"] = readiness

    register_file(
        project_id,
        "snapshots",
        {
            "step": "form_z",
            "path": str(outfile),
            "digest": digest,
            "timestamp": timestamp,
        },
    )
    pdf_filename = build_step_filename(project_id, "form-z", timestamp)
    pdf_path = reports_dir / pdf_filename
    snapshot_to_pdf(payload, pdf_path, "Form Z Snapshot")
    register_file(
        project_id,
        "reports",
        {
            "step": "form_z",
            "path": str(pdf_path),
            "timestamp": timestamp,
            "digest": sha256_bytes(pdf_path.read_bytes()),
            "type": "pdf",
        },
    )
    st.success("Form Z snapshot saved and PDF generated.")
    with pdf_path.open("rb") as handle:
        st.download_button("Download Form Z snapshot (PDF)", handle, file_name=pdf_path.name)

    primary = rows[0] if rows else default_row
    update_project_meta(
        project_id,
        {
            "compliance": {
                "form_z_rows": rows,
                "form_z_template_path": str(excel_template_path),
                "formblatt_z_template_path": str(word_template_path),
                "form_z_start_row": start_row,
                "form_z_number": primary.get("Lfd. Nr. in Anlage 13/15 Project S2", ""),
                "spender_rg": primary.get("Spender — RG", ""),
                "empfaenger_rg": primary.get("Empfänger — RG", ""),
                "nucleic_acid_present": primary.get("übertragene Nukleinsäure vorhanden?", ""),
                "nucleic_acid_description": primary.get("übertragene Nukleinsäure — Bezeichnung (Description)", ""),
                "hazard_potential": primary.get("Gefährdungspotential (Hazard potential)", ""),
                "gvo_description": primary.get("GVO — Bezeichnung (Description)", ""),
                "gvo_rg": primary.get("GVO — RG", ""),
                "gvo_generated_on": primary.get("GVO — erzeugt oder entsorgt am", ""),
                "gvo_received_on": primary.get("GVO — erhalten am", ""),
            }
        },
    )


def generate_excel(rows: List[Dict[str, str]]):
    if load_workbook is None:
        st.error("openpyxl is required (`pip install openpyxl`).")
        return
    if not excel_template_path.exists():
        st.error(f"Excel template not found: {excel_template_path}")
        return

    wb = load_workbook(excel_template_path, keep_vba=True)
    sheet_name = wb.sheetnames[0]
    ws = wb[sheet_name]

    for offset, row in enumerate(rows or [default_row]):
        for col_idx, header in enumerate(COLUMNS, start=1):
            ws.cell(row=start_row + offset, column=col_idx, value=row.get(header, ""))

    timestamp = int(time.time())
    out_path = reports_dir / f"{project_id}_FormZ_{timestamp}.xlsm"
    wb.save(out_path)
    register_file(
        project_id,
        "reports",
        {
            "step": "form_z",
            "path": str(out_path),
            "timestamp": timestamp,
            "type": "xlsm",
            "label": "Form Z Excel",
        },
    )
    st.success(f"Form Z Excel generated: {out_path.name}")
    with out_path.open("rb") as handle:
        st.download_button("Download Form Z Excel", handle, file_name=out_path.name)


def generate_word(rows: List[Dict[str, str]]):
    if Document is None:
        st.error("python-docx is required (`pip install python-docx`).")
        return
    if not word_template_path.exists():
        st.error(f"Word template not found: {word_template_path}")
        return

    doc = Document(word_template_path)
    replacements = {
        "{{PROJECT_ID}}": project_id,
        "{{LFD_NR}}": rows[0].get("Lfd. Nr. in Anlage 13/15 Project S2", ""),
        "{{SPENDER}}": rows[0].get("Spender — Bezeichnung (Description)", ""),
        "{{SPENDER_RG}}": rows[0].get("Spender — RG", ""),
        "{{EMPFAENGER}}": rows[0].get("Empfänger — Bezeichnung (Description)", ""),
        "{{EMPFAENGER_RG}}": rows[0].get("Empfänger — RG", ""),
        "{{VECTOR}}": rows[0].get("Vektor — Bezeichnung (Description)", ""),
        "{{NA_VORHANDEN}}": rows[0].get("übertragene Nukleinsäure vorhanden?", ""),
        "{{NA_BESCHREIBUNG}}": rows[0].get("übertragene Nukleinsäure — Bezeichnung (Description)", ""),
        "{{HAZARD}}": rows[0].get("Gefährdungspotential (Hazard potential)", ""),
        "{{GVO}}": rows[0].get("GVO — Bezeichnung (Description)", ""),
        "{{GVO_RG}}": rows[0].get("GVO — RG", ""),
        "{{GVO_GENERIERT}}": rows[0].get("GVO — erzeugt oder entsorgt am", ""),
        "{{GVO_ERHALTEN}}": rows[0].get("GVO — erhalten am", ""),
    }

    def replace(node):
        for paragraph in node.paragraphs:
            for key, value in replacements.items():
                if key in paragraph.text:
                    for run in paragraph.runs:
                        run.text = run.text.replace(key, value)
        for table in node.tables:
            for row in table.rows:
                for cell in row.cells:
                    replace(cell)

    replace(doc)
    timestamp = int(time.time())
    out_path = reports_dir / f"{project_id}_FormblattZ_{timestamp}.docx"
    doc.save(out_path)
    register_file(
        project_id,
        "reports",
        {
            "step": "form_z",
            "path": str(out_path),
            "timestamp": timestamp,
            "type": "docx",
            "label": "Formblatt Z Word",
        },
    )
    st.success(f"Formblatt Z generated: {out_path.name}")
    with out_path.open("rb") as handle:
        st.download_button("Download Formblatt Z", handle, file_name=out_path.name)


def append_to_sd_template(rows: List[Dict[str, str]]):
    """Append project data to Sheet 3 of 13_15 SD template."""
    template_path = templates_dir / "13_15 SD Proj 1-3.xlsm"
    if not template_path.exists():
        st.error(f"Template not found: {template_path}")
        return
    
    # Load template (not read-only so we can write)
    wb = load_workbook(template_path)
    if len(wb.sheetnames) < 3:
        st.error("Template must have at least 3 sheets")
        wb.close()
        return
    
    ws = wb[wb.sheetnames[2]]  # Sheet 3 (0-indexed as sheet 2)
    
    # Find next empty row (skip header at row 3)
    next_row = ws.max_row + 1
    
    # Get auto-filled metadata
    autofill = st.session_state.get("form_z_autofill", {})
    parental_cell_line = autofill.get("parental_cell_line", "")
    cell_line_names = autofill.get("cell_line_names", [])
    gene_symbol = autofill.get("gene_symbol", "")
    sgrna_sequences = autofill.get("sgrna_sequences", "")
    donor_sequence = autofill.get("donor_sequence", "")
    vector_name = autofill.get("vector_name", "")
    modification_type = autofill.get("modification_type", "")
    modification_description = autofill.get("modification_description", "")
    project_id_val = autofill.get("project_id", project_id)
    
    # SD Template columns (Row 3):
    # A: Lfd.Nr., B: Parental cell line, C: Cell line name, D: Link Form Z, 
    # E: IRIS ID, F: Gene symbol, G: crRNA/sgRNA, H: Donor/Vector, I: (empty),
    # J: Modification Type, K: Modification description, L: Zygosity,
    # M: Date of Registration, N: RG, O: Disease
    
    # If multiple cell lines from master bank, create a row for each
    if cell_line_names:
        for idx, cell_line in enumerate(cell_line_names, start=1):
            row_data = rows[idx - 1] if idx - 1 < len(rows) else rows[0] if rows else {}
            
            ws.cell(next_row, 1, idx)  # Lfd.Nr
            ws.cell(next_row, 2, parental_cell_line)  # Parental cell line
            ws.cell(next_row, 3, cell_line)  # Cell line name from master bank
            ws.cell(next_row, 4, "")  # Link Form Z
            ws.cell(next_row, 5, project_id_val)  # IRIS ID (Project ID)
            ws.cell(next_row, 6, gene_symbol)  # Gene symbol from charter
            ws.cell(next_row, 7, sgrna_sequences)  # crRNA/sgRNA from design
            ws.cell(next_row, 8, vector_name or donor_sequence[:100] if donor_sequence else "")  # Donor/Vector
            ws.cell(next_row, 10, modification_type)  # Modification Type from charter
            ws.cell(next_row, 11, modification_description)  # Modification description from charter
            ws.cell(next_row, 12, row_data.get("Zygosity", ""))  # Zygosity (manual entry)
            ws.cell(next_row, 13, row_data.get("GVO — erzeugt oder entsorgt am", ""))  # Date
            ws.cell(next_row, 14, row_data.get("GVO — RG", "1"))  # RG
            ws.cell(next_row, 15, meta.get("disease", ""))  # Disease from project meta
            next_row += 1
    else:
        # No master bank entries, use Form Z data
        for idx, row in enumerate(rows, start=1):
            ws.cell(next_row, 1, idx)  # Lfd.Nr
            ws.cell(next_row, 2, parental_cell_line)  # Parental cell line
            ws.cell(next_row, 3, row.get("GVO — Bezeichnung (Description)", ""))  # Cell line name
            ws.cell(next_row, 4, "")  # Link Form Z
            ws.cell(next_row, 5, project_id_val)  # IRIS ID
            ws.cell(next_row, 6, gene_symbol)  # Gene symbol
            ws.cell(next_row, 7, sgrna_sequences)  # crRNA/sgRNA
            ws.cell(next_row, 8, vector_name or donor_sequence[:100] if donor_sequence else "")  # Donor/Vector
            ws.cell(next_row, 10, modification_type)  # Modification Type
            ws.cell(next_row, 11, modification_description)  # Modification description
            ws.cell(next_row, 12, "")  # Zygosity
            ws.cell(next_row, 13, row.get("GVO — erzeugt oder entsorgt am", ""))  # Date
            ws.cell(next_row, 14, row.get("GVO — RG", "1"))  # RG
            ws.cell(next_row, 15, meta.get("disease", ""))  # Disease
            next_row += 1
    
    # Save to reports directory
    timestamp = int(time.time())
    output_path = reports_dir / f"13_15_SD_updated_{timestamp}.xlsm"
    wb.save(output_path)
    wb.close()
    
    digest = sha256_bytes(output_path.read_bytes())
    register_file(
        project_id,
        "reports",
        {
            "step": "form_z",
            "filename": output_path.name,
            "path": str(output_path),
            "sha256": digest,
            "timestamp": timestamp,
            "type": "xlsm",
            "label": "13/15 SD Template Export",
        },
    )
    st.success(f"✅ Data appended to SD template: {output_path.name}")
    with output_path.open("rb") as handle:
        st.download_button("Download 13/15 SD Template", handle, file_name=output_path.name, key="sd_download")


col_snapshot, col_excel, col_word, col_sd = st.columns(4)
if col_snapshot.button("Save Form Z snapshot"):
    save_form_z_snapshot(table_rows)

if col_excel.button("Generate Excel"):
    generate_excel(table_rows)

if col_word.button("Generate Word"):
    generate_word(table_rows)

if col_sd.button("Export to 13/15 SD"):
    append_to_sd_template(table_rows)

col_anchor = st.container()
snapshot_state = st.session_state.get("form_z_snapshot")
readiness_state = st.session_state.get("form_z_readiness", readiness)
with col_anchor:
    st.markdown("### Anchoring")
    if snapshot_state:
        st.code(f"Snapshot hash: {snapshot_state['digest']}", language="text")
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
        if st.button("Anchor Form Z snapshot", disabled=disabled):
            try:
                result = send_log_tx(
                    hex_digest=snapshot_state["digest"],
                    step="Form Z",
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
                        "step": "form_z",
                        "tx_hash": result["tx_hash"],
                        "digest": snapshot_state["digest"],
                        "timestamp": int(time.time()),
                        "metadata_uri": snapshot_state["metadata_uri"],
                    },
                )
    else:
        st.info("Save a Form Z snapshot before anchoring.")

