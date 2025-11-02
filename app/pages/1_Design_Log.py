# --- make 'app' importable ---
import sys
import os
import json
import time
import hashlib
from pathlib import Path
from typing import Optional, Literal

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
# --------------------------------

import streamlit as st
from pydantic import (
    BaseModel,
    Field,
    FieldValidationInfo,
    ValidationError,
    field_validator,
)

from app.components.file_utils import build_step_filename, preview_file, save_uploaded_file, snapshot_to_pdf
from app.components.layout import init_page
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

FALLBACK_DATA_DIR = ROOT / "data" / "design_logs_v2"
FALLBACK_ATTACHMENT_DIR = FALLBACK_DATA_DIR / "attachments"
FALLBACK_DATA_DIR.mkdir(parents=True, exist_ok=True)
FALLBACK_ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)


# ---------- Models ----------
class Guide(BaseModel):
    id: str = Field(..., description="e.g., g1")
    sequence: str = Field(..., description="20nt guide (no PAM for SpCas9; per enzyme rules)")
    pam: str = Field(..., description="e.g., NGG / TTTV / NRN / enzyme-specific")
    strand: Optional[Literal["+", "-"]] = None
    genomic_locus: Optional[str] = None
    distance_to_edit_bp: Optional[int] = None
    on_target_algo: Optional[Literal["CFD", "MIT", "CCTop", "Other"]] = None
    on_target_score: Optional[float] = None
    off_target_algo: Optional[Literal["CFD", "MIT", "CCTop", "Other"]] = None
    off_target_score: Optional[float] = None
    notes: Optional[str] = None

    @field_validator("sequence")
    @classmethod
    def validate_sequence(cls, value: str) -> str:
        seq = (value or "").strip().upper()
        if not seq:
            raise ValueError("Guide sequence is required.")
        if not 19 <= len(seq) <= 24:
            raise ValueError(f"Guide sequence must be 19–24 nt, got {len(seq)} nt.")
        allowed = {"A", "C", "G", "T", "U", "N"}
        invalid = [base for base in seq if base not in allowed]
        if invalid:
            raise ValueError(f"Guide contains invalid bases: {', '.join(sorted(set(invalid)))}")
        return seq

    @field_validator("pam")
    @classmethod
    def normalize_pam(cls, value: str) -> str:
        return (value or "").strip().upper()


class PrimerPair(BaseModel):
    name: str
    forward: str
    reverse: str
    expected_amplicon_bp: Optional[int] = None

    @field_validator("forward", "reverse")
    @classmethod
    def validate_primer(cls, value: str, info: FieldValidationInfo) -> str:
        seq = (value or "").strip().upper()
        if not seq:
            raise ValueError(f"{info.field_name.capitalize()} primer sequence is required.")
        if len(seq) < 15:
            raise ValueError(f"{info.field_name.capitalize()} primer should be at least 15 nt long.")
        return seq


class Donor(BaseModel):
    donor_type: Optional[Literal["ssODN", "dsDNA", "plasmid", "none"]] = "ssODN"
    sequence: Optional[str] = None
    length_nt: Optional[int] = None
    asymmetry: Optional[str] = None
    strand: Optional[str] = None
    introduces_silent_pam_or_seed_mut: Optional[bool] = None
    hdr_notes: Optional[str] = None


class MutationMeta(BaseModel):
    gene: str
    transcript: Optional[str] = None
    genome_assembly: Literal["GRCh38", "GRCh37", "Other"] = "GRCh38"
    edit_intent: Literal["KO", "SNP-KI", "BaseEdit", "PrimeEdit"] = "KO"
    hgvs_c: Optional[str] = None
    hgvs_p: Optional[str] = None
    region_context: Optional[str] = None
    base_editor_window: Optional[str] = None
    pegRNA_notes: Optional[str] = None


class DesignSnapshotV2(BaseModel):
    project_id: str
    author: str
    timestamp_unix: int

    design_platform: Literal[
        "CRISPOR", "CHOPCHOP", "Benchling", "RGEN (Cas-Designer)", "Upload/CSV", "Other"
    ] = "CRISPOR"
    cas_variant: str = Field(..., description="e.g., SpCas9, SpCas9-HF1, LbCas12a, hfCas12Max, etc.")
    pam_rule: str = Field(..., description="e.g., NGG, TTTV, NRN, etc.")
    design_source_url: Optional[str] = None

    mutation: MutationMeta

    selected_guides: list[Guide]
    additional_guides: list[Guide] = []
    primer_pairs: list[PrimerPair] = []
    donor: Optional[Donor] = None

    off_target_review: Optional[str] = None
    design_decision: Optional[str] = None
    attachments: dict[str, str] = {}


# ---------- Helpers ----------
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------- Readiness checks ----------
def evaluate_design_readiness(payload: dict) -> dict:
    issues: list[str] = []
    warnings: list[str] = []

    guides = payload.get("selected_guides") or []
    if not guides:
        issues.append("At least one guide must be recorded.")
    else:
        for guide in guides:
            seq = (guide.get("sequence") or "").replace(" ", "").upper()
            gid = guide.get("id") or "guide"
            if not 19 <= len(seq) <= 24:
                issues.append(f"{gid}: guide length {len(seq)} nt (expected 19–24 nt).")
            invalid = [b for b in seq if b not in {"A", "C", "G", "T", "U", "N"}]
            if invalid:
                issues.append(f"{gid}: guide contains invalid bases {sorted(set(invalid))}.")
            pam = (guide.get("pam") or "").strip()
            if not pam:
                warnings.append(f"{gid}: PAM not specified.")

    primers = payload.get("primer_pairs") or []
    if not primers:
        issues.append("Enter at least one primer pair.")
    else:
        for primer in primers:
            name = primer.get("name") or "Primer pair"
            fwd = primer.get("forward", "").strip()
            rev = primer.get("reverse", "").strip()
            if not fwd or not rev:
                issues.append(f"{name}: forward and reverse sequences are required.")

    mutation = payload.get("mutation", {})
    edit_intent = mutation.get("edit_intent")
    donor = payload.get("donor")
    if edit_intent in {"SNP-KI", "BaseEdit", "PrimeEdit"}:
        if not donor or not donor.get("sequence"):
            issues.append(f"{edit_intent}: Donor sequence is required for this intent.")
        else:
            seq = (donor.get("sequence") or "").replace(" ", "").upper()
            if len(seq) < 60:
                warnings.append("Donor sequence is shorter than 60 nt; confirm protocol alignment.")

    attachments_map = payload.get("attachments") or {}
    if not attachments_map:
        warnings.append("No design attachments uploaded (CRISPOR export, Benchling PDF, etc.).")

    return {"ready": not issues, "issues": issues, "warnings": warnings}


# ---------- UI ----------
init_page("Step 1 - Design (v2)")
selected_project = use_project("project")
st.title("Step 1 - Design (v2)")
st.caption(
    "Tool-agnostic design capture with enzyme, PAM, mutation (SNP/base/prime), donor template, guides, and primers. "
    "Anchors on-chain as step 'Design'."
)

if selected_project:
    meta = load_project_meta(selected_project)
    objective_text = (meta.get("objective") or "").strip()
    if objective_text:
        st.info(f"**Project objective:** {objective_text}")
    else:
        st.warning("Set the project objective on the Project Charter page.")
else:
    st.info("Select a project to view its charter objective.")

st.markdown(
    """
    **Design resources**  
    • [CRISPOR](http://crispor.tefor.net/) – guide scoring, HDR templates, off-target screening  
    • [CHOPCHOP](https://chopchop.cbu.uib.no/) – multi-enzyme gRNA search with PAM filtering  
    • [Benchling](https://www.benchling.com/) – collaborative plasmid design and sequence management  
    • [RGEN Cas-Designer](http://www.rgenome.net/cas-designer/) – batch gRNA design with restriction site analysis  
    • [PrimeDesign](https://www.genscript.com/gencrispr-grna-design-tool.html?src=footer) – pegRNA planning for prime editing campaigns  
    • [Synthego Design Studio](https://design.synthego.com/#/) – automated guide selection with off-target analysis
    """
)

attachments: dict[str, str] = {}
saved_attachment_paths: list[Path] = []

if selected_project:
    snapshot_dir = project_subdir(selected_project, "snapshots", "design")
    attachment_dir = project_subdir(selected_project, "uploads", "design")
    report_dir = project_subdir(selected_project, "reports", "design")
else:
    snapshot_dir = FALLBACK_DATA_DIR
    attachment_dir = FALLBACK_ATTACHMENT_DIR
    report_dir = FALLBACK_DATA_DIR

with st.form("design_v2"):
    col_meta = st.columns(2)
    with col_meta[0]:
        project_id = st.text_input("Project ID", value="TIE1_SNPKI_v1")
    with col_meta[1]:
        author = st.text_input("Author", value="Narasimha Telugu")

    st.subheader("Platform & Enzyme")
    c1, c2, c3 = st.columns(3)
    with c1:
        design_platform = st.selectbox(
            "Design platform",
            ["CRISPOR", "CHOPCHOP", "Benchling", "RGEN (Cas-Designer)", "Upload/CSV", "Other"],
            index=0,
        )
    with c2:
        cas_variant = st.text_input("Cas variant", value="hfCas12Max")
    with c3:
        pam_rule = st.text_input("PAM rule", value="TTTV")
    design_source_url = st.text_input("Design source URL (optional)")

    st.subheader("Mutation / Locus")
    gene = st.text_input("Gene", value="TIE1")
    transcript = st.text_input("Transcript (e.g., NM_003266.5)")
    genome_assembly = st.selectbox("Genome assembly", ["GRCh38", "GRCh37", "Other"], index=0)
    edit_intent = st.selectbox("Edit intent", ["KO", "SNP-KI", "BaseEdit", "PrimeEdit"], index=1)
    col_hgvs = st.columns(2)
    with col_hgvs[0]:
        hgvs_c = st.text_input("HGVS c. (e.g., c.1441C>T)")
    with col_hgvs[1]:
        hgvs_p = st.text_input("HGVS p. (e.g., p.Arg481Cys)")
    region_context = st.text_input("Region/context (e.g., Exon 7; kinase domain)")

    base_editor_window = None
    pegRNA_notes = None
    if edit_intent == "BaseEdit":
        base_editor_window = st.text_input("Base editor window (e.g., positions 4–8)")
    if edit_intent == "PrimeEdit":
        pegRNA_notes = st.text_area("pegRNA notes (PBS/RT template, nicking sgRNA)")

    st.subheader("Guides (select 2–3 primary candidates)")
    selected_guides_raw: list[dict] = []
    num_guides = st.number_input("Number of selected guides", min_value=1, max_value=6, value=2)
    for idx in range(num_guides):
        with st.expander(f"Guide {idx + 1}"):
            selected_guides_raw.append(
                {
                    "id": st.text_input("Guide ID", value=f"g{idx + 1}", key=f"guide_id_{idx}"),
                    "sequence": st.text_input(
                        "Sequence (no PAM if Cas9; follow enzyme rule)", key=f"guide_seq_{idx}"
                    ),
                    "pam": st.text_input("PAM", value=pam_rule, key=f"guide_pam_{idx}"),
                    "strand": st.selectbox("Strand", ["", "+", "-"], index=0, key=f"guide_strand_{idx}") or None,
                    "genomic_locus": st.text_input(
                        "Genomic locus (chr:pos / HGVSg)", key=f"guide_locus_{idx}"
                    ),
                    "distance_to_edit_bp": int(
                        st.number_input(
                            "Distance to edit (bp)",
                            min_value=-500,
                            max_value=500,
                            value=0,
                            key=f"guide_dist_{idx}",
                        )
                    ),
                    "on_target_algo": st.selectbox(
                        "On-target scoring algorithm",
                        ["", "CFD", "MIT", "CCTop", "Other"],
                        index=0,
                        key=f"guide_on_algo_{idx}",
                    )
                    or None,
                    "on_target_score": st.number_input(
                        "On-target score", min_value=0.0, max_value=100.0, value=0.0, key=f"guide_on_score_{idx}"
                    )
                    or None,
                    "off_target_algo": st.selectbox(
                        "Off-target scoring algorithm",
                        ["", "CFD", "MIT", "CCTop", "Other"],
                        index=0,
                        key=f"guide_off_algo_{idx}",
                    )
                    or None,
                    "off_target_score": st.number_input(
                        "Off-target score", min_value=0.0, max_value=100.0, value=0.0, key=f"guide_off_score_{idx}"
                    )
                    or None,
                    "notes": st.text_input("Guide notes", key=f"guide_notes_{idx}") or None,
                }
            )

    st.subheader("Primers (amplicon spans the edit)")
    primer_pairs_raw: list[dict] = []
    num_primers = st.number_input("Number of primer pairs", min_value=0, max_value=5, value=1)
    for idx in range(num_primers):
        with st.expander(f"Primer Pair {idx + 1}"):
            primer_pairs_raw.append(
                {
                    "name": st.text_input("Name", value=f"TIE1_ex7_pair{idx + 1}", key=f"primer_name_{idx}"),
                    "forward": st.text_input("Forward primer", key=f"primer_forward_{idx}"),
                    "reverse": st.text_input("Reverse primer", key=f"primer_reverse_{idx}"),
                    "expected_amplicon_bp": int(
                        st.number_input(
                            "Expected amplicon (bp)",
                            min_value=150,
                            max_value=1200,
                            value=420,
                            key=f"primer_amplicon_{idx}",
                        )
                    ),
                }
            )

    donor: Optional[Donor] = None
    st.subheader("Donor / Editor details")
    if edit_intent in ("SNP-KI", "PrimeEdit", "BaseEdit"):
        default_index = {"ssODN": 0, "dsDNA": 1, "plasmid": 2, "none": 3}
        default_donor = "ssODN" if edit_intent == "SNP-KI" else "none"
        donor_type = st.selectbox(
            "Donor type",
            ["ssODN", "dsDNA", "plasmid", "none"],
            index=default_index.get(default_donor, 3),
        )
        donor_sequence = st.text_area("Donor sequence (optional; mask if sensitive)")
        donor_length = len(donor_sequence) if donor_sequence else None
        donor_asymmetry = (
            st.text_input("Asymmetry (e.g., 36/91)") if donor_type == "ssODN" else None
        )
        donor_strand = (
            st.text_input("Donor strand (e.g., non-PAM strand)") if donor_type == "ssODN" else None
        )
        prevent_recut = (
            st.checkbox(
                "Introduce silent PAM/seed mutations to prevent re-cutting",
                value=True if donor_type == "ssODN" else False,
            )
            if donor_type != "none"
            else False
        )
        hdr_notes = st.text_area("HDR / editing notes")
        donor = Donor(
            donor_type=donor_type,
            sequence=donor_sequence or None,
            length_nt=donor_length,
            asymmetry=donor_asymmetry,
            strand=donor_strand,
            introduces_silent_pam_or_seed_mut=prevent_recut if donor_type != "none" else None,
            hdr_notes=hdr_notes or None,
        )

    st.subheader("Attachments & Decisions")
    uploads = st.file_uploader(
        "Attach design exports (CSV/JSON/screenshots)", accept_multiple_files=True
    )
    if uploads:
        for idx, upload in enumerate(uploads):
            saved_path = save_uploaded_file(
                selected_project,
                upload,
                attachment_dir,
                category="uploads",
                context={"step": "design"},
            )
            attachments[upload.name] = str(saved_path)
            saved_attachment_paths.append(saved_path)

    off_target_review = st.text_area("Off-target review summary (top sites, in silico flags)")
    design_decision = st.text_input("Design decision (why these guides/donor)")

    submitted = st.form_submit_button("Save snapshot & compute hash")

if submitted:
    try:
        mutation = MutationMeta(
            gene=gene,
            transcript=transcript or None,
            genome_assembly=genome_assembly,
            edit_intent=edit_intent,
            hgvs_c=hgvs_c or None,
            hgvs_p=hgvs_p or None,
            region_context=region_context or None,
            base_editor_window=base_editor_window or None,
            pegRNA_notes=pegRNA_notes or None,
        )

        selected_guides = [
            Guide(
                id=guide["id"],
                sequence=guide["sequence"],
                pam=guide["pam"],
                strand=guide.get("strand") or None,
                genomic_locus=(guide.get("genomic_locus") or None),
                distance_to_edit_bp=guide.get("distance_to_edit_bp"),
                on_target_algo=guide.get("on_target_algo") or None,
                on_target_score=guide.get("on_target_score"),
                off_target_algo=guide.get("off_target_algo") or None,
                off_target_score=guide.get("off_target_score"),
                notes=guide.get("notes"),
            )
            for guide in selected_guides_raw
        ]

        primer_pairs = [
            PrimerPair(
                name=primer["name"],
                forward=primer["forward"],
                reverse=primer["reverse"],
                expected_amplicon_bp=primer.get("expected_amplicon_bp"),
            )
            for primer in primer_pairs_raw
        ]

        snapshot = DesignSnapshotV2(
            project_id=project_id,
            author=author,
            timestamp_unix=int(time.time()),
            design_platform=design_platform,
            cas_variant=cas_variant,
            pam_rule=pam_rule,
            design_source_url=design_source_url or None,
            mutation=mutation,
            selected_guides=selected_guides,
            primer_pairs=primer_pairs,
            donor=donor,
            off_target_review=off_target_review or None,
            design_decision=design_decision or None,
            attachments=attachments,
        )
        payload_dict = json.loads(snapshot.model_dump_json())
        payload = json.dumps(payload_dict, indent=2).encode("utf-8")
        digest = sha256_bytes(payload)
        outfile = snapshot_dir / f"{snapshot.project_id}_{snapshot.timestamp_unix}_{digest[:12]}.json"
        outfile.write_bytes(payload)

        st.session_state["design_v2_snapshot"] = {
            "digest": digest,
            "outfile": str(outfile),
            "metadata_uri": outfile.resolve().as_uri(),
            "payload": payload_dict,
            "project_id": project_id,
        }

        if selected_project:
            base_meta_update: dict[str, dict[str, object]] = {
                "compliance": {
                    "guides": [guide.model_dump() for guide in selected_guides],
                    "primers": [primer.model_dump() for primer in primer_pairs],
                    "donor": donor.model_dump() if donor else {},
                    "gene": mutation.gene,
                    "edit_intent": mutation.edit_intent,
                    "modification_description": mutation.region_context or mutation.hgvs_c or "",
                }
            }
            update_project_meta(selected_project, base_meta_update)

        if selected_project:
            register_file(
                selected_project,
                "snapshots",
                {
                    "step": "design",
                    "path": str(outfile),
                    "digest": digest,
                    "timestamp": snapshot.timestamp_unix,
                },
            )
            append_audit(
                selected_project,
                {
                    "ts": int(time.time()),
                    "step": "design",
                    "action": "snapshot_saved",
                    "snapshot_path": str(outfile),
                },
            )

        st.success("Design v2 snapshot saved.")
        st.code(f"SHA-256: {digest}", language="text")
        st.caption(f"Saved: {outfile}")

        if saved_attachment_paths:
            st.caption("Attachment previews")
            for idx, path in enumerate(saved_attachment_paths):
                preview_file(path, label=path.name, key_prefix=f"design_attach_{idx}")
        st.info("Snapshot ready for on-chain anchoring with step label 'Design'.")

        readiness = evaluate_design_readiness(payload_dict)
        st.session_state["design_readiness"] = readiness
        if readiness["ready"]:
            st.success("Readiness check passed: all required data present.")
        else:
            st.warning("Readiness check failed. Resolve the following before anchoring:")
            for issue in readiness["issues"]:
                st.write(f"- {issue}")
        for warning in readiness["warnings"]:
            st.caption(f"⚠️ {warning}")

        image_attachments = [p for p in saved_attachment_paths if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
        pdf_filename = build_step_filename(snapshot.project_id, "design", snapshot.timestamp_unix)
        pdf_path = report_dir / pdf_filename
        snapshot_to_pdf(
            payload_dict,
            pdf_path,
            f"Design Snapshot - {snapshot.project_id}",
            image_paths=image_attachments or None,
        )
        with pdf_path.open("rb") as pdf_file:
            st.download_button(
                "Download snapshot as PDF",
                pdf_file,
                file_name=pdf_path.name,
                key="design_pdf_download",
            )
        st.caption(f"PDF saved: {pdf_path}")
        if selected_project:
            register_file(
                selected_project,
                "reports",
                {
                    "step": "design",
                    "path": str(pdf_path),
                    "timestamp": snapshot.timestamp_unix,
                    "digest": sha256_bytes(pdf_path.read_bytes()),
                    "type": "pdf",
                },
            )
    except ValidationError as exc:
        st.error(exc)

snapshot_state = st.session_state.get("design_v2_snapshot")
readiness_state = st.session_state.get("design_readiness")
if snapshot_state:
    st.divider()
    st.subheader("On-chain Anchoring")
    st.write(
        "Anchor the design metadata hash to the AssuredRegistry contract with step `Design`."
    )
    st.code(f"SHA-256: {snapshot_state['digest']}", language="text")
    st.caption(f"Snapshot: {snapshot_state['outfile']}")

    if readiness_state:
        if readiness_state["ready"]:
            st.success("Ready to anchor: all readiness checks passed.")
        else:
            st.error("Resolve the following issues before anchoring:")
            for issue in readiness_state["issues"]:
                st.write(f"- {issue}")
            if readiness_state["warnings"]:
                st.caption("Warnings:")
                for warning in readiness_state["warnings"]:
                    st.caption(f"⚠️ {warning}")
    else:
        st.info("Generate or reload a snapshot to run the readiness checks.")

    anchor_disabled = not readiness_state or not readiness_state["ready"]

    if st.button("Anchor hash on-chain (Sepolia)", disabled=anchor_disabled):
        try:
            result = send_log_tx(
                hex_digest=snapshot_state["digest"],
                step="Design",
                metadata_uri=snapshot_state["metadata_uri"],
            )
            st.success("Anchored on-chain ✅")
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
                "step": "Design",
                "content_hash": snapshot_state["digest"],
                "entry_id": entry_id,
            }
            proof_path = Path(snapshot_state["outfile"]).with_suffix(".chainproof.json")
            proof_path.write_text(json.dumps(chainproof, indent=2), encoding="utf-8")
            st.caption(f"Chain proof saved: {proof_path}")
            if selected_project:
                register_chain_tx(
                    selected_project,
                    {
                        "step": "design",
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
                        "step": "design",
                        "action": "anchored",
                        "tx_hash": result["tx_hash"],
                    },
                )
        except Exception as exc:
            st.error(f"On-chain anchoring failed: {exc}")
