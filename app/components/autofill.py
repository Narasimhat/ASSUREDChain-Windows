from __future__ import annotations

import re
from typing import Any


DNA_RE = re.compile(r"[ACGTUacgtu]{15,}")


def _clean_seq(seq: str) -> str:
    return re.sub(r"[^ACGTUNacgtun]", "", (seq or "").strip()).upper().replace("U", "T")


def parse_guides_and_donors(pasted: str, default_pam: str = "") -> dict:
    """Parse pasted text and extract guides and ssODN donor.

    Supports common copy formats:
    - CRISPOR table: Position, Strand, Sequence, PAM, Specificity, Efficiency
    - Excel/TSV/CSV rows containing guide sequences.
    - Benchling-style blocks with labeled sequences.

    Returns:
      {
        "guides": [
          {
            "sequence": "...",
            "pam": "NGG",
            "genomic_locus": "chr1:44905942",
            "strand": "+",
            "on_target_score": 41.73,
            "off_target_score": 6.48,
          },
          ...
        ],
        "donor": {"donor_type": "ssODN", "sequence": "...", "length_nt": 123} | None,
      }
    """

    text = (pasted or "").strip()
    if not text:
        return {"guides": [], "donor": None}

    # Normalize whitespace and delimiters.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    guides: list[dict] = []

    # 1) Try CRISPOR-style TSV with header detection.
    # Header example: "Position	Strand	Sequence	PAM	Specificity score	Efficiency score"
    lines = normalized.split("\n")
    header_idx = -1
    for idx, line in enumerate(lines):
        lower = line.lower()
        if "position" in lower and "strand" in lower and "sequence" in lower and "pam" in lower:
            header_idx = idx
            break

    if header_idx >= 0:
        # Parse CRISPOR table rows
        header_parts = re.split(r"[\t,;|]", lines[header_idx].strip())
        header_map = {part.strip().lower(): i for i, part in enumerate(header_parts)}

        pos_col = header_map.get("position", -1)
        strand_col = header_map.get("strand", -1)
        seq_col = header_map.get("sequence", -1)
        pam_col = header_map.get("pam", -1)
        spec_col = header_map.get("specificity score", header_map.get("specificity", -1))
        eff_col = header_map.get("efficiency score", header_map.get("efficiency", -1))

        for line in lines[header_idx + 1 :]:
            if not line.strip():
                continue
            parts = re.split(r"[\t,;|]", line.strip())
            if len(parts) < 3:
                continue

            guide_seq = _clean_seq(parts[seq_col]) if seq_col >= 0 and seq_col < len(parts) else ""
            if not (19 <= len(guide_seq) <= 24):
                continue

            pam_val = (parts[pam_col].strip().upper() if pam_col >= 0 and pam_col < len(parts) else "") or default_pam
            strand_val = parts[strand_col].strip() if strand_col >= 0 and strand_col < len(parts) else ""
            strand_norm = "+" if strand_val in ("1", "+", "plus") else ("-" if strand_val in ("-1", "-", "minus") else None)

            genomic_locus = ""
            if pos_col >= 0 and pos_col < len(parts):
                pos_str = parts[pos_col].strip()
                if pos_str.isdigit():
                    genomic_locus = f"chr:pos={pos_str}"

            on_target = None
            off_target = None
            try:
                if spec_col >= 0 and spec_col < len(parts):
                    on_target = float(parts[spec_col].strip())
            except ValueError:
                pass
            try:
                if eff_col >= 0 and eff_col < len(parts):
                    off_target = float(parts[eff_col].strip())
            except ValueError:
                pass

            guides.append(
                {
                    "sequence": guide_seq,
                    "pam": pam_val,
                    "strand": strand_norm,
                    "genomic_locus": genomic_locus or None,
                    "on_target_score": on_target,
                    "off_target_score": off_target,
                }
            )

    # 2) Fallback: TSV/CSV style lines (take the longest DNA token in the row).
    # Only run if CRISPOR parsing didn't find any guides
    if not guides:
        for line in normalized.split("\n"):
            if not line.strip():
                continue
            parts = re.split(r"[\t,;|]", line)
            dna_tokens = []
            for part in parts:
                for token in DNA_RE.findall(part):
                    cleaned = _clean_seq(token)
                    if 19 <= len(cleaned) <= 24:
                        dna_tokens.append(cleaned)
            # Only take up to 3 guides from fallback parsing
            for tok in dna_tokens[:3]:
                guides.append({"sequence": tok, "pam": (default_pam or "").strip().upper()})

    # 3) Benchling-ish labeled sequences: look for "sgRNA"/"gRNA" labels.
    if not guides:
        label_patterns = [
            re.compile(r"\b(?:sgRNA|gRNA|guide)\b[^\n:]*[:\s]+([ACGTUacgtu\s-]{15,})"),
            re.compile(r"\bprotospacer\b[^\n:]*[:\s]+([ACGTUacgtu\s-]{15,})"),
        ]
        for pat in label_patterns:
            for m in pat.finditer(normalized):
                cleaned = _clean_seq(m.group(1))
                if 19 <= len(cleaned) <= 24:
                    guides.append({"sequence": cleaned, "pam": (default_pam or "").strip().upper()})

    # Deduplicate guides while preserving order.
    seen = set()
    deduped_guides: list[dict] = []
    for g in guides:
        seq = g.get("sequence") or ""
        if seq and seq not in seen:
            seen.add(seq)
            deduped_guides.append(g)
    guides = deduped_guides[:6]

    # Donor extraction: choose the longest DNA-ish sequence (>= 60) as likely ssODN.
    donor_candidates: list[str] = []
    for token in DNA_RE.findall(normalized):
        cleaned = _clean_seq(token)
        if len(cleaned) >= 60:
            donor_candidates.append(cleaned)

    donor_seq = max(donor_candidates, key=len) if donor_candidates else ""
    donor = None
    if donor_seq:
        donor = {
            "donor_type": "ssODN",
            "sequence": donor_seq,
            "length_nt": len(donor_seq),
        }

    return {"guides": guides, "donor": donor}


def d(data: Any, *keys: Any, default: Any = "") -> Any:
    """
    Safe nested getter: d(obj, "a", 0, "b", default="") → obj["a"][0]["b"] if present else default.
    Works with dict/list sequences and returns default when path missing.
    """
    current = data
    for key in keys:
        try:
            if isinstance(current, dict):
                current = current[key]
            elif isinstance(current, (list, tuple)) and isinstance(key, int):
                current = current[key]
            else:
                return default
        except (KeyError, IndexError, TypeError):
            return default
    return current if current not in (None, "") else default


def project_value(meta: dict, key: str, default: Any = "") -> Any:
    return meta.get(key, default)
