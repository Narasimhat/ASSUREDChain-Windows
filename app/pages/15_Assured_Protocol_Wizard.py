import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import streamlit as st

from app.components.layout import init_page

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "data" / "wizard_templates"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class WizardStep:
    slug: str
    title: str
    description: str
    recommended_attachments: List[str]
    page_path: str


WIZARD_STEPS: List[WizardStep] = [
    WizardStep(
        slug="design",
        title="Design",
        description="Capture guides, donors, primers, and target edits for each construct.",
        recommended_attachments=["CRISPR design export", "Primer design PDF", "gRNA scoring sheet"],
        page_path="pages/1_Design_Log.py",
    ),
    WizardStep(
        slug="delivery",
        title="Delivery",
        description="Document electroporation parameters, cell counts, and delivery reagents.",
        recommended_attachments=["Neon export", "Microscopy images", "RNP prep worksheet"],
        page_path="pages/2_Delivery_Log.py",
    ),
    WizardStep(
        slug="assessment",
        title="Assessment",
        description="Log PCR and sequencing outcomes, including ICE/TIDE/DECODR summaries.",
        recommended_attachments=["ICE/TIDE report", "Gel image", "QC spreadsheet"],
        page_path="pages/4_Assessment_Log.py",
    ),
    WizardStep(
        slug="cloning",
        title="Cloning",
        description="Track isoCell/iOTA cloning checkpoints and evidence of clone expansion.",
        recommended_attachments=["IOTA checklist", "Plate images", "Clone tracker"],
        page_path="pages/5_IOTA_Cloning.py",
    ),
    WizardStep(
        slug="screening",
        title="Screening",
        description="Select positives, record call criteria, and collate Sanger/NGS confirmation.",
        recommended_attachments=["Plate map", "Sanger traces", "Screening summary"],
        page_path="pages/6_Screening_Log.py",
    ),
    WizardStep(
        slug="master_bank",
        title="Master Bank",
        description="Review release criteria, STR/SNP verification, and mycoplasma results.",
        recommended_attachments=["STR report", "Mycoplasma certificate", "COA draft"],
        page_path="pages/7_Master_Bank_Registry.py",
    ),
    WizardStep(
        slug="seed_bank",
        title="Seed Bank",
        description="Plan cryopreservation batches, DNA pellets, and downstream QC.",
        recommended_attachments=["Cryofreezing log", "Inventory export", "QC plan"],
        page_path="pages/8_Seed_Bank_Log.py",
    ),
    WizardStep(
        slug="chainproof",
        title="Chainproof",
        description="Anchor registry snapshots on-chain and collect JSON receipts.",
        recommended_attachments=["Chainproof JSON", "Tx hash log"],
        page_path="pages/3_Onchain_Ledger.py",
    ),
]


DEFAULT_TEMPLATES: Dict[str, Dict[str, Dict[str, object]]] = {
    "SNP Knock-in": {
        "design": {
            "objectives": "Design ssODN donor with silent PAM and two guides (primary + rescue).",
            "notes": "Benchling project: SORCS1_KI_v1",
            "attachments": ["CRISPR design export", "Primer design PDF"],
        },
        "delivery": {
            "objectives": "Electroporate H9 line with RNP + ssODN (Neon 1200V/30ms/1 pulse).",
            "notes": "Expect viability ~65%. Use Cas9 HiFi lot HFI-0925.",
            "attachments": ["Neon export"],
        },
        "assessment": {
            "objectives": "Run ICE on Sanger traces for 48h post electroporation samples.",
            "notes": "Flag clones with >30% SNP KI for cloning.",
            "attachments": ["ICE/TIDE report"],
        },
        "cloning": {
            "objectives": "Plate single cells in isoCell 96-well; capture plate map and viability.",
            "notes": "Freeze supernatant for mycoplasma backup.",
            "attachments": ["IOTA checklist", "Plate images"],
        },
        "screening": {
            "objectives": "Prioritize KI clones >80% allelic fraction with clean off-targets.",
            "notes": "Compare sequencing run SB2025_11.",
            "attachments": ["Sanger traces"],
        },
        "master_bank": {
            "objectives": "Confirm STR ID, mycoplasma negative, and release criteria met.",
            "notes": "COA draft owner: QA Team.",
            "attachments": ["STR report", "Mycoplasma certificate"],
        },
        "seed_bank": {
            "objectives": "Cryopreserve 12 vials per clone with DNA pellets logged.",
            "notes": "Store in LN2 Rack B, Box 12 with duplicate inventory.",
            "attachments": ["Cryofreezing log", "Inventory export"],
        },
        "chainproof": {
            "objectives": "Anchor master bank registry snapshot and CTS report hashes.",
            "notes": "Use Sepolia account qa-anchor-01.",
            "attachments": ["Chainproof JSON"],
        },
    },
    "Base Edit": {
        "design": {
            "objectives": "Select BE4max compatible guides and pegRNA window for base edit.",
            "notes": "Target APOE c.388T>C protective allele.",
            "attachments": ["CRISPR design export"],
        },
        "delivery": {
            "objectives": "Deliver ABE RNP via nucleofection with enhancer mix.",
            "notes": "Monitor viability at 24h; add Rock inhibitor to recovery media.",
            "attachments": ["Nucleofection log"],
        },
        "assessment": {
            "objectives": "Run NGS amplicon sequencing with EditR quantification.",
            "notes": "Trigger follow-up if bystander edits >5%.",
            "attachments": ["NGS summary"],
        },
        "cloning": {
            "objectives": "Isolate high-edit clones and capture morphology time series.",
            "notes": "Base edit clones remain sensitive to freeze-thaw, extend recovery.",
            "attachments": ["Imaging series"],
        },
        "screening": {
            "objectives": "Validate off-target panel and transcript expression.",
            "notes": "Coordinate with transcriptomics core for RNA-Seq subset.",
            "attachments": ["Off-target panel report"],
        },
        "master_bank": {
            "objectives": "Complete extended QC: karyotype, SNP array, pluripotency markers.",
            "notes": "Add flow cytometry results to attachments.",
            "attachments": ["Flow cytometry plots"],
        },
        "seed_bank": {
            "objectives": "Seed bank 24 vials with duplicate DNA pellets for long-term storage.",
            "notes": "Schedule re-viability testing after 3 months.",
            "attachments": ["Re-viability schedule"],
        },
        "chainproof": {
            "objectives": "Anchor GenTAufzV work record and final QA approvals.",
            "notes": "Batch anchor with QA multi-sig wallet.",
            "attachments": ["Chainproof JSON", "QA approval memo"],
        },
    },
}


def _reset_wizard_state(template_name: str) -> None:
    template = DEFAULT_TEMPLATES.get(template_name, {})
    step_state: Dict[str, Dict[str, object]] = {}
    for step in WIZARD_STEPS:
        defaults = template.get(step.slug, {})
        step_state[step.slug] = {
            "objectives": defaults.get("objectives", step.description),
            "notes": defaults.get("notes", ""),
            "attachments": defaults.get("attachments", step.recommended_attachments[:]),
            "ready": False,
        }
    st.session_state["protocol_wizard"] = {
        "template": template_name,
        "step_index": 0,
        "steps": step_state,
        "project_id": st.session_state.get("wizard_project_id", ""),
        "owner": st.session_state.get("wizard_owner", ""),
    }


def _ensure_state() -> None:
    if "wizard_project_id" not in st.session_state:
        st.session_state["wizard_project_id"] = ""
    if "wizard_owner" not in st.session_state:
        st.session_state["wizard_owner"] = ""
    if "protocol_wizard" not in st.session_state:
        _reset_wizard_state(next(iter(DEFAULT_TEMPLATES)))


def _template_changed() -> None:
    template_name = st.session_state.get("wizard_template_select") or next(iter(DEFAULT_TEMPLATES))
    _reset_wizard_state(template_name)


def _update_meta() -> None:
    state = st.session_state.get("protocol_wizard")
    if not state:
        return
    state["project_id"] = st.session_state.get("wizard_project_id", "")
    state["owner"] = st.session_state.get("wizard_owner", "")


def _export_payload() -> Dict[str, object]:
    state = st.session_state["protocol_wizard"]
    payload = {
        "template": state["template"],
        "project_id": state.get("project_id"),
        "owner": state.get("owner"),
        "timestamp": int(time.time()),
        "steps": state["steps"],
    }
    return payload


def _save_to_disk(payload: Dict[str, object]) -> Path:
    project_part = payload.get("project_id") or "protocol"
    timestamp = payload["timestamp"]
    file_path = TEMPLATE_DIR / f"{project_part}_{timestamp}.json"
    file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return file_path


def main() -> None:
    init_page("ASSURED Protocol Wizard")
    _ensure_state()

    st.title("ASSURED Protocol Wizard")
    st.caption("Guide lab staff through Design -> QC with templates tuned to each edit program.")
    protocol_path = ROOT / "assets" / "ASSURED_CRISPR_PROTOCOL.pdf"
    proto_local, proto_remote = st.columns([1, 1])
    with proto_local:
        try:
            with protocol_path.open("rb") as handle:
                st.download_button(
                    "Download ASSURED protocol",
                    handle,
                    file_name=protocol_path.name,
                    mime="application/pdf",
                    use_container_width=True,
                )
        except FileNotFoundError:
            st.caption("Protocol PDF not found in assets/.")
    with proto_remote:
        st.link_button(
            "Open official protocol",
            "https://star-protocols.cell.com/protocols/2872",
            use_container_width=True,
        )

    state = st.session_state["protocol_wizard"]

    with st.container():
        left, right = st.columns([3, 2])
        with left:
            st.text_input(
                "Project ID",
                key="wizard_project_id",
                value=state.get("project_id", ""),
                placeholder="e.g., SORCS1_KI_v1",
                on_change=_update_meta,
            )
            st.text_input(
                "Protocol owner",
                key="wizard_owner",
                value=state.get("owner", ""),
                placeholder="e.g., Narasimha Telugu",
                on_change=_update_meta,
            )
        with right:
            st.selectbox(
                "Edit program template",
                options=list(DEFAULT_TEMPLATES.keys()),
                index=list(DEFAULT_TEMPLATES.keys()).index(state["template"]),
                key="wizard_template_select",
                on_change=_template_changed,
            )
            st.write("Switch templates to preload step objectives, attachments, and guardrails.")

    step_index = state["step_index"]
    current_step = WIZARD_STEPS[step_index]
    step_state = state["steps"][current_step.slug]

    progress_value = (step_index + 1) / len(WIZARD_STEPS)
    st.progress(progress_value, text=f"{current_step.title} ({step_index + 1} of {len(WIZARD_STEPS)})")

    with st.form(f"form_{current_step.slug}", clear_on_submit=False):
        st.subheader(current_step.title)
        st.write(current_step.description)

        objectives = st.text_area(
            "Objectives for this step",
            value=step_state.get("objectives", current_step.description),
            height=120,
        )
        notes = st.text_area(
            "Execution notes",
            value=step_state.get("notes", ""),
            placeholder="Lab-specific reminders, responsible techs, timing windows...",
            height=140,
        )
        attachments = st.multiselect(
            "Planned attachments",
            options=sorted(
                set(current_step.recommended_attachments + step_state.get("attachments", []))
            ),
            default=step_state.get("attachments", current_step.recommended_attachments),
        )
        ready = st.checkbox("Mark this step ready for execution", value=step_state.get("ready", False))

        st.page_link(current_step.page_path, label=f"Open {current_step.title} page")

        submitted = st.form_submit_button("Save step")
        if submitted:
            step_state["objectives"] = objectives
            step_state["notes"] = notes
            step_state["attachments"] = attachments
            step_state["ready"] = ready
            st.session_state["protocol_wizard"]["steps"][current_step.slug] = step_state
            st.success(f"{current_step.title} updated.")

    nav_cols = st.columns([1, 1, 2])
    with nav_cols[0]:
        if st.button("Previous", disabled=step_index == 0):
            st.session_state["protocol_wizard"]["step_index"] = step_index - 1
            st.rerun()
    with nav_cols[1]:
        if st.button("Next", disabled=step_index >= len(WIZARD_STEPS) - 1):
            st.session_state["protocol_wizard"]["step_index"] = step_index + 1
            st.rerun()
    with nav_cols[2]:
        if st.button("Reset wizard"):
            _reset_wizard_state(state["template"])
            st.rerun()

    st.divider()

    payload = _export_payload()
    json_blob = json.dumps(payload, indent=2)
    file_name = f"{payload.get('project_id') or 'protocol'}_{payload['timestamp']}.json"

    st.subheader("Export plan")
    st.write("Download the protocol plan or persist it in the repository for reuse.")
    st.download_button(
        "Download JSON plan",
        json_blob,
        file_name=file_name,
        mime="application/json",
    )

    if st.button("Save plan to data/wizard_templates"):
        saved_path = _save_to_disk(payload)
        st.success(f"Saved plan to {saved_path.relative_to(ROOT)}")

    with st.expander("Step overview"):
        overview = {
            step.title: {
                "ready": state["steps"][step.slug]["ready"],
                "attachments": state["steps"][step.slug]["attachments"],
            }
            for step in WIZARD_STEPS
        }
        st.json(overview)


if __name__ == "__main__":
    main()
