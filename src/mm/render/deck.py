"""Deck assembly — clones the reference deck's cover + analysis stub, then
rebuilds brand table slides and project slides at the measured geometry
(docs/template_map.md).

pptx engineering rules honored here:
- never assign text_frame.text (kills run formatting) — runs only
- structural work (slide deletion) before content edits
- XML edits through python-pptx's lxml elements (no xml.etree round-trips)
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from ..config import TEMPLATE_DIR
from ..dates import date_runs, parse_iso

TABLE_STYLE_ID = "{073A0DAA-6AF3-43AB-8588-CEC1D06C72B9}"  # "Medium Style 2" (black/grey)
TABLE_FONT = "Futura Lt BT"
BODY_FONT = "Calibre"

COL_WIDTHS_IN = [1.473, 3.149, 0.960, 2.322, 0.957, 1.171]
TABLE_X, TABLE_Y, TABLE_W = 1.654, 1.879, 10.032
HEADER_ROW_H, BODY_ROW_H = 0.503, 0.675

LOGO_X, LOGO_Y, LOGO_MAX_W, LOGO_MAX_H = 1.54, 1.06, 2.30, 0.55
# Per-brand logo sizes measured from the reference deck (w, h in inches).
LOGO_SIZES = {
    "chanel": (1.821, 0.553), "lv": (2.264, 0.321), "tiffany": (2.468, 0.511),
    "gucci": (1.635, 0.505), "fendi": (1.134, 0.407),
}

ICON_X0, ICON_Y, ICON_SIZE, ICON_PITCH = 0.416, 6.95, 0.245, 0.30
ICON_ORDER = ["weibo", "red", "wechat", "douyin"]  # fixed display order

GRID_COLS, GRID_CAP = 7, 14
GRID_X0, GRID_PITCH = 1.39, 1.51
GRID_ROW_Y = [2.02, 4.26]
GRID_CELL_W, GRID_CELL_H = 1.37, 1.82

MEDIA_BAND_TOP, MEDIA_BAND_H = 2.0, 4.3
ROW_MAX_W = 11.9          # usable width for a one-row layout
ROW_GAP = 0.28
CENTER_X = 13.333 / 2

LOGOS = {
    "chanel": "chanel.emf", "lv": "lv.png", "tiffany": "tiffany.png",
    "gucci": "gucci.png", "fendi": "fendi.png",
}


@dataclass
class Visual:
    image: str                       # local path
    kind: str = "photo"              # photo | video_still
    link: str | None = None
    label_top: str | None = None     # relationship / occupation line
    label_name: str | None = None    # bold celeb name line


@dataclass
class ProjectSpec:
    title: str                       # canonical title, no suffix
    phase_suffix: str | None
    date_start: str                  # ISO
    date_end: str | None
    ongoing: bool
    assets: str                      # PHOTO | VIDEO | PHOTO VIDEO
    platforms: list[str]             # ingestion keys or display names
    description: str                 # PROJECT cell line
    visuals: list[Visual] = field(default_factory=list)

    @property
    def display_title(self) -> str:
        t = self.title.strip().upper()
        if self.phase_suffix:
            t += f" — {self.phase_suffix.strip().upper()}"
        return t


@dataclass
class BrandSpec:
    key: str
    display_name: str
    projects: list[ProjectSpec] = field(default_factory=list)


def _norm_platform(p: str) -> str:
    p = p.lower()
    return {"xhs": "red", "wechat_mp": "wechat", "wechat_channels": "wechat",
            "rednote": "red"}.get(p, p)


def _social_text(platforms: list[str]) -> str:
    present = {_norm_platform(p) for p in platforms}
    order = {"weibo": "WEIBO", "red": "RED", "wechat": "WECHAT", "douyin": "DOUYIN"}
    return "/".join(v for k, v in order.items() if k in present)


def _resolve_media(path: str) -> Path | None:
    """Anchor relative visual paths at the repo root; None if missing."""
    p = Path(path)
    if not p.is_absolute():
        from ..config import ROOT
        p = ROOT / p
    return p if p.is_file() else None


def _img_aspect(path: str) -> float:
    # one unreadable file must never abort a render — a square default only
    # costs that image its ideal crop
    try:
        with Image.open(path) as im:
            w, h = im.size
        return w / h if h else 1.0
    except Exception:
        return 1.0


class DeckBuilder:
    def __init__(self, template_path: Path | None = None):
        self.template_path = template_path or (TEMPLATE_DIR / "reference.pptx")
        self.prs = Presentation(str(self.template_path))
        # capture archetype shapes from slide 3 (brand table slide) before deletion
        src = self.prs.slides[2]
        self._header_sp = None
        self._pagenum_sp = None
        for sh in src.shapes:
            if sh.has_text_frame and "COMPETITOR LOCAL ASSETS" in sh.text_frame.text:
                self._header_sp = copy.deepcopy(sh._element)
            if sh.is_placeholder and sh.placeholder_format.idx == 12:
                self._pagenum_sp = copy.deepcopy(sh._element)
        if self._header_sp is None or self._pagenum_sp is None:
            raise RuntimeError("Template forensics mismatch: header/page-number "
                               "shapes not found on reference slide 3")
        self._body_layout = src.slide_layout
        self._delete_slides_from(2)   # keep cover + analysis stub

    # -- slide plumbing -------------------------------------------------------

    def _delete_slides_from(self, index: int) -> None:
        sldIdLst = self.prs.slides._sldIdLst
        for sldId in list(sldIdLst)[index:]:
            rId = sldId.get(qn("r:id"))
            self.prs.part.drop_rel(rId)
            sldIdLst.remove(sldId)

    def _new_body_slide(self):
        slide = self.prs.slides.add_slide(self._body_layout)
        for sh in list(slide.shapes):          # drop placeholders add_slide cloned
            sh._element.getparent().remove(sh._element)
        slide.shapes._spTree.append(copy.deepcopy(self._header_sp))
        slide.shapes._spTree.append(copy.deepcopy(self._pagenum_sp))
        return slide

    # -- text helpers ---------------------------------------------------------

    def _add_textbox(self, slide, x, y, w, h):
        return slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))

    @staticmethod
    def _para(tf, first: bool):
        return tf.paragraphs[0] if first and not tf.paragraphs[0].runs \
            else tf.add_paragraph()

    @staticmethod
    def _run(para, text, *, size, bold=False, name=BODY_FONT,
             superscript=False, color=None):
        r = para.add_run()
        r.text = text
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.name = name
        if color is not None:
            r.font.color.rgb = RGBColor.from_string(color)
        if superscript:
            r.font._rPr.set("baseline", "30000")
        return r

    def _add_title_box(self, slide, brand_name: str, title: str) -> None:
        box = self._add_textbox(slide, 0.35, 0.95, 8.2, 0.892)
        tf = box.text_frame
        tf.word_wrap = True
        for i, text in enumerate([brand_name, title, "SOCIAL CONTENT"]):
            p = self._para(tf, i == 0)
            p.space_before = Pt(3)
            self._run(p, text, size=14, bold=True,
                      color="000000" if i == 1 else None)

    CELEB_LABEL_SIZE = 9      # celeb relationship + name: Calibre 9 (owner spec)

    def _add_label(self, slide, cx: float, top: float, *, link: str | None,
                   label_top: str | None, label_name: str | None,
                   size: float = 10, max_w: float = 3.2) -> None:
        """Centered link/relationship/name label block under a visual. The
        link keeps the caller's size; the celeb title and name lines are
        always Calibre 9."""
        lines = []
        if link:
            lines.append((link, False, size))
        if label_top:
            lines.append((label_top, False, self.CELEB_LABEL_SIZE))
        if label_name:
            lines.append((label_name, True, self.CELEB_LABEL_SIZE))
        if not lines:
            return
        h = 0.05 + 0.19 * len(lines)
        box = self._add_textbox(slide, cx - max_w / 2, top, max_w, h)
        tf = box.text_frame
        tf.word_wrap = True
        tf.margin_top = tf.margin_bottom = 0
        for i, (text, bold, sz) in enumerate(lines):
            p = self._para(tf, i == 0)
            p.alignment = PP_ALIGN.CENTER
            self._run(p, text, size=sz, bold=bold)

    # -- icons / logos --------------------------------------------------------

    def _add_platform_icons(self, slide, platforms: list[str]) -> None:
        present = {_norm_platform(p) for p in platforms}
        i = 0
        for key in ICON_ORDER:
            if key not in present:
                continue
            path = TEMPLATE_DIR / "icons" / f"{key}.png"
            slide.shapes.add_picture(str(path), Inches(ICON_X0 + i * ICON_PITCH),
                                     Inches(ICON_Y), Inches(ICON_SIZE), Inches(ICON_SIZE))
            i += 1

    def _add_media_marker(self, slide, kind: str, first_x: float, first_y: float) -> None:
        if kind == "video":
            path = TEMPLATE_DIR / "icons" / "video_marker.png"
            w, h = 0.30, 0.393
        else:
            path = TEMPLATE_DIR / "icons" / "photo_marker.png"
            w, h = 0.354, 0.29
        x = max(0.12, first_x - 0.48)
        slide.shapes.add_picture(str(path), Inches(x), Inches(max(1.9, first_y - 0.03)),
                                 Inches(w), Inches(h))

    def _add_logo(self, slide, brand_key: str, display_name: str = "") -> None:
        filename = LOGOS.get(brand_key)
        path = (TEMPLATE_DIR / "logos" / filename) if filename else None
        if path is None or not path.is_file():
            # unknown brand: render the display name as a wordmark-ish label
            box = self._add_textbox(slide, LOGO_X, LOGO_Y, 4.0, LOGO_MAX_H)
            p = box.text_frame.paragraphs[0]
            self._run(p, display_name or brand_key.upper(), size=22, bold=True)
            return
        if brand_key in LOGO_SIZES:
            w, h = LOGO_SIZES[brand_key]
        else:
            aspect = 3.3 if path.suffix in (".emf", ".wmf") else _img_aspect(str(path))
            w = min(LOGO_MAX_W, LOGO_MAX_H * aspect)
            h = w / aspect
        y = LOGO_Y + (LOGO_MAX_H - h) / 2
        slide.shapes.add_picture(str(path), Inches(LOGO_X), Inches(y),
                                 Inches(w), Inches(h))

    # -- table slide ----------------------------------------------------------

    # max body rows per table slide before splitting to a continuation slide
    TABLE_MAX_ROWS = 7

    def add_table_slides(self, brand: BrandSpec) -> list:
        chunks = [brand.projects[i:i + self.TABLE_MAX_ROWS]
                  for i in range(0, max(len(brand.projects), 1),
                                 self.TABLE_MAX_ROWS)]
        return [self._table_slide(brand, chunk) for chunk in chunks]

    def _table_slide(self, brand: BrandSpec, projects: list[ProjectSpec]):
        slide = self._new_body_slide()
        self._add_logo(slide, brand.key, brand.display_name)
        rows = len(projects) + 1
        gfx = slide.shapes.add_table(rows, 6, Inches(TABLE_X), Inches(TABLE_Y),
                                     Inches(TABLE_W), Inches(HEADER_ROW_H +
                                                             BODY_ROW_H * (rows - 1)))
        table = gfx.table
        tbl = table._tbl
        tblPr = tbl.tblPr
        tblPr.set("firstRow", "1")
        tblPr.set("bandRow", "1")
        for el in tblPr.findall(qn("a:tableStyleId")):
            tblPr.remove(el)
        styleId = tblPr.makeelement(qn("a:tableStyleId"), {})
        styleId.text = TABLE_STYLE_ID
        tblPr.append(styleId)

        for col, w in zip(table.columns, COL_WIDTHS_IN):
            col.width = Inches(w)
        table.rows[0].height = Inches(HEADER_ROW_H)
        for r in list(table.rows)[1:]:
            r.height = Inches(BODY_ROW_H)

        headers = ["DATE", "PROJECT", "ASSETS", "SOCIAL", "EC", "AD"]
        for c, text in enumerate(headers):
            self._fill_cell(table.cell(0, c), [(text, False, False)])
        for ri, proj in enumerate(projects, start=1):
            runs = [(t, False, sup) for t, sup in
                    date_runs(parse_iso(proj.date_start),
                              parse_iso(proj.date_end), proj.ongoing)]
            self._fill_cell(table.cell(ri, 0), runs)
            self._fill_cell(table.cell(ri, 1), [(proj.description, False, False)])
            self._fill_cell(table.cell(ri, 2), [(proj.assets, False, False)])
            self._fill_cell(table.cell(ri, 3),
                            [(_social_text(proj.platforms), False, False)])
            self._fill_cell(table.cell(ri, 4), [("N/A", False, False)])
            self._fill_cell(table.cell(ri, 5), [("N/A", False, False)])
        return slide

    def _fill_cell(self, cell, runs: list[tuple[str, bool, bool]]) -> None:
        """runs: (text, bold, superscript). 9pt Futura Lt BT, centered."""
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.margin_left = cell.margin_right = Emu(78848)
        cell.margin_top = cell.margin_bottom = Emu(39424)
        tf = cell.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        for text, bold, sup in runs:
            self._run(p, text, size=9, bold=bold, name=TABLE_FONT, superscript=sup)

    # -- project slides -------------------------------------------------------

    def add_project_slides(self, brand: BrandSpec, proj: ProjectSpec):
        """One slide per chunk of visuals; overflow continues on extra slides.
        Photo slides hold up to GRID_CAP (14); video layouts hold up to 6."""
        usable = []
        for v in proj.visuals:            # drop visuals whose file is missing
            resolved = _resolve_media(v.image)
            if resolved is not None:
                v.image = str(resolved)
                usable.append(v)
        cap = 6 if proj.assets == "VIDEO" else GRID_CAP
        chunks = [usable[i:i + cap]
                  for i in range(0, max(len(usable), 1), cap)]
        slides = []
        for chunk in chunks:
            slide = self._new_body_slide()
            self._add_title_box(slide, brand.display_name, proj.display_title)
            self._add_platform_icons(slide, proj.platforms)
            if chunk:
                if proj.assets == "VIDEO" or all(v.kind == "video_still" for v in chunk):
                    self._layout_video(slide, chunk)
                else:
                    self._layout_photo(slide, chunk)
            slides.append(slide)
        return slides

    def _layout_photo(self, slide, visuals: list[Visual]) -> None:
        n = len(visuals)
        if n >= 7:
            return self._layout_grid(slide, visuals)
        aspects = [_img_aspect(v.image) for v in visuals]
        has_links = any(v.link for v in visuals)
        h_max = 4.23 - (0.25 if has_links else 0.0)
        if n == 5:
            h_max = min(h_max, 2.95)
        elif n == 6:
            h_max = min(h_max, 2.45)
        h = min(h_max, (ROW_MAX_W - (n - 1) * ROW_GAP) / sum(aspects))
        widths = [h * a for a in aspects]
        total_w = sum(widths) + (n - 1) * ROW_GAP
        x = CENTER_X - total_w / 2
        y = MEDIA_BAND_TOP + max(0.0, (MEDIA_BAND_H - h - 0.9) / 2)
        self._add_media_marker(slide, "photo", x, y)
        for v, w in zip(visuals, widths):
            slide.shapes.add_picture(v.image, Inches(x), Inches(y),
                                     Inches(w), Inches(h))
            self._add_label(slide, x + w / 2, y + h + 0.08, link=v.link,
                            label_top=v.label_top, label_name=v.label_name,
                            max_w=max(2.2, w + 0.5))
            x += w + ROW_GAP

    def _layout_grid(self, slide, visuals: list[Visual]) -> None:
        self._add_media_marker(slide, "photo", GRID_X0, GRID_ROW_Y[0])
        for i, v in enumerate(visuals):
            row, col = divmod(i, GRID_COLS)
            cx = GRID_X0 + col * GRID_PITCH + GRID_CELL_W / 2
            y = GRID_ROW_Y[row]
            a = _img_aspect(v.image)
            w, h = GRID_CELL_W, GRID_CELL_W / a
            if h > GRID_CELL_H:
                h, w = GRID_CELL_H, GRID_CELL_H * a
            slide.shapes.add_picture(v.image, Inches(cx - w / 2), Inches(y),
                                     Inches(w), Inches(h))
            self._add_label(slide, cx, y + GRID_CELL_H + 0.03, link=None,
                            label_top=v.label_top, label_name=v.label_name,
                            size=9, max_w=1.6)

    def _layout_video(self, slide, visuals: list[Visual]) -> None:
        n = len(visuals)
        if n == 1:
            v = visuals[0]
            a = _img_aspect(v.image)
            w = min(7.29, 4.10 * a)
            h = w / a
            x, y = CENTER_X - w / 2, 2.066
            self._add_media_marker(slide, "video", x, y)
            slide.shapes.add_picture(v.image, Inches(x), Inches(y),
                                     Inches(w), Inches(h))
            self._add_label(slide, CENTER_X, y + h + 0.1, link=v.link,
                            label_top=v.label_top, label_name=v.label_name,
                            max_w=5.6)
        elif n == 2:
            w, h, y = 4.945, 2.781, 2.524
            xs = [1.722, 6.86]
            self._add_media_marker(slide, "video", xs[0], y)
            for v, x in zip(visuals, xs):
                slide.shapes.add_picture(v.image, Inches(x), Inches(y),
                                         Inches(w), Inches(h))
                self._add_label(slide, x + w / 2, y + h + 0.1, link=v.link,
                                label_top=v.label_top, label_name=v.label_name,
                                max_w=4.5)
        else:
            w, h = 3.59, 1.96
            xs = [1.271, 4.964, 8.656]
            ys = [2.178, 4.239]
            self._add_media_marker(slide, "video", xs[0], ys[0])
            for i, v in enumerate(visuals[:6]):
                row, col = divmod(i, 3)
                slide.shapes.add_picture(v.image, Inches(xs[col]), Inches(ys[row]),
                                         Inches(w), Inches(h))
            v0 = visuals[0]
            self._add_label(slide, CENTER_X, ys[1] + h + 0.12, link=v0.link,
                            label_top=v0.label_top, label_name=v0.label_name,
                            max_w=5.6)

    # -- assembly -------------------------------------------------------------

    def build(self, brands: list[BrandSpec], out_path: Path) -> Path:
        for brand in brands:
            self.add_table_slides(brand)
            for proj in brand.projects:
                self.add_project_slides(brand, proj)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(str(out_path))
        return out_path


def load_spec(spec: dict) -> list[BrandSpec]:
    brands = []
    for b in spec["brands"]:
        projects = []
        for p in b.get("projects", []):
            projects.append(ProjectSpec(
                title=p["title"], phase_suffix=p.get("phase_suffix"),
                date_start=p["date_start"], date_end=p.get("date_end"),
                ongoing=bool(p.get("ongoing")), assets=p.get("assets", "PHOTO"),
                platforms=p.get("platforms", ["weibo"]),
                description=p.get("description", p["title"]),
                visuals=[Visual(**v) for v in p.get("visuals", [])],
            ))
        brands.append(BrandSpec(key=b["key"], display_name=b["display_name"],
                                projects=projects))
    return brands
