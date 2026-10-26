"""Streamlit UI components for the ASSURED-Biomni Co-Scientist Studio."""

from __future__ import annotations

import json
from typing import Any, Optional

import pandas as pd
import streamlit as st

from app.components.assured_tools import (
    apply_design_import_to_session,
    parse_assured_design_export,
)
from app.components.in_app_analyzer import apply_assessment_to_session
from assistant.biomni_agent import A1
from assistant.biomni_tools import list_environment_catalog


PRESET_MISSIONS = {
    "🧬 DIPK2A Knockout + Lindel LoF Ranking + gnomAD/UniProt": (
        "Design a constitutive knockout for DIPK2A, query UniProt domains and gnomAD loss-of-function "
        "constraint, rank all non-terminal coding exon guides using Lindel repair outcomes and NMD rules, "
        "compare CRISPR nucleases, and calculate clonal screening sample size."
    ),
    "🔬 APOE R176C Pathogenic SNP + ClinVar + Sanger Trace QC": (
        "Design an APOE R176C point mutation knock-in with silent PAM/seed blocking, check ClinVar "
        "pathogenic variants and UniProt protein architecture, compare CRISPR enzymes, and deconvolve "
        "the APOE Sanger sequencing (.ab1) traces to rank edited clones."
    ),
    "🏷️ TP53 C-Terminal FLAG Tag + Multi-Enzyme Comparison": (
        "Design a C-terminal FLAG tag knock-in for TP53, fetch UniProt domains and PubMed literature, "
        "compare all 7 CRISPR nucleases (SpCas9, SpCas9-NG, SaCas9, SpRY, enAsCas12a, LbCas12a, Mad7), "
        "and validate screening primers."
    ),
}


def render_biomni_coscientist(selected_project: Optional[str] = None) -> None:
    """Render the interactive Stanford Biomni-style Co-Scientist Workbench."""
    st.markdown(
        """
        <div style="padding: 16px 20px; background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%); color: white; border-radius: 10px; margin-bottom: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                <div>
                    <h3 style="margin: 0; color: #f8fafc; font-size: 1.3rem;">🧬 ASSURED-Biomni Autonomous Co-Scientist (A1 + E1)</h3>
                    <p style="margin: 4px 0 0 0; color: #cbd5e1; font-size: 0.9rem;">
                        Inspired by <a href="https://biomni.stanford.edu" target="_blank" style="color: #93c5fd; text-decoration: underline;">Stanford Biomni</a>:
                        Multi-step biomedical reasoning, live database retrieval (Ensembl, ClinVar, UniProt, gnomAD, OpenTargets, PubMed),
                        7-enzyme CRISPR design, Lindel LoF repair modeling, Sanger <code>.ab1</code> deconvolution, and sandboxed code execution.
                    </p>
                </div>
                <span style="background: rgba(255,255,255,0.14); padding: 4px 10px; border-radius: 999px; font-size: 0.78rem; font-weight: 600;">
                    12 Tools · 7 Nucleases · 6 Databases
                </span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("🧰 Inspect ASSURED-E1 Biomedical Action Environment (12 Tools & Databases)", expanded=False):
        catalog = list_environment_catalog()
        cat_df = pd.DataFrame(
            [
                {
                    "Tool Identifier": c["name"],
                    "Category": c["category"],
                    "Description": c["description"],
                    "Required Inputs": ", ".join(c["parameters"].get("required", [])) or "Optional",
                }
                for c in catalog
            ]
        )
        st.dataframe(cat_df, use_container_width=True, hide_index=True)
        st.caption(
            "Python SDK usage: `from assistant.biomni_agent import A1; agent = A1(); result = agent.go('...')` "
            "or CLI: `python scripts/assured_biomni_cli.py '...'`"
        )

    st.markdown("#### 1. Select a Co-Scientist Mission Preset or Enter a Natural-Language Objective")
    preset_cols = st.columns(len(PRESET_MISSIONS))
    for idx, (label, prompt_text) in enumerate(PRESET_MISSIONS.items()):
        with preset_cols[idx]:
            if st.button(label, use_container_width=True, key=f"biomni_preset_{idx}"):
                st.session_state["biomni_prompt_input"] = prompt_text

    default_prompt = st.session_state.get(
        "biomni_prompt_input",
        list(PRESET_MISSIONS.values())[0],
    )
    prompt = st.text_area(
        "Biomedical Goal / Experiment Prompt",
        value=default_prompt,
        height=95,
        key="biomni_prompt_textarea",
        help="Describe your target gene, edit type, clinical variant, enzyme comparison, or trace QC task.",
    )

    col_run, col_llm = st.columns([2.2, 1])
    with col_llm:
        llm_choice = st.selectbox(
            "Reasoning Backbone",
            ["auto (LLM + Deterministic Tool Engine)", "deterministic (Offline / Fast Tool Engine)", "openai (GPT-4o / mini)"],
            index=0,
            key="biomni_llm_choice",
        )
    with col_run:
        st.write("")
        run_clicked = st.button(
            "🚀 Launch ASSURED-Biomni Co-Scientist (`agent.go`)",
            type="primary",
            use_container_width=True,
            key="biomni_run_btn",
        )

    if run_clicked and prompt.strip():
        llm_mode = llm_choice.split()[0]
        with st.spinner("ASSURED-A1 decomposing task, querying biomedical APIs, and running CRISPR/QC engines..."):
            agent = A1(llm=llm_mode, project_id=selected_project)
            res = agent.go(prompt.strip(), project_id=selected_project)
            st.session_state["biomni_last_result"] = res

    res = st.session_state.get("biomni_last_result")
    if not res:
        return

    st.markdown("---")
    steps = res.get("steps") or []
    intent = res.get("intent") or {}

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Target Gene", intent.get("gene", "—"))
    m2.metric("Workflow / Edit", f"{intent.get('edit_type', 'ko').upper()} ({intent.get('enzyme', 'spcas9')})")
    m3.metric("Executed Tools", f"{len(steps)} steps")
    m4.metric("Total Execution Time", f"{res.get('total_duration_ms', 0)} ms")

    tab_report, tab_trace, tab_visuals, tab_sdk = st.tabs(
        [
            "📑 Co-Scientist Report",
            f"🔍 Step-by-Step Execution Trace ({len(steps)})",
            "🧬 Locus Schematic & Interactive HTML Report",
            "💻 Reproducible Python SDK / CLI Snippet",
        ]
    )

    with tab_report:
        st.markdown(res.get("reply", ""))

        # 1-Click Handoff into ASSUREDChain Session State
        design_out = res.get("design_output")
        qc_out = res.get("qc_output")
        act_c1, act_c2 = st.columns(2)
        if design_out:
            with act_c1:
                if st.button(
                    "📋 Apply Design to ASSUREDChain Stage 2 Form",
                    type="primary",
                    use_container_width=True,
                    key="biomni_apply_design_btn",
                ):
                    st.session_state["in_app_design_result"] = design_out
                    spec = design_out.get("qcDesignSpec") or {}
                    if spec:
                        parsed = parse_assured_design_export(spec)
                        apply_design_import_to_session(parsed)
                    st.success("Applied CRISPR design candidates & primers to ASSUREDChain Stage 2 session!")
        if qc_out:
            with act_c2:
                if st.button(
                    "🔬 Apply Trace QC Decision to Stage 4/5 Form",
                    use_container_width=True,
                    key="biomni_apply_qc_btn",
                ):
                    st.session_state["in_app_analyzer_output"] = qc_out
                    apply_assessment_to_session(qc_out)
                    st.success("Applied preferred clone QC metrics to ASSUREDChain session!")

    with tab_trace:
        for s in steps:
            badge = "✅" if s.get("status") == "completed" else "⚠️"
            with st.expander(
                f"{badge} Step {s['step']}: {s['title']} — `{s['tool']}` ({s['duration_ms']} ms)",
                expanded=(s["step"] <= 3),
            ):
                st.caption(f"**Rationale:** {s.get('rationale')}")
                st.markdown(f"**Arguments:** `{json.dumps(s.get('args', {}))}`")
                st.json(s.get("output", {}))

    with tab_visuals:
        design_out = res.get("design_output") or {}
        svg = design_out.get("svg")
        report_html = design_out.get("reportHtml")
        if svg:
            st.markdown("##### Vector Target Locus Schematic (SVG)")
            st.components.v1.html(svg, height=230, scrolling=True)
        if report_html:
            st.markdown("##### Full Authoritative ASSURED HTML Report")
            st.components.v1.html(report_html, height=560, scrolling=True)
        if not svg and not report_html:
            st.info("Run a design mission to preview the SVG schematic and full HTML report.")

    with tab_sdk:
        escaped_prompt = res.get("prompt", "").replace('"', '\\"')
        st.code(
            f'from assistant.biomni_agent import A1\n\n'
            f'# Initialize the ASSURED-Biomni Co-Scientist Agent\n'
            f'agent = A1(path="./data", llm="auto")\n\n'
            f'# Run autonomous multi-step biomedical workflow\n'
            f'result = agent.go("{escaped_prompt}")\n'
            f'print(result["reply"])\n',
            language="python",
        )
        st.code(
            f'python scripts/assured_biomni_cli.py "{escaped_prompt}"',
            language="bash",
        )
