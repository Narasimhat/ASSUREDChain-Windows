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
    bench_url,
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

SAMPLE_ASSURED_BUNDLE = {
    "id": "proj_12345",
    "name": "TP53 KO Experiment",
    "gene": "TP53",
    "editType": "ko",
    "mutation": "Exon 4 Frameshift",
    "designResult": {
        "gene": "TP53",
        "type": "ko",
        "guides": [
            {"id": "g1", "sequence": "CCCTGTCACCGAGACCCCTG", "pam": "CGG", "strand": "+", "offsetFromEdit": 0}
        ],
        "primers": [
            {"name": "TP53_Fw", "sequence": "AGACCCAGTAGCAGCCACCT"},
            {"name": "TP53_Rev", "sequence": "GCTCGACGCTAGGATCTGAC"},
        ],
        "amplicons": [
            {"name": "TP53_amp", "forward": "TP53_Fw", "reverse": "TP53_Rev", "wtBp": 420}
        ],
    },
    "qcResults": [
        {
            "name": "clone_1",
            "decision": {"decision": "accept", "reasons": ["Clean homozygous knockout", "No off-target"]},
            "summary": {"indelPct": 94.5, "intendedEditPct": 0.0, "editedPct": 94.5},
            "recommendation": {"rank": 1},
        },
        {
            "name": "clone_2",
            "decision": {"decision": "reject", "reasons": ["Wild-type retained"]},
            "summary": {"indelPct": 12.0, "intendedEditPct": 0.0, "editedPct": 12.0},
            "recommendation": {"rank": 2},
        },
    ],
}

SAMPLE_BENCH_SCREENING_CSV = """Sample,Decision,HDR_Fraction,BlockingOnly_Fraction,Indel_Fraction,WT_Fraction,Rank,Reasons
"clone_1","accept","88","0","6","6","1","High HDR; High confidence"
"clone_2","hold","45","5","40","10","2","Moderate HDR; Mixed alleles"
"clone_3","reject","0","0","15","85","3","Wild-type dominant"
"""


class AssuredToolsTests(unittest.TestCase):
    def test_bench_url(self) -> None:
        self.assertTrue(bench_url().startswith("https://"))
        self.assertTrue(bench_url().endswith("/"))

    def test_design_import(self) -> None:
        parsed = parse_assured_design_export(SAMPLE_DESIGN)
        self.assertEqual(parsed["design_platform"], "Assured Design")
        self.assertEqual(parsed["mutation"]["gene"], "APOE")
        self.assertEqual(parsed["mutation"]["edit_intent"], "SNP-KI")
        self.assertEqual(len(parsed["selected_guides"]), 2)
        self.assertEqual(parsed["selected_guides"][0]["sequence"], "ACACTGCCAGGCGCTTCTGC")
        self.assertEqual(len(parsed["donors"]), 1)
        self.assertEqual(parsed["primer_pairs"][0]["expected_amplicon_bp"], 470)

    def test_assured_bundle_design_import(self) -> None:
        parsed = parse_assured_design_export(SAMPLE_ASSURED_BUNDLE)
        self.assertEqual(parsed["design_platform"], "ASSURED Bench")
        self.assertEqual(parsed["mutation"]["gene"], "TP53")
        self.assertEqual(parsed["mutation"]["edit_intent"], "KO")
        self.assertEqual(len(parsed["selected_guides"]), 1)
        self.assertEqual(parsed["selected_guides"][0]["sequence"], "CCCTGTCACCGAGACCCCTG")
        self.assertEqual(parsed["primer_pairs"][0]["expected_amplicon_bp"], 420)

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

    def test_assured_bench_screening_csv_import(self) -> None:
        parsed = parse_assured_qc_export(SAMPLE_BENCH_SCREENING_CSV, "tp53_screening_decisions.csv")
        self.assertEqual(parsed["tool_used"], "ASSURED Bench (Trace Analysis)")
        self.assertEqual(parsed["preferred_sample"], "clone_1")
        self.assertEqual(parsed["decision"], "proceed_to_cloning")
        self.assertEqual(parsed["hdr_pct"], 88.0)
        self.assertEqual(parsed["total_indel_pct"], 6.0)
        self.assertIn("High HDR", parsed["notes"])

    def test_assured_bundle_qc_import(self) -> None:
        parsed = parse_assured_qc_export(SAMPLE_ASSURED_BUNDLE, "tp53_experiment.assured")
        self.assertEqual(parsed["tool_used"], "ASSURED Bench (Trace Analysis)")
        self.assertEqual(parsed["preferred_sample"], "clone_1")
        self.assertEqual(parsed["decision"], "proceed_to_cloning")
        self.assertEqual(parsed["total_indel_pct"], 94.5)
        self.assertIn("Clean homozygous knockout", parsed["notes"])


if __name__ == "__main__":
    unittest.main()

