"""
Generate an ICE batch Excel and a flat zip of ab1 files for a single project folder.

Usage example:
python generate_upload_package.py \
  --project-dir "Data/For_ICE_Analysis/Data/Clone Seq/Knockout/Sorcs1_KO1" \
  --guides "GATAATGTTACTCACAGACC,ATAAACTGCTCTCAATCTCC" \
  --donor "" \
  --control-hint "WT" \
  --zip-out "Data/For_ICE_Analysis/Sorcs1_KO1_upload.zip"
"""

import argparse
import zipfile
from pathlib import Path
from typing import List, Optional

import pandas as pd


def find_control(ab1_files: List[Path], control_file: Optional[str], control_hint: str) -> Path:
    if control_file:
        for f in ab1_files:
            if f.name == control_file:
                return f
        raise FileNotFoundError(f"Specified control file not found: {control_file}")

    hints = [h.strip().lower() for h in control_hint.split(",") if h.strip()]
    for f in ab1_files:
        name = f.name.lower()
        if any(h in name for h in hints):
            return f
    raise ValueError("Could not infer control file; specify --control-file or adjust --control-hint")


def build_excel(project_dir: Path, guides: str, donor: str, control: Path, ab1_files: List[Path], label_prefix: str,
                excel_out: Path) -> Path:
    rows = []
    experiments = [f for f in ab1_files if f != control]
    for idx, exp in enumerate(sorted(experiments)):
        rows.append({
            # Use the experiment filename (without .ab1) as the label for clearer ICE output
            "Label": exp.stem,
            "Control File": control.name,
            "Experiment File": exp.name,
            "Guide Sequence": guides,
            "Donor": donor or ""
        })
    df = pd.DataFrame(rows)
    df.to_excel(excel_out, index=False)
    return excel_out


def make_zip(zip_path: Path, files: List[Path]):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, arcname=p.name)


def main():
    parser = argparse.ArgumentParser(description="Create ICE batch Excel and zip for a project folder")
    parser.add_argument("--project-dir", required=True, help="Folder containing .ab1 files for one project")
    parser.add_argument("--guides", required=True, help="Guide sequence(s), comma-separated")
    parser.add_argument("--donor", default="", help="Donor sequence if applicable (HDR/SNP)")
    parser.add_argument("--control-file", default=None, help="Exact control filename (optional)")
    parser.add_argument("--control-hint", default="wt,control,ctrl,utf", help="Comma-separated substrings to detect control")
    parser.add_argument("--label-prefix", default=None, help="Label prefix; defaults to project folder name")
    parser.add_argument("--excel-out", default=None, help="Path for output Excel; defaults to <project_dir>/ice_batch.xlsx")
    parser.add_argument("--zip-out", default=None, help="Path for output zip; defaults to <project_dir>/upload.zip")
    args = parser.parse_args()

    project_dir = Path(args.project_dir)
    ab1_files = sorted(project_dir.glob("*.ab1"))
    if len(ab1_files) < 2:
        raise SystemExit(f"Need at least 2 .ab1 files in {project_dir}")

    control = find_control(ab1_files, args.control_file, args.control_hint)
    label_prefix = args.label_prefix or project_dir.name.replace(" ", "_")
    excel_out = Path(args.excel_out) if args.excel_out else project_dir / "ice_batch.xlsx"
    zip_out = Path(args.zip_out) if args.zip_out else project_dir / "upload.zip"

    excel_path = build_excel(project_dir, args.guides, args.donor, control, ab1_files, label_prefix, excel_out)
    make_zip(zip_out, [excel_path] + ab1_files)

    print(f"Excel written to: {excel_path}")
    print(f"Zip written to:   {zip_out}")
    print(f"Detected control: {control.name}")
    print(f"Experiments: {[p.name for p in ab1_files if p != control]}")


if __name__ == "__main__":
    main()
