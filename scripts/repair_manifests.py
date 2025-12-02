#!/usr/bin/env python3
"""Automatically discover and register orphaned PDFs in project manifests.

Scans all projects for PDF files in reports directories that exist on disk
but are not registered in manifest.json. Registers them with proper step,
timestamp, digest, and type metadata.

Usage:
  python scripts/repair_manifests.py                    # repair all projects
  python scripts/repair_manifests.py --project <id>     # repair single project
  python scripts/repair_manifests.py --dry-run          # preview without changes
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

sys.path.append(str(Path(__file__).resolve().parents[1]))
from app.components.project_state import list_projects, load_manifest, manifest_path, project_subdir  # noqa: E402

STEP_PATTERNS = {
    "charter": r"charter",
    "design": r"design",
    "delivery": r"delivery",
    "assessment": r"assessment",
    "cloning": r"cloning",
    "seed_bank": r"seed[-_]?bank",
    "master_bank_registry": r"master[-_]?bank",
    "screening": r"screening",
    "form_z": r"form[-_]?z",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_timestamp_from_filename(filename: str) -> int:
    """Extract timestamp from standardized filenames like project-step-1234567890.pdf"""
    match = re.search(r"[-_](\d{10,})[-_\.]", filename)
    if match:
        return int(match.group(1))
    # Fallback to file modification time
    return 0


def detect_step_from_path(pdf_path: Path) -> str:
    """Detect step name from path components"""
    path_str = str(pdf_path).lower()
    for step, pattern in STEP_PATTERNS.items():
        if re.search(pattern, path_str):
            return step
    return "misc"


def find_orphaned_pdfs(project_id: str) -> List[Dict[str, object]]:
    """Find PDFs in reports/ that aren't registered in manifest"""
    manifest = load_manifest(project_id)
    reports_dir = project_subdir(project_id, "reports")
    
    # Get all registered PDF paths
    registered_paths = set()
    for entry in manifest.get("files", {}).get("reports", []):
        if isinstance(entry, dict) and entry.get("type") == "pdf":
            registered_paths.add(Path(entry.get("path", "")).resolve())
    
    # Scan for all PDFs in reports directory
    orphans = []
    if reports_dir.exists():
        for pdf_file in reports_dir.rglob("*.pdf"):
            if pdf_file.resolve() not in registered_paths:
                step = detect_step_from_path(pdf_file)
                timestamp = extract_timestamp_from_filename(pdf_file.name)
                if timestamp == 0:
                    timestamp = int(pdf_file.stat().st_mtime)
                
                orphans.append({
                    "step": step,
                    "path": str(pdf_file),
                    "timestamp": timestamp,
                    "digest": sha256_file(pdf_file),
                    "type": "pdf",
                })
    
    return orphans


def repair_manifest(project_id: str, dry_run: bool = False) -> Dict[str, object]:
    """Register orphaned PDFs in the manifest"""
    orphans = find_orphaned_pdfs(project_id)
    
    if not orphans:
        return {"project_id": project_id, "status": "clean", "added": 0}
    
    if dry_run:
        return {
            "project_id": project_id,
            "status": "dry-run",
            "would_add": len(orphans),
            "files": [{"step": o["step"], "path": Path(o["path"]).name} for o in orphans],
        }
    
    # Load and update manifest
    manifest_file = manifest_path(project_id)
    manifest = load_manifest(project_id)
    reports = manifest.setdefault("files", {}).setdefault("reports", [])
    
    for orphan in orphans:
        reports.append(orphan)
    
    manifest_file.write_text(json.dumps(manifest, indent=2))
    
    return {
        "project_id": project_id,
        "status": "repaired",
        "added": len(orphans),
        "files": [{"step": o["step"], "path": Path(o["path"]).name} for o in orphans],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair project manifests by registering orphaned PDFs.")
    parser.add_argument("--project", dest="project", help="Repair specific project ID")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying manifests")
    args = parser.parse_args()

    if args.project:
        projects = [args.project]
    else:
        projects = list_projects()
    
    if not projects:
        print(json.dumps({"status": "no-projects"}))
        return
    
    results = []
    for project_id in projects:
        result = repair_manifest(project_id, dry_run=args.dry_run)
        results.append(result)
        if result["status"] in ("repaired", "dry-run") and result.get("added", 0) > 0:
            print(f"\n{project_id}:")
            for file_info in result.get("files", []):
                print(f"  [{file_info['step']}] {file_info['path']}")
    
    summary = {
        "total_projects": len(results),
        "repaired": sum(1 for r in results if r["status"] == "repaired"),
        "clean": sum(1 for r in results if r["status"] == "clean"),
        "total_added": sum(r.get("added", 0) for r in results),
    }
    
    print(f"\n{'=' * 60}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
