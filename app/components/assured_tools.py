"""Launch Assured Design / Assured QC and import their exports into ASSUREDChain forms."""

from __future__ import annotations

import csv
import io
import json
import os
from typing import Any, Optional

DEFAULT_DESIGN_URL = "https://assured-design-fair.vercel.app/"
DEFAULT_QC_URL = "https://assured-qc-fair.vercel.app/"
DEFAULT_HUB_URL = "https://assured-hub-fair.vercel.app/"

EDIT_KIND_TO_INTENT = {
    "snp": "SNP-KI",
    "pm": "SNP-KI",
    "deletion": "KO",
    "ko": "KO",
    "tag_small": "SNP-KI",
    "tag_large": "SNP-KI",
    "reporter": "SNP-KI",
    "internal_tag": "SNP-KI",
    "it": "SNP-KI",
    "ct": "SNP-KI",
    "nt": "SNP-KI",
}

QC_DECISION_TO_CHAIN = {
    "accept": "proceed_to_cloning",
    "hold": "repeat_edit",
    "re-sequence": "repeat_edit",
    "reject": "archive",
}


def design_url() -> str:
    return (os.getenv("ASSURED_DESIGN_URL") or DEFAULT_DESIGN_URL).rstrip("/") + "/"


def qc_url() -> str:
    return (os.getenv("ASSURED_QC_URL") or DEFAULT_QC_URL).rstrip("/") + "/"


def hub_url() -> str:
    return (os.getenv("ASSURED_HUB_FAIR_URL") or DEFAULT_HUB_URL).rstrip("/") + "/"


def render_assured_launcher(
    *,
    title: str,
    url: str,
    caption: str,
    height: int = 640,
    key: str,
) -> None:
    """Show open-in-new-tab controls and an optional embedded view of an Assured tool."""
    import streamlit as st

    st.markdown(f"**{title}**")
    st.caption(caption)
    cols = st.columns([1, 1, 2])
    with cols[0]:
        st.link_button(f"Open {title}", url, use_container_width=True)
    with cols[1]:
        show = st.checkbox("Show embedded view", value=False, key=f"{key}_embed")
    if show:
        st.components.v1.iframe(url, height=height, scrolling=True)
        st.caption(
            "If the embed is blank, the host blocks framing — use Open instead. "
            "Export from the tool, then import the file below."
        )


def _as_dict(payload: Any) -> dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        return json.loads(payload)
    raise ValueError("Expected a JSON object.")


def _pair_primers(primers: list[dict], amplicons: list[dict]) -> list[dict]:
    by_name = {str(p.get("name") or ""): p for p in primers if p.get("name")}
    pairs: list[dict] = []
    for amp in amplicons or []:
        fwd = by_name.get(str(amp.get("forward") or ""))
        rev = by_name.get(str(amp.get("reverse") or ""))
        if not fwd or not rev:
            continue
        edited = amp.get("editedBp")
        expected = None
        if isinstance(edited, dict) and edited:
            expected = next(iter(edited.values()), None)
        if expected is None:
            expected = amp.get("wtBp")
        pairs.append(
            {
                "name": amp.get("name") or f"{fwd.get('name')}+{rev.get('name')}",
                "forward": str(fwd.get("sequence") or "").upper(),
                "reverse": str(rev.get("sequence") or "").upper(),
                "expected_amplicon_bp": int(expected) if expected else None,
            }
        )
    if pairs:
        return pairs

    # Fallback: pair Fw/Rev by stem when amplicons are absent.
    fwds = [p for p in primers if str(p.get("name") or "").lower().endswith(("fw", "fwd", "forward", "_f"))]
    revs = [p for p in primers if str(p.get("name") or "").lower().endswith(("rev", "reverse", "_r", "rv"))]
    for idx, fwd in enumerate(fwds):
        rev = revs[idx] if idx < len(revs) else (revs[0] if revs else None)
        if not rev:
            break
        pairs.append(
            {
                "name": f"{fwd.get('name')}+{rev.get('name')}",
                "forward": str(fwd.get("sequence") or "").upper(),
                "reverse": str(rev.get("sequence") or "").upper(),
                "expected_amplicon_bp": None,
            }
        )
    return pairs


def parse_assured_design_export(payload: Any) -> dict:
    """Map an Assured Design QC hand-off (`assured-qc-design/1`) into Design Log fields."""
    data = _as_dict(payload)
    if data.get("schema") and data.get("schema") != "assured-qc-design/1":
        raise ValueError(f"Unsupported design schema: {data.get('schema')}")
    if "guides" not in data and "design" not in data:
        raise ValueError("Not an Assured Design export (missing design/guides).")

    design = data.get("design") or {}
    coding = data.get("coding") or {}
    guides_in = data.get("guides") or []
    donors_in = data.get("donors") or []
    primers_in = data.get("primers") or []
    amplicons = data.get("amplicons") or []

    edit_kind = str(design.get("editKind") or design.get("type") or "").lower()
    edit_intent = EDIT_KIND_TO_INTENT.get(edit_kind, "SNP-KI")

    guides = []
    for idx, g in enumerate(guides_in):
        spacer = str(g.get("spacer") or g.get("sequence") or "").upper().replace("U", "T")
        if not spacer:
            continue
        guides.append(
            {
                "id": g.get("name") or f"g{idx + 1}",
                "sequence": spacer,
                "pam": str(g.get("pam") or "NGG").upper(),
                "strand": g.get("strand") or None,
                "genomic_locus": None,
                "distance_to_edit_bp": g.get("offsetFromEdit"),
                "on_target_algo": "Other",
                "on_target_score": None,
                "off_target_algo": None,
                "off_target_score": None,
                "notes": "Imported from Assured Design (assured-qc-design/1)",
            }
        )

    donors = []
    for d in donors_in:
        seq = str(d.get("sequence") or d.get("orderSequence") or "").upper().replace("U", "T")
        if not seq:
            continue
        fmt = str(d.get("format") or "ssodn").lower()
        donor_type = "ssODN" if fmt == "ssodn" else ("dsDNA" if fmt in {"block", "aav"} else "ssODN")
        donors.append(
            {
                "donor_type": donor_type,
                "sequence": seq,
                "length_nt": len(seq),
                "asymmetry": None,
                "strand": d.get("orderStrand") or None,
                "introduces_silent_pam_or_seed_mut": True,
                "hdr_notes": d.get("name") or "Assured Design donor",
            }
        )

    primer_pairs = _pair_primers(primers_in, amplicons)
    gene = design.get("gene") or coding.get("gene") or ""
    transcript = coding.get("transcript") or ""
    label = design.get("label") or ""

    return {
        "design_platform": "Assured Design",
        "cas_variant": "SpCas9",
        "pam_rule": "NGG",
        "design_source_url": design_url(),
        "mutation": {
            "gene": gene,
            "transcript": transcript,
            "genome_assembly": "GRCh38",
            "edit_intent": edit_intent,
            "hgvs_c": None,
            "hgvs_p": label or None,
            "region_context": label or None,
            "base_editor_window": None,
            "pegRNA_notes": None,
        },
        "selected_guides": guides,
        "primer_pairs": primer_pairs,
        "donors": donors,
        "off_target_review": "Guides and donor taken from Assured Design export; off-target review remains lab responsibility.",
        "design_decision": f"Imported Assured Design hand-off for {gene or 'target'} ({edit_kind or 'edit'}).",
        "raw_schema": data.get("schema") or "assured-qc-design/1",
    }


def apply_design_import_to_session(parsed: dict) -> None:
    """Write parsed Assured Design fields into Streamlit session_state for the Design form."""
    import streamlit as st

    mutation = parsed.get("mutation") or {}
    guides = parsed.get("selected_guides") or []
    donors = parsed.get("donors") or []
    primers = parsed.get("primer_pairs") or []

    st.session_state["assured_design_import"] = parsed
    st.session_state["num_guides_autofill"] = max(len(guides), 1)
    st.session_state["num_donors_autofill"] = max(len(donors), 0)
    st.session_state["num_primers_autofill"] = max(len(primers), 0)

    for idx, g in enumerate(guides[:6]):
        st.session_state[f"guide_id_{idx}"] = g.get("id") or f"g{idx + 1}"
        st.session_state[f"guide_seq_{idx}"] = g.get("sequence") or ""
        st.session_state[f"guide_pam_{idx}"] = g.get("pam") or "NGG"
        if g.get("strand"):
            st.session_state[f"guide_strand_{idx}"] = g["strand"]
        if g.get("distance_to_edit_bp") is not None:
            st.session_state[f"guide_dist_{idx}"] = int(g["distance_to_edit_bp"] or 0)

    for idx, d in enumerate(donors[:4]):
        st.session_state[f"donor_type_{idx}"] = d.get("donor_type") or "ssODN"
        st.session_state[f"donor_sequence_{idx}"] = d.get("sequence") or ""

    for idx, p in enumerate(primers[:4]):
        st.session_state[f"primer_name_{idx}"] = p.get("name") or f"pair{idx + 1}"
        st.session_state[f"primer_forward_{idx}"] = p.get("forward") or ""
        st.session_state[f"primer_reverse_{idx}"] = p.get("reverse") or ""
        if p.get("expected_amplicon_bp"):
            st.session_state[f"primer_amplicon_{idx}"] = int(p["expected_amplicon_bp"])

    # Soft hints for fields that live inside the form defaults path.
    st.session_state["assured_design_gene"] = mutation.get("gene") or ""
    st.session_state["assured_design_transcript"] = mutation.get("transcript") or ""
    st.session_state["assured_design_edit_intent"] = mutation.get("edit_intent") or "SNP-KI"
    st.session_state["assured_design_region"] = mutation.get("region_context") or ""
    st.session_state["assured_design_platform"] = "Assured Design"
    st.session_state["assured_design_cas"] = parsed.get("cas_variant") or "SpCas9"
    st.session_state["assured_design_pam"] = parsed.get("pam_rule") or "NGG"
    st.session_state["assured_design_source_url"] = parsed.get("design_source_url") or design_url()


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_assured_qc_export(payload: Any, filename: str = "") -> dict:
    """Map Assured QC CSV/JSON results into Assessment Log defaults."""
    name = (filename or "").lower()
    rows: list[dict] = []

    if isinstance(payload, dict):
        if isinstance(payload.get("results"), list):
            for r in payload["results"]:
                rows.append(
                    {
                        "sample": r.get("name") or r.get("sample"),
                        "decision": (r.get("decision") or {}).get("decision")
                        if isinstance(r.get("decision"), dict)
                        else r.get("decision"),
                        "indel_pct": (r.get("summary") or {}).get("indelPct"),
                        "intended_edit_pct": (r.get("summary") or {}).get("intendedEditPct"),
                        "edited_pct": (r.get("summary") or {}).get("editedPct"),
                        "ko_score_pct": (r.get("summary") or {}).get("koScorePct"),
                        "notes": "; ".join(r.get("warnings") or []),
                    }
                )
        elif payload.get("schema") == "assured-qc-run-provenance/1":
            raise ValueError("Upload the Assured QC results table (CSV) or results JSON, not run provenance alone.")
        else:
            raise ValueError("Unrecognized Assured QC JSON export.")
    else:
        text = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else str(payload)
        # Prefer CSV when filename says so, else try JSON then CSV.
        if name.endswith(".json") or text.lstrip().startswith("{") or text.lstrip().startswith("["):
            try:
                return parse_assured_qc_export(json.loads(text), filename="payload.json")
            except json.JSONDecodeError:
                pass
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            rows.append(
                {
                    "sample": row.get("sample") or row.get("name"),
                    "decision": row.get("decision"),
                    "indel_pct": row.get("indel_pct"),
                    "intended_edit_pct": row.get("intended_edit_pct"),
                    "edited_pct": row.get("edited_pct"),
                    "ko_score_pct": row.get("ko_score_pct"),
                    "notes": row.get("notes") or row.get("decision_reason") or "",
                    "recommendation": row.get("recommendation"),
                }
            )

    if not rows:
        raise ValueError("No Assured QC sample rows found in the export.")

    # Prefer an accepted clone; otherwise the first row.
    preferred = next((r for r in rows if str(r.get("decision") or "").lower() == "accept"), rows[0])
    indel = _float_or_none(preferred.get("indel_pct"))
    hdr = _float_or_none(preferred.get("intended_edit_pct"))
    if hdr is None:
        hdr = _float_or_none(preferred.get("edited_pct"))
    ko = _float_or_none(preferred.get("ko_score_pct"))
    if indel is None and ko is not None:
        indel = ko

    qc_decision = str(preferred.get("decision") or "").lower()
    chain_decision = QC_DECISION_TO_CHAIN.get(qc_decision, "repeat_edit")

    notes_bits = [
        f"Imported Assured QC sample: {preferred.get('sample') or 'unknown'}",
        f"QC decision: {preferred.get('decision') or 'n/a'}",
    ]
    if preferred.get("notes"):
        notes_bits.append(str(preferred["notes"]))
    if preferred.get("recommendation"):
        notes_bits.append(f"Recommendation: {preferred['recommendation']}")
    if len(rows) > 1:
        notes_bits.append(f"{len(rows)} samples in export; form defaults use the preferred/accept row.")

    return {
        "tool_used": "Assured QC",
        "assay_type": "Sanger-Indel",
        "total_indel_pct": indel if indel is not None else 0.0,
        "hdr_pct": hdr if hdr is not None else 0.0,
        "ki_pct": hdr if hdr is not None else 0.0,
        "decision": chain_decision,
        "notes": " | ".join(notes_bits),
        "samples": rows,
        "preferred_sample": preferred.get("sample"),
    }
