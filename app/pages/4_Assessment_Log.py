import hashlib
import json
import os
import re
import sys
import time
import tempfile
import subprocess
import shutil
import io
import zipfile
import types
from datetime import datetime
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
from scripts.prepare_ice_upload import build_excel, find_control, make_zip

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
    hdr_pct: Optional[float] = None
    top_indels: list[IndelItem] = []
    decision: str = Field(..., description="proceed_to_cloning / repeat_edit / archive")
    pcr: Optional[PCRBands] = None
    notes: Optional[str] = None
    author: str
    timestamp_unix: int
    files: dict[str, str] = {}


def _render_ice_html(output_base: Path, summary_path: Path) -> Path | None:
    """Build a minimal ICE summary HTML (no indel breakdown)."""
    try:
        data = json.loads(summary_path.read_text())
        if not isinstance(data, list):
            return None
    except Exception:
        return None

    summary_rows = []
    for entry in data:
        summary_rows.append(
            "<tr>"
            f"<td>{entry.get('sample_name','')}</td>"
            f"<td>{entry.get('ice','')}</td>"
            f"<td>{entry.get('ice_d','')}</td>"
            f"<td>{entry.get('r_squared') or entry.get('rsq','')}</td>"
            f"<td>{entry.get('hdr_pct','')}</td>"
            f"<td>{entry.get('ko_score','')}</td>"
            f"<td>{entry.get('experiment_file','')}</td>"
            f"<td>{entry.get('control_file','')}</td>"
            "</tr>"
        )

    html = f"""
    <html><head><title>ICE Report</title></head><body>
    <h2>ICE Batch Summary</h2>
    <table border='1' cellpadding='6'>
      <tr><th>Sample</th><th>ICE %</th><th>ICE-D %</th><th>R²</th><th>HDR %</th><th>KO %</th><th>Experiment</th><th>Control</th></tr>
      {''.join(summary_rows)}
    </table>
    </body></html>
    """
    html_path = output_base / f"{summary_path.stem}.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def run_ice_locally(batch_file, ab1_files, output_base: Path):
    """Run synthego_ice_batch locally. Returns (summary_json_path, summary_xlsx_path or None, html_report or None)."""
    cli = shutil.which("synthego_ice_batch")
    if cli is None:
        # fallback: look in local .venv/Scripts (Windows) or .venv/bin (POSIX)
        venv_bin_candidates = [
            ROOT / ".venv" / "Scripts" / "synthego_ice_batch.exe",
            ROOT / ".venv" / "Scripts" / "synthego_ice_batch",
            ROOT / ".venv" / "bin" / "synthego_ice_batch",
        ]
        for candidate in venv_bin_candidates:
            if candidate.exists():
                cli = str(candidate)
                break
    if cli is None:
        raise RuntimeError(
            "synthego_ice_batch CLI not found. Install with `pip install synthego-ice` in this environment "
            "and ensure the .venv Scripts/bin directory is on PATH."
        )

    # Monkeypatch ICE runtime for Biopython/XlsxWriter compatibility via sitecustomize
    patch_dir = Path(tempfile.mkdtemp(prefix="ice_patch_"))
    sitecustomize = patch_dir / "sitecustomize.py"
    sitecustomize.write_text(
        """
import io
try:
    from Bio import AlignIO
    from Bio.Align import MultipleSeqAlignment
    if not hasattr(MultipleSeqAlignment, "format"):
        def _msa_format(self, fmt):
            buf = io.StringIO()
            AlignIO.write([self], buf, fmt)
            return buf.getvalue()
        MultipleSeqAlignment.format = _msa_format
except Exception:
    pass

try:
    import xlsxwriter.workbook as _wb
    if not hasattr(_wb.Workbook, "save"):
        _wb.Workbook.save = _wb.Workbook.close
except Exception:
    pass

try:
    # pandas' xlsxwriter backend calls .close(); alias .save for older code paths
    import pandas.io.excel._xlsxwriter as _pwx
    if hasattr(_pwx, "XlsxWriter") and not hasattr(_pwx.XlsxWriter, "save"):
        _pwx.XlsxWriter.save = _pwx.XlsxWriter.close
except Exception:
    pass
"""
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="ice_run_"))
    data_dir = temp_dir / "ab1"
    data_dir.mkdir(parents=True, exist_ok=True)
    batch_path = temp_dir / "batch.xlsx"
    batch_path.write_bytes(batch_file.getbuffer())
    for f in ab1_files:
        (data_dir / f.name).write_bytes(f.getbuffer())

    # Ensure labels in the batch sheet mirror experiment filenames (stem without .ab1)
    try:
        import pandas as _pd

        df = _pd.read_excel(batch_path)
        if "Experiment File" in df.columns:
            df["Label"] = df["Experiment File"].apply(lambda name: Path(str(name)).stem)
            df.to_excel(batch_path, index=False)
    except Exception:
        # If anything fails, fall back to the user-provided batch as-is
        pass

    out_dir = temp_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [cli, "--in", str(batch_path), "--data", str(data_dir), "--out", str(out_dir)]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{patch_dir}{os.pathsep}{env.get('PYTHONPATH', '')}"
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise RuntimeError(f"ICE run failed: {res.stderr or res.stdout}")

    summary_json = next(out_dir.glob("ice.results.*.json"), None)
    summary_xlsx = next(out_dir.glob("ice.results.*.xlsx"), None)
    if not summary_json:
        raise RuntimeError("ICE run completed but summary JSON not found.")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_json = output_base / f"ice.results.{ts}.json"
    dest_xlsx = output_base / f"ice.results.{ts}.xlsx"
    shutil.copy(summary_json, dest_json)
    if summary_xlsx:
        shutil.copy(summary_xlsx, dest_xlsx)
    else:
        dest_xlsx = None

    # Harvest richer ICE artifacts (indel distributions, chromatograms, proposals)
    extra_patterns = ["*.contribs.json", "*.indel.json", "*.trace.json", "*.allproposals.json"]
    for pattern in extra_patterns:
        for src in out_dir.glob(pattern):
            target = output_base / src.name
            try:
                shutil.copy(src, target)
            except Exception:
                pass
    html_report = _render_ice_html(output_base, dest_json)

    return dest_json, dest_xlsx, html_report


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


def _default_guides_and_donor(project_meta: dict) -> tuple[str, str]:
    # Prefer design snapshot for this project if available
    try:
        project_id = project_meta.get("meta", {}).get("project_id") or project_meta.get("project_id") or None
        if project_id:
            design_dir = project_subdir(project_id, "snapshots", "design")
            if design_dir.exists():
                design_jsons = sorted(design_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                for jf in design_jsons:
                    data = json.loads(jf.read_text())
                    guides = []
                    for g in data.get("selected_guides", []):
                        seq = (g or {}).get("sequence") or ""
                        if seq:
                            guides.append(seq.strip())
                    donor = (
                        data.get("ssodn_sequence")
                        or data.get("donor_sequence")
                        or data.get("ssodn")
                        or data.get("ssodn_template")
                        or ""
                    )
                    if guides or donor:
                        return ",".join(guides), (donor or "").strip()
    except Exception:
        # Fallback to compliance block below
        pass

    compliance = project_meta.get("compliance", {}) if project_meta else {}
    guides = []
    for g in compliance.get("guides", []):
        seq = (g or {}).get("sequence") or ""
        if seq:
            guides.append(seq.strip())
    donor = ""
    for d in compliance.get("donors", []):
        donor = (d or {}).get("sequence") or ""
        if donor:
            donor = donor.strip()
            break
    return ",".join(guides), donor


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return cleaned or "project"


def _wrap_path_for_buffer(path: Path):
    data = path.read_bytes()
    return types.SimpleNamespace(name=path.name, getbuffer=lambda d=data: d)


init_page("Step 4 - Assessment (Sanger / PCR)")
selected_project = use_project("project")
project_meta = load_project_meta(selected_project) if selected_project else {}
st.title("Step 4 - Assessment (Sanger / PCR)")
st.caption(
    "Upload Sanger results (TIDE / ICE / DECODR / SeqScreener / TIDER) or log PCR genotyping. "
    "Then anchor on-chain as step 'Assessment'."
)

# resolve project paths early for ICE runner
if selected_project:
    snapshot_dir = project_subdir(selected_project, "snapshots", "assessment")
    upload_dir = project_subdir(selected_project, "uploads", "assessment")
    report_dir = project_subdir(selected_project, "reports", "assessment")
else:
    snapshot_dir = FALLBACK_DATA_DIR
    upload_dir = FALLBACK_UPLOAD_DIR
    report_dir = FALLBACK_DATA_DIR

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

assessment_defaults = project_meta.get("assessment_defaults", {})
ice_defaults = {}
default_guides, default_donor = _default_guides_and_donor(project_meta)

with st.expander("Run ICE locally (beta)"):
    st.caption("Runs local synthego_ice_batch and attaches the summary JSON/XLSX as the tool result.")
    ice_batch = st.file_uploader("ICE batch Excel (.xlsx)", type=["xlsx"], key="ice_batch_upload")
ice_ab1 = st.file_uploader("ab1 files (or zip containing ab1)", type=["ab1", "zip"], accept_multiple_files=True, key="ice_ab1_upload")
# If a prior ICE summary exists in session, keep it visible even before a new run
if st.session_state.get("ice_summary_html_content"):
    with st.expander("ICE summary (HTML)", expanded=True):
        st.components.v1.html(st.session_state["ice_summary_html_content"], height=400, scrolling=True)
        if st.session_state.get("ice_summary_html_path"):
            try:
                st.download_button(
                    "Download ICE summary HTML",
                    Path(st.session_state["ice_summary_html_path"]).read_bytes(),
                    file_name=Path(st.session_state["ice_summary_html_path"]).name,
                    key="ice_summary_html_download_cached",
                )
            except FileNotFoundError:
                pass
    # Automatically prepare ICE batch/zip when ab1 files are uploaded
    auto_pack = None
    if ice_ab1:
        dest_dir = upload_dir / "ice_autopack"
        dest_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: list[Path] = []
        control_hint = "wt,control,ctrl,utf"

        def _write_ab1_file(name: str, data: bytes) -> Path:
            base = Path(name).name
            path = dest_dir / base
            counter = 1
            while path.exists():
                stem = Path(base).stem
                suffix = Path(base).suffix
                path = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            path.write_bytes(data)
            if selected_project:
                register_file(
                    selected_project,
                    "uploads",
                    {
                        "filename": Path(name).name,
                        "stored_as": str(path),
                        "timestamp": int(time.time() * 1000),
                        "step": "assessment",
                        "kind": "ab1",
                    },
                )
            return path

        uploaded_ab1 = []
        for f in ice_ab1:
            name = f.name.lower()
            if name.endswith(".zip"):
                buf = io.BytesIO(f.getbuffer())
                with zipfile.ZipFile(buf) as zf:
                    for info in zf.infolist():
                        if info.filename.lower().endswith(".ab1"):
                            content = zf.read(info.filename)
                            saved_paths.append(_write_ab1_file(Path(info.filename).name, content))
                            uploaded_ab1.append(types.SimpleNamespace(name=Path(info.filename).name, getbuffer=lambda d=content: d))
            elif name.endswith(".ab1"):
                data = f.getbuffer()
                saved_paths.append(_write_ab1_file(f.name, data))
                uploaded_ab1.append(f)

        if len(saved_paths) >= 2:
            try:
                control_path = find_control(saved_paths, None, control_hint)
                label_prefix = _slugify(selected_project or "assessment")
                excel_out = dest_dir / "ice_batch.xlsx"
                zip_out = dest_dir / "upload.zip"
                guides_to_use = (default_guides or "").strip()
                if not guides_to_use:
                    st.error("No guide sequences found in the selected project. Add guides to the project or upload a batch Excel.")
                    st.stop()
                excel_path = build_excel(dest_dir, guides_to_use, default_donor, control_path, saved_paths, label_prefix, excel_out)
                try:
                    # Force labels to mirror experiment filenames (stem without .ab1)
                    import pandas as _pd

                    df = _pd.read_excel(excel_path)
                    if "Experiment File" in df.columns:
                        df["Label"] = df["Experiment File"].apply(lambda name: Path(str(name)).stem)
                        df.to_excel(excel_path, index=False)
                except Exception:
                    pass
                make_zip(zip_out, [excel_path] + saved_paths)
                auto_pack = {
                    "batch": _wrap_path_for_buffer(excel_path),
                    "ab1": [_wrap_path_for_buffer(p) for p in saved_paths],
                    "excel_path": excel_path,
                    "zip_path": zip_out,
                    "control": control_path.name,
                }
                st.info(
                    f"Auto-built ICE batch ({excel_out.name}) and zip ({zip_out.name}) using control {control_path.name}."
                )
            except Exception as e:
                st.error(f"Auto-build failed: {e}")
        elif saved_paths:
            st.warning("Need at least two .ab1 files to build an ICE batch.")

    if st.button("Run ICE now"):
        if not ice_batch or not ice_ab1:
            if auto_pack:
                ice_batch_for_run = auto_pack["batch"]
                processed_ab1 = auto_pack["ab1"]
            else:
                st.error("Upload a batch Excel and at least one .ab1 file (or let auto-build create the batch).")
                st.stop()
        else:
            ice_batch_for_run = ice_batch
            processed_ab1 = []
            for f in ice_ab1:
                name = f.name.lower()
                if name.endswith(".zip"):
                    buf = f.getbuffer()
                    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
                        for info in zf.infolist():
                            if info.filename.lower().endswith(".ab1"):
                                content = zf.read(info.filename)
                                processed_ab1.append(
                                    types.SimpleNamespace(
                                        name=Path(info.filename).name,
                                        getbuffer=lambda d=content: d,
                                    )
                                )
                elif name.endswith(".ab1"):
                    processed_ab1.append(f)

        if auto_pack and not ice_batch:
            ice_batch_for_run = auto_pack["batch"]
            processed_ab1 = auto_pack["ab1"]

        if not processed_ab1:
            st.error("No .ab1 files found (even inside the zip).")
            st.stop()

        try:
            output_base = report_dir if selected_project else FALLBACK_DATA_DIR
            summary_json, summary_xlsx, summary_html = run_ice_locally(ice_batch_for_run, processed_ab1, output_base)
            attachments["tool_result"] = str(summary_json)
            saved_attachment_paths.append(summary_json)
            if summary_xlsx:
                saved_attachment_paths.append(summary_xlsx)
            if summary_html:
                saved_attachment_paths.append(summary_html)
                try:
                    with open(summary_html, "r", encoding="utf-8") as f:
                        html_content = f.read()
                        st.session_state["ice_summary_html_content"] = html_content
                        st.session_state["ice_summary_html_path"] = str(summary_html)
                    with st.expander("ICE summary (HTML)", expanded=True):
                        st.components.v1.html(html_content, height=400, scrolling=True)
                        st.download_button(
                            "Download ICE summary HTML",
                            html_content,
                            file_name=Path(summary_html).name,
                            key="ice_summary_html_download",
                        )
                except Exception:
                    pass
            data = json.loads(Path(summary_json).read_text())
            if data:
                first = data[0]
                # Show ICE batch summary (no indel breakdown)
                try:
                    summary_df = pd.DataFrame(data)[
                        [
                            "sample_name",
                            "experiment_file",
                            "control_file",
                            "ice",
                            "ice_d",
                            "r_squared",
                            "hdr_pct",
                            "ko_score",
                        ]
                    ]
                    st.subheader("ICE batch summary")
                    st.dataframe(summary_df, use_container_width=True)
                    st.download_button(
                        "Download ICE summary JSON",
                        Path(summary_json).read_bytes(),
                        file_name=Path(summary_json).name,
                        key="ice_summary_download",
                    )
                    st.caption(f"Stored: {summary_json}")
                except Exception:
                    pass
                ice_defaults = {
                    "tool_used": "ICE",
                    "total_indel_pct": first.get("ice"),
                    "hdr_pct": first.get("hdr_pct"),
                }
                st.success(f"ICE run complete. Attached {Path(summary_json).name}. Total indel (ICE): {first.get('ice')}")
            else:
                st.success(f"ICE run complete. Attached {Path(summary_json).name}.")
        except Exception as e:
            st.error(f"ICE run failed: {e}")

if ice_defaults:
    assessment_defaults = {**assessment_defaults, **ice_defaults}

delivery_defaults = project_meta.get("delivery_defaults", {})
default_cell_line = (
    assessment_defaults.get("cell_line")
    or delivery_defaults.get("cell_line")
    or project_meta.get("cell_line")
    or "BIHi005-A-1X (bulk)"
)

assay_options = ["Sanger-Indel", "PCR-Genotyping"]
default_assay_type = assessment_defaults.get("assay_type", "Sanger-Indel")
default_assay_index = assay_options.index(default_assay_type) if default_assay_type in assay_options else 0


with st.form("assessment_form"):
    project_id = st.text_input(
        "Project ID",
        value=selected_project or "SORCS1_KI_v1",
        help="Defaults to the project selected in the sidebar.",
    )
    cell_line = st.text_input(
        "Cell line / clone (if known)",
        value=default_cell_line,
    )
    assay_type = st.selectbox(
        "Assessment type",
        assay_options,
        index=default_assay_index,
    )

    tool_used = None
    total_indel_pct = None
    ki_pct = None
    hdr_pct = None
    top_indels: list[IndelItem] = []
    pcr_bands: Optional[PCRBands] = None

    if assay_type == "Sanger-Indel":
        tool_options = ["DECODR", "ICE", "TIDE", "SeqScreener", "TIDER"]
        default_tool = assessment_defaults.get("tool_used", tool_options[0])
        tool_used = st.selectbox(
            "Tool used",
            tool_options,
            index=tool_options.index(default_tool) if default_tool in tool_options else 0,
        )
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
            value=float(assessment_defaults.get("total_indel_pct") or 0.0),
        )

        if tool_used == "TIDER":
            ki_pct = st.number_input(
                "Knock-in % (from TIDER)",
                min_value=0.0,
                max_value=100.0,
                step=0.1,
                value=float(assessment_defaults.get("ki_pct") or 0.0),
            )
        hdr_pct = st.number_input(
            "HDR % (if reported)",
            min_value=0.0,
            max_value=100.0,
            step=0.1,
            value=float(assessment_defaults.get("hdr_pct") or 0.0),
        )

        st.markdown("Top indels (optional)")
        default_top_indels = assessment_defaults.get("top_indels", [])
        for idx in range(3):
            default_indel = default_top_indels[idx] if idx < len(default_top_indels) else {}
            size_value = st.number_input(
                f"Indel {idx + 1} size (bp, negative=deletion, positive=insertion)",
                value=int(default_indel.get("size_bp", 0)),
            )
            freq_value = st.number_input(
                f"Indel {idx + 1} frequency (%)",
                min_value=0.0,
                max_value=100.0,
                value=float(default_indel.get("frequency_pct", 0.0)),
            )
            if freq_value > 0:
                top_indels.append(IndelItem(size_bp=int(size_value), frequency_pct=float(freq_value)))

    else:
        st.markdown("**PCR genotyping**")
        pcr_defaults = assessment_defaults.get("pcr_defaults", {})
        wt_bp = st.number_input(
            "Expected WT band (bp)",
            min_value=0,
            value=int(pcr_defaults.get("wt_bp") or 0),
        )
        edited_bp = st.number_input(
            "Expected edited band (bp)",
            min_value=0,
            value=int(pcr_defaults.get("edited_bp") or 0),
        )
        observed_default = ", ".join(str(v) for v in pcr_defaults.get("observed_bands_bp", []))
        observed_bands = st.text_input(
            "Observed bands (comma-separated bp)",
            value=observed_default,
        )
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

    decision_options = ["proceed_to_cloning", "repeat_edit", "archive"]
    default_decision = assessment_defaults.get("decision", "proceed_to_cloning")
    decision = st.selectbox(
        "Decision",
        decision_options,
        index=decision_options.index(default_decision) if default_decision in decision_options else 0,
    )
    notes = st.text_area(
        "Notes",
        value=assessment_defaults.get("notes", ""),
    )
    author = st.text_input(
        "Author",
        value=assessment_defaults.get("author", "Narasimha Telugu"),
    )

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
            hdr_pct=hdr_pct,
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
        outfile = ensure_local_path(snapshot_dir / f"{snapshot.project_id}_{snapshot.timestamp_unix}_{digest[:12]}.json")
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
                    "path": normalize_path_for_storage(outfile),
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
        pdf_data = pdf_path.read_bytes()
        st.download_button(
            "Download assessment snapshot as PDF",
            pdf_data,
            file_name=pdf_path.name,
            mime="application/pdf",
            key="assessment_pdf_download",
        )
        st.caption(f"PDF saved: {pdf_path}")
        if selected_project:
            # Check if already registered to avoid duplicates
            from app.components.project_state import load_manifest
            current_manifest = load_manifest(selected_project)
            existing_reports = current_manifest.get("files", {}).get("reports", [])
            pdf_path_str = str(pdf_path)
            already_registered = any(
                isinstance(e, dict) and e.get("path") == pdf_path_str and e.get("step") == "assessment"
                for e in existing_reports
            )
            if not already_registered:
                register_file(
                    selected_project,
                    "reports",
                    {
                        "step": "assessment",
                        "path": pdf_path_str,
                        "timestamp": snapshot.timestamp_unix,
                        "digest": sha256_bytes(pdf_path.read_bytes()),
                        "type": "pdf",
                    },
                )
        if selected_project:
            defaults_payload = {
                "cell_line": snapshot.cell_line,
                "assay_type": snapshot.assay_type,
                "tool_used": snapshot.tool_used or "",
                "total_indel_pct": snapshot.total_indel_pct,
                "ki_pct": snapshot.ki_pct,
                "hdr_pct": snapshot.hdr_pct,
                "top_indels": [item.model_dump() for item in top_indels],
                "pcr_defaults": pcr_bands.model_dump() if pcr_bands else {},
                "decision": snapshot.decision,
                "notes": snapshot.notes or "",
                "author": snapshot.author,
            }
            update_project_meta(selected_project, {"assessment_defaults": defaults_payload})
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
            proof_path = ensure_local_path(Path(snapshot_state["outfile"]).with_suffix(".chainproof.json"))
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
