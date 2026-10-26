"""In-App CRISPR Design Studio and Report Generator for ASSUREDChain."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import streamlit as st

from app.components.assured_tools import (
    apply_design_import_to_session,
    parse_assured_design_export,
)
import hashlib

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

from app.components.project_state import (
    append_audit,
    normalize_path_for_storage,
    project_subdir,
    register_file,
)

ROOT = Path(__file__).resolve().parents[2]
CLI_SCRIPT = ROOT / "scripts" / "bench_design_cli.mjs"


def get_bundled_references() -> dict[str, Path]:
    """Return dictionary of gene symbol -> Path for bundled GenBank reference files."""
    ref_dirs = [
        ROOT / "data" / "references",
        ROOT.parent / "assured-bench" / "public" / "references",
        ROOT.parent / "assured-bench" / "test" / "fixtures",
        Path("c:/Users/ntelugu/Desktop/assured-bench/public/references"),
        Path("c:/Users/ntelugu/Desktop/assured-bench/test/fixtures"),
    ]
    refs: dict[str, Path] = {}
    for rdir in ref_dirs:
        if not rdir.exists():
            continue
        for f in rdir.glob("*.gb"):
            gene_name = f.stem.split("-")[0].split("_")[0].upper()
            if gene_name not in refs:
                refs[gene_name] = f
    return refs


def run_in_silico_design(
    gene: str,
    edit_type: str = "ko",
    mutation: str = "",
    tag: str = "FLAG",
    arm_length: int = 400,
    genbank_text: str = "",
    options: Optional[dict] = None,
) -> dict[str, Any]:
    """Execute the ASSURED Bench in-silico CRISPR design engine via Node CLI."""
    if not CLI_SCRIPT.exists():
        raise RuntimeError(f"Design engine CLI script not found at {CLI_SCRIPT}")

    # If no custom GenBank sequence is passed, check bundled offline references
    if not (genbank_text or "").strip() and gene:
        bundled = get_bundled_references()
        sym = gene.strip().upper()
        if sym in bundled and bundled[sym].exists():
            try:
                genbank_text = bundled[sym].read_text(encoding="utf-8")
            except Exception:
                pass

    payload = {
        "gene": (gene or "").strip(),
        "editType": (edit_type or "ko").strip().lower(),
        "mutation": (mutation or "").strip(),
        "tag": (tag or "FLAG").strip(),
        "armLength": int(arm_length),
        "genbankText": (genbank_text or "").strip(),
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
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError("Node.js is not installed or not found on PATH.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("In-silico design timed out after 30 seconds.")

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip() or f"Process exited with code {proc.returncode}"
        try:
            parsed_err = json.loads(proc.stdout)
            if parsed_err.get("error"):
                err_msg = parsed_err["error"]
        except Exception:
            pass
        raise RuntimeError(f"Design engine error: {err_msg}")

    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid response from design engine: {exc}") from exc

    if not output.get("success"):
        raise RuntimeError(output.get("error") or "Design generation failed.")

    return output


def save_design_reports(
    selected_project: Optional[str],
    design_output: dict[str, Any],
) -> dict[str, Path]:
    """Save HTML report and design JSON into the project's repository directories."""
    gene = design_output.get("gene") or "target"
    edit_type = design_output.get("editType") or "edit"
    timestamp = int(time.time())

    if selected_project:
        reports_dir = project_subdir(selected_project, "reports", "design")
        snapshots_dir = project_subdir(selected_project, "snapshots", "design")
    else:
        reports_dir = ROOT / "data" / "design_logs_v2"
        snapshots_dir = ROOT / "data" / "design_logs_v2"

    reports_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    slug = f"{gene.lower()}_{edit_type.lower()}_{timestamp}"

    # 1. Save HTML report
    html_content = design_output.get("reportHtml") or ""
    html_file = reports_dir / f"{slug}_report.html"
    html_file.write_text(html_content, encoding="utf-8")

    # 2. Save Design JSON (schema assured-qc-design/1)
    qc_spec = design_output.get("qcDesignSpec") or {}
    json_file = snapshots_dir / f"{slug}_design.json"
    json_file.write_text(json.dumps(qc_spec, indent=2), encoding="utf-8")

    # 3. Save SVG locus schematic if available
    svg_content = design_output.get("svg") or ""
    svg_file = reports_dir / f"{slug}_locus.svg"
    if svg_content:
        svg_file.write_text(svg_content, encoding="utf-8")

    # Register files in project manifest
    if selected_project:
        now_unix = timestamp
        register_file(
            selected_project,
            "reports",
            {
                "step": "design",
                "path": normalize_path_for_storage(html_file),
                "timestamp": now_unix,
                "digest": sha256_bytes(html_file.read_bytes()),
                "type": "html",
            },
        )
        register_file(
            selected_project,
            "snapshots",
            {
                "step": "design",
                "path": normalize_path_for_storage(json_file),
                "timestamp": now_unix,
                "digest": sha256_bytes(json_file.read_bytes()),
                "type": "json",
            },
        )
        append_audit(
            selected_project,
            {
                "ts": now_unix,
                "step": "design",
                "action": "in_app_design_saved",
                "gene": gene,
                "edit_type": edit_type,
                "report_html": html_file.name,
                "design_json": json_file.name,
            },
        )

    return {
        "html_file": html_file,
        "json_file": json_file,
        "svg_file": svg_file if svg_content else None,
    }


def render_in_app_designer(selected_project: Optional[str]) -> None:
    """Render the In-App CRISPR Design Studio UI in Streamlit."""
    from app.components.project_state import load_project_meta
    meta = load_project_meta(selected_project) if selected_project else {}
    default_gene = meta.get("target_gene") or meta.get("gene") or "APOE"
    default_mutation = meta.get("mutation_label") or meta.get("mutation") or "R176C"
    meta_edit = str(meta.get("edit_program") or meta.get("edit_type") or "").lower()

    with st.expander("⚡ In-App CRISPR Designer (Instant Design & Report Generation)", expanded=True):
        st.caption(
            "Design CRISPR guides, donor templates (with silent blocking mutations), and primers "
            "directly inside ASSUREDChain. Automatically preview locus schematics and save FAIR design reports."
        )

        col_g, col_t, col_e, col_m = st.columns([1.3, 1.4, 1.3, 1.8])
        with col_g:
            target_gene = st.text_input("Target Gene Symbol", value=default_gene, key="in_app_target_gene")
        with col_t:
            edit_options = {
                "Point Mutation (SNP-KI)": "pm",
                "Gene Knockout (KO)": "ko",
                "C-Terminal Tag": "ct",
                "N-Terminal Tag": "nt",
                "Internal Tag (In-Frame)": "it",
            }
            default_edit_idx = 1 if ("ko" in meta_edit or "knockout" in meta_edit) else (2 if ("tag" in meta_edit or "ct" in meta_edit) else 0)
            edit_choice = st.selectbox("Edit Type", list(edit_options.keys()), index=default_edit_idx, key="in_app_edit_choice")
            selected_edit_type = edit_options[edit_choice]
        with col_e:
            enzyme_options = {
                "SpCas9 (NGG)": "spcas9",
                "SpCas9-NG (NG)": "spcas9ng",
                "SaCas9 (NNGRRT)": "sacas9",
                "SpRY (NNN)": "spry",
                "AsCas12a (TTTV)": "ascas12a",
                "LbCas12a (TTTV)": "lbcas12a",
                "Mad7 (YTTN)": "mad7",
            }
            enzyme_choice = st.selectbox("Enzyme / Nuclease", list(enzyme_options.keys()), index=0, key="in_app_enzyme_choice")
            selected_enzyme = enzyme_options[enzyme_choice]
        with col_m:
            if selected_edit_type == "pm":
                mutation_val = st.text_input("Mutation / Codon Edit (e.g. R176C)", value=default_mutation, key="in_app_mutation")
                tag_val = ""
            elif selected_edit_type == "it":
                c_it1, c_it2 = st.columns(2)
                with c_it1:
                    tag_val = st.selectbox("Internal Tag", ["SPOT", "alphaBtx"], index=0, key="in_app_it_tag")
                with c_it2:
                    mutation_val = st.text_input("After AA (e.g. R154)", value="R154", key="in_app_it_pos")
            elif selected_edit_type in ("ct", "nt"):
                tag_presets = ["FLAG", "2xHA-only", "SD40-V5", "dTAG-V5", "dTAG-2xHA", "T2A-EGFP", "P2A-mCherry", "P2A-mScarlet"]
                tag_val = st.selectbox("Tag / Reporter", tag_presets, index=0, key="in_app_tag")
                mutation_val = ""
            else:
                st.caption("Knockout: ranks pairs by NMD/truncation protein loss & Lindel LoF allele share.")
                mutation_val = ""
                tag_val = ""

        with st.expander("🧬 Reference GenBank & Homology Options", expanded=False):
            bundled_refs = get_bundled_references()
            target_upper = (target_gene or "").strip().upper()
            has_bundled = target_upper in bundled_refs

            ref_mode = st.radio(
                "Reference Sequence Mode",
                [
                    "✨ Auto-Resolve (Bundled Reference or Ensembl MANE Select)",
                    "📁 Choose Bundled Benchmark Reference",
                    "📤 Upload Custom GenBank File (.gb)",
                    "📝 Paste GenBank Text",
                ],
                index=0,
                key="in_app_ref_mode",
            )

            custom_gb = ""
            if "Auto-Resolve" in ref_mode:
                if has_bundled:
                    st.caption(f"✓ Found verified bundled reference for **{target_upper}** (`{bundled_refs[target_upper].name}`) — instant offline design enabled.")
            elif "Choose Bundled" in ref_mode:
                ref_list = sorted(list(bundled_refs.keys()))
                sel_idx = ref_list.index(target_upper) if target_upper in ref_list else 0
                chosen = st.selectbox("Bundled Gene Target", ref_list, index=sel_idx if ref_list else 0, key="in_app_chosen_ref")
                if chosen in bundled_refs:
                    custom_gb = bundled_refs[chosen].read_text(encoding="utf-8")
                    st.caption(f"Loaded `{bundled_refs[chosen].name}` ({len(custom_gb)} characters).")
            elif "Upload" in ref_mode:
                uploaded = st.file_uploader("Upload GenBank file (.gb, .genbank)", type=["gb", "genbank", "txt"], key="in_app_upload_gb")
                if uploaded:
                    custom_gb = uploaded.getvalue().decode("utf-8", errors="ignore")
                    st.caption(f"Uploaded `{uploaded.name}` ({len(custom_gb)} characters).")
            elif "Paste" in ref_mode:
                custom_gb = st.text_area(
                    "Paste GenBank text",
                    value="",
                    height=120,
                    key="in_app_custom_gb",
                )

            col_arm, col_meth = st.columns(2)
            with col_arm:
                arm_len = st.number_input("Homology arm length (bp)", value=400, step=50, key="in_app_arm_len")
            with col_meth:
                delivery_method = st.selectbox("Delivery method", ["rnp", "plasmid", "mrna"], index=0, key="in_app_delivery")

        if st.button("🚀 Run In-App CRISPR Design", type="primary", use_container_width=True, key="in_app_run_btn"):
            with st.spinner(f"Designing CRISPR edit for {target_gene} ({edit_choice})..."):
                try:
                    res = run_in_silico_design(
                        gene=target_gene,
                        edit_type=selected_edit_type,
                        mutation=mutation_val,
                        tag=tag_val,
                        arm_length=arm_len,
                        genbank_text=custom_gb,
                        options={"deliveryMethod": delivery_method, "enzyme": selected_enzyme},
                    )
                    st.session_state["in_app_design_result"] = res
                    st.session_state["in_app_design_project"] = selected_project
                    st.success(f"Design completed successfully for {target_gene}!")
                except Exception as exc:
                    st.error(f"Design failed: {exc}")
                    st.info("💡 Tip: If Ensembl is unreachable, you can select '📁 Choose Bundled Benchmark Reference' or '📤 Upload Custom GenBank File' under Reference Options above.")

        # Render results if available
        result_data = st.session_state.get("in_app_design_result") if st.session_state.get("in_app_design_project") == selected_project else None
        if result_data:
            dr = result_data.get("designResult") or {}
            guides = dr.get("gs") or dr.get("guides") or []
            donors = dr.get("os") or dr.get("donors") or ([{"seq": dr["donor"]}] if "donor" in dr else [])
            primers = dr.get("ps") or dr.get("primers") or []
            svg = result_data.get("svg") or ""
            html_report = result_data.get("reportHtml") or ""

            st.markdown("---")
            enz_info = dr.get("enzyme") or (result_data.get("qcDesignSpec") or {}).get("design", {}).get("enzyme") or {}
            enz_badge = f" · {enz_info.get('name', 'SpCas9')} ({enz_info.get('pam_pattern', 'NGG')})" if enz_info else ""
            st.markdown(f"#### 🎯 Design Candidates for **{result_data.get('gene')}** ({result_data.get('editType').upper()}{enz_badge})")

            ko_assess = dr.get("koAssessment")
            if ko_assess:
                tier = (ko_assess.get("tier") or "robust").upper()
                outcomes = ko_assess.get("outcomes") or {}
                min_lof = outcomes.get("minLofPct")
                lof_text = f" | **Min Predicted LoF Alleles (Lindel):** `{min_lof}%` (~`{round((min_lof / 100.0) ** 2 * 100)}%` biallelic KO clones)" if min_lof is not None else ""
                st.info(f"🧬 **Knockout Protein-Loss Tier:** `{tier}`{lof_text}" + (f"\n\n{ko_assess.get('isoformNote')}" if ko_assess.get("isoformNote") else ""))

            tab_guides, tab_donor, tab_primers, tab_scheme = st.tabs(
                [f"Guides ({len(guides)})", "Donor Template", f"Primers ({len(primers)})", "Locus Schematic"]
            )

            with tab_guides:
                for idx, g in enumerate(guides):
                    spacer = g.get("sp") or g.get("spacer") or g.get("sequence") or ""
                    pam = g.get("pm") or g.get("pam") or enz_info.get("pam_pattern") or "NGG"
                    strand = g.get("str") or g.get("strand") or "+"
                    offset = g.get("d") if g.get("d") is not None else g.get("offsetFromEdit", "N/A")
                    ko_g = (g.get("ko") or {}).get("outcomes") or {}
                    ko_extra = f" | Lindel LoF: `{ko_g['lofPct']}%` (Biallelic: `{ko_g.get('biallelicPct')}%`)" if "lofPct" in ko_g else ""
                    st.markdown(f"**Guide {idx + 1}**: `{spacer}` &nbsp; (PAM: **{pam}** | Strand: `{strand}` | Offset: `{offset} bp`{ko_extra})")

            with tab_donor:
                if donors:
                    d0 = donors[0]
                    d_seq = d0.get("seq") or d0.get("sequence") or ""
                    d_fmt = d0.get("format") or d0.get("donor_type") or "ssODN/dsDNA"
                    st.markdown(f"**Format**: `{d_fmt}` &nbsp;|&nbsp; **Length**: `{len(d_seq)} bp`")
                    st.code(d_seq, language="text")
                else:
                    st.info("Dual-guide knockout does not require a donor template.")

            with tab_primers:
                if primers:
                    for p in primers:
                        p_name = p.get("name") or "Primer"
                        p_seq = p.get("seq") or p.get("sequence") or ""
                        p_tm = p.get("tm") or ""
                        st.markdown(f"**{p_name}**: `{p_seq}`" + (f" (Tm: {p_tm}°C)" if p_tm else ""))
                    if dr.get("amp"):
                        st.caption(f"Amplicon: {dr['amp']}")
                else:
                    st.info("Primers auto-calculated for PCR validation.")

            with tab_scheme:
                if svg:
                    st.components.v1.html(svg, height=220, scrolling=True)
                else:
                    st.caption("Schematic not generated.")

            st.markdown("---")
            # Action buttons: Apply to form + Save reports
            c_apply, c_save = st.columns([1.5, 2])

            with c_apply:
                if st.button("📋 Apply to Design Log Form", use_container_width=True, key="in_app_apply_btn"):
                    try:
                        spec = result_data.get("qcDesignSpec") or {}
                        parsed = parse_assured_design_export(spec)
                        apply_design_import_to_session(parsed, project_id=selected_project)
                        st.success("Autofilled Design Log form below!")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Could not apply to form: {err}")

            with c_save:
                if st.button("💾 Save Design Reports & Register", type="primary", use_container_width=True, key="in_app_save_btn"):
                    try:
                        saved_paths = save_design_reports(selected_project, result_data)
                        st.session_state["in_app_saved_reports"] = saved_paths
                        st.success(f"Saved reports: {saved_paths['html_file'].name} & {saved_paths['json_file'].name}")
                    except Exception as err:
                        st.error(f"Could not save reports: {err}")

            saved_reports = st.session_state.get("in_app_saved_reports")
            if saved_reports and saved_reports.get("html_file") and saved_reports["html_file"].exists():
                c_d_html, c_d_json = st.columns(2)
                with c_d_html:
                    with open(saved_reports["html_file"], "rb") as hf:
                        st.download_button(
                            "📥 Download Full HTML Report",
                            hf,
                            file_name=saved_reports["html_file"].name,
                            mime="text/html",
                            use_container_width=True,
                            key="in_app_download_html",
                        )
                with c_d_json:
                    with open(saved_reports["json_file"], "rb") as jf:
                        st.download_button(
                            "📥 Download QC Design JSON",
                            jf,
                            file_name=saved_reports["json_file"].name,
                            mime="application/json",
                            use_container_width=True,
                            key="in_app_download_json",
                        )