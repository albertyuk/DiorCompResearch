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


def _matches_path(month: str, brand_key: str) -> Path:
    p = RUNS_DIR / month / brand_key
    p.mkdir(parents=True, exist_ok=True)
    return p / "crosscheck_matches.json"


def run_resolve_check(cfg: BrandsConfig) -> list[dict]:
    from .resolve import unresolved_accounts
    return unresolved_accounts(cfg)


def _set_phase(engine, month: str, phase: str, status: str) -> None:
    with engine.begin() as conn:
        db.set_phase(conn, month, phase, status)


def run_ingest(month: str, brand_keys: list[str] | None = None) -> dict:
    cfg = BrandsConfig.load()
    settings = Settings.load()
    client = TikHubClient(settings)
    engine = db.get_engine()
    results = {}
    _set_phase(engine, month, "ingest", "running")
    try:
        for brand in cfg.brands:
            if brand_keys and brand.key not in brand_keys:
                continue
            try:
                results[brand.key] = ingest.ingest_weibo(engine, client, cfg,
                                                         month, brand.key)
            except Exception as e:
                results[brand.key] = {"error": str(e)}
    finally:
        client.close()
    ok = not any("error" in r for r in results.values())
    _set_phase(engine, month, "ingest", "done" if ok else "error")
    return results


def run_filter(month: str, brand_keys: list[str] | None = None) -> dict:
    cfg = BrandsConfig.load()
    llm = LLM()
    engine = db.get_engine()
    _set_phase(engine, month, "filter", "running")
    try:
        stats = filtering.filter_month(engine, llm, cfg, month,
                                       brand_keys[0] if brand_keys and
                                       len(brand_keys) == 1 else None)
    except Exception:
        _set_phase(engine, month, "filter", "error")
        raise
    ok = stats.get("errors", 0) == 0
    _set_phase(engine, month, "filter", "done" if ok
               else f"error: {stats['errors']} posts failed — re-run filter")
    _set_phase(engine, month, "review_posts", "waiting")
    return stats


def confirm_posts_review(month: str) -> None:
    engine = db.get_engine()
    with engine.begin() as conn:
        db.set_phase(conn, month, "review_posts", "confirmed")


def run_crosscheck(month: str, brand_keys: list[str] | None = None) -> dict:
    cfg = BrandsConfig.load()
    settings = Settings.load()
    client = TikHubClient(settings)
    llm = LLM(settings)
    engine = db.get_engine()
    results = {}
    _set_phase(engine, month, "crosscheck", "running")
    try:
        for brand in cfg.brands:
            if brand_keys and brand.key not in brand_keys:
                continue
            try:
                pulls = xc.pull_all(engine, client, cfg, month, brand.key)
                res = xc.crosscheck_brand(engine, llm, cfg, month, brand.key)
                _matches_path(month, brand.key).write_text(
                    json.dumps(res["matches"], ensure_ascii=False, indent=1))
                results[brand.key] = {"pulls": pulls, "orphans": res["orphans"]}
            except Exception as e:
                results[brand.key] = {"error": str(e)}
    finally:
        client.close()
    ok = not any(isinstance(r, dict) and "error" in r for r in results.values())
    _set_phase(engine, month, "crosscheck", "done" if ok else "error")
    return results


def run_enrich(month: str, brand_keys: list[str] | None = None) -> dict:
    cfg = BrandsConfig.load()
    llm = LLM()
    engine = db.get_engine()
    results = {}
    _set_phase(engine, month, "enrich", "running")
    for brand in cfg.brands:
        if brand_keys and brand.key not in brand_keys:
            continue
        matches = {}
        mp = _matches_path(month, brand.key)
        if mp.exists():
            matches = json.loads(mp.read_text())
        try:
            results[brand.key] = enrich_mod.enrich_brand(
                engine, llm, cfg, month, brand.key, matches)
        except Exception as e:
            results[brand.key] = {"error": str(e)}
    ok = not any("error" in r for r in results.values())
    _set_phase(engine, month, "enrich", "done" if ok else "error")
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
                     celebs: list[dict]) -> list[dict]:
    from .render.deck import GRID_CAP
    rows = list(conn.execute(
        select(db.posts)
        .join(db.project_posts, db.project_posts.c.post_id == db.posts.c.post_id)
        .where(db.project_posts.c.project_id == project["id"])
        .order_by(db.posts.c.created_at)).mappings())
    is_video = project["assets"] == "VIDEO"
    visuals = []
    for row in rows[: GRID_CAP * 2]:
        post = dict(row)
        if is_video:
            img = factory.video_cover_for_post(brand_key, post) \
                  or factory.visual_for_post(brand_key, post)
        else:
            img = factory.visual_for_post(brand_key, post)
        if img is None:
            continue
        label_top = label_name = None
        caption = (post.get("caption") or "")
        for c in celebs:
            if c.get("name_cn") and c["name_cn"] in caption:
                label_top, label_name = c["relation_display"], c["display"]
                break
        visuals.append({"image": str(img),
                        "kind": "video_still" if is_video else "photo",
                        "link": post["url"] if is_video else None,
                        "label_top": label_top, "label_name": label_name})
        if len(visuals) >= GRID_CAP:
            break
    return visuals


def run_render(month: str, *, visuals_mode: str = "live",
               include_drafts: bool = False, qa_pngs: bool = True) -> dict:
    from .render.deck import BrandSpec, DeckBuilder, ProjectSpec, Visual
    from .render.qa import run_qa
    from .render.visuals import VisualFactory
    from .render.xlsx import write_projects_xlsx

    cfg = BrandsConfig.load()
    engine = db.get_engine()
    _set_phase(engine, month, "render", "running")
    brands_spec: list[BrandSpec] = []
    with engine.connect() as conn, \
            VisualFactory(month, mode=visuals_mode) as factory:
        for brand in cfg.brands:
            q = select(db.projects).where(db.projects.c.month == month,
                                          db.projects.c.brand == brand.key,
                                          db.projects.c.status != "dropped")
            if not include_drafts:
                q = q.where(db.projects.c.status != "draft")
            rows = [dict(r) for r in conn.execute(
                q.order_by(db.projects.c.date_start)).mappings()]
            projects = []
            for p in rows:
                celebs = json.loads(p["celebs"] or "[]")
                plats = [r["platform"] for r in conn.execute(
                    select(db.platform_matches)
                    .where(db.platform_matches.c.project_id == p["id"],
                           db.platform_matches.c.present.is_(True))).mappings()]
                vis = _project_visuals(conn, factory, brand.key, p, celebs)
                projects.append(ProjectSpec(
                    title=p["title"], phase_suffix=p["phase_suffix"],
                    date_start=p["date_start"] or f"{month}-01",
                    date_end=p["date_end"],
                    ongoing=bool(p["ongoing"]), assets=p["assets"],
                    platforms=plats or ["weibo"],
                    description=p["description"] or p["title"],
                    visuals=[Visual(**v) for v in vis]))
            if projects:
                brands_spec.append(BrandSpec(key=brand.key,
                                             display_name=brand.display_name,
                                             projects=projects))
    year, mm_ = month.split("-")
    name = f"_CREATIVE_{year}_{deck_month_token(month)}_PR_COMPETITOR_REPORT_FASHION.pptx"
    out_pptx = OUTPUT_DIR / name
    DeckBuilder().build(brands_spec, out_pptx)
    out_xlsx = OUTPUT_DIR / f"{month}_projects.xlsx"
    write_projects_xlsx(brands_spec, out_xlsx)
    qa_dir = OUTPUT_DIR / f"{month}_qa" if qa_pngs else None
    report = run_qa(out_pptx, qa_dir)
    with db.get_engine().begin() as conn:
        db.set_phase(conn, month, "render", "done" if report["ok"] else "error")
        conn.execute(db.projects.update()
                     .where(db.projects.c.month == month,
                            db.projects.c.status == "confirmed")
                     .values(status="rendered"))
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
