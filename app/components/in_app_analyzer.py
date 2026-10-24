"""In-App Sanger Trace QC and Clone Assessment Studio for ASSUREDChain."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
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


def run_in_silico_assessment(
    control_path: Path | str,
    sample_paths: list[Path | str],
    qc_design_spec: Optional[dict[str, Any]] = None,
    qc_design_path: Optional[Path | str] = None,
    workflow: Optional[str] = None,
    guides: Optional[list[str]] = None,
    donor: Optional[str] = None,
    gene: Optional[str] = None,
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
        "workflow": workflow,
        "guides": guides or [],
        "donor": donor or "",
        "gene": gene or "",
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
) -> dict[str, Path]:
    """Save screening CSV, HTML report, SVG figure, and JSON snapshot into project dirs."""
    workflow = qc_output.get("workflow") or "qc"
    timestamp = int(time.time())

    if selected_project:
        reports_dir = project_subdir(selected_project, "reports", "assessment")
        snapshots_dir = project_subdir(selected_project, "snapshots", "assessment")
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
            context={"step": "assessment", "kind": "screening_decisions_csv"},
        )
        register_file(
            selected_project,
            "reports",
            html_file,
            sha256=sha256_bytes(html_file.read_bytes()),
            size=html_file.stat().st_size,
            mime="text/html",
            context={"step": "assessment", "kind": "assessment_report_html"},
        )
        if svg_file:
            register_file(
                selected_project,
                "reports",
                svg_file,
                sha256=sha256_bytes(svg_file.read_bytes()),
                size=svg_file.stat().st_size,
                mime="image/svg+xml",
                context={"step": "assessment", "kind": "clone_selection_svg"},
            )
        # Register snapshot
        register_file(
            selected_project,
            "snapshots",
            json_file,
            sha256=sha256_bytes(json_file.read_bytes()),
            size=json_file.stat().st_size,
            mime="application/json",
            context={"step": "assessment", "kind": "qc_results_json"},
        )

        pref = qc_output.get("preferred") or {}
        append_audit(
            selected_project,
            "assessment_in_app_screen",
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


def _find_project_design_specs(selected_project: Optional[str]) -> list[Path]:
    """Locate design specs in project snapshots."""
    if not selected_project:
        return []
    snapshots_dir = project_subdir(selected_project, "snapshots", "design")
    if not snapshots_dir.exists():
        return []
    return sorted(snapshots_dir.glob("*_design_spec.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def render_in_app_analyzer(selected_project: Optional[str]) -> None:
    """Render the In-App Trace Analysis (Deconvolution & Clone Decision Engine) UI component."""
    with st.expander("⚡ In-App Trace Analysis (Deconvolution & Clone Decision Engine)", expanded=True):
        st.markdown(
            "Run Sanger trace deconvolution, quantify WT / Indel / HDR fractions, evaluate zygosity, "
            "and generate ranked clone screening decisions directly inside ASSUREDChain."
        )

        col_cfg1, col_cfg2 = st.columns(2)

        # 1. Design Spec Source
        with col_cfg1:
            st.markdown("##### 1. CRISPR Design Specification")
            design_files = _find_project_design_specs(selected_project)
            design_options = ["Demo: APOE R176C SNP Spec", "Upload Custom Design JSON"]
            if design_files:
                for f in design_files:
                    design_options.insert(0, f"Step 1 Design: {f.name}")

            design_choice = st.selectbox(
                "Design Spec Source",
                design_options,
                key="in_app_analyzer_design_choice",
                help="Select design generated from Step 1 Design Log, upload a JSON spec, or use demo.",
            )

            qc_design_spec = None
            qc_design_path = None

            demo_dir = get_demo_trace_dir()
            apoe_design_file = demo_dir / "apoe_r176c_snp.design.json"

            if design_choice.startswith("Step 1 Design:"):
                chosen_name = design_choice.replace("Step 1 Design:", "").strip()
                match_files = [f for f in design_files if f.name == chosen_name]
                if match_files:
                    qc_design_path = match_files[0]
                    try:
                        qc_design_spec = json.loads(qc_design_path.read_text(encoding="utf-8"))
                        st.caption(f"Loaded design: {qc_design_spec.get('design', {}).get('gene', 'Target')} ({qc_design_spec.get('design', {}).get('editKind', 'edit')})")
                    except Exception as e:
                        st.error(f"Failed to read design spec: {e}")
            elif design_choice == "Demo: APOE R176C SNP Spec":
                if apoe_design_file.exists():
                    qc_design_path = apoe_design_file
                    try:
                        qc_design_spec = json.loads(apoe_design_file.read_text(encoding="utf-8"))
                        st.caption("Loaded bundled APOE R176C SNP design spec.")
                    except Exception as e:
                        st.error(f"Failed to read demo design: {e}")
                else:
                    st.warning("Demo design spec not found on disk.")
            else:
                uploaded_design = st.file_uploader(
                    "Upload .design.json or .json spec",
                    type=["json"],
                    key="in_app_analyzer_design_upload",
                )
                if uploaded_design is not None:
                    try:
                        qc_design_spec = json.loads(uploaded_design.getvalue().decode("utf-8"))
                        st.caption(f"Loaded design spec: {uploaded_design.name}")
                    except Exception as e:
                        st.error(f"Invalid design JSON: {e}")

            workflow_choice = st.selectbox(
                "Experiment Workflow",
                ["snp", "knockout", "deletion", "tag_small"],
                format_func=lambda x: {
                    "snp": "SNP Correction (ssODN, HDR)",
                    "knockout": "Knockout (Indels at 1 cut)",
                    "deletion": "Dual-cut Deletion",
                    "tag_small": "Small Tag Knock-in",
                }.get(x, x),
                key="in_app_analyzer_workflow",
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
                if not qc_design_spec and apoe_design_file.exists():
                    qc_design_path = apoe_design_file
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
                        qc_design_spec=qc_design_spec,
                        qc_design_path=qc_design_path,
                        workflow=workflow_choice,
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