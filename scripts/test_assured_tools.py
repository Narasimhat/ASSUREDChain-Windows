"""Smoke tests for Assured Design / Assured QC importers (no Streamlit runtime required for parsers)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.components.assured_tools import (  # noqa: E402
    parse_assured_design_export,
    parse_assured_qc_export,
)


SAMPLE_DESIGN = {
    "schema": "assured-qc-design/1",
    "design": {"type": "pm", "editKind": "snp", "gene": "APOE", "label": "R176C"},
    "coding": {"transcript": "NM_000041.4", "gene": "APOE"},
    "guides": [
        {"name": "APOE_gRNA1", "spacer": "ACACTGCCAGGCGCTTCTGC", "pam": "AGG", "strand": "-", "offsetFromEdit": -4},
        {"name": "APOE_gRNA2", "spacer": "CCCCGGCCTGGTACACTGCC", "pam": "AGG", "strand": "-", "offsetFromEdit": 8},
    ],
    "donors": [{"name": "ssODN1", "format": "ssodn", "sequence": "A" * 80}],
    "primers": [
        {"name": "APOE_Fw", "sequence": "TGGAGGAACAACTGACCC"},
        {"name": "APOE_Rev", "sequence": "CTCCTTCACCTCGTCCAG"},
    ],
    "amplicons": [
        {"name": "pair", "forward": "APOE_Fw", "reverse": "APOE_Rev", "wtBp": 470, "editedBp": {"ssODN1": 470}}
    ],
}

SAMPLE_QC_CSV = """sample,well,decision,decision_reason,status,result,wild_type_pct,edited_pct,indel_pct,intended_edit_pct,ko_score_pct,notes,recommendation
cloneA,,accept,clean HDR,analysed,ok,10,85,5,85,0,good,advance
cloneB,,reject,noise,analysed,fail,90,5,5,5,0,noisy,drop
"""


class AssuredToolsTests(unittest.TestCase):
    def test_design_import(self) -> None:
        parsed = parse_assured_design_export(SAMPLE_DESIGN)
        self.assertEqual(parsed["design_platform"], "Assured Design")
        self.assertEqual(parsed["mutation"]["gene"], "APOE")
        self.assertEqual(parsed["mutation"]["edit_intent"], "SNP-KI")
        self.assertEqual(len(parsed["selected_guides"]), 2)
        self.assertEqual(parsed["selected_guides"][0]["sequence"], "ACACTGCCAGGCGCTTCTGC")
        self.assertEqual(len(parsed["donors"]), 1)
        self.assertEqual(parsed["primer_pairs"][0]["expected_amplicon_bp"], 470)

    def test_qc_csv_import_prefers_accept(self) -> None:
        parsed = parse_assured_qc_export(SAMPLE_QC_CSV, "assured_qc.csv")
        self.assertEqual(parsed["tool_used"], "Assured QC")
        self.assertEqual(parsed["preferred_sample"], "cloneA")
        self.assertEqual(parsed["decision"], "proceed_to_cloning")
        self.assertEqual(parsed["total_indel_pct"], 5.0)
        self.assertEqual(parsed["hdr_pct"], 85.0)

    def test_qc_json_import(self) -> None:
        payload = {
            "results": [
                {
                    "name": "bulk1",
                    "decision": {"decision": "hold"},
                    "summary": {"indelPct": 40.0, "intendedEditPct": 12.0, "editedPct": 40.0},
                    "warnings": ["low confidence"],
                }
            ]
        }
        parsed = parse_assured_qc_export(json.dumps(payload), "results.json")
        self.assertEqual(parsed["decision"], "repeat_edit")
        self.assertEqual(parsed["total_indel_pct"], 40.0)
        self.assertIn("low confidence", parsed["notes"])


if __name__ == "__main__":
    unittest.main()
