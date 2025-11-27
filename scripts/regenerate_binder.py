#!/usr/bin/env python3
"""Standalone ASSURED binder regeneration.

Collects step PDFs for a given project, validates them with PyPDF2, merges
into a single binder PDF, registers the artifact in the manifest, and prints
JSON summary to stdout.

Usage:
  python scripts/regenerate_binder.py --project <project_id>
If --project is omitted, the first project found will be used.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

from PyPDF2 import PdfReader

# Local imports
sys.path.append(str(Path(__file__).resolve().parents[1]))  # add repo root for app.components
from app.components.project_state import list_projects, load_manifest, project_subdir, register_file  # noqa: E402
from app.components.file_utils import build_step_filename, merge_pdfs  # noqa: E402

ASSURED_STEP_ORDER = [
    ("charter", "Project Charter"),
    ("design", "Design"),
    ("delivery", "Delivery"),
    ("assessment", "Assessment"),
    ("cloning", "Cloning"),
    ("seed_bank", "Seed Bank"),
    ("master_bank_registry", "Master Bank Registry"),
]
STEP_LABEL_LOOKUP = {s: l for s, l in ASSURED_STEP_ORDER}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ordered_pdf_entries(manifest: Dict[str, object]) -> List[Dict[str, object]]:
    reports = manifest.get("files", {}).get("reports", [])
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for entry in reports:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") and entry.get("type") != "pdf":
            continue
        step = entry.get("step") or "misc"
        grouped.setdefault(step, []).append(entry)
    for entries in grouped.values():
        entries.sort(key=lambda e: e.get("timestamp", 0))
    ordered: List[Dict[str, object]] = []
    for step, _ in ASSURED_STEP_ORDER:
        ordered.extend(grouped.pop(step, []))
    remaining: List[Dict[str, object]] = []
    for entries in grouped.values():
        remaining.extend(entries)
    remaining.sort(key=lambda e: e.get("timestamp", 0))
    ordered.extend(remaining)
    return ordered


def build_binder(project_id: str) -> Dict[str, object]:
    manifest = load_manifest(project_id)
    pdf_entries = ordered_pdf_entries(manifest)
    
    # De-duplicate: keep only latest PDF per step
    latest_per_step: Dict[str, Dict[str, object]] = {}
    for entry in pdf_entries:
        if entry.get("step") == "assured_binder":
            continue
        path_str = entry.get("path") or ""
        p = Path(path_str)
        if not p.exists():
            continue
        step_id = entry.get("step") or "misc"
        timestamp = entry.get("timestamp", 0)
        if step_id not in latest_per_step or timestamp > latest_per_step[step_id].get("timestamp", 0):
            label = STEP_LABEL_LOOKUP.get(step_id, step_id.replace("_", " ").title())
            latest_per_step[step_id] = {"step": step_id, "label": label, "path": p, "timestamp": timestamp}
    
    # Build candidates list in proper order
    candidates = []
    for step, _ in ASSURED_STEP_ORDER:
        if step in latest_per_step:
            candidates.append(latest_per_step[step])
    # Add remaining steps not in order
    for step_id, entry in latest_per_step.items():
        if not any(s == step_id for s, _ in ASSURED_STEP_ORDER):
            candidates.append(entry)

    if not candidates:
        return {"status": "no-candidates", "project_id": project_id}

    valid_paths: List[Path] = []
    skipped: List[str] = []
    for item in candidates:
        p = item["path"]
        try:
            with p.open("rb") as fh:
                PdfReader(fh)
            valid_paths.append(p)
        except Exception:
            skipped.append(p.name)

    if not valid_paths:
        return {
            "status": "none-valid",
            "project_id": project_id,
            "skipped": skipped,
        }

    binder_dir = project_subdir(project_id, "reports", "binders")
    ts = int(time.time())
    binder_filename = build_step_filename(project_id, "assured-binder", ts)
    binder_path = binder_dir / binder_filename
    merge_pdfs(valid_paths, binder_path)
    digest = sha256_bytes(binder_path.read_bytes())
    payload = {
        "step": "assured_binder",
        "path": str(binder_path),
        "timestamp": ts,
        "digest": digest,
        "type": "pdf",
        "skipped": skipped,
        "included_count": len(valid_paths),
    }
    register_file(project_id, "reports", payload)
    return {
        "status": "ok",
        "project_id": project_id,
        "binder": payload,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate ASSURED binder for a project.")
    parser.add_argument("--project", dest="project", help="Project ID")
    args = parser.parse_args()

    project_id = args.project
    if not project_id:
        projects = list_projects()
        if not projects:
            print(json.dumps({"status": "no-projects"}))
            return
        project_id = projects[0]

    result = build_binder(project_id)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
