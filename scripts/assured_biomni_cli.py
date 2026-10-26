#!/usr/bin/env python3
"""Standalone CLI runner for the ASSURED-Biomni Co-Scientist Agent (`A1`).

Examples:
    python scripts/assured_biomni_cli.py "Design a knockout for DIPK2A, check gnomAD and UniProt, rank guides with Lindel, and compare enzymes"
    python scripts/assured_biomni_cli.py --list-tools
    python scripts/assured_biomni_cli.py --json "Design APOE R176C point mutation, check ClinVar, and deconvolve demo Sanger traces"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.biomni_agent import A1  # noqa: E402
from assistant.biomni_tools import list_environment_catalog  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ASSURED-Biomni Autonomous Biomedical Co-Scientist CLI (A1 + ASSURED-E1 Environment)"
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default="",
        help="Natural-language biomedical goal for the agent to plan and execute.",
    )
    parser.add_argument("--project", default=None, help="Optional ASSUREDChain project ID.")
    parser.add_argument("--llm", default="auto", help="LLM mode: auto, openai, local, or deterministic.")
    parser.add_argument("--json", action="store_true", help="Emit full JSON trace instead of Markdown report.")
    parser.add_argument("--list-tools", action="store_true", help="List all tools in the ASSURED-E1 environment.")

    args = parser.parse_args()

    if args.list_tools:
        catalog = list_environment_catalog()
        print(f"ASSURED-E1 Biomedical Environment ({len(catalog)} tools registered):\n")
        for idx, t in enumerate(catalog, 1):
            print(f"  {idx:2d}. [{t['category']}] {t['name']}")
            print(f"      {t['description']}")
        return

    if not args.prompt.strip():
        parser.error("Provide a natural-language prompt or pass --list-tools.")

    agent = A1(llm=args.llm, project_id=args.project)
    result = agent.go(args.prompt, project_id=args.project)

    if args.json:
        # Remove raw_design_output / raw_qc_output for clean stdout JSON
        clean = {k: v for k, v in result.items() if k not in ("design_output", "qc_output")}
        print(json.dumps(clean, indent=2))
    else:
        print("=" * 78)
        print(f"ASSURED-Biomni Execution Plan ({len(result['steps'])} steps in {result['total_duration_ms']} ms)")
        print("=" * 78)
        for s in result["steps"]:
            status_icon = "✓" if s["status"] == "completed" else "!"
            print(f"  [{status_icon}] Step {s['step']}: {s['title']} ({s['tool']} · {s['duration_ms']} ms)")
        print("\n" + result["reply"])


if __name__ == "__main__":
    main()
