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
    clean_dna,
    parse_design_file,
)


def test_clean_dna():
    print("Testing clean_dna helper...")
    assert clean_dna("  acgtNN-123\n") == "ACGTNN"
    assert clean_dna("TGTCAAAGCTGACGTCCAGG") == "TGTCAAAGCTGACGTCCAGG"
    assert clean_dna("") == ""
    print("[PASS] clean_dna tests passed.")


def test_parse_design_file():
    print("Testing parse_design_file on JSON spec and HTML report...")
    demo_dir = get_demo_trace_dir()
    json_path = demo_dir / "apoe_r176c_snp.design.json"
    assert json_path.exists(), f"Missing {json_path}"

    res_json = parse_design_file(path=json_path)
    assert res_json.get("success") is True
    assert res_json.get("kind") == "spec"
    assert len(res_json.get("guides", [])) >= 1
    assert res_json.get("nuclease") == "SpCas9"
    print(f"[PASS] Parsed JSON spec: {res_json.get('label')}, guides: {len(res_json.get('guides'))}")

    html_path = ROOT / "data" / "projects" / "Test" / "reports" / "design" / "dipk2a_ko_1792932371_report.html"
    if html_path.exists():
        res_html = parse_design_file(path=html_path)
        assert res_html.get("success") is True
        assert res_html.get("kind") == "report"
        assert len(res_html.get("guides", [])) == 2
        assert res_html.get("editKind") == "deletion"
        print(f"[PASS] Parsed HTML report: {res_html.get('label')}, guides: {len(res_html.get('guides'))}")
    else:
        print(f"Note: HTML fixture at {html_path} not found, skipping HTML path test.")


def test_manual_sequence_deconvolution():
    print("Testing deconvolution using manual sequence input (no design file)...")
    demo_dir = get_demo_trace_dir()
    ctrl_path = demo_dir / "apoe_control.ab1"
    sample_path = demo_dir / "apoe_snp_clone_hom_edited.ab1"

    # Guide spacer and donor ssODN matching APOE R176C
    guide_seq = "ACACTGCCAGGCGCTTCTGC"
    donor_seq = "TCGGCCAGAGCACCGAGGAGCTGCGGGTGCGCCTCGCCTCCCACCTGCGCAAGCTGCGTAAGCGGCTCCTCCGCGATGCCGATGACCTCCAAAAGTGCCTGGCTGTCTACCAGGCCGGGGCCCGCGA"

    output = run_in_silico_assessment(
        control_path=ctrl_path,
        sample_paths=[sample_path],
        guides=[guide_seq],
        donor=donor_seq,
        workflow="snp",
        gene="APOE",
        nuclease="SpCas9",
        options={"goal": "homozygous"},
    )

    assert output.get("success") is True, f"Manual sequence deconvolution failed: {output.get('error')}"
    assert output.get("totalSamples") == 1
    pref = output.get("preferred")
    assert pref is not None
    assert pref.get("decision") == "accept"
    assert pref.get("intendedEditPct", 0) > 90
    print(f"[PASS] Manual sequence deconvolution succeeded: HDR={pref.get('intendedEditPct')}%, Decision={pref.get('decision')}")


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
    test_clean_dna()
    test_parse_design_file()
    test_manual_sequence_deconvolution()
    test_in_app_assessment_e2e()