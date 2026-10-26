#!/usr/bin/env python3
"""
End-to-end verification tests for the ASSURED-Biomni Co-Scientist Agent (A1)
and ASSURED-E1 Biomedical Environment.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from assistant.biomni_agent import A1, AssuredBiomniAgent, parse_scientific_intent
from assistant.biomni_tools import execute_tool, list_environment_catalog


def test_environment_catalog():
    catalog = list_environment_catalog()
    assert len(catalog) == 12, f"Expected 12 ASSURED-E1 tools, got {len(catalog)}"
    names = {t["name"] for t in catalog}
    assert "design_crispr_edit" in names
    assert "rank_ko_guides_lindel" in names
    assert "analyze_sanger_traces" in names
    assert "execute_bioinformatics_code" in names
    print("PASS: ASSURED-E1 environment catalog has 12 registered tools.")


def test_intent_parser_and_planner():
    intent_ko = parse_scientific_intent("Design a knockout for TP53 and check gnomAD constraint and Lindel frameshift")
    assert intent_ko["gene"] == "TP53"
    assert intent_ko["edit_type"] == "ko"
    assert intent_ko["wants_gnomad"] is True
    assert intent_ko["wants_lindel"] is True

    intent_snp = parse_scientific_intent("Design R176C point mutation knock-in in APOE and compare all enzymes")
    assert intent_snp["gene"] == "APOE"
    assert intent_snp["edit_type"] == "pm"
    assert intent_snp["mutation"] == "R176C"
    assert intent_snp["wants_enzyme_compare"] is True

    intent_tag = parse_scientific_intent("N-terminal EGFP tag knock-in for LMNA")
    assert intent_tag["gene"] == "LMNA"
    assert intent_tag["edit_type"] == "nt"
    assert intent_tag["tag"] == "T2A-EGFP"

    agent = A1(llm="none")
    plan = agent.build_plan("Design R176C point mutation knock-in in APOE", intent_snp)
    tool_names = [s["tool"] for s in plan]
    assert "fetch_gene_reference" in tool_names
    assert "design_crispr_edit" in tool_names
    print("PASS: Intent parser and multi-step execution planner work as expected.")


def test_core_tools_execution():
    ref = execute_tool("fetch_gene_reference", {"gene": "APOE"})
    assert ref.get("ok") is True and "Bundled verified GenBank" in ref.get("source", "")

    brunello = execute_tool("lookup_brunello_and_tags", {"gene": "TP53", "tag_query": "mNeonGreen"})
    assert brunello.get("ok") is True
    assert len(brunello.get("brunello_guides", [])) > 0

    lindel = execute_tool("rank_ko_guides_lindel", {"gene": "APOE", "top_n": 3})
    assert lindel.get("ok") is True
    assert lindel.get("total_candidates_ranked", 0) > 0
    assert len(lindel.get("top_guides", [])) == 3

    code_res = execute_tool(
        "execute_bioinformatics_code",
        {"language": "python", "code": "seq = 'ATGGCCCTG'\nprint('GC:', (seq.count('G')+seq.count('C'))/len(seq))"},
    )
    assert code_res.get("ok") is True
    assert "GC:" in code_res.get("stdout", "")
    print("PASS: Core ASSURED-E1 tools (reference, Brunello/FPbase, Lindel KO rank, sandbox code) succeeded.")


def test_agent_go_end_to_end():
    agent = A1(llm="none")
    res = agent.go("Design R176C SNP knock-in for APOE")
    assert res.get("ok") is True
    assert res["intent"]["gene"] == "APOE"
    assert res["intent"]["mutation"] == "R176C"
    assert res.get("design_output") is not None
    assert "ASSURED Biomni Co-Scientist Report" in res.get("reply", "")
    # Clean up temporary design report files generated during test
    for ref in res.get("references", []):
        p = Path(ref.get("path", ""))
        if p.is_file():
            p.unlink(missing_ok=True)
    shutil.rmtree(ROOT_DIR / "data" / "biomni_workspace", ignore_errors=True)
    print("PASS: A1.go() end-to-end execution completed and cleaned up.")


if __name__ == "__main__":
    test_environment_catalog()
    test_intent_parser_and_planner()
    test_core_tools_execution()
    test_agent_go_end_to_end()
    print("\nALL ASSURED-BIOMNI AGENT TESTS PASSED.")
