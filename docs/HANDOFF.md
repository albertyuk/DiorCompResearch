# MAISON MONITOR — session handoff

Read this first in a fresh session. It is the compressed memory of the
sessions that built this system (2026-07). `docs/ASSUMPTIONS.md` is the
detailed decision log (80 numbered entries — skim its headlines); this file
is the map. When the owner asks for a change: implement → test → verify in a
real browser where UI is involved → update ASSUMPTIONS.md → commit → push →
reply ending with the deploy command:

    cd ~/DiorCompResearch && git pull && uv run mm deploy

## What this is

A competitive-intelligence pipeline for Dior China's PR team. One month of
competitor social activity goes in; a PPTX competitor report + XLSX project
table comes out. Hosted on Fly.io (app `maison-monitor`, region sin,
https://maison-monitor.fly.dev, always-on + kill_timeout=300 — deliberate,
do not "optimize" to scale-to-zero). The owner (Albert) is non-technical:
replies should be plain, and every change ships the same turn.

Pipeline: ingest (Weibo via TikHub) → LLM filter (claude-sonnet-5) →
**Review · Posts** (human checkpoint #1) → cross-check (Douyin/XHS/WeChat,
per-POST matches) → enrich (consolidate posts into "projects", celebs,
descriptions) → **Review · Projects** (checkpoint #2) → render (PPTX/XLSX)
→ Decks page. Phases are resumable/idempotent per (month, phase); statuses
live in `runs.phase_status`; Stop is cooperative.

## Hard rules (never violate)

- Filter model is NEVER downgraded below claude-sonnet-5 (no Haiku); speed
  comes from concurrency.
- Perfume/fragrance AND makeup/skincare are OUT of scope (owner directive).
- Never commit the Anthropic key or any secret (GitHub push protection;
  never circumvent). Fly secrets via `fly secrets set` only. The dev
  container's `.env` holds a real TikHub key — usable for live API work,
  never committed.
- Account (Phase R) bindings need explicit human confirmation — EXCEPT the
  strict `auto_resolve_pending` path, which the owner pre-authorized
  (2026-07-17) for the current roster.
- UI: original design in Dior's aesthetic spirit only — no copied assets/
  fonts/imagery, no external font/asset downloads.
- The model identifier string must not appear in commits/PRs/code — chat
  only. Commit trailer format (use the session URL the harness gives you):

      Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
      Claude-Session: <this session's URL>

- Branch: `claude/maison-monitor-build-48uqjw` — develop, commit, push
  there; never push elsewhere. Do not create PRs unless asked.
- API costs are hidden from the web UI (CLI `mm costs` remains).
- Translations are meaning-first, never word-for-word (see i18n below).

## Code map (src/mm/)

- `pipeline.py` — phase orchestration. `BRAND_WORKERS=10` caps concurrent
  brand processing (ingest/crosscheck/enrich; `MM_BRAND_WORKERS` overrides).
  `RENDER_WORKERS=4` per-thread Playwright browsers. Render writes NEW
  timestamped files each run (`_PARTIAL` marker for subset renders),
  supports `only_ids` (per-project render selection). Beijing-time stamps.
  `run_crosscheck` starts with an `auto_resolve_pending` retry.
- `crosscheck.py` — per-POST cross-platform matching. Shared campaign
  hashtag → deterministic 0.9; celeb/keyword overlap only NOMINATES for
  prompts/match.md; judgments cached in `match_judgments` (a cached
  same_event=False — including human vetoes — blocks even the hashtag
  tier). Accepted matches persist per post in `post_matches` (source of
  truth); project SOCIAL ticks are derived.
- `enrich.py` — consolidation into projects; reads `post_matches` from DB;
  every matched post joins its project as role=match; only replaces
  draft/dropped projects, never confirmed.
- `resolve.py` — Phase R. `auto_resolve_pending`: binds only on exact
  normalized-name match + platform verification mark, exactly one
  candidate; else stays pending. Account status `absent` = confirmed no
  official account (excluded from unresolved list). TikHub schemas DRIFT:
  weibo search moved to `data.parsed_data.users[]` (handled; `fans` there
  is a truncated display number — verify via `weibo_user_info`).
- `render/` — deck.py (python-pptx, per-brand logo sizes; unknown brands
  render a text wordmark — the 7 new brands have NO logo files yet),
  visuals.py (card/live-screenshot builders — now only a fallback for
  image-less posts; slides embed the post's own photos), imgprep.py
  (downscale cache), qa.py (LibreOffice; raster skipped on hosted).
- `console/` — FastAPI + Jinja2. `i18n.py`: `T()` keyed by byte-identical
  English source strings; ZH dict is meaning-first under a glossary
  (筛选=filter, 整合=enrich, 归组=grouping, 报告=deck/Decks, 明星=celebs,
  任务=runs, 未匹配帖子=orphans, AI=the model, 删除=drop/delete). Every new
  user-facing string needs a ZH entry.
- `db.py` — SQLite WAL + SQLAlchemy Core. Key tables: posts, verdicts,
  projects, project_posts (role member|match), platform_matches (derived
  ticks), post_matches, match_judgments, orphans, celeb_registry,
  filter_feedback + learned rules, audit_log, archives. `now_iso()` is
  Beijing time (CST from dates.py) — ALL new timestamps must be.
- `learn.py` — self-tuning filter loop (human corrections → learned rules
  appended to filter prompt before each run).

## Console UX patterns (all established, keep consistent)

- One page anatomy: banner → workflow stepper (+ single Next CTA) →
  `.page-head` (serif title + description + ONE primary action) → collapsible
  guide (`_guide.html`, "How this page works" / "What happens next") →
  content. Button grammar: `.primary` solid (one per page), outline normal,
  `.ghost` tertiary, `.danger` red destructive, `.sm` in-table.
- Brand tabs (`.board-tabs`/`data-brand-pane`, localStorage per month) on
  Review·Posts, Review·Projects, and the Grouping board.
- Move things by drag & drop AND by click ("Move to…" pickers over
  `window.MM_PROJECTS`, shared `mmOpenPicker`/`mmWireMovers` in base.html).
- Evidence follows the post: eject/ungroup/adopt recompute per-post; human
  detach writes a match veto.
- `mmToast` for async errors (never `alert()`); scroll position restored
  across the save-then-reload pattern (`mmSaveScroll` + `data-scroll-keep`);
  lightbox (`img.zoom` + `data-gallery`); real progress bars with % + ETA
  (`mmProgress`); first-visit welcome card (Guide button reopens);
  EN/中文 + Light/Dark toggles (cookies, server-rendered); review tabs
  always visible in nav (NAV_MONTH fallback).
- Decks page: versioned files newest-first, Last changed (Beijing), type
  pills, Download + Delete file (traversal-hardened).
- Runs page: brand checkboxes (ready brands ticked; blocked show ⚠;
  bare POST = all ready brands), Auto-resolve all button, live activity
  log, per-month stepper.

## Brand roster (12)

Original 5 fully bound: chanel, lv, tiffany, gucci, fendi.
Added 2026-07-17: prada, loewe, valentino, bottega, cartier, hermes,
bvlgari — all Weibo-verified (checked via user-info: 蓝V corporate record,
real follower counts). Douyin bound: prada, loewe, cartier, bvlgari.
XHS bound: prada, loewe, valentino, bottega, hermes, bvlgari, cartier.
Douyin `absent` (confirmed no official account): valentino, bottega,
hermes. ALL 14 wechat_mp/wechat_channels still `resolve`: TikHub's
wechat_search family returned zero items on 2026-07-17 (outage — our
params verified against their OpenAPI); auto-resolve retries every
cross-check run and will bind when it recovers. Beauty/fragrance/hotel
side-accounts are noted as exclusions in brands.yaml.

## Testing & verification conventions

- `uv run pytest -q` — 214 passing as of handoff. Never leave red.
- Test style: tmp_db fixture (monkeypatch DB_PATH + _engine), TestClient
  with CONSOLE_PASSPHRASE/MM_SECRET_KEY, seeded posts/projects. Config
  tests copy brands.yaml to tmp and monkeypatch `mm.config.BRANDS_YAML`.
  Parallel-execution tests pin an explicit 5-brand subset (barriers vs the
  10-brand cap).
- UI changes: verify in a real browser. Pattern: scratchpad script seeds a
  tmp `MM_DATA_DIR`, launches uvicorn, drives Playwright with
  `executable_path="/opt/pw-browsers/chromium"`, screenshots; send key
  screenshots to the owner with SendUserFile.
- The cohesion contract test (`test_every_page_shares_one_anatomy`) will
  fail if a page gains a second `.page-title`, loses the toast container,
  or the nav separator.

## Gotchas

- TikHub response schemas drift without notice — when a search returns
  empty, dump the raw payload before assuming "no results" (that's how the
  weibo parser break and the wechat outage were found). `client.call`
  retries 429/5xx/transient-400.
- Old data written before the Beijing-time fix keeps UTC strings; before
  the versioned-deck change, un-timestamped deck files exist on the volume.
- Legacy `crosscheck_matches.json` files seed `post_matches` once
  (`_seed_legacy_matches`) — new runs don't write them.
- Duplicate keys in the ZH dict silently take the LAST value (caused the
  下载报告 bug) — grep before adding a key.
- `test_i18n` asserts exact rendered fragments (e.g. `EN</button>`) — keep
  toggle markup shape.
- Hosted account bindings write to a volume overlay
  (`account_overrides.yaml`), not the repo yaml; local writes edit
  brands.yaml directly.

## Open threads

1. WeChat accounts bind automatically when TikHub recovers — nothing to do
   unless the owner asks; check `pending` via the Auto-resolve button.
2. Logos for the 7 new brands: owner may supply PNGs → template/logos/ +
   optional LOGO_SIZES entry in render/deck.py.
3. Douyin absences (valentino/bottega/hermes): re-check occasionally.
4. If brands ever exceed ~15, revisit rate-limit caps (BRAND_WORKERS,
   MM_FILTER_WORKERS, MATCH_LLM_WORKERS).
