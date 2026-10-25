"""End-to-End Pipeline Test for ASSUREDChain 6-Stage FDA Cell Line Delivery Record."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sys
import time
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root to sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.components.lifecycle_header import is_stage_completed
from app.components.project_state import (
    PROJECTS_DIR,
    ensure_dirs,
    load_manifest,
    load_project_meta,
    project_subdir,
    register_file,
    update_project_meta,
)
from scripts.regenerate_binder import build_binder

stage6 = importlib.import_module("app.pages.6_Master_Bank_and_CoA")
generate_fda_coa_pdf = stage6.generate_fda_coa_pdf
generate_fda_dual_clone_coa = stage6.generate_fda_dual_clone_coa


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_full_e2e_pipeline() -> None:
    test_pid = "TEST-FDA-SORCS1-E2E"
    p_dir = PROJECTS_DIR / test_pid
    if p_dir.exists():
        shutil.rmtree(p_dir)
    ensure_dirs(test_pid)

    print(f"=== Starting E2E Pipeline Test for {test_pid} ===")

    # ----------------------------------------------------
    # STAGE 1: Project Request & Target Product Profile (TPP)
    # ----------------------------------------------------
    print("[1/6] Testing Stage 1: Project Request & TPP...")
    now_ts = int(time.time())
    stage1_meta = {
        "project_id": test_pid,
        "project_name": test_pid,
        "target_gene": "SORCS1",
        "edit_program": "HDR Knock-In",
        "mutation_label": "c.214C>T (p.R72W)",
        "cell_line": "BIHi005-A",
        "clone_a_id": "BIHi005-A-SORCS1-C1",
        "clone_b_id": "BIHi005-A-SORCS1-C2",
        "owner": "Dr. Narasimha Telugu",
        "compliance_tier": "FDA CBER/CDER Human Cell Therapy (IND/Phase 1)",
        "stage_1_locked_at": now_ts,
        "active_stage": 2,
    }
    update_project_meta(test_pid, stage1_meta)
    meta = load_project_meta(test_pid)
    assert is_stage_completed(meta, 1), "Stage 1 should be completed"
    print("  [OK] Stage 1 verified & locked.")

    # ----------------------------------------------------
    # STAGE 2: CRISPR In-Silico Design Studio
    # ----------------------------------------------------
    print("[2/6] Testing Stage 2: CRISPR Design Studio...")
    design_payload = {
        "gene": "SORCS1",
        "guides": [{"name": "sgSORCS1-ex2", "seq": "GTCGACCCAGTGCTGAGCGG", "pam": "TGG", "cfd": 98.4}],
        "donors": [{"name": "ssODN-R72W", "seq": "ACGT...TGG...TGGA", "pam_mutation": "Silent PAM block"}],
        "primers": [{"name": "Fwd", "seq": "ACGT"}, {"name": "Rev", "seq": "TGCA"}],
    }
    design_json = json.dumps(design_payload)
    design_hash = hashlib.sha256(design_json.encode("utf-8")).hexdigest()
    update_project_meta(
        test_pid,
        {
            "design_locked_at": now_ts,
            "design_hash": design_hash,
            "compliance": design_payload,
            "active_stage": 3,
        },
    )
    meta = load_project_meta(test_pid)
    assert is_stage_completed(meta, 2), "Stage 2 should be completed"
    print("  [OK] Stage 2 verified & locked.")

    # ----------------------------------------------------
    # STAGE 3: Transfection & Bulk In-Process Control (IPC)
    # ----------------------------------------------------
    print("[3/6] Testing Stage 3: Transfection & Bulk QC...")
    transfection_data = {
        "cell_count": 250000,
        "viability_pct": 92.0,
        "rnp_ratio": "1:1.2",
        "program": "CA-137",
        "bulk_hdr_pct": 38.5,
        "bulk_indel_pct": 42.0,
    }
    bulk_json = json.dumps(transfection_data)
    bulk_hash = hashlib.sha256(bulk_json.encode("utf-8")).hexdigest()
    update_project_meta(
        test_pid,
        {
            "transfection": transfection_data,
            "stage3_bulk_qc_hash": bulk_hash,
            "active_stage": 4,
        },
    )
    meta = load_project_meta(test_pid)
    assert is_stage_completed(meta, 3), "Stage 3 should be completed"
    print("  [OK] Stage 3 verified & locked.")

    # ----------------------------------------------------
    # STAGE 4: Single-Cell Cloning & AssuredQC Screening
    # ----------------------------------------------------
    print("[4/6] Testing Stage 4: Single-Cell Cloning & AssuredQC Screening...")
    cloning_payload = {
        "deposition_method": "FACS Single-Cell Index Sorting",
        "day0_monoclonality_proof": "Confirmed (single cell per well imaged at 4x)",
        "outgrowth_wells": 48,
        "selected_clones": ["BIHi005-A-SORCS1-C1", "BIHi005-A-SORCS1-C2"],
        "screening": {
            "tool": "AssuredQC",
            "workflow": "snp",
            "total_samples": 3,
            "accepted_count": 2,
            "preferred_sample": "BIHi005-A-SORCS1-C1",
        },
    }
    cloning_json = json.dumps(cloning_payload)
    cloning_hash = hashlib.sha256(cloning_json.encode("utf-8")).hexdigest()
    update_project_meta(
        test_pid,
        {
            "clone_a_id": "BIHi005-A-SORCS1-C1",
            "clone_b_id": "BIHi005-A-SORCS1-C2",
            "cloning_locked_at": now_ts,
            "cloning_hash": cloning_hash,
            "screening_hash": cloning_hash,
            "active_stage": 5,
        },
    )
    meta = load_project_meta(test_pid)
    assert is_stage_completed(meta, 4), "Stage 4 should be completed"
    print("  [OK] Stage 4 verified & locked.")

    # ----------------------------------------------------
    # STAGE 5: Dual-Clone Comprehensive Release QC
    # ----------------------------------------------------
    print("[5/6] Testing Stage 5: Dual-Clone Quality Control...")
    clone_a_qc = {
        "id": "BIHi005-A-SORCS1-C1",
        "ontarget_verdict": "PASS: 100% Intended Edit (Homozygous/Targeted)",
        "hdr_pct": 96.0,
        "offtarget_verdict": "PASS: Top 3 Loci Verified Negative / WT",
        "karyotype_verdict": "PASS: Normal Diploid 46,XY (No Clonal Aneuploidies)",
        "sterility_verdict": "PASS: Mycoplasma Negative & Culture Sterile",
        "str_verdict": "PASS: 100% Genetic Match to Parental Master",
        "viability_pct": 89,
        "pluripotency_verdict": "PASS: >90% Marker Positive",
        "released": True,
    }
    clone_b_qc = {
        "id": "BIHi005-A-SORCS1-C2",
        "ontarget_verdict": "PASS: 100% Intended Edit (Homozygous/Targeted)",
        "hdr_pct": 94.5,
        "offtarget_verdict": "PASS: Top 3 Loci Verified Negative / WT",
        "karyotype_verdict": "PASS: Normal Diploid 46,XY (No Clonal Aneuploidies)",
        "sterility_verdict": "PASS: Mycoplasma Negative & Culture Sterile",
        "str_verdict": "PASS: 100% Genetic Match to Parental Master",
        "viability_pct": 87,
        "pluripotency_verdict": "PASS: >90% Marker Positive",
        "released": True,
    }
    qc_payload = {
        "clone_a": clone_a_qc,
        "clone_b": clone_b_qc,
        "overall_verdict": "RELEASE APPROVED: Both Clone A and Clone B meet all FDA Critical Quality Attributes.",
        "inspector": "Dr. Narasimha Telugu",
    }
    qc_json = json.dumps(qc_payload)
    qc_hash = hashlib.sha256(qc_json.encode("utf-8")).hexdigest()
    update_project_meta(
        test_pid,
        {
            "dual_clone_qc": qc_payload,
            "stage5_qc_hash": qc_hash,
            "active_stage": 6,
        },
    )
    meta = load_project_meta(test_pid)
    assert is_stage_completed(meta, 5), "Stage 5 should be completed"
    print("  [OK] Stage 5 verified & locked.")

    # ----------------------------------------------------
    # STAGE 6: Master Banking, Dual CoA (DOCX + PDF) & FAIR Release
    # ----------------------------------------------------
    print("[6/6] Testing Stage 6: Master Banking, Dual CoA & FAIR Release...")
    banking_data = {
        "vials_a": 15,
        "passage_a": 18,
        "ln2_a": "Tank 2, Rack 3, Box A, Slots 1-15",
        "vials_b": 15,
        "passage_b": 18,
        "ln2_b": "Tank 2, Rack 3, Box B, Slots 1-15",
        "cryo_medium": "Bambanker (Serum-Free)",
        "freeze_date": date.today().isoformat(),
    }
    update_project_meta(test_pid, {"banking": banking_data})

    reports_dir = project_subdir(test_pid, "reports", "release")

    # 1. Generate DOCX CoA
    doc = generate_fda_dual_clone_coa(meta, clone_a_qc, clone_b_qc, banking_data, "Dr. Narasimha Telugu")
    docx_path = reports_dir / f"{test_pid}_Certificate_of_Analysis_{now_ts}.docx"
    doc.save(str(docx_path))
    assert docx_path.exists() and docx_path.stat().st_size > 1000, "DOCX CoA must be generated"
    docx_hash = sha256_bytes(docx_path.read_bytes())
    register_file(test_pid, "reports", docx_path, sha256=docx_hash, mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", context={"step": "coa", "stage": 6})
    print(f"  [OK] DOCX CoA generated: {docx_path.name} ({docx_path.stat().st_size} bytes)")

    # 2. Generate PDF CoA
    pdf_path = reports_dir / f"{test_pid}_Certificate_of_Analysis_{now_ts}.pdf"
    generate_fda_coa_pdf(meta, clone_a_qc, clone_b_qc, banking_data, "Dr. Narasimha Telugu", pdf_path)
    assert pdf_path.exists() and pdf_path.stat().st_size > 1000, "PDF CoA must be generated"
    pdf_hash = sha256_bytes(pdf_path.read_bytes())
    register_file(test_pid, "reports", pdf_path, sha256=pdf_hash, mime="application/pdf", context={"step": "coa", "stage": 6})
    print(f"  [OK] PDF CoA generated: {pdf_path.name} ({pdf_path.stat().st_size} bytes)")

    # 3. FAIR Blockchain Release Passport
    release_passport = {
        "project_id": test_pid,
        "gene": "SORCS1",
        "parental_cell_line": "BIHi005-A",
        "delivered_clones": ["BIHi005-A-SORCS1-C1", "BIHi005-A-SORCS1-C2"],
        "stage_hashes": {
            "stage1": now_ts,
            "stage2": design_hash,
            "stage3": bulk_hash,
            "stage4": cloning_hash,
            "stage5": qc_hash,
            "stage6_pdf": pdf_hash,
        },
        "final_verdict": "RELEASE APPROVED",
    }
    passport_json = json.dumps(release_passport, indent=2)
    passport_hash = hashlib.sha256(passport_json.encode("utf-8")).hexdigest()

    update_project_meta(
        test_pid,
        {
            "coa_generated_at": now_ts,
            "coa_hash": pdf_hash,
            "coa_pdf_file": pdf_path.name,
            "coa_docx_file": docx_path.name,
            "final_release_hash": passport_hash,
            "final_release_at": now_ts,
            "status": "RELEASED",
        },
    )

    meta = load_project_meta(test_pid)
    assert is_stage_completed(meta, 6), "Stage 6 should be completed"
    print("  [OK] Stage 6 verified & locked.")

    # ----------------------------------------------------
    # VERIFY ALL 6 STAGES COMPLETED
    # ----------------------------------------------------
    for s in range(1, 7):
        assert is_stage_completed(meta, s), f"Stage {s} must be marked as completed"
    print("  [OK] All 6 stages confirmed completed in lifecycle tracker!")

    # ----------------------------------------------------
    # BINDER COMPILATION VERIFICATION
    # ----------------------------------------------------
    print("Verifying consolidated batch binder generation...")
    binder_result = build_binder(test_pid)
    b_path_str = binder_result.get("binder", {}).get("path")
    assert b_path_str, "Binder PDF path must be returned"
    b_path = Path(b_path_str)
    assert b_path.exists() and b_path.stat().st_size > 0, "Binder PDF must exist on disk"
    print(f"  [OK] Binder verified on disk: {b_path.name} ({b_path.stat().st_size} bytes)")

    # Cleanup
    shutil.rmtree(p_dir)
    print(f"=== E2E Pipeline Test PASSED for all 6 Stages! Cleaned up {test_pid} ===")


if __name__ == "__main__":
    test_full_e2e_pipeline()