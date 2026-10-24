"""Test suite for In-App Sanger Trace QC and Clone Assessment."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.components.in_app_analyzer import (
    run_in_silico_assessment,
    save_assessment_reports,
    apply_assessment_to_session,
    get_demo_trace_dir,
)


def test_in_app_assessment_e2e():
    demo_dir = get_demo_trace_dir()
    ctrl_path = demo_dir / "apoe_control.ab1"
    sample_paths = [
        demo_dir / "apoe_snp_clone_hom_edited.ab1",
        demo_dir / "apoe_snp_clone_het_edited.ab1",
        demo_dir / "apoe_snp_clone_partial_het_edited.ab1",
    ]
    design_path = demo_dir / "apoe_r176c_snp.design.json"

    assert ctrl_path.exists(), f"Control trace missing: {ctrl_path}"
    assert design_path.exists(), f"Design spec missing: {design_path}"
    for sp in sample_paths:
        assert sp.exists(), f"Sample trace missing: {sp}"

    print(f"Running in-silico trace assessment on {len(sample_paths)} samples...")
    output = run_in_silico_assessment(
        control_path=ctrl_path,
        sample_paths=sample_paths,
        qc_design_path=design_path,
        workflow="snp",
    )

    assert output.get("success") is True, f"Execution failed: {output.get('error')}"
    assert output.get("totalSamples") == 3, f"Expected 3 samples, got {output.get('totalSamples')}"
    assert output.get("acceptedCount") >= 1, f"Expected at least 1 accepted sample, got {output.get('acceptedCount')}"

    pref = output.get("preferred")
    assert pref is not None, "Preferred clone not found"
    assert pref.get("name") == "apoe_snp_clone_hom_edited", f"Unexpected preferred clone: {pref.get('name')}"
    assert pref.get("decision") == "accept", f"Expected decision accept, got {pref.get('decision')}"
    assert pref.get("intendedEditPct", 0) > 90, f"Expected HDR > 90%, got {pref.get('intendedEditPct')}"

    print(f"Top clone: {pref.get('name')} -> Decision: {pref.get('decision')}, HDR: {pref.get('intendedEditPct')}%")

    # Verify CSV and SVG reports
    assert "screeningCsv" in output and len(output["screeningCsv"]) > 100
    assert "reportHtml" in output and len(output["reportHtml"]) > 1000
    assert "selectionFigureSvg" in output and len(output["selectionFigureSvg"]) > 500

    # Test saving reports
    saved = {}
    try:
        saved = save_assessment_reports(selected_project=None, qc_output=output)
        assert "csv" in saved and saved["csv"].exists()
        assert "html" in saved and saved["html"].exists()
        assert "json" in saved and saved["json"].exists()
        print(f"Saved reports: CSV={saved['csv'].name}, HTML={saved['html'].name}, JSON={saved['json'].name}")
    finally:
        for p in saved.values():
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    print("All In-App Assessment tests passed successfully!")


if __name__ == "__main__":
    test_in_app_assessment_e2e()