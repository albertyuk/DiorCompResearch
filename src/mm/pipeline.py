"""Phase orchestration. Each phase is resumable and idempotent per (month, phase).

Statuses in runs.phase_status: pending → running → done | waiting (checkpoints)
| error:<msg>. Checkpoints (review_posts, review_projects) block until the
Console confirms them.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from . import crosscheck as xc
from . import db, enrich as enrich_mod, filtering, ingest, naming
from .config import RUNS_DIR, BrandsConfig, OUTPUT_DIR, Settings
from .dates import deck_month_token, parse_iso
from .llm import LLM
from .tikhub import TikHubClient


# concurrent visual-assembly workers for the render phase — each owns a
# headless Chromium (~150-200MB; the 2GB Fly VM fits the default comfortably
# and the browsers close before LibreOffice QA starts). MM_RENDER_WORKERS
# overrides.
RENDER_WORKERS = 4

# at most this many brands are processed concurrently in ingest / cross-check
# / enrichment — each brand thread fans out its own TikHub and LLM calls, so
# an unbounded one-thread-per-brand pool would blow through API rate limits
# as the brand list grows past ten. MM_BRAND_WORKERS overrides.
BRAND_WORKERS = 10


def _brand_pool(n: int) -> int:
    import os
    return max(1, min(int(os.environ.get("MM_BRAND_WORKERS",
                                         BRAND_WORKERS)), n))


def _matches_path(month: str, brand_key: str) -> Path:
    p = RUNS_DIR / month / brand_key
    p.mkdir(parents=True, exist_ok=True)
    return p / "crosscheck_matches.json"


def _seed_legacy_matches(engine, month: str, brand_key: str) -> None:
    """Months crosschecked before per-post matches were first-class persisted
    them only to crosscheck_matches.json — seed db.post_matches from that
    file once, so enriching an old month keeps its evidence. New crosscheck
    runs write the table directly and never need this."""
    from sqlalchemy import func
    mp = _matches_path(month, brand_key)
    if not mp.exists():
        return
    with engine.connect() as conn:
        have = conn.execute(
            select(func.count()).select_from(
                db.post_matches.join(
                    db.posts,
                    db.posts.c.post_id == db.post_matches.c.ref_post_id))
            .where(db.post_matches.c.month == month,
                   db.posts.c.brand == brand_key)).scalar()
    if have:
        return
    try:
        legacy = json.loads(mp.read_text())
    except (ValueError, OSError):
        return
    with engine.begin() as conn:
        for ref_id, hits in (legacy or {}).items():
            for plat, hit in (hits or {}).items():
                if not hit.get("post_id"):
                    continue
                db.upsert(conn, db.post_matches, {
                    "ref_post_id": ref_id, "cand_post_id": hit["post_id"],
                    "platform": plat, "month": month,
                    "confidence": hit.get("confidence"),
                    "reason": hit.get("why"), "at": db.now_iso()},
                    ["ref_post_id", "cand_post_id"])


def run_resolve_check(cfg: BrandsConfig) -> list[dict]:
    from .resolve import unresolved_accounts
    return unresolved_accounts(cfg)


def _set_phase(engine, month: str, phase: str, status: str) -> None:
    with engine.begin() as conn:
        db.set_phase(conn, month, phase, status)


def run_ingest(month: str, brand_keys: list[str] | None = None,
               progress=None, should_stop=None) -> dict:
    """All brands ingest in parallel (one worker each): TikHub calls and
    media downloads are I/O-bound, page batches commit in short WAL
    transactions, and one brand failing never touches the others."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    cfg = BrandsConfig.load()
    settings = Settings.load()
    client = TikHubClient(settings)
    engine = db.get_engine()
    results = {}
    _set_phase(engine, month, "ingest", "running")

    brand_state: dict[str, str] = {}
    note_lock = threading.Lock()

    def page_note(bk, page, n):
        if not progress:
            return
        with note_lock:
            brand_state[bk] = f"p{page}·{n} in window"
            line = "  ".join(f"{k} {v}" for k, v in brand_state.items())
        progress(f"ingest · {line}")

    def one(brand):
        if should_stop and should_stop():
            return brand.key, None
        if progress:
            with note_lock:
                brand_state[brand.key] = "fetching…"
        try:
            return brand.key, ingest.ingest_weibo(
                engine, client, cfg, month, brand.key, progress=page_note,
                should_stop=should_stop)
        except Exception as e:
            return brand.key, {"error": str(e)}

    wanted = [b for b in cfg.brands
              if not brand_keys or b.key in brand_keys]
    try:
        with ThreadPoolExecutor(max_workers=_brand_pool(len(wanted))) as ex:
            for key, res in ex.map(one, wanted):
                if res is not None:
                    results[key] = res
    finally:
        client.close()
    errs = {k: r["error"] for k, r in results.items() if "error" in r}
    if should_stop and should_stop():
        _set_phase(engine, month, "ingest", "stopped — Start month resumes")
    elif errs:
        # the failure reason must be visible in the UI, not buried in a dict
        msg = "error: " + "; ".join(f"{k}: {v[:90]}" for k, v in errs.items())
        _set_phase(engine, month, "ingest", msg[:300])
    else:
        _set_phase(engine, month, "ingest", "done")
    return results


def run_filter(month: str, brand_keys: list[str] | None = None,
               progress=None, should_stop=None) -> dict:
    cfg = BrandsConfig.load()
    llm = LLM()
    engine = db.get_engine()
    _set_phase(engine, month, "filter", "running")

    # self-tuning: fold any new human corrections into the learned guidance
    # BEFORE filtering, so this run already benefits. Never blocks the run.
    try:
        from . import learn
        upd = learn.synthesize_rules(engine, llm)
        if upd and progress:
            progress(f"filter · learned rules updated from "
                     f"{upd['corrections']} corrections")
    except Exception:
        pass

    def note(stats):
        if progress:
            done = stats["filtered"] + stats["errors"]
            msg = f"filter · {done}/{stats.get('pending', stats['total'])} posts"
            if stats["errors"]:
                msg += f" · {stats['errors']} failed so far"
            progress(msg)

    try:
        stats = filtering.filter_month(engine, llm, cfg, month,
                                       brand_keys[0] if brand_keys and
                                       len(brand_keys) == 1 else None,
                                       progress=note, should_stop=should_stop)
    except Exception:
        _set_phase(engine, month, "filter", "error")
        raise
    if stats.get("stopped"):
        _set_phase(engine, month, "filter", "stopped — Start month resumes")
        return stats
    ok = stats.get("errors", 0) == 0
    _set_phase(engine, month, "filter", "done" if ok
               else f"error: {stats['errors']} posts failed — re-run filter")
    _set_phase(engine, month, "review_posts", "waiting")
    return stats


def confirm_posts_review(month: str) -> None:
    engine = db.get_engine()
    with engine.begin() as conn:
        db.set_phase(conn, month, "review_posts", "confirmed")


def run_crosscheck(month: str, brand_keys: list[str] | None = None,
                   progress=None, should_stop=None) -> dict:
    """All brands cross-check in parallel (one worker each): the four
    platform pulls inside a brand also run concurrently, and match.md
    escalations use their own small pool — I/O-bound throughout, short WAL
    transactions, per-brand failures isolated."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    cfg = BrandsConfig.load()
    settings = Settings.load()
    client = TikHubClient(settings)
    llm = LLM(settings)
    engine = db.get_engine()
    results = {}
    _set_phase(engine, month, "crosscheck", "running")

    # owner-authorized auto-resolution: accounts still pending (e.g. the
    # 2026-07-17 wechat_search outage) are retried at the start of every
    # cross-check run and bind the moment the platform search recovers —
    # strict rules (exact official name + platform verification mark) live
    # in resolve.auto_resolve_pending
    if any(a.status == "resolve" for b in cfg.brands
           for a in b.accounts.values()):
        from .resolve import auto_resolve_pending
        try:
            if progress:
                progress("crosscheck · auto-resolving pending accounts…")
            ar = auto_resolve_pending(client, cfg, engine, note=progress)
            if ar["resolved"]:
                cfg = BrandsConfig.load()          # pick up the new bindings
        except Exception:
            pass                                    # never blocks the run

    brand_state: dict[str, str] = {}
    note_lock = threading.Lock()

    def note_for(bk):
        def note(msg):
            if not progress:
                return
            with note_lock:
                brand_state[bk] = msg
                line = "  ".join(f"{k} {v}" for k, v in brand_state.items())
            progress(f"crosscheck · {line}")
        return note

    def one(brand):
        if should_stop and should_stop():
            return brand.key, None
        note = note_for(brand.key)
        try:
            note("pulling…")
            pulls = xc.pull_all(engine, client, cfg, month, brand.key,
                                progress=note)
            note("matching…")
            res = xc.crosscheck_brand(engine, llm, cfg, month, brand.key,
                                      should_stop=should_stop)
            # sift the orphans with the same rubric as review #1, so the
            # orphan list at checkpoint #2 arrives pre-filtered
            ostats = {}
            if res["orphans"] and not (should_stop and should_stop()):
                note(f"filtering {res['orphans']} orphans…")
                ostats = filtering.filter_orphans(
                    engine, llm, cfg, month, brand.key,
                    should_stop=should_stop)
            # xhs URLs from timeline pulls are dead for humans (wrong id, no
            # xsec_token) — swap in the official share link for every xhs
            # post that will be shown as a link (matches + kept orphans)
            hstats = {}
            if not (should_stop and should_stop()):
                xhs_ids = {hit["post_id"]
                           for plats in res["matches"].values()
                           for plat, hit in plats.items()
                           if plat == "xhs" and hit.get("post_id")}
                with engine.connect() as conn:
                    xhs_ids |= {r[0] for r in conn.execute(
                        select(db.posts.c.post_id)
                        .join(db.orphans,
                              db.orphans.c.post_id == db.posts.c.post_id)
                        .join(db.verdicts,
                              db.verdicts.c.post_id == db.posts.c.post_id)
                        .where(db.orphans.c.month == month,
                               db.orphans.c.resolution == "pending",
                               db.posts.c.brand == brand.key,
                               db.posts.c.platform == "xhs",
                               db.verdicts.c.keep.is_(True)))}
                if xhs_ids:
                    note(f"fixing {len(xhs_ids)} xhs links…")
                    hstats = xc.hydrate_xhs_links(engine, client, month,
                                                  brand.key, xhs_ids)
                    # matches live in db.post_matches WITHOUT frozen urls —
                    # enrich joins the posts table, so hydrated links are
                    # picked up with no refresh step
            note("done")
            return brand.key, {"pulls": pulls, "orphans": res["orphans"],
                               "orphans_kept": ostats.get("kept"),
                               "xhs_links_fixed": hstats.get("hydrated")}
        except Exception as e:
            note("error")
            return brand.key, {"error": str(e)}

    wanted = [b for b in cfg.brands if not brand_keys or b.key in brand_keys]
    try:
        with ThreadPoolExecutor(max_workers=_brand_pool(len(wanted))) as ex:
            for key, res in ex.map(one, wanted):
                if res is not None:
                    results[key] = res
    finally:
        client.close()
    errs = {k: r["error"] for k, r in results.items()
            if isinstance(r, dict) and "error" in r}
    if should_stop and should_stop():
        _set_phase(engine, month, "crosscheck", "stopped — Confirm posts resumes")
    elif errs:
        _set_phase(engine, month, "crosscheck",
                   ("error: " + "; ".join(f"{k}: {v[:90]}" for k, v in errs.items()))[:300])
    else:
        _set_phase(engine, month, "crosscheck", "done")
    return results


def run_enrich(month: str, brand_keys: list[str] | None = None,
               progress=None, should_stop=None) -> dict:
    """Brands enrich in parallel (one worker each); inside a brand the
    relation.md and describe.md calls run in their own small pools. Registry
    writes are serialized by a lock in enrich.py, so concurrent brands can't
    clobber a shared celeb's relations."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    cfg = BrandsConfig.load()
    llm = LLM()
    engine = db.get_engine()
    results = {}
    _set_phase(engine, month, "enrich", "running")

    brand_state: dict[str, str] = {}
    note_lock = threading.Lock()

    def note(bk, msg):
        if not progress:
            return
        with note_lock:
            brand_state[bk] = msg
            line = "  ".join(f"{k} {v}" for k, v in brand_state.items())
        progress(f"enrich · {line}")

    def one(brand):
        if should_stop and should_stop():
            return brand.key, None
        note(brand.key, "consolidating…")
        _seed_legacy_matches(engine, month, brand.key)
        try:
            res = enrich_mod.enrich_brand(engine, llm, cfg, month, brand.key)
            note(brand.key, "done")
            return brand.key, res
        except Exception as e:
            note(brand.key, "error")
            return brand.key, {"error": str(e)}

    wanted = [b for b in cfg.brands if not brand_keys or b.key in brand_keys]
    with ThreadPoolExecutor(max_workers=_brand_pool(len(wanted))) as ex:
        for key, res in ex.map(one, wanted):
            if res is not None:
                results[key] = res
    errs = {k: r["error"] for k, r in results.items() if "error" in r}
    if should_stop and should_stop():
        _set_phase(engine, month, "enrich", "stopped — Confirm posts resumes")
    else:
        _set_phase(engine, month, "enrich",
                   ("error: " + "; ".join(f"{k}: {v[:90]}" for k, v in errs.items()))[:300]
                   if errs else "done")
    # only (re)open checkpoint #2 when enrichment actually produced/refreshed
    # drafts — a wholesale "confirmed already, untouched" rerun must not
    # regress a confirmed review
    produced = any(r.get("projects", 0) > 0 for r in results.values()
                   if isinstance(r, dict))
    with engine.connect() as conn:
        current = db.get_run(conn, month)["phases"].get("review_projects")
    if produced or current not in ("confirmed",):
        _set_phase(engine, month, "review_projects", "waiting")
    return results


def confirm_projects_review(month: str) -> None:
    engine = db.get_engine()
    with engine.begin() as conn:
        db.set_phase(conn, month, "review_projects", "confirmed")
        conn.execute(db.projects.update()
                     .where(db.projects.c.month == month,
                            db.projects.c.status == "draft")
                     .values(status="confirmed"))


# -- render (Phase 6) -------------------------------------------------------------

def _project_visuals(conn, factory, brand_key: str, project: dict,
                     celebs: list[dict], note=None, should_stop=None) -> list[dict]:
    from .render.deck import GRID_CAP
    rows = list(conn.execute(
        select(db.posts)
        .join(db.project_posts, db.project_posts.c.post_id == db.posts.c.post_id)
        .where(db.project_posts.c.project_id == project["id"])
        .order_by(db.posts.c.created_at)).mappings())
    is_video = project["assets"] == "VIDEO"
    visuals = []
    labeled_names: set[str] = set()
    batch = rows[: GRID_CAP * 2]
    for idx, row in enumerate(batch, 1):
        # a seeding project can hold dozens of posts — narrate movement
        # inside it and honor Stop between posts, not just between projects
        if should_stop and should_stop():
            return visuals
        if note and len(batch) > 1:
            note(f"post {idx}/{len(batch)}")
        post = dict(row)
        label_top = label_name = None
        caption = (post.get("caption") or "")
        for c in celebs:
            if c.get("name_cn") and c["name_cn"] in caption:
                label_top, label_name = c["relation_display"], c["display"]
                break
        # human-selected images (review checkboxes / HQ uploads) always win;
        # label goes on the first one
        media_list = json.loads(post.get("media") or "[]")
        chosen = [m for m in media_list
                  if m.get("selected") and m.get("local_path")
                  and Path(m["local_path"]).exists()]
        images = [Path(m["local_path"]) for m in chosen]
        if not images:
            # cross-platform members (role=match) only render what a human
            # ticked — automatic visuals come from Weibo posts alone
            if post.get("platform") != "weibo":
                continue

            def first(kind):
                return next((Path(m["local_path"]) for m in media_list
                             if m.get("kind") == kind and m.get("local_path")
                             and Path(m["local_path"]).exists()), None)
            # nothing ticked → the post's OWN photo (what the review-2
            # lightbox shows), never a whole-post card with the caption
            # baked in; the card/live screenshot survives only as the last
            # resort for posts with no usable image file at all
            if is_video:
                img = first("video_cover") or first("image")
            else:
                img = first("image") or first("video_cover")
            img = img or factory.visual_for_post(brand_key, post)
            if img is None:
                continue
            images = [img]
        for i, img in enumerate(images):
            if i == 0 and label_name:
                labeled_names.add(label_name)
            visuals.append({"image": str(img),
                            "kind": "video_still" if is_video else "photo",
                            "link": post["url"] if is_video else None,
                            "label_top": label_top if i == 0 else None,
                            "label_name": label_name if i == 0 else None})
            if len(visuals) >= GRID_CAP:
                return visuals
    # celebs with a curated photo library (Celebs page) but no labeled post
    # visual get their first library image as a labeled visual
    for c in celebs:
        if len(visuals) >= GRID_CAP:
            break
        if not c.get("name_cn") or c.get("display") in labeled_names:
            continue
        reg = conn.execute(
            select(db.celeb_registry.c.images_json)
            .where(db.celeb_registry.c.name_cn == c["name_cn"])).scalar()
        imgs = [p for p in json.loads(reg or "[]") if Path(p).exists()]
        if imgs:
            visuals.append({"image": imgs[0], "kind": "photo", "link": None,
                            "label_top": c.get("relation_display"),
                            "label_name": c.get("display")})
    return visuals


def run_render(month: str, *, visuals_mode: str | None = None,
               include_drafts: bool = False, qa_pngs: bool | None = None,
               only_ids: list[int] | None = None,
               progress=None, should_stop=None) -> dict:
    from .config import DEFAULT_VISUALS, IS_HOSTED
    visuals_mode = visuals_mode or DEFAULT_VISUALS   # local→live, hosted→card
    from .render.deck import BrandSpec, DeckBuilder, ProjectSpec, Visual
    from .render.imgprep import slide_ready
    from .render.qa import run_qa
    from .render.visuals import VisualFactory
    from .render.xlsx import write_projects_xlsx

    def note(msg: str) -> None:
        if progress:
            progress(msg)

    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor

    cfg = BrandsConfig.load()
    engine = db.get_engine()
    _set_phase(engine, month, "render", "running")
    # collect first so progress can report a real i/N over all projects
    with engine.connect() as conn:
        brand_rows = []
        n_available = 0
        for brand in cfg.brands:
            q = select(db.projects).where(db.projects.c.month == month,
                                          db.projects.c.brand == brand.key,
                                          db.projects.c.status != "dropped")
            if not include_drafts:
                q = q.where(db.projects.c.status != "draft")
            rows = [dict(r) for r in conn.execute(
                q.order_by(db.projects.c.date_start)).mappings()]
            n_available += len(rows)
            # the reviewer can render a chosen subset — dropped stays out,
            # untouched projects keep their state for the next full render
            if only_ids is not None:
                rows = [r for r in rows if r["id"] in set(only_ids)]
            brand_rows.append((brand, rows))
    total = sum(len(rows) for _, rows in brand_rows)
    partial = total < n_available
    note(f"render · visuals 0/{total} projects ({visuals_mode} mode"
         f"{', selection' if partial else ''})")

    # projects render in parallel: each is an independent read + Playwright
    # composition. Sync Playwright objects are single-threaded, so every
    # worker THREAD owns its own factory (browser); deck order is restored
    # from (brand index, project index) afterwards.
    tasks = [(bi, brand, pi, p)
             for bi, (brand, rows) in enumerate(brand_rows)
             for pi, p in enumerate(rows)]
    state = {"done": 0}
    lock = threading.Lock()
    tl = threading.local()
    factories: list = []

    def thread_factory():
        f = getattr(tl, "factory", None)
        if f is None:
            f = VisualFactory(month, mode=visuals_mode).__enter__()
            tl.factory = f
            with lock:
                factories.append(f)
        return f

    def build(task):
        bi, brand, pi, p = task
        # cooperative stop: finished visuals are cached on disk per post,
        # so a stopped render re-runs quickly
        if should_stop and should_stop():
            return bi, pi, None
        head = (f"render · visuals {state['done']}/{total} · "
                f"{brand.key} {p['title'][:44]}")
        note(head)
        celebs = json.loads(p["celebs"] or "[]")
        with engine.connect() as conn:
            plats = [r["platform"] for r in conn.execute(
                select(db.platform_matches)
                .where(db.platform_matches.c.project_id == p["id"],
                       db.platform_matches.c.present.is_(True))).mappings()]
            vis = _project_visuals(conn, thread_factory(), brand.key, p,
                                   celebs,
                                   note=(lambda msg, h=head:
                                         note(f"{h} · {msg}")),
                                   should_stop=should_stop)
        # slides embed a shrunk copy of oversized originals (30MB HQ uploads
        # would bloat the PPTX and multiply the QA raster time); the shrink
        # itself runs here so the pool parallelizes it too. slide_ready
        # returns None for unreadable files — drop that visual, never the
        # whole render
        for v in vis:
            v["image"] = slide_ready(v["image"],
                                     OUTPUT_DIR / "deck_img_cache")
        vis = [v for v in vis if v["image"]]
        with lock:
            state["done"] += 1
            done_now = state["done"]
        note(f"render · visuals {done_now}/{total} projects")
        return bi, pi, ProjectSpec(
            title=p["title"], phase_suffix=p["phase_suffix"],
            date_start=p["date_start"] or f"{month}-01",
            date_end=p["date_end"],
            ongoing=bool(p["ongoing"]), assets=p["assets"],
            platforms=plats or ["weibo"],
            description=p["description"] or p["title"],
            visuals=[Visual(**v) for v in vis])

    workers = int(os.environ.get("MM_RENDER_WORKERS", RENDER_WORKERS))
    specs: dict = {}
    try:
        if tasks:
            with ThreadPoolExecutor(
                    max_workers=max(1, min(workers, len(tasks)))) as ex:
                for bi, pi, spec in ex.map(build, tasks):
                    specs[(bi, pi)] = spec
    finally:
        # browsers close before the memory-hungry QA raster starts
        for f in factories:
            f.__exit__(None, None, None)

    if should_stop and should_stop():
        note("render · stopped — nothing written; Confirm & render restarts")
        _set_phase(engine, month, "render",
                   "stopped — Confirm & render restarts")
        return {"stopped": True, "visuals_done": state["done"],
                "visuals_total": total}
    brands_spec: list[BrandSpec] = []
    for bi, (brand, rows) in enumerate(brand_rows):
        projects = [specs[(bi, pi)] for pi in range(len(rows))
                    if specs.get((bi, pi)) is not None]
        if projects:
            brands_spec.append(BrandSpec(key=brand.key,
                                         display_name=brand.display_name,
                                         projects=projects))
    year, mm_ = month.split("-")
    # every render writes NEW files — a Beijing-time stamp (plus a PARTIAL
    # marker for subset renders) keeps versions side by side on the Decks
    # page instead of silently overwriting the previous deck
    from datetime import datetime as _dt
    from .dates import CST
    stamp = _dt.now(CST).strftime("%Y%m%d-%H%M%S")
    tag = f"{'_PARTIAL' if partial else ''}_{stamp}"
    name = (f"_CREATIVE_{year}_{deck_month_token(month)}"
            f"_PR_COMPETITOR_REPORT_FASHION{tag}.pptx")
    out_pptx = OUTPUT_DIR / name
    note("render · composing the deck (PPTX)…")
    DeckBuilder().build(brands_spec, out_pptx)
    note("render · writing the spreadsheet (XLSX)…")
    out_xlsx = OUTPUT_DIR / f"{month}_projects{tag}.xlsx"
    write_projects_xlsx(brands_spec, out_xlsx)
    # the QA slide raster (LibreOffice → PDF → PNGs) is minutes of work whose
    # output nothing on the hosted box ever displays — hosted skips it by
    # default (MM_QA_PNGS=1 re-enables); the cheap package/placeholder/
    # geometry checks always run and alone decide report["ok"]
    if qa_pngs is None:
        qa_pngs = os.environ.get("MM_QA_PNGS",
                                 "0" if IS_HOSTED else "1") == "1"
    qa_dir = OUTPUT_DIR / f"{month}_qa" if qa_pngs else None
    note("render · QA raster via LibreOffice (the slowest step)…" if qa_pngs
         else "render · QA checks (raster skipped on hosted; MM_QA_PNGS=1 "
              "enables)…")
    report = run_qa(out_pptx, qa_dir)
    with db.get_engine().begin() as conn:
        db.set_phase(conn, month, "render", "done" if report["ok"] else "error")
        conn.execute(db.projects.update()
                     .where(db.projects.c.month == month,
                            db.projects.c.status == "confirmed")
                     .values(status="rendered"))
    note("render · done" if report["ok"] else "render · QA flagged issues")
    return {"pptx": str(out_pptx), "xlsx": str(out_xlsx), "qa": report}


def status(month: str) -> dict:
    engine = db.get_engine()
    with engine.connect() as conn:
        run = db.get_run(conn, month)
        n_posts = conn.execute(
            select(db.posts.c.post_id).where(db.posts.c.month == month)).all()
        n_projects = conn.execute(
            select(db.projects.c.id).where(db.projects.c.month == month)).all()
    return {"month": month, "phases": run["phases"],
            "posts": len(n_posts), "projects": len(n_projects)}
