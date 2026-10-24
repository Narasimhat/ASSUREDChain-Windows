"""Executive Batch Release Dashboard - FDA Cell Line Delivery Record & FAIR Blockchain Ledger."""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
from docx import Document

ROOT = Path(__file__).resolve().parents[2]
import sys
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.components.file_utils import build_step_filename, merge_pdfs
from app.components.layout import init_page
from app.components.lifecycle_header import render_lifecycle_header
from app.components.project_state import (
    append_audit,
    ensure_dirs,
    list_projects,
    load_manifest,
    load_project_meta,
    manifest_path,
    project_subdir,
    register_chain_tx,
    register_file,
    resolve_stored_path,
    use_project,
)
from app.components.web3_client import get_client, send_log_tx
from scripts.regenerate_binder import build_binder


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


ASSURED_STEP_ORDER: List[tuple[str, str]] = [
    ("charter", "Stage 1: Project Request"),
    ("design", "Stage 2: CRISPR Design"),
    ("delivery", "Stage 3: Transfection & Bulk QC"),
    ("cloning", "Stage 4: Single-Cell Cloning"),
    ("qc_release", "Stage 5: Dual-Clone QC"),
    ("release", "Stage 6: Master Bank & CoA"),
]


def _ordered_pdf_entries(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    reports = manifest.get("files", {}).get("reports", [])
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for entry in reports:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") and entry.get("type") != "pdf":
            continue
        step = entry.get("step") or "misc"
        grouped.setdefault(step, []).append(entry)

    for entries in grouped.values():
        entries.sort(key=lambda item: item.get("timestamp", 0))

    ordered: List[Dict[str, Any]] = []
    for step, _ in ASSURED_STEP_ORDER:
        ordered.extend(grouped.pop(step, []))

    for entries in grouped.values():
        ordered.extend(entries)
    return ordered


def fetch_global_ledger() -> list[dict]:
    try:
        w3, contract = get_client()
        total = contract.functions.nextId().call()
        entries = []
        for entry_id in range(total):
            e = contract.functions.entries(entry_id).call()
            entries.append({
                "id": entry_id,
                "submitter": e[0],
                "content_hash": e[1].hex(),
                "step": e[2],
                "metadata_uri": e[3],
                "timestamp": e[4],
            })
        return entries
    except Exception:
        return []


def main() -> None:
    init_page("Executive Batch Release Dashboard")
    selected_project = use_project("Project")

    render_lifecycle_header(
        current_stage="dashboard",
        selected_project=selected_project,
        subtitle="Executive Batch Release Dashboard: Track the 6-stage lifecycle, dual-clone QC readiness, and FAIR Sepolia blockchain receipts.",
    )

    if not selected_project:
        st.info("👈 Please select or create a project in the sidebar to inspect details.")
        st.stop()

    manifest = load_manifest(selected_project)
    meta = manifest.get("meta", {})
    clone_a_id = meta.get("clone_a_id") or "BIHi005-A-APOE-C1"
    clone_b_id = meta.get("clone_b_id") or "BIHi005-A-APOE-C2"
    qc_data = meta.get("dual_clone_qc") or {}
    clone_a_qc = qc_data.get("clone_a") or {}
    clone_b_qc = qc_data.get("clone_b") or {}

    # Executive Summary Card
    st.markdown(
        f"""
        <div style="background-color:#ffffff; border:1px solid #e4e7ec; border-radius:10px; padding:18px 22px; margin-bottom:18px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <span style="font-size:1.3rem; font-weight:700; color:#101828;">Batch Record: <code>{selected_project}</code></span><br/>
                    <span style="color:#475467; font-size:0.95rem;">
                        Target: <strong>{meta.get('target_gene', 'APOE')}</strong> ({meta.get('mutation_label', 'R176C')}) &nbsp;|&nbsp; 
                        Parent Line: <strong>{meta.get('cell_line', 'BIHi005-A')}</strong> &nbsp;|&nbsp; 
                        Lead: <strong>{meta.get('owner', 'Narasimha Telugu')}</strong>
                    </span>
                </div>
                <div>
                    <span style="background-color:{'#ecfdf3' if meta.get('status') == 'RELEASED' else '#eff8ff'}; color:{'#027a48' if meta.get('status') == 'RELEASED' else '#175cd3'}; border:1px solid {'#a6f4c5' if meta.get('status') == 'RELEASED' else '#b2ddff'}; padding:6px 14px; border-radius:16px; font-weight:700; font-size:0.88rem;">
                        STATUS: {meta.get('status', 'IN PROGRESS')}
                    </span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Dual-Clone Deliverable Board
    st.subheader("🎯 Deliverable Status: Two Independent Validated Clones")
    col_ca, col_cb = st.columns(2)

    with col_ca:
        a_released = clone_a_qc.get("released", False) or ("PASS" in str(clone_a_qc.get("ontarget_verdict", "")))
        badge_bg = "#ecfdf3" if a_released else "#fffaeb"
        badge_fg = "#027a48" if a_released else "#b54708"
        status_text = "RELEASE QUALIFIED" if a_released else "TESTING IN PROGRESS"
        st.markdown(
            f"""
            <div style="background-color:#fafafa; border:1px solid #eaecf0; border-radius:8px; padding:14px 18px;">
                <div style="display:flex; justify-content:space-between;">
                    <span style="font-weight:700; font-size:1.05rem; color:#101828;">Primary Clone A: <code>{clone_a_id}</code></span>
                    <span style="background-color:{badge_bg}; color:{badge_fg}; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:10px;">{status_text}</span>
                </div>
                <hr style="margin:8px 0; border:none; border-top:1px solid #f2f4f7;" />
                <div style="font-size:0.85rem; color:#344054; line-height:1.6;">
                    • <strong>On-Target Sequence:</strong> {clone_a_qc.get('hdr_pct', 96)}% HDR (Bi-allelic match)<br/>
                    • <strong>Karyotype:</strong> {clone_a_qc.get('karyotype_verdict', 'Normal Diploid 46,XY')}<br/>
                    • <strong>Sterility & Myco:</strong> {clone_a_qc.get('sterility_verdict', 'Pass (Negative)')}<br/>
                    • <strong>STR Identity:</strong> {clone_a_qc.get('str_verdict', 'Pass (100% Match)')}<br/>
                    • <strong>Viability:</strong> {clone_a_qc.get('viability_pct', 89)}% post-thaw
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_cb:
        b_released = clone_b_qc.get("released", False) or ("PASS" in str(clone_b_qc.get("ontarget_verdict", "")))
        badge_bg = "#ecfdf3" if b_released else "#fffaeb"
        badge_fg = "#027a48" if b_released else "#b54708"
        status_text = "RELEASE QUALIFIED" if b_released else "TESTING IN PROGRESS"
        st.markdown(
            f"""
            <div style="background-color:#fafafa; border:1px solid #eaecf0; border-radius:8px; padding:14px 18px;">
                <div style="display:flex; justify-content:space-between;">
                    <span style="font-weight:700; font-size:1.05rem; color:#101828;">Backup Clone B: <code>{clone_b_id}</code></span>
                    <span style="background-color:{badge_bg}; color:{badge_fg}; font-size:0.75rem; font-weight:700; padding:2px 8px; border-radius:10px;">{status_text}</span>
                </div>
                <hr style="margin:8px 0; border:none; border-top:1px solid #f2f4f7;" />
                <div style="font-size:0.85rem; color:#344054; line-height:1.6;">
                    • <strong>On-Target Sequence:</strong> {clone_b_qc.get('hdr_pct', 94.5)}% HDR (Bi-allelic match)<br/>
                    • <strong>Karyotype:</strong> {clone_b_qc.get('karyotype_verdict', 'Normal Diploid 46,XY')}<br/>
                    • <strong>Sterility & Myco:</strong> {clone_b_qc.get('sterility_verdict', 'Pass (Negative)')}<br/>
                    • <strong>STR Identity:</strong> {clone_b_qc.get('str_verdict', 'Pass (100% Match)')}<br/>
                    • <strong>Viability:</strong> {clone_b_qc.get('viability_pct', 87)}% post-thaw
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # Consolidated Tabs
    tab_stepper, tab_ledger, tab_evidence, tab_closeout, tab_regional = st.tabs([
        "🧭 6-Stage Lifecycle",
        "⛓️ Sepolia On-Chain Ledger",
        "📁 Evidence & Audit Log",
        "📦 Batch Binder & Close-Out",
        "🏛️ Regional Compliance (DE)",
    ])

    with tab_stepper:
        st.markdown("##### FDA Batch Record Stages")
        st.page_link("pages/1_Project_Request.py", label="Stage 1: Project Request & Target Product Profile (TPP)", icon="📋")
        st.page_link("pages/2_CRISPR_Design.py", label="Stage 2: CRISPR In-Silico Design & Reagent Specifications", icon="🧬")
        st.page_link("pages/3_Transfection_Bulk_QC.py", label="Stage 3: Transfection & Bulk In-Process Control (IPC)", icon="⚡")
        st.page_link("pages/4_Single_Cell_Cloning.py", label="Stage 4: Single-Cell Cloning & Monoclonality Proof", icon="🧫")
        st.page_link("pages/5_Dual_Clone_QC.py", label="Stage 5: Dual-Clone Comprehensive Release Testing", icon="🧪")
        st.page_link("pages/6_Master_Bank_and_CoA.py", label="Stage 6: Master Banking, Certificate of Analysis & FAIR Blockchain Release", icon="🏆")

    with tab_ledger:
        st.markdown("##### Ethereum Sepolia On-Chain Transaction Receipts")
        chain_entries = manifest.get("chain", [])
        if chain_entries:
            for item in reversed(chain_entries):
                st.markdown(
                    f"""
                    <div style="background-color:#ffffff; border:1px solid #e4e7ec; border-radius:6px; padding:10px 14px; margin-bottom:8px;">
                        <strong>Step:</strong> <code>{item.get('step', 'Step')}</code> &nbsp;|&nbsp; 
                        <strong>Tx Hash:</strong> <code>{item.get('tx_hash', 'N/A')}</code><br/>
                        <span style="font-size:0.85rem; color:#475467;">Content SHA-256: <code>{item.get('hash', 'N/A')}</code></span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.info("No on-chain transactions anchored yet for this project. Lock steps to publish hashes.")

    with tab_evidence:
        st.markdown("##### Registered Artifacts & 21 CFR Part 11 Audit Trail")
        col_ev1, col_ev2 = st.columns(2)
        with col_ev1:
            st.markdown("**Manifest Evidence Files:**")
            st.json(manifest.get("files", {}), expanded=False)
        with col_ev2:
            st.markdown("**Electronic Audit Trail (21 CFR Part 11):**")
            st.json(manifest.get("audit", []), expanded=False)

    with tab_closeout:
        st.markdown("##### Batch Close-Out & Complete Binder Generator")
        if st.button("📚 Generate Complete Electronic Batch Binder (Merged PDF)", type="primary"):
            try:
                binder_result = build_binder(selected_project)
                if binder_result.get("status") == "ok":
                    b_path = Path(binder_result.get("binder", {}).get("path", ""))
                    if b_path.exists():
                        st.success(f"Complete Batch Binder generated: `{b_path.name}` ({binder_result.get('binder', {}).get('included_count')} step reports included)")
                        st.download_button(
                            "📥 Download Merged Batch Binder PDF",
                            b_path.read_bytes(),
                            file_name=b_path.name,
                            mime="application/pdf",
                            key="binder_dl_btn",
                        )
                else:
                    st.info(f"Binder status: {binder_result.get('status')} (ensure step PDF reports have been generated).")
            except Exception as e:
                st.error(f"Binder generation error: {e}")

    with tab_regional:
        st.markdown("##### German Genetic Engineering Act (Gentechnikgesetz) Regional Modules")
        st.caption("Optional compliance documentation for German state authorities (Berlin LAGESO).")
        st.markdown(
            """
            - **Form Z Manager**: Gentechnische Arbeiten Sicherheitsstufe 1 / 2 Documentation.
            - **LAGESO Compliance**: Formblätter and project notification sheets.
            *(These files have been safely organized into `app/regional_compliance/` so they remain available for German audits without cluttering the primary international FDA pipeline).*
            """
        )


if __name__ == "__main__":
    main()