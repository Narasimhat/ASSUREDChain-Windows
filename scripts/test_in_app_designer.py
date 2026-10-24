"""Unit tests for the In-App CRISPR Designer and Report Generator."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.components.in_app_designer import run_in_silico_design, save_design_reports
from app.components.assured_tools import parse_assured_design_export


class InAppDesignerTests(unittest.TestCase):
    def test_in_silico_design_tp53_flag(self) -> None:
        result = run_in_silico_design(
            gene="TP53",
            edit_type="ct",
            tag="FLAG",
            arm_length=400,
        )
        self.assertTrue(result.get("success"), "Design must succeed")
        dr = result.get("designResult") or {}
        self.assertEqual(dr.get("gene"), "TP53")
        self.assertEqual(dr.get("type"), "ct")
        self.assertTrue(len(dr.get("gs") or []) >= 1, "Must select at least 1 guide")
        self.assertTrue(bool(dr.get("donor") or dr.get("os")), "Must design donor template")
        self.assertTrue(len(dr.get("ps") or []) == 2, "Must design 2 primers")

        qc_spec = result.get("qcDesignSpec") or {}
        self.assertEqual(qc_spec.get("schema"), "assured-qc-design/1")

        svg = result.get("svg") or ""
        self.assertTrue(svg.startswith("<svg") and svg.strip().endswith("</svg>"))

        html_report = result.get("reportHtml") or ""
        self.assertTrue("<html" in html_report.lower())

        # Test parsing for Design Log
        parsed = parse_assured_design_export(qc_spec)
        self.assertEqual(parsed["mutation"]["gene"], "TP53")
        self.assertTrue(len(parsed["selected_guides"]) >= 1)

    def test_save_design_reports(self) -> None:
        result = run_in_silico_design(
            gene="TP53",
            edit_type="ct",
            tag="FLAG",
        )
        saved = save_design_reports(selected_project=None, design_output=result)
        html_file = saved["html_file"]
        json_file = saved["json_file"]

        self.assertTrue(html_file.exists(), "HTML report file must be written")
        self.assertTrue(json_file.exists(), "JSON spec file must be written")
        self.assertGreater(html_file.stat().st_size, 100)
        self.assertGreater(json_file.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()