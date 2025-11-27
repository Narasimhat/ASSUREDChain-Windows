import mimetypes
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.components.project_state import register_file

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGO_PATH = ROOT / "assets" / "assured_logo.png"

try:
    from PyPDF2 import PdfMerger
except ImportError:  # pragma: no cover
    PdfMerger = None


def save_uploaded_file(
    project_id: Optional[str],
    upload,
    dest_dir: Path,
    category: str,
    context: Optional[Dict[str, Any]] = None,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time() * 1000)
    raw_name = getattr(upload, "name", "") or "upload"
    base_name = raw_name.splitlines()[0][:200]
    original = Path(base_name)
    suffix = original.suffix if len(original.suffix) <= 10 else ""
    unique_slug = uuid.uuid4().hex[:10]
    safe_name = f"{timestamp}_{unique_slug}{suffix}"
    path = dest_dir / safe_name
    try:
        path.write_bytes(upload.getbuffer())
    except OSError:
        fallback = dest_dir / f"{timestamp}_{uuid.uuid4().hex}"
        fallback.write_bytes(upload.getbuffer())
        path = fallback
    if project_id:
        register_file(
            project_id,
            category,
            {
                "filename": upload.name,
                "stored_as": str(path),
                "timestamp": timestamp,
                **(context or {}),
            },
        )
    return path


def _slugify(value: Optional[str], default: str = "project") -> str:
    if not value:
        return default
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip())
    cleaned = cleaned.strip("-")
    return cleaned or default


def build_step_filename(
    project_id: Optional[str],
    step_slug: str,
    timestamp: int,
    extension: str = "pdf",
) -> str:
    project_part = _slugify(project_id, default="project")
    step_part = _slugify(step_slug, default="step")
    return f"{project_part}-{step_part}-{timestamp}.{extension.lstrip('.')}"


def preview_file(path: Path, label: Optional[str] = None, key_prefix: str = "") -> None:
    mime, _ = mimetypes.guess_type(path.name)
    label = label or path.name
    if mime and mime.startswith("image/"):
        st.image(str(path), caption=label, use_container_width=True)
    elif mime == "application/pdf":
        with path.open("rb") as handle:
            st.download_button(
                f"Download {label}",
                handle,
                file_name=path.name,
                key=f"{key_prefix}_download_{path.name}",
            )
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            st.caption(f"Saved file: {path}")
        else:
            preview = text[:1000]
            if len(text) > 1000:
                preview += "\n..."
            st.text_area(f"{label} preview", preview, height=200, key=f"{key_prefix}_preview_{path.name}")


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value)
    # Wrap long unbroken strings (e.g., nucleotide sequences) so they render fully in PDFs
    if len(text) > 60 and "\n" not in text and not text.startswith("http"):
        chunks = [text[i : i + 40] for i in range(0, len(text), 40)]
        text = "<br/>".join(chunks)
    return text


def _collect_file_paths(snapshot: Any) -> List[Tuple[str, Path]]:
    entries: List[Tuple[str, Path]] = []
    seen: set[Path] = set()

    def walk(node: Any, label: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                next_label = f"{label}.{key}" if label else key
                walk(value, next_label)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                next_label = f"{label}[{idx}]"
                walk(item, next_label)
        elif isinstance(node, str):
            candidate = Path(node)
            if candidate.exists() and candidate.is_file() and candidate not in seen:
                entries.append((label or candidate.name, candidate))
                seen.add(candidate)

    walk(snapshot, "")
    return entries


def _styled_table(data: List[List[Paragraph]], col_widths: List[int]) -> Table:
    table = Table(data, colWidths=col_widths, repeatRows=1 if len(data) > 1 else 0)
    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f5")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#222222")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 0), "LEFT"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("BOX", (0, 0), (-1, -1), 0.25, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if len(data) > 2:
        for row in range(1, len(data)):
            if row % 2 == 0:
                table_style.append(("BACKGROUND", (0, row), (-1, row), colors.HexColor("#fafafa")))
    table.setStyle(TableStyle(table_style))
    return table


def snapshot_to_pdf(
    snapshot: Dict[str, Any],
    pdf_path: Path,
    title: str,
    image_paths: Optional[Iterable[Path]] = None,
    logo_path: Optional[Path] = None,
) -> Path:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=50,
        bottomMargin=40,
    )
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("Body", parent=styles["BodyText"], spaceAfter=4)
    bold_style = ParagraphStyle("BodyBold", parent=body_style, fontName="Helvetica-Bold")
    caption_style = ParagraphStyle(
        "Caption",
        parent=styles["Normal"],
        fontSize=8,
        italic=True,
        alignment=1,
        textColor=colors.HexColor("#555555"),
    )
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#666666"),
        spaceBefore=12,
    )

    heading_styles = {
        1: styles["Heading2"],
        2: styles["Heading3"],
        3: styles["Heading4"],
        4: styles["Heading5"],
    }

    elements: List[Any] = []
    candidate_logo: Optional[Path] = None
    if logo_path is not None:
        candidate_logo = Path(logo_path)
    elif DEFAULT_LOGO_PATH.exists():
        candidate_logo = DEFAULT_LOGO_PATH

    if candidate_logo and candidate_logo.exists():
        try:
            img = ImageReader(str(candidate_logo))
            img_width, img_height = img.getSize()
            max_width = 180
            max_height = 80
            scale = min(max_width / img_width, max_height / img_height, 1.0)
            elements.append(
                Image(
                    str(candidate_logo),
                    width=img_width * scale,
                    height=img_height * scale,
                )
            )
            elements.append(Spacer(1, 12))
        except Exception:
            pass

    elements.append(Paragraph(title, styles["Title"]))
    elements.append(Spacer(1, 14))

    def render_scalar_table(rows: List[Tuple[str, Any]]) -> None:
        if not rows:
            return
        table_data: List[List[Paragraph]] = [
            [Paragraph("Field", bold_style), Paragraph("Value", bold_style)]
        ]
        for key, value in rows:
            table_data.append(
                [
                    Paragraph(str(key), bold_style),
                    Paragraph(_format_value(value), body_style),
                ]
            )
        elements.append(_styled_table(table_data, [190, 310]))
        elements.append(Spacer(1, 12))

    def _column_widths(pair_count: int) -> List[int]:
        if pair_count <= 1:
            return [190, 310]
        return [120, 140, 120, 140]

    def _grid_table(items: List[Tuple[str, Any]], pair_count: int) -> List[List[Paragraph]]:
        pair_count = max(1, pair_count)
        header: List[Paragraph] = []
        for _ in range(pair_count):
            header.append(Paragraph("Field", bold_style))
            header.append(Paragraph("Value", bold_style))
        table_rows: List[List[Paragraph]] = [header]
        for idx in range(0, len(items), pair_count):
            chunk = items[idx : idx + pair_count]
            row: List[Paragraph] = []
            for key, value in chunk:
                row.append(Paragraph(str(key), bold_style))
                row.append(Paragraph(_format_value(value), body_style))
            while len(chunk) < pair_count:
                row.append(Paragraph("", bold_style))
                row.append(Paragraph("", body_style))
                chunk += [("", "")]
            table_rows.append(row)
        return table_rows

    def render_dict_block(title: Optional[str], mapping: Dict[str, Any], level: int) -> None:
        if title:
            heading_style = heading_styles.get(level, styles["Heading5"])
            elements.append(Paragraph(title, heading_style))
        scalar_items: List[Tuple[str, Any]] = []
        nested_items: List[Tuple[str, Any]] = []
        for key, val in mapping.items():
            if isinstance(val, (dict, list)):
                nested_items.append((key, val))
            else:
                scalar_items.append((key, val))
        if scalar_items:
            pair_count = 2 if len(scalar_items) >= 6 else 1
            table_rows = _grid_table(scalar_items, pair_count)
            elements.append(_styled_table(table_rows, _column_widths(pair_count)))
            elements.append(Spacer(1, 8))
        for key, val in nested_items:
            render_section(key, val, level + 1)

    def render_section(name: str, value: Any, level: int = 1) -> None:
        heading_style = heading_styles.get(level, styles["Heading5"])
        if isinstance(value, dict):
            render_dict_block(name, value, level)
        elif isinstance(value, list):
            if name.lower() == "entries" and all(isinstance(item, dict) for item in value):
                elements.append(Paragraph(name, heading_style))
                for idx, item in enumerate(value, start=1):
                    label = item.get("label") or f"Entry {idx}"
                    elements.append(Paragraph(label, heading_styles.get(level + 1, heading_style)))
                    data_block = item.get("data")
                    if isinstance(data_block, dict):
                        render_dict_block(None, data_block, level + 2)
                    attachments_block = item.get("attachments")
                    if isinstance(attachments_block, dict) and attachments_block:
                        render_dict_block("Attachments", attachments_block, level + 2)
                    other_items = {k: v for k, v in item.items() if k not in {"label", "data", "attachments"}}
                    for key, val in other_items.items():
                        render_section(key, val, level + 2)
                    elements.append(Spacer(1, 6))
                return
            elements.append(Paragraph(name, heading_style))
            if not value:
                elements.append(Paragraph("No entries", body_style))
                elements.append(Spacer(1, 8))
            elif all(not isinstance(item, (dict, list)) for item in value):
                for item in value:
                    elements.append(Paragraph(f"- {_format_value(item)}", body_style))
                elements.append(Spacer(1, 8))
            elif all(isinstance(item, dict) for item in value):
                for idx, item in enumerate(value, start=1):
                    elements.append(Paragraph(f"{name} {idx}", heading_styles.get(level + 1, heading_style)))
                    if isinstance(item, dict):
                        render_dict_block(None, item, level + 2)
                    else:
                        render_section(f"{name} {idx}", item, level + 1)
                    elements.append(Spacer(1, 8))
            else:
                for idx, item in enumerate(value, start=1):
                    render_section(f"{name} {idx}", item, level + 1)
        else:
            elements.append(Paragraph(f"{name}: {_format_value(value)}", body_style))
            elements.append(Spacer(1, 6))

    if isinstance(snapshot, dict):
        scalar_rows = [
            (key, value)
            for key, value in snapshot.items()
            if not isinstance(value, (dict, list))
        ]
        if scalar_rows:
            elements.append(Paragraph("Summary", heading_styles[1]))
            render_scalar_table(scalar_rows)

        for key, value in snapshot.items():
            if key in {"attachments", "files"}:
                continue
            if isinstance(value, (dict, list)):
                render_section(key, value, level=1)
    else:
        render_section("Snapshot", snapshot, level=1)

    # Attachments table (non-image)
    attachment_entries = _collect_file_paths(snapshot)
    image_paths = list(image_paths or [])
    image_set = {Path(p).resolve() for p in image_paths}
    other_attachments = [(label, path) for label, path in attachment_entries if path.resolve() not in image_set]

    if other_attachments:
        elements.append(Paragraph("Attachments", heading_styles[1]))
        attachment_data = [
            [Paragraph("Label", bold_style), Paragraph("Path", bold_style)],
        ]
        for label, path in other_attachments:
            attachment_data.append(
                [
                    Paragraph(label or path.name, body_style),
                    Paragraph(path.as_posix(), body_style),
                ]
            )
        elements.append(_styled_table(attachment_data, [200, 290]))
        elements.append(Spacer(1, 12))

    if image_paths:
        elements.append(Paragraph("Images", heading_styles[1]))
        max_width, max_height = 380, 220
        for img_path in image_paths:
            try:
                img = Image(str(img_path))
                img.hAlign = "CENTER"
                img.drawWidth, img.drawHeight = _limit_image_size(img.drawWidth, img.drawHeight, max_width, max_height)
                elements.append(img)
                elements.append(Paragraph(Path(img_path).name, caption_style))
                elements.append(Spacer(1, 12))
            except Exception:
                elements.append(
                    Paragraph(f"[Image attachment could not be rendered: {img_path.name}]", caption_style)
                )
                elements.append(Spacer(1, 12))

    elements.append(Spacer(1, 6))
    elements.append(
        Paragraph(
            f"Generated on {time.strftime('%Y-%m-%d %H:%M:%S')} (local)",
            footer_style,
        )
    )

    doc.build(elements)
    return pdf_path


def merge_pdfs(pdf_paths: Iterable[Path], output_path: Path) -> Path:
    if PdfMerger is None:
        raise RuntimeError("PyPDF2 is required to merge PDFs. Install with `pip install PyPDF2`.")

    normalized: List[Path] = []
    for path in pdf_paths:
        candidate = Path(path)
        if candidate.exists() and candidate.is_file():
            normalized.append(candidate)

    if not normalized:
        raise ValueError("No existing PDF files to merge.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    merger = PdfMerger()
    skipped: List[str] = []
    try:
        for path in normalized:
            try:
                merger.append(str(path))
            except Exception:  # Corrupt / unreadable PDF; skip
                skipped.append(path.name)
        if len(merger.pages) == 0:
            merger.close()
            raise ValueError("All candidate PDFs were invalid or unreadable. Skipped: " + ", ".join(skipped))
        with output_path.open("wb") as handle:
            merger.write(handle)
    finally:
        merger.close()

    return output_path


def _limit_image_size(width: float, height: float, max_width: float, max_height: float) -> Tuple[float, float]:
    scale = min(max_width / width, max_height / height, 1.0)
    return width * scale, height * scale
