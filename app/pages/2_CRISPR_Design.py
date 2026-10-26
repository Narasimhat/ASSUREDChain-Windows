"""Stage 2: CRISPR Design & Reagent Specifications - FDA Cell Line Delivery Record."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.components.design_enzymes import ENZYMES, enzyme_metadata, build_locked_reagent_spec
from app.components.file_utils import build_step_filename, save_uploaded_file
from app.components.in_app_designer import render_in_app_designer
from app.components.layout import init_page
from app.components.lifecycle_header import render_lifecycle_header
from app.components.project_state import (
    append_audit,
    load_project_meta,
    project_subdir,
    register_chain_tx,
    register_file,
    update_project_meta,
    use_project,
)
from app.components.web3_client import send_log_tx


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    init_page("Stage 2 - CRISPR Design & Reagents")
    selected_project = use_project("Project")

    render_lifecycle_header(
        current_stage=2,
        selected_project=selected_project,
        subtitle="Stage 2: Design and verify enzyme-specific guides, HDR donors and PCR/Sanger primer pairs.",
    )

    if not selected_project:
        st.info("👈 Please select or create a project in the sidebar to begin.")
        st.stop()

    meta = load_project_meta(selected_project)
    snapshots_dir = project_subdir(selected_project, "snapshots", "design")
    reports_dir = project_subdir(selected_project, "reports", "design")

    # Hero Component: In-App CRISPR Design Studio
    render_in_app_designer(selected_project)

    st.markdown("---")
    st.subheader("🔒 Approve & Lock Reagent Specification for Delivery")

    # Form to lock approved design specification into electronic batch record
    design_import = (st.session_state.get("assured_design_import") or {}) if st.session_state.get("assured_design_project") == selected_project else {}
    seed = design_import or meta.get("compliance", {})
    guides = seed.get("selected_guides", seed.get("guides")) or []
    donors = seed.get("donors") or []
    primers = seed.get("primer_pairs", seed.get("primers")) or []
    try:
        seed_enzyme = enzyme_metadata(seed.get("enzyme_id") or seed.get("cas_variant") or "spcas9")
    except ValueError as error:
        st.error(f"{error} Re-import a design with the correct enzyme before approval.")
        return
    token = hashlib.sha256(json.dumps(seed, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    options = [value[0] for value in ENZYMES.values()] + ["SpCas9 High-Fidelity (HiFi)", "SpCas9 Wild-Type"]
    default_variant = seed.get("cas_variant") if seed.get("cas_variant") in options else seed_enzyme["cas_variant"]
    for warning in design_import.get("import_warnings", []):
        st.warning(warning)
    col_cas, col_pam, col_vendor = st.columns(3)
    with col_cas:
        cas_variant = st.selectbox("Nuclease", options, index=options.index(default_variant),
                                   disabled=bool(design_import) and not design_import.get("enzyme_metadata_assumed"), key=f"lock_enzyme_{selected_project}_{token}")
    selected_enzyme = enzyme_metadata(cas_variant)
    pam_rule = selected_enzyme["pam_rule"]
    with col_pam:
        st.text_input("PAM Requirement", value=pam_rule, disabled=True,
                      key=f"lock_pam_{selected_project}_{token}_{selected_enzyme['enzyme_id']}")
    with col_vendor:
        reagent_vendor = st.selectbox("Synthesis Vendor", ["IDT (Alt-R RNP & Megamer)", "Synthego (CleanCap)", "ThermoFisher"], index=0)
    needs_confirmation = bool(design_import.get("enzyme_metadata_assumed")) or bool(not design_import and guides and not seed.get("enzyme_id") and not seed.get("cas_variant"))

    with st.form("design_lock_form"):
        col_summary1, col_summary2, col_summary3 = st.columns(3)
        with col_summary1:
            st.metric("Verified Guides", len(guides))
        with col_summary2:
            st.metric("HDR Donor Templates", len(donors))
        with col_summary3:
            st.metric("PCR/Sanger Primer Pairs", len(primers))
        legacy_confirmed = st.checkbox("I verified the enzyme and PAM for these previously unlabelled guide records.",
                                        value=False, key=f"lock_confirm_{selected_project}_{token}") if needs_confirmation else False

        design_signoff = st.text_input("Design Qualified By (Scientist / Lead)", value=meta.get("owner") or "Narasimha Telugu")
        design_notes = st.text_area(
            "Specification Notes / Lot Information",
            value=meta.get("design_decision") or "Review the design report, enzyme identity, reagent compatibility and release requirements.",
            height=60,
        )

        col_b1, col_b2 = st.columns([1, 1])
        with col_b1:
            lock_btn = st.form_submit_button("💾 Lock Reagent Spec & Advance to Stage 3", type="primary")
        with col_b2:
            anchor_chain = st.checkbox("Anchor Reagent Hash on Sepolia Blockchain", value=True)

    if lock_btn:
        timestamp = int(time.time())
        try:
            design_payload = build_locked_reagent_spec(
                project_id=selected_project, cas_variant=cas_variant, pam_rule=pam_rule,
                guides=guides, donors=donors, primers=primers, vendor=reagent_vendor,
                signoff=design_signoff, notes=design_notes, timestamp=timestamp,
                imported=design_import or None, legacy_confirmed=legacy_confirmed,
                requires_confirmation=needs_confirmation,
            )
        except ValueError as error:
            st.error(str(error))
            return

        spec_json = json.dumps(design_payload, indent=2)
        spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
        spec_file = snapshots_dir / f"{selected_project}_design_locked_{timestamp}.json"
        spec_file.write_text(spec_json, encoding="utf-8")

        register_file(
            selected_project,
            "snapshots",
            spec_file,
            sha256=spec_hash,
            size=spec_file.stat().st_size,
            mime="application/json",
            context={"step": "design", "stage": 2},
        )

        update_project_meta(
            selected_project,
            {
                "design_locked_at": timestamp,
                "design_hash": spec_hash,
                "active_stage": 3,
                "compliance": {
                    **{key: design_payload.get(key) for key in ("enzyme_id", "cas_variant", "pam_rule", "pam_side", "enzyme_config_version", "enzyme_metadata_assumed", "enzyme_metadata_confirmed")},
                    "guides": design_payload["guides"],
                    "donors": donors,
                    "primers": primers,
                },
            },
        )

        append_audit(
            selected_project,
            "design_locked",
            details={"step": "design", "hash": spec_hash, "signoff": design_signoff},
        )

        st.success(f"Stage 2 Complete! Design locked with SHA-256 `{spec_hash[:12]}...`. Ready for Stage 3 Transfection.")

        if anchor_chain:
            try:
                tx_hash = send_log_tx(selected_project, spec_hash, step="Design")
                if tx_hash:
                    register_chain_tx(selected_project, {"step": "Design", "tx_hash": tx_hash, "hash": spec_hash})
                    st.info(f"Anchored on Sepolia: `{tx_hash}`")
            except Exception as chain_err:
                st.warning(f"Blockchain anchoring notice: {chain_err}")

        st.page_link("pages/3_Transfection_Bulk_QC.py", label="👉 Open Stage 3: Transfection & Bulk QC", icon="⚡")


if __name__ == "__main__":
    main()