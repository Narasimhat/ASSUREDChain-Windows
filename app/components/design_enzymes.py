"""Enzyme identity at the design-import and reagent-lock boundaries.

PAM motifs describe the enzyme; an observed guide PAM is stored separately.
Legacy records without an enzyme retain the SpCas9 compatibility default, marked
as assumed so a scientist must confirm it before creating a new locked record.
"""
from __future__ import annotations

import re
from typing import Any

ENZYMES = {
    "spcas9": ("SpCas9", "NGG", "3prime"),
    "spcas9ng": ("SpCas9-NG", "NG", "3prime"),
    "sacas9": ("SaCas9", "NNGRRT", "3prime"),
    "spry": ("SpRY", "NNN", "3prime"),
    "ascas12a": ("AsCas12a", "TTTV", "5prime"),
    "lbcas12a": ("LbCas12a", "TTTV", "5prime"),
    "mad7": ("Mad7", "YTTN", "5prime"),
    "cas12a": ("Cas12a", "TTTV", "5prime"),  # legacy generic QC identity
}
ALIASES = {
    "spcas9highfidelityhifi": "spcas9", "spcas9wildtype": "spcas9",
    "spcas9hifi": "spcas9", "hificas9": "spcas9", "spcas9hf1": "spcas9",
}


def enzyme_id(value: Any) -> str:
    if isinstance(value, dict):
        identity = value.get("id") or value.get("name")
        if not identity:
            raise ValueError("Enzyme metadata has no id or name.")
        resolved = enzyme_id(identity)
        if value.get("id") and value.get("name"):
            if enzyme_id(value["name"]) != resolved:
                raise ValueError("Conflicting enzyme id and name.")
        return resolved
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Choose a supported enzyme.")
    # Older menus appended a PAM to the name.
    value = re.sub(r"\s*\((?:NGG|NG|NNGRRT|NNN|TTTV|YTTN)\)\s*$", "", value, flags=re.I)
    key = re.sub(r"[^a-z0-9]", "", value.lower())
    key = ALIASES.get(key, key)
    if key not in ENZYMES:
        raise ValueError(f"Unsupported enzyme '{value}'. Re-export with an explicit supported enzyme.")
    return key


def enzyme_metadata(identity: Any) -> dict:
    key = enzyme_id(identity)
    name, pam, side = ENZYMES[key]
    return {"enzyme_id": key, "cas_variant": name, "pam_rule": pam, "pam_side": side}


def extract_design_enzyme(payload: dict) -> dict:
    """Read authoritative metadata through supported wrappers and guide records.

    Do not descend into recommendation alternatives: those intentionally contain
    multiple enzymes and are not the enzyme of the applied design.
    """
    identities: set[str] = set()
    patterns: set[str] = set()
    sides: set[str] = set()
    versions: set[str] = set()
    seen: set[int] = set()

    def declared(value: Any) -> None:
        if value is None or value == "":
            return
        identities.add(enzyme_id(value))
        if isinstance(value, dict):
            motif = value.get("pam_pattern") or value.get("pam_rule")
            if motif:
                patterns.add(str(motif).strip().upper())
            side = value.get("pam_side")
            if side:
                sides.add(str(side))
            version = value.get("version") or value.get("configVersion") or value.get("config_version")
            if version:
                versions.add(str(version))

    def visit(obj: Any) -> None:
        if not isinstance(obj, dict) or id(obj) in seen:
            return
        seen.add(id(obj))
        for key in ("enzyme", "enzyme_id", "nuclease", "cas_variant"):
            if key in obj:
                declared(obj[key])
        options = obj.get("options")
        if isinstance(options, dict):
            declared(options.get("enzyme"))
        for key in ("pam_pattern", "pam_rule"):
            if obj.get(key):
                patterns.add(str(obj[key]).strip().upper())
        if obj.get("pam_side"):
            sides.add(str(obj["pam_side"]))
        for key in ("configVersion", "enzyme_config_version"):
            if obj.get(key):
                versions.add(str(obj[key]))
        for key in ("project", "designResult", "qcDesignSpec", "design"):
            visit(obj.get(key))
        for key in ("guides", "gs"):
            for guide in obj.get(key) or []:
                visit(guide)

    visit(payload)
    if len(identities) > 1:
        raise ValueError("Conflicting or mixed enzymes in this design. Import separate, consistent designs.")
    assumed = not identities
    metadata = enzyme_metadata(next(iter(identities), "spcas9"))
    if patterns and patterns != {metadata["pam_rule"]}:
        raise ValueError("The declared PAM motif conflicts with the design enzyme.")
    if sides and sides != {metadata["pam_side"]}:
        raise ValueError("The declared PAM side conflicts with the design enzyme.")
    if len(versions) > 1:
        raise ValueError("Conflicting enzyme configuration versions in this design.")
    return {
        **metadata,
        "enzyme_config_version": next(iter(versions), None),
        "enzyme_metadata_assumed": assumed,
        "import_warnings": ["Enzyme metadata is absent. SpCas9/NGG is a legacy compatibility default; confirm the enzyme before locking."] if assumed else [],
    }


def build_locked_reagent_spec(*, project_id: str, cas_variant: str, pam_rule: str,
                              guides: list, donors: list, primers: list,
                              vendor: str, signoff: str, notes: str, timestamp: int,
                              imported: dict | None = None, legacy_confirmed: bool = False,
                              requires_confirmation: bool = False) -> dict:
    """Validate identity before writing/hashing a new reagent specification."""
    metadata = enzyme_metadata(cas_variant)
    if str(pam_rule).strip().upper() != metadata["pam_rule"]:
        raise ValueError("The PAM requirement does not match the selected enzyme.")
    if not guides or any(not str(g.get("sequence") or g.get("spacer") or g.get("sp") or "").strip() for g in guides):
        raise ValueError("Apply a design containing guide sequences before locking the reagent specification.")
    if not signoff.strip():
        raise ValueError("Enter the scientist responsible for this design.")
    pam_regex = "".join({"N": "[ACGT]", "R": "[AG]", "Y": "[CT]", "V": "[ACG]"}.get(base, base) for base in metadata["pam_rule"])
    for guide in guides:
        observed_pam = str(guide.get("pam") or guide.get("pm") or "").strip().upper()
        if re.fullmatch("[ACGT]+", observed_pam) and not re.fullmatch(pam_regex, observed_pam):
            raise ValueError("An observed guide PAM does not match the selected enzyme. Re-import or verify the original design.")
        declared = guide.get("enzyme_id") or guide.get("enzyme")
        if declared and enzyme_id(declared) != metadata["enzyme_id"]:
            raise ValueError("A guide belongs to a different enzyme. Re-import the matching design.")
    if imported:
        expected = enzyme_metadata(imported.get("enzyme_id") or imported.get("cas_variant"))
        if not imported.get("enzyme_metadata_assumed") and (expected["enzyme_id"] != metadata["enzyme_id"] or imported.get("pam_rule") != metadata["pam_rule"]):
            raise ValueError("Approval enzyme/PAM differs from the imported design. Re-import the intended design.")
        metadata["enzyme_config_version"] = imported.get("enzyme_config_version") if not imported.get("enzyme_metadata_assumed") else None
        requires_confirmation = requires_confirmation or bool(imported.get("enzyme_metadata_assumed"))
    if requires_confirmation and not legacy_confirmed:
        raise ValueError("Confirm the enzyme/PAM for this older, unlabelled design before locking.")
    return {
        "project_id": project_id, **metadata,
        "cas_variant": imported.get("cas_variant") if imported and not imported.get("enzyme_metadata_assumed") else cas_variant,
        "enzyme_metadata_assumed": bool(requires_confirmation),
        "enzyme_metadata_confirmed": bool(legacy_confirmed) if requires_confirmation else True,
        "vendor": vendor, "guides": [{**guide, "enzyme_id": metadata["enzyme_id"]} for guide in guides], "donors": donors, "primers": primers,
        "signoff": signoff, "notes": notes, "timestamp_unix": timestamp,
    }
