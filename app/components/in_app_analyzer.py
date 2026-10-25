"""In-App Sanger Trace QC and Clone Assessment Studio for ASSUREDChain."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import re
from pathlib import Path
from typing import Any, Optional

import streamlit as st
import pandas as pd

from app.components.assured_tools import QC_DECISION_TO_CHAIN
from app.components.project_state import (
    append_audit,
    normalize_path_for_storage,
    project_subdir,
    register_file,
)

ROOT = Path(__file__).resolve().parents[2]
CLI_SCRIPT = ROOT / "scripts" / "bench_trace_cli.mjs"
DEMO_TRACES_DIR = ROOT / "data" / "demo_traces"
BENCH_DEMO_DIR = Path("c:/Users/ntelugu/Desktop/assured-bench/public/demo_traces")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_demo_trace_dir() -> Path:
    if DEMO_TRACES_DIR.exists() and (DEMO_TRACES_DIR / "apoe_control.ab1").exists():
        return DEMO_TRACES_DIR
    if BENCH_DEMO_DIR.exists():
        return BENCH_DEMO_DIR
    return DEMO_TRACES_DIR


def clean_dna(seq: str) -> str:
    """Clean and validate DNA sequence string (uppercase ACGT only)."""
    return "".join(c for c in (seq or "").upper() if c in "ACGTN")


def parse_design_file(
    path: Optional[Path | str] = None,
    text: Optional[str] = None,
) -> dict[str, Any]:
    """Parse an HTML design report or JSON spec using the AssuredQC engine."""
    if not CLI_SCRIPT.exists():
        raise RuntimeError(f"CLI script not found at {CLI_SCRIPT}")
    payload: dict[str, Any] = {"action": "parse_design"}
    if path:
        payload["path"] = str(Path(path).resolve())
    if text:
        payload["text"] = text

    proc = subprocess.run(
        ["node", str(CLI_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        timeout=15,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        try:
            parsed_err = json.loads(err)
            raise RuntimeError(parsed_err.get("error") or err)
        except Exception:
            raise RuntimeError(err)

    return json.loads(proc.stdout)


def run_in_silico_assessment(
    control_path: Path | str,
    sample_paths: list[Path | str],
    qc_design_spec: Optional[dict[str, Any]] = None,
    qc_design_path: Optional[Path | str] = None,
    qc_design_html: Optional[str] = None,
    workflow: Optional[str] = None,
    guides: Optional[list[str]] = None,
    donor: Optional[str] = None,
    gene: Optional[str] = None,
    nuclease: Optional[str] = None,
    title: Optional[str] = None,
    options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Execute the ASSURED Bench headless trace QC deconvolution engine via Node CLI."""
    if not CLI_SCRIPT.exists():
        raise RuntimeError(f"Trace analysis CLI script not found at {CLI_SCRIPT}")

    ctrl = Path(control_path).resolve()
    if not ctrl.exists():
        raise RuntimeError(f"Control trace file does not exist: {ctrl}")

    samples = [str(Path(p).resolve()) for p in sample_paths if Path(p).exists()]
    if not samples:
        raise RuntimeError("No valid sample trace files were provided.")

    payload = {
        "controlFile": str(ctrl),
        "sampleFiles": samples,
        "qcDesignSpec": qc_design_spec,
        "qcDesignPath": str(Path(qc_design_path).resolve()) if qc_design_path else None,
        "qcDesignHtml": qc_design_html,
        "workflow": workflow,
        "guides": guides or [],
        "donor": donor or "",
        "gene": gene or "",
        "nuclease": nuclease or "SpCas9",
        "title": title or "",
        "options": options or {},
    }

    try:
        proc = subprocess.run(
            ["node", str(CLI_SCRIPT)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError("Node.js is not installed or not found on PATH.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Trace deconvolution timed out after 60 seconds.")

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip() or f"Process exited with code {proc.returncode}"
        try:
            parsed_err = json.loads(proc.stderr or proc.stdout)
            if parsed_err.get("error"):
                err_msg = parsed_err["error"]
        except Exception:
            pass
        raise RuntimeError(f"Trace analysis engine error: {err_msg}")

    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid response from trace analysis engine: {exc} (raw output: {proc.stdout[:200]})") from exc

    if not output.get("success"):
        raise RuntimeError(output.get("error") or "Trace analysis failed.")

    return output


def save_assessment_reports(
    selected_project: Optional[str],
    qc_output: dict[str, Any],
    subfolder: str = "screening",
    context_step: str = "screening",
) -> dict[str, Path]:
    """Save screening CSV, HTML report, SVG figure, and JSON snapshot into project dirs."""
    workflow = qc_output.get("workflow") or "qc"
    timestamp = int(time.time())

    if selected_project:
        reports_dir = project_subdir(selected_project, "reports", subfolder)
        snapshots_dir = project_subdir(selected_project, "snapshots", subfolder)
    else:
        reports_dir = ROOT / "data" / "assessment_logs"
        snapshots_dir = ROOT / "data" / "assessment_logs"

    reports_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    slug = f"screening_{workflow.lower()}_{timestamp}"

    # 1. Save screening decisions CSV
    csv_content = qc_output.get("screeningCsv") or ""
    csv_file = reports_dir / f"{slug}_screening_decisions.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    # 2. Save interactive HTML report
    html_content = qc_output.get("reportHtml") or ""
    html_file = reports_dir / f"{slug}_assessment_report.html"
    html_file.write_text(html_content, encoding="utf-8")

    # 3. Save Clone Selection SVG figure (if present)
    svg_content = qc_output.get("selectionFigureSvg") or ""
    svg_file = None
    if svg_content:
        svg_file = reports_dir / f"{slug}_clone_selection.svg"
        svg_file.write_text(svg_content, encoding="utf-8")

    # 4. Save JSON Snapshot
    json_snapshot = {
        "workflow": workflow,
        "timestamp_unix": timestamp,
        "totalSamples": qc_output.get("totalSamples", 0),
        "acceptedCount": qc_output.get("acceptedCount", 0),
        "preferredSample": qc_output.get("preferredSample", ""),
        "preferred": qc_output.get("preferred", {}),
        "results": qc_output.get("results", []),
        "qcSpec": qc_output.get("qcSpec", {}),
        "files": {
            "screening_csv": normalize_path_for_storage(csv_file),
            "html_report": normalize_path_for_storage(html_file),
        },
    }
    if svg_file:
        json_snapshot["files"]["selection_svg"] = normalize_path_for_storage(svg_file)

    json_file = snapshots_dir / f"{slug}_qc_results.json"
    json_file.write_text(json.dumps(json_snapshot, indent=2), encoding="utf-8")

    saved_files = {
        "csv": csv_file,
        "html": html_file,
        "json": json_file,
    }
    if svg_file:
        saved_files["svg"] = svg_file

    # Register in project manifest and audit log if project is selected
    if selected_project:
        # Register reports
        register_file(
            selected_project,
            "reports",
            csv_file,
            sha256=sha256_bytes(csv_file.read_bytes()),
            size=csv_file.stat().st_size,
            mime="text/csv",
            context={"step": context_step, "kind": "screening_decisions_csv"},
        )
        register_file(
            selected_project,
            "reports",
            html_file,
            sha256=sha256_bytes(html_file.read_bytes()),
            size=html_file.stat().st_size,
            mime="text/html",
            context={"step": context_step, "kind": "assessment_report_html"},
        )
        if svg_file:
            register_file(
                selected_project,
                "reports",
                svg_file,
                sha256=sha256_bytes(svg_file.read_bytes()),
                size=svg_file.stat().st_size,
                mime="image/svg+xml",
                context={"step": context_step, "kind": "clone_selection_svg"},
            )
        # Register snapshot
        register_file(
            selected_project,
            "snapshots",
            json_file,
            sha256=sha256_bytes(json_file.read_bytes()),
            size=json_file.stat().st_size,
            mime="application/json",
            context={"step": context_step, "kind": "qc_results_json"},
        )

        pref = qc_output.get("preferred") or {}
        append_audit(
            selected_project,
            f"{context_step}_in_app_screen",
            details={
                "tool": "ASSURED Bench (Trace Analysis)",
                "workflow": workflow,
                "preferred_sample": pref.get("name"),
                "preferred_decision": pref.get("decision"),
                "hdr_pct": pref.get("intendedEditPct"),
                "indel_pct": pref.get("indelPct"),
                "total_samples": qc_output.get("totalSamples", 0),
                "csv_file": str(csv_file.name),
                "html_file": str(html_file.name),
            },
        )

    return saved_files


def apply_assessment_to_session(
    qc_output: dict[str, Any],
    selected_sample_name: Optional[str] = None,
) -> None:
    """Populate Streamlit session_state with preferred clone metrics for Step 4 form submission."""
    results = qc_output.get("results") or []
    pref = qc_output.get("preferred") or {}

    sample_item = None
    if selected_sample_name:
        for r in results:
            if r.get("name") == selected_sample_name:
                sample_item = r
                break
    if not sample_item:
        sample_item = pref or (results[0] if results else {})

    qc_decision = str(sample_item.get("decision") or "hold").lower()
    chain_decision = QC_DECISION_TO_CHAIN.get(qc_decision, "repeat_edit")

    sample_name = sample_item.get("name") or "Sample"
    hdr_val = float(sample_item.get("intendedEditPct") or 0.0)
    indel_val = float(sample_item.get("indelPct") or sample_item.get("koScorePct") or 0.0)
    wt_val = float(sample_item.get("wtPct") or 0.0)

    reasons_list = sample_item.get("reasons") or []
    reasons_str = "; ".join(str(x) for x in reasons_list) if isinstance(reasons_list, list) else str(reasons_list)

    notes_text = (
        f"ASSURED Bench In-App Trace Analysis: Preferred clone '{sample_name}'. "
        f"QC Decision: {qc_decision.upper()} (Rank {sample_item.get('rank', 1)}). "
        f"HDR={hdr_val}%, Indel={indel_val}%, WT={wt_val}%. {reasons_str}"
    )

    imported_dict = {
        "tool_used": "ASSURED Bench (Trace Analysis)",
        "assay_type": "Sanger-Indel",
        "total_indel_pct": indel_val,
        "hdr_pct": hdr_val,
        "ki_pct": hdr_val,
        "decision": chain_decision,
        "notes": notes_text,
        "cell_line": sample_name,
        "preferred_sample": sample_name,
        "samples": results,
    }

    st.session_state["assured_qc_import"] = imported_dict
    st.session_state["total_indel_pct_autofill"] = indel_val
    st.session_state["hdr_pct_autofill"] = hdr_val
    st.session_state["decision_autofill"] = chain_decision
    st.session_state["tool_used_autofill"] = "ASSURED Bench (Trace Analysis)"
    st.session_state["cell_line_autofill"] = sample_name
    st.session_state["notes_autofill"] = notes_text

    # Auto-bind to Stage 5 Dual-Clone release inputs
    st.session_state["qc_hdr_a"] = hdr_val
    if len(results) > 1:
        st.session_state["qc_hdr_b"] = float(results[1].get("intendedEditPct") or results[1].get("koScorePct") or 94.5)


def find_project_design_files(selected_project: Optional[str]) -> list[Path]:
    """Locate all Stage 2 design files (HTML reports and JSON specs) for a project."""
    if not selected_project:
        return []
    files: list[Path] = []
    reports_dir = project_subdir(selected_project, "reports", "design")
    if reports_dir.exists():
        files.extend(reports_dir.glob("*.html"))
        files.extend(reports_dir.glob("*.htm"))
    snapshots_dir = project_subdir(selected_project, "snapshots", "design")
    if snapshots_dir.exists():
        files.extend(snapshots_dir.glob("*.json"))
    seen = set()
    unique_files = []
    for f in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
        if str(f) not in seen:
            seen.add(str(f))
            unique_files.append(f)
    return unique_files


_find_project_design_specs = find_project_design_files


def render_assured_qc_design_section(
    key_prefix: str,
    selected_project: Optional[str],
    default_gene: str = "Target",
    default_workflow: str = "snp",
) -> dict[str, Any]:
    """Unified CRISPR Design & Sequence input section matching AssuredQC.
    
    Supports:
    1. Loading/uploading exported Assured HTML design reports (.html/.htm) and JSON specs (.json).
    2. Real-time extraction and preview of gRNAs, ssODN donors, and enzyme.
    3. Manual sequence entry (gRNA spacers, donor ssODN, nuclease, workflow) with DNA validation.
    4. One-click sequence override/customization directly from loaded designs.
    """
    mode_key = f"{key_prefix}_design_mode"
    if mode_key not in st.session_state:
        st.session_state[mode_key] = "📁 Design File (HTML Report / JSON Spec)"

    # Segmented mode selector
    mode = st.radio(
        "CRISPR Design Specification Mode",
        ["📁 Design File (HTML Report / JSON Spec)", "✍️ Manual Sequences (gRNA Spacers & Donor ssODN)"],
        horizontal=True,
        key=f"{key_prefix}_mode_radio",
        index=0 if st.session_state[mode_key].startswith("📁") else 1,
    )
    st.session_state[mode_key] = mode

    demo_dir = get_demo_trace_dir()
    apoe_design_file = demo_dir / "apoe_r176c_snp.design.json"

    resolved: dict[str, Any] = {
        "mode": "file" if mode.startswith("📁") else "manual",
        "qc_design_path": None,
        "qc_design_html": None,
        "qc_design_spec": None,
        "guides": [],
        "donor": "",
        "nuclease": "SpCas9",
        "gene": default_gene,
        "workflow": default_workflow,
        "goal": "homozygous",
        "label": "",
    }

    if mode.startswith("📁"):
        # ---------------------------------------------------------
        # MODE 1: Design File (HTML Report or JSON Spec)
        # ---------------------------------------------------------
        design_files = find_project_design_files(selected_project)
        design_options = ["Demo: APOE R176C SNP Spec", "Upload Design File (.html / .json)"]
        if design_files:
            for f in design_files:
                prefix = "Stage 2 HTML Report" if f.suffix.lower() in [".html", ".htm"] else "Stage 2 JSON Spec"
                design_options.insert(0, f"{prefix}: {f.name}")

        choice = st.selectbox(
            "Design Report / Spec Source",
            design_options,
            key=f"{key_prefix}_file_choice",
            help="Select an exported Assured HTML design report, JSON spec, or demo.",
        )

        chosen_path: Optional[Path] = None
        chosen_html: Optional[str] = None
        parsed_info: Optional[dict[str, Any]] = None

        if choice.startswith("Stage 2"):
            clean_name = choice.split(":", 1)[1].strip()
            matches = [f for f in design_files if f.name == clean_name]
            if matches:
                chosen_path = matches[0]
                try:
                    parsed_info = parse_design_file(path=chosen_path)
                except Exception as e:
                    st.warning(f"Could not parse design file: {e}")
        elif choice == "Demo: APOE R176C SNP Spec":
            if apoe_design_file.exists():
                chosen_path = apoe_design_file
                try:
                    parsed_info = parse_design_file(path=chosen_path)
                except Exception as e:
                    st.warning(f"Could not parse demo design: {e}")
        else:
            uploaded = st.file_uploader(
                "Upload Assured Design Report (.html / .htm) or JSON Spec (.json)",
                type=["html", "htm", "json"],
                key=f"{key_prefix}_uploaded_design",
                help="Upload the exported Assured CRISPR design report HTML or JSON spec.",
            )
            if uploaded is not None:
                text_content = uploaded.getvalue().decode("utf-8", errors="replace")
                if uploaded.name.lower().endswith((".html", ".htm")) or text_content.strip().startswith("<"):
                    chosen_html = text_content
                else:
                    try:
                        resolved["qc_design_spec"] = json.loads(text_content)
                    except Exception:
                        pass
                try:
                    parsed_info = parse_design_file(text=text_content)
                except Exception as e:
                    st.warning(f"Could not parse uploaded design: {e}")

        # If a design was successfully parsed, display rich preview matching AssuredQC
        if parsed_info and parsed_info.get("success"):
            p_label = parsed_info.get("label") or "Design File"
            p_nuc = parsed_info.get("nuclease") or "SpCas9"
            p_guides = parsed_info.get("guides") or []
            p_donors = parsed_info.get("donors") or []
            p_gene = parsed_info.get("gene") or default_gene
            p_kind = parsed_info.get("editKind") or default_workflow

            resolved["qc_design_path"] = chosen_path
            resolved["qc_design_html"] = chosen_html
            resolved["nuclease"] = p_nuc
            resolved["gene"] = p_gene
            resolved["workflow"] = p_kind
            resolved["label"] = p_label
            resolved["guides"] = [g.get("sequence") for g in p_guides if g.get("sequence")]
            resolved["donor"] = p_donors[0].get("sequence", "") if p_donors else ""

            # Card preview matching AssuredQC
            st.markdown(
                f"""
                <div style="padding: 10px 14px; background: #ecfdf3; border-radius: 8px; border: 1px solid #a6f4c5; margin-bottom: 8px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-weight: 700; color: #027a48; font-size: 0.92rem;">
                            ✓ Design Loaded &amp; Parsed: <code>{p_label}</code>
                        </span>
                        <span style="font-size: 0.8rem; color: #475467; background: #f2f4f7; padding: 2px 8px; border-radius: 4px;">
                            Enzyme: <strong>{p_nuc}</strong>
                        </span>
                    </div>
                    <div style="font-size: 0.85rem; color: #344054;">
                        <strong>gRNA Spacers ({len(p_guides)}):</strong> {', '.join([f"<code>{g.get('sequence', '')[:10]}...</code> ({g.get('length', 20)} nt)" for g in p_guides]) if p_guides else 'None (cut site inferred)'}<br/>
                        <strong>Donor ssODN ({len(p_donors)}):</strong> {', '.join([f"{d.get('name', 'Donor')}: {d.get('length', 0)} nt" for d in p_donors]) if p_donors else 'None (Knockout/Deletion)'}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Button to customize / override sequences
            if st.button("✎ Customize / Override Sequences", key=f"{key_prefix}_override_btn", help="Transfer parsed sequences to manual input for editing"):
                st.session_state[f"{key_prefix}_manual_nuc"] = p_nuc
                st.session_state[f"{key_prefix}_manual_guides"] = ", ".join([g.get("sequence", "") for g in p_guides if g.get("sequence")])
                st.session_state[f"{key_prefix}_manual_donor"] = p_donors[0].get("sequence", "") if p_donors else ""
                st.session_state[f"{key_prefix}_manual_gene"] = p_gene
                st.session_state[f"{key_prefix}_manual_wf"] = p_kind
                st.session_state[mode_key] = "✍️ Manual Sequences (gRNA Spacers & Donor ssODN)"
                st.rerun()

    else:
        # ---------------------------------------------------------
        # MODE 2: Manual Sequence Input (matching AssuredQC Custom Mode)
        # ---------------------------------------------------------
        st.markdown(
            """
            <div style="padding: 8px 12px; background: #fffbeb; border: 1px solid #fef08a; border-radius: 6px; font-size: 0.85rem; color: #854d0e; margin-bottom: 8px;">
                ⚠️ <strong>Manual Sequence Input Active:</strong> The deconvolution engine will use your custom gRNA spacers, donor ssODN, and enzyme entered below.
            </div>
            """,
            unsafe_allow_html=True,
        )

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            nuc_list = ["SpCas9", "enAsCas12a", "Cas12a", "SaCas9"]
            cur_nuc = st.session_state.get(f"{key_prefix}_manual_nuc", "SpCas9")
            nuc_idx = nuc_list.index(cur_nuc) if cur_nuc in nuc_list else 0
            nuclease_val = st.selectbox("Enzyme / Nuclease", nuc_list, index=nuc_idx, key=f"{key_prefix}_nuc_input")

            raw_guides = st.text_area(
                "gRNA Spacer Sequence(s) *",
                value=st.session_state.get(f"{key_prefix}_manual_guides", ""),
                height=68,
                placeholder="e.g. ACACTGCCAGGCGCTTCTGC, CCCCGGCCTGGTACACTGCC",
                help="Enter 1 to 3 gRNA spacers. Comma, space, or newline separated. 20 nt DNA.",
                key=f"{key_prefix}_guides_text",
            )
            # Parse and validate guides
            guide_list = [clean_dna(g) for g in re.split(r"[,;\s]+", raw_guides) if clean_dna(g)]
            if guide_list:
                guide_badges = " ".join([f"`{g[:10]}...` ({len(g)} nt)" for g in guide_list])
                st.caption(f"✓ {len(guide_list)} guide(s) entered: {guide_badges}")
            else:
                st.caption("ℹ️ If empty, the cut site will be inferred from where sample reads depart from control.")

        with col_m2:
            raw_donor = st.text_area(
                "Donor Template Sequence (ssODN)",
                value=st.session_state.get(f"{key_prefix}_manual_donor", ""),
                height=68,
                placeholder="Paste single-stranded donor oligonucleotide sequence (ssODN)...",
                help="Required for SNP correction (HDR) or small tag insertion. Leave blank for knockout.",
                key=f"{key_prefix}_donor_text",
            )
            clean_donor_val = clean_dna(raw_donor)
            if clean_donor_val:
                st.caption(f"✓ Donor entered: **{len(clean_donor_val)} nt DNA**")
            else:
                st.caption("ℹ️ Optional (leave blank for knockout or deletion workflows).")

            col_wf1, col_wf2 = st.columns(2)
            with col_wf1:
                wf_opts = ["snp", "knockout", "deletion", "tag_small"]
                cur_wf = st.session_state.get(f"{key_prefix}_manual_wf", "snp" if clean_donor_val else "knockout")
                wf_idx = wf_opts.index(cur_wf) if cur_wf in wf_opts else (0 if clean_donor_val else 1)
                wf_val = st.selectbox(
                    "Workflow",
                    wf_opts,
                    index=wf_idx,
                    format_func=lambda x: {
                        "snp": "SNP Correction (ssODN, HDR)",
                        "knockout": "Knockout (Indels at 1 cut)",
                        "deletion": "Dual-cut Deletion",
                        "tag_small": "Small Tag Knock-in",
                    }.get(x, x),
                    key=f"{key_prefix}_wf_input",
                )
            with col_wf2:
                goal_val = st.selectbox("Editing Goal", ["homozygous", "heterozygous", "any"], index=0, key=f"{key_prefix}_goal_input")

        resolved["guides"] = guide_list
        resolved["donor"] = clean_donor_val
        resolved["nuclease"] = nuclease_val
        resolved["workflow"] = wf_val
        resolved["goal"] = goal_val
        resolved["label"] = f"Manual ({nuclease_val}, {len(guide_list)} guides)"

        if st.button("↺ Revert to Design File Mode", key=f"{key_prefix}_revert_btn"):
            st.session_state[mode_key] = "📁 Design File (HTML Report / JSON Spec)"
            st.rerun()

    return resolved


def render_in_app_analyzer(selected_project: Optional[str]) -> None:
    """Render the In-App Trace Analysis (Deconvolution & Clone Decision Engine) UI component."""
    with st.expander("⚡ In-App Trace Analysis (Deconvolution & Clone Decision Engine)", expanded=True):
        st.markdown(
            "Run Sanger trace deconvolution, quantify WT / Indel / HDR fractions, evaluate zygosity, "
            "and generate ranked clone screening decisions directly inside ASSUREDChain."
        )

        col_cfg1, col_cfg2 = st.columns(2)

        # 1. CRISPR Design Specification & Sequences (Matching AssuredQC)
        with col_cfg1:
            st.markdown("##### 1. CRISPR Design & Sequences")
            design_info = render_assured_qc_design_section(
                key_prefix="in_app_analyzer",
                selected_project=selected_project,
            )

        # 2. Control and Sample Traces
        with col_cfg2:
            st.markdown("##### 2. Chromatograms (.ab1 Traces)")
            control_upload = st.file_uploader(
                "Parental / WT Control Trace (.ab1)",
                type=["ab1", "abi", "scf"],
                key="in_app_analyzer_control_upload",
            )
            samples_upload = st.file_uploader(
                "Edited Clone Sample Trace(s) (.ab1)",
                type=["ab1", "abi", "scf"],
                accept_multiple_files=True,
                key="in_app_analyzer_samples_upload",
            )

            load_demo_clicked = st.button("✨ Load Demo Traces (APOE Control + 3 Clones)", key="load_demo_traces_btn")
            if load_demo_clicked:
                st.session_state["in_app_analyzer_use_demo"] = True
                st.success("APOE Demo Traces loaded (1 parental control + 3 edited clones: homozygous, heterozygous, partial)!")

            if st.session_state.get("in_app_analyzer_use_demo"):
                st.info("Using bundled APOE demo traces (Control + 3 Clones).")

        # Execute Analysis
        run_col1, run_col2 = st.columns([1, 3])
        with run_col1:
            run_btn = st.button("🚀 Run In-App Trace Analysis", key="run_in_app_analyzer_btn", type="primary")

        temp_dir = ROOT / "data" / "temp_qc"
        temp_dir.mkdir(parents=True, exist_ok=True)

        if run_btn:
            use_demo = st.session_state.get("in_app_analyzer_use_demo", False)
            ctrl_path: Optional[Path] = None
            sample_paths: list[Path] = []

            if use_demo:
                demo_dir = get_demo_trace_dir()
                ctrl_path = demo_dir / "apoe_control.ab1"
                sample_paths = [
                    demo_dir / "apoe_snp_clone_hom_edited.ab1",
                    demo_dir / "apoe_snp_clone_het_edited.ab1",
                    demo_dir / "apoe_snp_clone_partial_het_edited.ab1",
                ]
                if not design_info.get("qc_design_spec") and not design_info.get("qc_design_path") and not design_info.get("qc_design_html") and not design_info.get("guides"):
                    apoe_design_file = demo_dir / "apoe_r176c_snp.design.json"
                    if apoe_design_file.exists():
                        design_info["qc_design_path"] = apoe_design_file
            else:
                if control_upload is not None:
                    ctrl_path = temp_dir / control_upload.name
                    ctrl_path.write_bytes(control_upload.getvalue())
                if samples_upload:
                    for s in samples_upload:
                        sp = temp_dir / s.name
                        sp.write_bytes(s.getvalue())
                        sample_paths.append(sp)

            if not ctrl_path or not ctrl_path.exists():
                st.error("Please upload a Parental Control trace (.ab1) or click 'Load Demo Traces'.")
                return

            if not sample_paths:
                st.error("Please upload at least one edited Sample trace (.ab1) or click 'Load Demo Traces'.")
                return

            with st.spinner("Deconvolving chromatograms and evaluating clone zygosity..."):
                try:
                    qc_output = run_in_silico_assessment(
                        control_path=ctrl_path,
                        sample_paths=sample_paths,
                        qc_design_spec=design_info.get("qc_design_spec"),
                        qc_design_path=design_info.get("qc_design_path"),
                        qc_design_html=design_info.get("qc_design_html"),
                        workflow=design_info.get("workflow", "snp"),
                        guides=design_info.get("guides"),
                        donor=design_info.get("donor"),
                        gene=design_info.get("gene"),
                        nuclease=design_info.get("nuclease"),
                        options={"goal": design_info.get("goal")},
                    )
                    st.session_state["in_app_analyzer_output"] = qc_output
                    st.success(f"Analysis complete: {qc_output.get('totalSamples', 0)} samples evaluated!")
                except Exception as err:
                    st.error(f"Trace analysis failed: {err}")
                    return

        # Render Results
        qc_output = st.session_state.get("in_app_analyzer_output")
        if qc_output and qc_output.get("success"):
            st.markdown("---")
            pref = qc_output.get("preferred") or {}
            pref_name = pref.get("name") or "Sample"
            pref_decision = str(pref.get("decision") or "hold").upper()

            # Preferred Clone Banner
            banner_bg = "#d1fadf" if pref_decision == "ACCEPT" else ("#fef0c7" if pref_decision == "HOLD" else "#fee4e2")
            banner_fg = "#067647" if pref_decision == "ACCEPT" else ("#b54708" if pref_decision == "HOLD" else "#b42318")

            st.markdown(
                f"""
                <div style="background-color: {banner_bg}; border: 1px solid {banner_fg}; border-radius: 8px; padding: 14px 18px; margin-bottom: 15px;">
                    <span style="font-size: 1.15rem; font-weight: bold; color: {banner_fg};">
                        🏆 Top Recommended Clone: <code>{pref_name}</code> — Decision: {pref_decision} (Rank {pref.get('rank', 1)})
                    </span><br/>
                    <span style="color: #344054; font-size: 0.95rem;">
                        <strong>HDR / Intended Edit:</strong> {pref.get('intendedEditPct', 0)}% &nbsp;|&nbsp;
                        <strong>Indels:</strong> {pref.get('indelPct', 0)}% &nbsp;|&nbsp;
                        <strong>Wild-Type:</strong> {pref.get('wtPct', 0)}% &nbsp;|&nbsp;
                        <strong>Recommendation:</strong> {pref.get('action') or 'Review trace'}
                    </span>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Metrics row
            col_m1, col_m2, col_m3, col_m4 = st.columns(4)
            with col_m1:
                st.metric("Total Samples", qc_output.get("totalSamples", 0))
            with col_m2:
                st.metric("Accepted Clones", qc_output.get("acceptedCount", 0))
            with col_m3:
                holds = sum(1 for r in qc_output.get("results", []) if str(r.get("decision", "")).lower() == "hold")
                st.metric("Hold / Review", holds)
            with col_m4:
                others = qc_output.get("totalSamples", 0) - qc_output.get("acceptedCount", 0) - holds
                st.metric("Re-sequence / Reject", others)

            # Table of sample results
            st.markdown("##### Analyzed Clone Screen Decisions")
            results_rows = []
            for r in qc_output.get("results", []):
                results_rows.append({
                    "Sample": r.get("name"),
                    "Decision": str(r.get("decision", "")).upper(),
                    "HDR %": r.get("intendedEditPct"),
                    "Indel %": r.get("indelPct"),
                    "WT %": r.get("wtPct"),
                    "Rank": r.get("rank"),
                    "Genotype / Notes": r.get("genotypeSummary") or "; ".join(r.get("reasons", [])[:1]),
                })
            results_df = pd.DataFrame(results_rows)
            st.dataframe(results_df, use_container_width=True)

            # Clone Selection Figure (SVG)
            svg_content = qc_output.get("selectionFigureSvg")
            if svg_content:
                with st.expander("📊 Vector Clone Selection Figure", expanded=True):
                    st.components.v1.html(
                        f"""
                        <div style="background-color: white; padding: 10px; border-radius: 6px; overflow-x: auto;">
                            {svg_content}
                        </div>
                        """,
                        height=360,
                        scrolling=True,
                    )

            # HTML Report Preview
            html_report = qc_output.get("reportHtml")
            if html_report:
                with st.expander("📄 Full Interactive QC & Screening Report (HTML)", expanded=False):
                    st.components.v1.html(html_report, height=500, scrolling=True)

            # Action Buttons: Save Reports & Apply to Form
            btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([1.5, 1.5, 1, 1])

            with btn_col1:
                if st.button("📋 Apply Preferred Clone to Form", key="in_app_analyzer_apply_btn", type="primary"):
                    apply_assessment_to_session(qc_output)
                    st.success(f"Applied {pref_name} ({pref_decision}) to the Step 4 form below!")
                    st.rerun()

            with btn_col2:
                if st.button("💾 Save Screening Reports & Register", key="in_app_analyzer_save_btn"):
                    try:
                        saved_paths = save_assessment_reports(selected_project, qc_output)
                        st.session_state["in_app_analyzer_saved_paths"] = saved_paths
                        # Also register attachment for on-chain anchoring
                        st.session_state["assured_qc_attachment"] = str(saved_paths["csv"])
                        st.success(
                            f"Saved & registered reports:\n"
                            f"• Decisions CSV: `{saved_paths['csv'].name}`\n"
                            f"• Full Report: `{saved_paths['html'].name}`\n"
                            f"• Snapshot JSON: `{saved_paths['json'].name}`"
                        )
                    except Exception as save_err:
                        st.error(f"Failed to save reports: {save_err}")

            with btn_col3:
                if qc_output.get("screeningCsv"):
                    st.download_button(
                        "📥 Download CSV",
                        qc_output["screeningCsv"],
                        file_name=f"screening_decisions_{pref_name}.csv",
                        mime="text/csv",
                        key="in_app_analyzer_dl_csv",
                    )

            with btn_col4:
                if html_report:
                    st.download_button(
                        "📥 Download HTML",
                        html_report,
                        file_name=f"assessment_report_{pref_name}.html",
                        mime="text/html",
                        key="in_app_analyzer_dl_html",
                    )


def render_stage4_clone_screening(selected_project: Optional[str]) -> Optional[dict[str, Any]]:
    """Render the AssuredQC Clone Screening & Sanger Deconvolution studio for Stage 4."""
    from app.components.project_state import load_project_meta, update_project_meta

    st.markdown(
        """
        <div style="background-color:#f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 12px 16px; margin-bottom: 12px;">
            <span style="font-weight: 700; color: #166534; font-size: 0.95rem;">🧬 AssuredQC In-App Clone Screening & Sanger Deconvolution</span><br/>
            <span style="color: #374151; font-size: 0.88rem;">
                Screen expanded single-cell colonies against parental WT control. AssuredQC deconvolves chromatograms via NNLS, 
                quantifies intended edit % vs indels, and ranks candidates to assign <strong>Primary Clone A</strong> and <strong>Backup Clone B</strong> for Stage 5 release testing.
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    meta = load_project_meta(selected_project) if selected_project else {}
    target_gene = meta.get("target_gene") or meta.get("gene") or "Target"
    edit_program = meta.get("edit_program") or meta.get("edit_type") or "SNP Knock-in (ssODN)"

    # Map program to default workflow
    default_wf_idx = 0
    if "knockout" in edit_program.lower() or "ko" in edit_program.lower():
        default_wf_idx = 1
    elif "deletion" in edit_program.lower():
        default_wf_idx = 2
    elif "tag" in edit_program.lower():
        default_wf_idx = 3

    col_cfg1, col_cfg2 = st.columns(2)

    # 1. CRISPR Target, Design & Sequences (Matching AssuredQC)
    with col_cfg1:
        st.markdown("##### 1. CRISPR Target, Design & Sequences")
        wf_name_map = {0: "snp", 1: "knockout", 2: "deletion", 3: "tag_small"}
        default_wf = wf_name_map.get(default_wf_idx, "snp")
        design_info = render_assured_qc_design_section(
            key_prefix="stage4_screen",
            selected_project=selected_project,
            default_gene=target_gene,
            default_workflow=default_wf,
        )

    # 2. Control and Sample Traces
    with col_cfg2:
        st.markdown("##### 2. Colony Sanger Chromatograms (.ab1)")
        control_upload = st.file_uploader(
            "Parental / WT Control Trace (.ab1)",
            type=["ab1", "abi", "scf"],
            key="stage4_screen_ctrl_upload",
        )
        samples_upload = st.file_uploader(
            "Expanded Colony Traces (.ab1)",
            type=["ab1", "abi", "scf"],
            accept_multiple_files=True,
            key="stage4_screen_samples_upload",
            help="Upload Sanger sequencing reads from 96-well plate colonies.",
        )

        load_demo_clicked = st.button("✨ Load Demo Clone Traces (Control + 3 Clones)", key="stage4_load_demo_traces_btn")
        if load_demo_clicked:
            st.session_state["stage4_screen_use_demo"] = True
            st.success("APOE Demo Colony Traces loaded (1 parental control + 3 clone reads)!")

        if st.session_state.get("stage4_screen_use_demo"):
            st.info("Using bundled APOE clone traces (Control + 3 single-cell colonies).")

    # Run Analysis Button
    run_col1, run_col2 = st.columns([1.5, 3])
    with run_col1:
        run_screen_btn = st.button("🚀 Run AssuredQC Clone Screening", key="stage4_run_screening_btn", type="primary")

    temp_dir = ROOT / "data" / "temp_qc"
    temp_dir.mkdir(parents=True, exist_ok=True)

    if run_screen_btn:
        use_demo = st.session_state.get("stage4_screen_use_demo", False)
        ctrl_path: Optional[Path] = None
        sample_paths: list[Path] = []

        if use_demo:
            demo_dir = get_demo_trace_dir()
            ctrl_path = demo_dir / "apoe_control.ab1"
            sample_paths = [
                demo_dir / "apoe_snp_clone_hom_edited.ab1",
                demo_dir / "apoe_snp_clone_het_edited.ab1",
                demo_dir / "apoe_snp_clone_partial_het_edited.ab1",
            ]
            if not design_info.get("qc_design_spec") and not design_info.get("qc_design_path") and not design_info.get("qc_design_html") and not design_info.get("guides"):
                apoe_design_file = demo_dir / "apoe_r176c_snp.design.json"
                if apoe_design_file.exists():
                    design_info["qc_design_path"] = apoe_design_file
        else:
            if control_upload is not None:
                ctrl_path = temp_dir / control_upload.name
                ctrl_path.write_bytes(control_upload.getvalue())
            if samples_upload:
                for s in samples_upload:
                    sp = temp_dir / s.name
                    sp.write_bytes(s.getvalue())
                    sample_paths.append(sp)

        if not ctrl_path or not ctrl_path.exists():
            st.error("Please upload a Parental Control trace (.ab1) or click 'Load Demo Clone Traces'.")
            return None

        if not sample_paths:
            st.error("Please upload at least one Colony trace (.ab1) or click 'Load Demo Clone Traces'.")
            return None

        with st.spinner("Deconvolving chromatograms via NNLS and ranking clone candidates..."):
            try:
                qc_output = run_in_silico_assessment(
                    control_path=ctrl_path,
                    sample_paths=sample_paths,
                    qc_design_spec=design_info.get("qc_design_spec"),
                    qc_design_path=design_info.get("qc_design_path"),
                    qc_design_html=design_info.get("qc_design_html"),
                    workflow=design_info.get("workflow", default_wf),
                    guides=design_info.get("guides"),
                    donor=design_info.get("donor"),
                    gene=design_info.get("gene", target_gene),
                    nuclease=design_info.get("nuclease"),
                    title=f"Stage 4 Screening - {selected_project or 'Project'}",
                    options={"goal": design_info.get("goal")},
                )
                st.session_state["stage4_screening_output"] = qc_output
                # Automatically save and register reports
                if selected_project:
                    saved_paths = save_assessment_reports(
                        selected_project,
                        qc_output,
                        subfolder="screening",
                        context_step="screening",
                    )
                    st.session_state["stage4_screening_saved_paths"] = saved_paths
                    st.session_state["stage4_screening_hash"] = sha256_bytes(saved_paths["json"].read_bytes())
                    update_project_meta(selected_project, {"screening_hash": st.session_state["stage4_screening_hash"]})

                st.success(f"AssuredQC complete: {qc_output.get('totalSamples', 0)} single-cell colonies screened and ranked!")
            except Exception as err:
                st.error(f"AssuredQC screening failed: {err}")
                return None

    # Render Results & Candidate Assignment
    qc_output = st.session_state.get("stage4_screening_output")
    if qc_output and qc_output.get("success"):
        st.markdown("---")
        results = qc_output.get("results") or []
        pref = qc_output.get("preferred") or {}
        pref_name = pref.get("name") or "Clone_1"
        pref_decision = str(pref.get("decision") or "accept").upper()

        # Highlights for Top 2 Clones
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            bg_a = "#d1fadf" if pref_decision == "ACCEPT" else "#fef0c7"
            fg_a = "#067647" if pref_decision == "ACCEPT" else "#b54708"
            st.markdown(
                f"""
                <div style="background-color: {bg_a}; border: 1px solid {fg_a}; border-radius: 8px; padding: 12px 16px;">
                    <span style="font-size: 1.05rem; font-weight: bold; color: {fg_a};">
                        🥇 Rank 1 Recommended: <code>{pref_name}</code> ({pref_decision})
                    </span><br/>
                    <span style="color: #344054; font-size: 0.9rem;">
                        <strong>Edit (HDR):</strong> {pref.get('intendedEditPct', 0)}% &nbsp;|&nbsp;
                        <strong>Indel:</strong> {pref.get('indelPct', 0)}% &nbsp;|&nbsp;
                        <strong>WT:</strong> {pref.get('wtPct', 0)}%<br/>
                        <strong>Genotype:</strong> {pref.get('genotypeSummary') or 'Targeted homozygous/heterozygous edit'}
                    </span>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col_c2:
            runner_up = results[1] if len(results) > 1 else pref
            runner_name = runner_up.get("name") or "Clone_2"
            runner_dec = str(runner_up.get("decision") or "hold").upper()
            bg_b = "#d1fadf" if runner_dec == "ACCEPT" else "#fef0c7"
            fg_b = "#067647" if runner_dec == "ACCEPT" else "#b54708"
            st.markdown(
                f"""
                <div style="background-color: {bg_b}; border: 1px solid {fg_b}; border-radius: 8px; padding: 12px 16px;">
                    <span style="font-size: 1.05rem; font-weight: bold; color: {fg_b};">
                        🥈 Rank 2 Backup: <code>{runner_name}</code> ({runner_dec})
                    </span><br/>
                    <span style="color: #344054; font-size: 0.9rem;">
                        <strong>Edit (HDR):</strong> {runner_up.get('intendedEditPct', 0)}% &nbsp;|&nbsp;
                        <strong>Indel:</strong> {runner_up.get('indelPct', 0)}% &nbsp;|&nbsp;
                        <strong>WT:</strong> {runner_up.get('wtPct', 0)}%<br/>
                        <strong>Genotype:</strong> {runner_up.get('genotypeSummary') or 'Confirmed targeted clone'}
                    </span>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # Leaderboard Table
        st.markdown("##### 🏆 Colony Screening & Ranking Leaderboard")
        results_rows = []
        for r in results:
            results_rows.append({
                "Rank": f"#{r.get('rank', 1)}",
                "Colony Sample": r.get("name"),
                "Decision": str(r.get("decision", "")).upper(),
                "Intended Edit %": r.get("intendedEditPct"),
                "Indel %": r.get("indelPct"),
                "WT %": r.get("wtPct"),
                "R² Quality": r.get("quality", {}).get("r2") if isinstance(r.get("quality"), dict) else r.get("r2"),
                "Genotype Call": r.get("genotypeSummary") or "; ".join(r.get("reasons", [])[:1]),
            })
        results_df = pd.DataFrame(results_rows)
        st.dataframe(results_df, use_container_width=True)

        # Vector Clone Selection Figure
        svg_content = qc_output.get("selectionFigureSvg")
        if svg_content:
            with st.expander("📊 Vector Clone Selection & Trace Alignment Figure", expanded=True):
                st.components.v1.html(
                    f"""
                    <div style="background-color: white; padding: 10px; border-radius: 6px; overflow-x: auto;">
                        {svg_content}
                    </div>
                    """,
                    height=360,
                    scrolling=True,
                )

        # Full HTML Report Preview
        html_report = qc_output.get("reportHtml")
        if html_report:
            with st.expander("📄 Full Interactive AssuredQC Screening Report (HTML)", expanded=False):
                st.components.v1.html(html_report, height=500, scrolling=True)

        # Download & Assignment Controls
        col_act1, col_act2, col_act3 = st.columns([2, 1, 1])
        with col_act1:
            auto_assign = st.button("🎯 Auto-Assign Screened Candidates (Rank 1 ➔ Clone A, Rank 2 ➔ Clone B)", key="stage4_auto_assign_btn", type="primary")
            if auto_assign:
                st.session_state["stage4_cand_a_val"] = pref_name
                st.session_state["stage4_cand_b_val"] = runner_name
                st.session_state["stage4_cand_a_origin"] = "Plate 1 - Well B04"
                st.session_state["stage4_cand_b_origin"] = "Plate 1 - Well E09"
                st.session_state["qc_hdr_a"] = float(pref.get("intendedEditPct") or 96.0)
                st.session_state["qc_hdr_b"] = float(runner_up.get("intendedEditPct") or 94.5)
                apply_assessment_to_session(qc_output)
                st.success(f"Assigned {pref_name} to Clone A and {runner_name} to Clone B! Scroll down to lock milestone.")
                st.rerun()

        with col_act2:
            if qc_output.get("screeningCsv"):
                st.download_button(
                    "📥 Download CSV",
                    qc_output["screeningCsv"],
                    file_name=f"screening_decisions_{selected_project or 'clones'}.csv",
                    mime="text/csv",
                    key="stage4_screen_dl_csv",
                )
        with col_act3:
            if html_report:
                st.download_button(
                    "📥 Download HTML",
                    html_report,
                    file_name=f"screening_report_{selected_project or 'clones'}.html",
                    mime="text/html",
                    key="stage4_screen_dl_html",
                )

        return qc_output

    return None