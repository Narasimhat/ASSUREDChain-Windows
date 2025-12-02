"""Post-processing utilities to run after PDF generation.

This module provides automatic manifest synchronization to ensure
all generated PDFs are properly registered.
"""
from pathlib import Path
from typing import Optional
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from app.components.project_state import load_manifest, register_file  # noqa: E402
import hashlib  # noqa: E402


def ensure_pdf_registered(
    project_id: str,
    step: str,
    pdf_path: Path,
    timestamp: int,
) -> bool:
    """Ensure a PDF is registered in the manifest. Returns True if added, False if already exists."""
    if not pdf_path.exists():
        return False
    
    manifest = load_manifest(project_id)
    existing_reports = manifest.get("files", {}).get("reports", [])
    pdf_path_str = str(pdf_path)
    
    # Check if already registered
    already_registered = any(
        isinstance(e, dict) and 
        e.get("path") == pdf_path_str and 
        e.get("step") == step and
        e.get("type") == "pdf"
        for e in existing_reports
    )
    
    if already_registered:
        return False
    
    # Register the PDF
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    register_file(
        project_id,
        "reports",
        {
            "step": step,
            "path": pdf_path_str,
            "timestamp": timestamp,
            "digest": digest,
            "type": "pdf",
        },
    )
    return True
