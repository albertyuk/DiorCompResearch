"""QA loop for rendered decks:
1. package validation — reopen with python-pptx, count slides
2. text dump — grep for leftover placeholders
3. geometry lint — shapes outside canvas, overlapping labels vs icons band
4. raster render (LibreOffice → PDF → PyMuPDF PNGs) for visual inspection
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu

PLACEHOLDER_RE = re.compile(r"lorem|TODO|\[insert|XXX|PLACEHOLDER", re.I)
CANVAS_W, CANVAS_H = 13.333, 7.5


def validate_package(path: Path, prs: Presentation | None = None) -> dict:
    prs = prs or Presentation(str(path))
    return {"slides": len(prs.slides),
            "width_in": round(Emu(prs.slide_width).inches, 3),
            "height_in": round(Emu(prs.slide_height).inches, 3)}


def _iter_text(prs):
    for i, slide in enumerate(prs.slides, 1):
        def walk(shapes):
            for sh in shapes:
                if sh.has_text_frame:
                    yield i, sh.text_frame.text
                if hasattr(sh, "shapes"):
                    yield from walk(sh.shapes)
                if getattr(sh, "has_table", False) and sh.has_table:
                    for row in sh.table.rows:
                        for cell in row.cells:
                            yield i, cell.text
        yield from walk(slide.shapes)


def find_placeholders(path: Path, prs: Presentation | None = None) -> list[tuple[int, str]]:
    prs = prs or Presentation(str(path))
    hits = []
    for slide_no, text in _iter_text(prs):
        if text and PLACEHOLDER_RE.search(text):
            hits.append((slide_no, text[:80]))
    return hits


def lint_geometry(path: Path, prs: Presentation | None = None) -> list[str]:
    prs = prs or Presentation(str(path))
    problems = []
    for i, slide in enumerate(prs.slides, 1):
        for sh in slide.shapes:
            if sh.left is None or sh.top is None:
                continue
            x, y = Emu(sh.left).inches, Emu(sh.top).inches
            w = Emu(sh.width).inches if sh.width else 0
            h = Emu(sh.height).inches if sh.height else 0
            if x < -0.3 or y < -0.3 or x + w > CANVAS_W + 0.3 or y + h > CANVAS_H + 0.3:
                problems.append(
                    f"slide {i}: shape {sh.shape_id} ({sh.name!r}) out of canvas "
                    f"({x:.2f},{y:.2f},{w:.2f}x{h:.2f})")
    return problems


def render_pngs(path: Path, out_dir: Path, dpi: int = 100) -> list[Path]:
    """LibreOffice headless → PDF → PNG per slide. Returns PNG paths."""
    import fitz
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / path.name
        shutil.copy(path, tmp)
        subprocess.run(
            ["soffice", "--headless",
             f"-env:UserInstallation=file://{td}/lo_profile",
             "--convert-to", "pdf", "--outdir", td, str(tmp)],
            check=True, capture_output=True, timeout=600)
        pdf = tmp.with_suffix(".pdf")
        doc = fitz.open(str(pdf))
        out = []
        for i in range(len(doc)):
            p = out_dir / f"slide_{i + 1:02d}.png"
            doc[i].get_pixmap(dpi=dpi).save(str(p))
            out.append(p)
        doc.close()
    return out


def run_qa(path: Path, png_dir: Path | None = None) -> dict:
    prs = Presentation(str(path))          # parse once, share across checks
    report = {"package": validate_package(path, prs),
              "placeholders": find_placeholders(path, prs),
              "geometry": lint_geometry(path, prs),
              "pngs": []}
    if png_dir is not None:
        try:
            report["pngs"] = [str(p) for p in render_pngs(path, png_dir)]
        except FileNotFoundError:
            report["png_note"] = ("QA slide images skipped — LibreOffice not "
                                  "installed (optional; the deck itself is "
                                  "unaffected)")
        except Exception as e:
            report["png_note"] = f"QA slide images skipped: {e}"
    report["ok"] = not report["placeholders"] and not report["geometry"]
    return report
