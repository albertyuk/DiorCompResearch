# Template Map — measured from `template/reference.pptx` (July 2026 deck)

Cross-validated against `template/reference_june.pptx` (June 2026, 28 slides).
All coordinates in inches, `(x, y, w, h)`. Slide canvas **13.333 × 7.5** (16:9).
EMU per inch = 914400.

## Deck skeleton

| # | Archetype | Layout | Notes |
|---|-----------|--------|-------|
| 1 | Cover | `1_Title Only` | Only shape: text box `(0.0, 4.813, 12.572 × 0.559)`, right-aligned, **22 pt bold Calibre**, text `COMPETITOR LOCAL ASSETS`. Big Dior logo + hairline come from the layout. |
| 2 | Analysis stub | `1_Title Slide` | Header box + 5 label text boxes + `STRENGTHS:  / WEAKNESSES:` box at `(0.501, 6.471, 10.746 × 0.656)` (11 pt bold, content already empty). Cloned verbatim, left as stub. |
| 3+ | Brand table slide, then one slide per project | `1_Title Slide` | Per brand in fixed order Chanel → LV → Tiffany & Co. → Gucci → Fendi. |

The `1_Title Slide` layout carries the Dior wordmark (top-left) and the full-width
hairline rule — they are **not** slide shapes; any slide on this layout inherits them.

## Shared per-slide elements (layout `1_Title Slide`)

- **Header**: free text box named `Titre 3` at `(2.052, 0.40, 10.655 × 0.404)`,
  right-aligned, 20 pt Calibre, `COMPETITOR LOCAL ASSETS` (slide 2 appends ` ANALYSIS`).
- **Page number**: placeholder idx 12, type `SLIDE_NUMBER`, at `(9.417, 6.951, 3.0 × 0.399)`
  (a couple of slides drift to y 6.872 — we standardize on 6.951). Field-based `<a:fld>`.
- Body font family everywhere: **Calibre** (not embedded in the file; the typeface
  reference survives cloning). Table cells use **Futura Lt BT 9 pt**.

## Brand table slide

- Brand logo picture ≈ `(1.54, 1.06, 1.82 × 0.55)` (Chanel reference position; other
  humans drifted — we standardize on the Chanel slide's placement, scaling each logo
  to its native aspect ratio within that box). Extracted logos:
  `template/logos/{chanel.emf, lv.png, tiffany.png, gucci.png, fendi.png}`
  (Chanel is a WMF/EMF vector, 203×61; others PNG).
- Table at `(1.654, 1.879)`, total width **10.032**, column widths
  `[1.473, 3.149, 0.960, 2.322, 0.957, 1.171]` (June Chanel slide matches exactly —
  this is the locked geometry; later July slides drifted).
- Header row `DATE | PROJECT | ASSETS | SOCIAL | EC | AD`, height 0.503; body rows ≈ 0.675.
- Table style: `<a:tblPr firstRow="1" bandRow="1">` with **built-in table style ID
  `{073A0DAA-6AF3-43AB-8588-CEC1D06C72B9}`** ("Medium Style 2" on text-1) → black header
  row with white text, alternating grey body bands. `tableStyles.xml` does not define it;
  it resolves inside PowerPoint/LibreOffice.
- Cell runs: `sz="900"`, `Futura Lt BT` latin + `Arial` cs, centered (`algn="ctr"`),
  cell margins L/R 78848, T/B 39424 EMU, `anchor="ctr"`.
- DATE ordinals are superscript: separate run with `baseline="30000"`
  (e.g. `JULY 9` + superscript `TH`).
- Date text (standardized per Decision Record): `JULY 9TH`, ranges `JULY 2ND – 3RD`
  (en dash, spaces), ongoing `JULY 1ST – TBD`. Humans wrote `JULY 2-3TH`, `JUN 15th-30TH`,
  `JULY 1-TBDST` — do not imitate.
- SOCIAL: platforms joined by `/` in fixed order `WEIBO/RED/WECHAT/DOUYIN`, only those
  present. (July human wrote `TIKTOK` twice; June used `DOUYIN` — DOUYIN is locked.)
- EC and AD columns: literal `N/A`.

## Project slide

- **Title box** at `(0.35, 0.95, 8.2 × 0.892)` (July uses 0.332–0.384 x; 0.932–0.989 y):
  plain text box, `<a:spAutoFit/>`, `lIns/rIns 50376`, list style lvl1
  `defRPr sz="1400" b="1"` + `spcBef 300`; three paragraphs, runs override latin to
  `Calibre`: `BRAND NAME` / `PROJECT TITLE (ALL CAPS)` / `SOCIAL CONTENT`.
  Second paragraph run carries explicit `solidFill 000000`.
- **Media marker icon**, one per slide, sits just left of / above the first media item:
  - PHOTO → `template/icons/photo_marker.png` (80×66) at ≈ `0.354 × 0.29`.
  - VIDEO → `template/icons/video_marker.png` (45×59) at ≈ `0.30 × 0.393`.
  (These are the little camera / movie-camera glyphs; the deck does NOT overlay a play
  triangle on stills — the movie-camera marker is the video cue.)
- **Platform presence icons** bottom-left row starting `(0.416, 6.95)`, each ≈ `0.245 × 0.247`,
  ~0.055 gap (pitch ≈ 0.30): `template/icons/{weibo,red,wechat,douyin}.png`.
  Standardized order WEIBO, RED, WECHAT, DOUYIN (the humans varied: e.g. July slide 4
  shows weibo/wechat/red). Weibo icon = orange eye (256×256), RED = 小红书 badge,
  WeChat = green bubble, Douyin = note glyph (52×53).
- **Celebrity label** centered under each screenshot featuring a celeb: two paragraphs,
  10 pt Calibre centered — line 1 relationship (regular), line 2 name (**bold**).
  On the 14-image grid slides labels drop to **9 pt**. Uncertain relations render as
  occupation + trailing ` ?` (improvement over the examples' yellow-highlight hack).
- **Link text box** (VIDEO projects, and any post where the humans pasted a link):
  10 pt centered, plain text, directly under the still, above the celeb label.
  We emit **full post URLs** (never fabricate t.cn short links).

### PHOTO layouts (measured)

- **n ≤ 6, one row**: images centered as a row around x-center ≈ 6.67, top y ≈ 2.02.
  Observed sizes: 3 portraits `2.794 × 3.726`; 3 portraits `3.29 × 4.231`;
  5 squares `2.318 × 2.318` (row at y 2.854); 5 portraits `2.31 × 2.88`;
  4 portraits `2.678 × 4.017`; 2 portraits `2.87 × 4.31`; 1 square `4.123 × 4.123`.
  Rule: pick a common height (3.4–4.2 for ≤4 items, ~2.9 for 5, ~2.3 for 6), scale
  each image by aspect, distribute with even gaps (~0.2–0.3) centered horizontally.
  Labels at row bottom + 0.1..0.25.
- **n ≥ 7, two-row grid** (cap 14/slide, overflow → continuation slide with same title):
  7 columns; rows at y ≈ 2.02 and 4.26; cells ≈ `1.37 × 1.82`; labels 9 pt at
  y ≈ 3.87 / 6.07; column pitch ≈ 1.51 starting x ≈ 1.39.

### VIDEO layouts (measured)

- 1–2 stills: `4.945 × 2.781` at y 2.524, x 1.722 / 6.86 (July slide 4); each still gets
  link + label beneath.
- 1 big still: `7.29 × 4.10` at `(3.022, 2.066)` (July slide 11).
- 3–6 stills: 3-column × 2-row grid `~3.59 × 1.96`, rows y 2.178 / 4.239 (June slide 12),
  single link + label at bottom.

## Icon → platform identification evidence

Two-platform slides whose SOCIAL column says `WEIBO/RED` consistently carry sha1
`1dee9bcf` (weibo) + `3dbfa926` (red). Three-platform WEIBO/RED/WECHAT slides add
`77ec45b3` (wechat, green in render). DOUYIN rows add `9fb03fe0`. June uses a different
weibo file (`3470bcb5`) with identical art — July's extracted copies are canonical.

## Notation standardization (locked)

- ASSETS: `PHOTO`, `VIDEO`, or `PHOTO VIDEO`.
- Phase suffixes: ` — TEASER`, ` — CELEBS`, ` — EVENT`, ` — MAKING OF`, ` — VIP ARRIVAL`,
  ` — SCENOGRAPHY`, ` — ARRIVAL` (em dash, single space each side). Humans wrote `--`,
  `–`, `-` inconsistently.
- Month names always full caps: `JULY`, `JUNE` (June deck's `JUN` abbreviations are not
  imitated).
- Relationship vocabulary: `BRAND AMBASSADOR`, `BRAND FRIEND`, else occupation
  (`ACTRESS`, `SINGER`, `ARTIST`, `PIANIST`, `DIRECTOR`, …), with trailing ` ?` when
  unverified.

## pptx engineering notes

- python-pptx cannot duplicate slides: we deep-copy the archetype slide's `<p:cSld>`
  XML into a new slide part and re-create image relationships (`r:embed`) per target.
- Never assign `text_frame.text` (collapses run formatting) — write runs or XML.
- Parse/edit XML with lxml via python-pptx's own element tree; never round-trip
  `xml.etree` (namespace corruption).
- The Chanel logo is EMF: embed the blob as-is (python-pptx accepts it via image part
  with `image/x-emf` content type — we register it from the extracted file).
- Output package baseline: validated against the reference deck itself (same layouts,
  same master, same content types).
