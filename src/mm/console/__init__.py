"""Maison Monitor Console — one FastAPI process serving UI + JSON API on
localhost. Pipeline phases run as background threads in-process; the UI polls
/api/runs/{month}/status. Remote access is out of scope by design (see README).
"""
from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .. import db, pipeline
from ..config import DATA_DIR, OUTPUT_DIR, BrandsConfig, Settings
from ..dates import previous_month
from ..resolve import confirm_account, lookup_candidates, unresolved_accounts
from ..tikhub import TikHubClient

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# background task registry: {(month, phase): {"state": .., "detail": ..}}
TASKS: dict = {}
_LOCK = threading.Lock()


def _spawn(month: str, name: str, fn, *args, **kwargs):
    key = f"{month}:{name}"
    with _LOCK:
        if TASKS.get(key, {}).get("state") == "running":
            return False
        TASKS[key] = {"state": "running", "detail": ""}

    def worker():
        try:
            result = fn(*args, **kwargs)
            TASKS[key] = {"state": "done",
                          "detail": json.dumps(result, ensure_ascii=False,
                                               default=str)[:2000]}
        except Exception as e:
            traceback.print_exc()
            TASKS[key] = {"state": "error", "detail": str(e)[:500]}

    threading.Thread(target=worker, daemon=True).start()
    return True


def _ingest_and_filter(month):
    r1 = pipeline.run_ingest(month)
    r2 = pipeline.run_filter(month)
    return {"ingest": r1, "filter": r2}


def _crosscheck_and_enrich(month):
    r1 = pipeline.run_crosscheck(month)
    r2 = pipeline.run_enrich(month)
    return {"crosscheck": r1, "enrich": r2}


def create_app() -> FastAPI:
    app = FastAPI(title="Maison Monitor Console")

    # ---------- pages ----------

    @app.get("/", response_class=HTMLResponse)
    def runs_view(request: Request):
        cfg = BrandsConfig.load()
        engine = db.get_engine()
        months = []
        with engine.connect() as conn:
            for row in conn.execute(select(db.runs).order_by(db.runs.c.month.desc())).mappings():
                months.append({**dict(row), "phases": json.loads(row["phase_status"] or "{}"),
                               "cost": db.cost_summary(conn, row["month"])})
        return TEMPLATES.TemplateResponse(request, "runs.html", {
            "months": months, "default_month": previous_month(),
            "unresolved": unresolved_accounts(cfg), "tasks": TASKS})

    @app.get("/review/{month}", response_class=HTMLResponse)
    @app.get("/review/{month}/posts", response_class=HTMLResponse)
    def posts_view(request: Request, month: str):
        cfg = BrandsConfig.load()
        engine = db.get_engine()
        groups = []
        with engine.connect() as conn:
            for brand in cfg.brands:
                rows = list(conn.execute(
                    select(db.posts, db.verdicts)
                    .join(db.verdicts, db.verdicts.c.post_id == db.posts.c.post_id,
                          isouter=True)
                    .where(db.posts.c.month == month,
                           db.posts.c.brand == brand.key,
                           db.posts.c.platform == "weibo")
                    .order_by(db.verdicts.c.needs_review.desc(),
                              db.posts.c.created_at)).mappings())
                posts = []
                for r in rows:
                    d = dict(r)
                    d["media_list"] = json.loads(d.get("media") or "[]")
                    d["thumb"] = next((m.get("local_path") for m in d["media_list"]
                                       if m.get("local_path")), None)
                    d["reasons_list"] = json.loads(d.get("reasons") or "[]")
                    d["celebs_list"] = json.loads(d.get("celebs_tagged") or "[]")
                    decision = d.get("human_decision")
                    d["effective_keep"] = (d.get("keep") if decision is None
                                           else decision == "keep")
                    posts.append(d)
                groups.append({"brand": brand, "posts": posts})
            run = db.get_run(conn, month)
        return TEMPLATES.TemplateResponse(request, "posts.html", {
            "month": month, "groups": groups, "phases": run["phases"]})

    @app.get("/review/{month}/projects", response_class=HTMLResponse)
    def projects_view(request: Request, month: str):
        cfg = BrandsConfig.load()
        engine = db.get_engine()
        groups, orphans = [], []
        with engine.connect() as conn:
            for brand in cfg.brands:
                rows = [dict(r) for r in conn.execute(
                    select(db.projects).where(db.projects.c.month == month,
                                              db.projects.c.brand == brand.key)
                    .order_by(db.projects.c.date_start)).mappings()]
                for p in rows:
                    p["celebs_list"] = json.loads(p["celebs"] or "[]")
                    p["hero_list"] = json.loads(p["hero_media"] or "[]")
                    p["platforms"] = {
                        r["platform"]: dict(r) for r in conn.execute(
                            select(db.platform_matches)
                            .where(db.platform_matches.c.project_id == p["id"])).mappings()}
                groups.append({"brand": brand, "projects": rows})
            for r in conn.execute(
                    select(db.orphans, db.posts)
                    .join(db.posts, db.posts.c.post_id == db.orphans.c.post_id)
                    .where(db.orphans.c.month == month,
                           db.orphans.c.resolution == "pending")).mappings():
                d = dict(r)
                d["media_list"] = json.loads(d.get("media") or "[]")
                d["thumb"] = next((m.get("local_path") for m in d["media_list"]
                                   if m.get("local_path")), None)
                orphans.append(d)
            run = db.get_run(conn, month)
            registry = [dict(r) for r in conn.execute(select(db.celeb_registry)).mappings()]
        return TEMPLATES.TemplateResponse(request, "projects.html", {
            "month": month, "groups": groups, "orphans": orphans,
            "phases": run["phases"], "registry": registry, "tasks": TASKS})

    @app.get("/decks", response_class=HTMLResponse)
    def decks_view(request: Request):
        files = sorted(OUTPUT_DIR.glob("*.pptx")) + sorted(OUTPUT_DIR.glob("*.xlsx"))
        return TEMPLATES.TemplateResponse(request, "decks.html", {
            "files": [{"name": f.name, "size_mb": round(f.stat().st_size / 1e6, 1)}
                      for f in files]})

    # ---------- actions ----------

    @app.post("/runs/{month}/start")
    def start_run(month: str):
        cfg = BrandsConfig.load()
        missing = [u for u in unresolved_accounts(cfg) if u["platform"] == "weibo"]
        if missing:
            return JSONResponse({"error": "unresolved weibo accounts",
                                 "accounts": missing}, status_code=409)
        _spawn(month, "ingest_filter", _ingest_and_filter, month)
        return RedirectResponse("/", status_code=303)

    @app.get("/api/runs/{month}/status")
    def run_status(month: str):
        st = pipeline.status(month)
        st["tasks"] = {k: v for k, v in TASKS.items() if k.startswith(month)}
        return st

    @app.post("/review/{month}/posts/{post_id}/decision")
    def post_decision(month: str, post_id: str, decision: str = Form(...)):
        engine = db.get_engine()
        with engine.begin() as conn:
            value = None if decision == "restore" else decision
            conn.execute(db.verdicts.update()
                         .where(db.verdicts.c.post_id == post_id)
                         .values(human_decision=value, decided_at=db.now_iso()))
        return RedirectResponse(f"/review/{month}/posts", status_code=303)

    @app.post("/review/{month}/posts/confirm")
    def posts_confirm(month: str):
        pipeline.confirm_posts_review(month)
        _spawn(month, "crosscheck_enrich", _crosscheck_and_enrich, month)
        return RedirectResponse(f"/review/{month}/projects", status_code=303)

    @app.post("/review/{month}/projects/{project_id}/update")
    async def project_update(month: str, project_id: int, request: Request):
        form = dict(await request.form())
        engine = db.get_engine()
        values = {}
        for k in ("title", "phase_suffix", "description", "date_start",
                  "date_end", "assets"):
            if k in form:
                v = form[k].strip()
                values[k] = v or None
        # checkboxes: browsers omit unchecked boxes entirely, so treat any
        # submission of this form (marked by _platforms_submitted) as
        # authoritative for ongoing too
        if "ongoing" in form or form.get("_platforms_submitted"):
            values["ongoing"] = form.get("ongoing") == "on"
        if "celebs_json" in form:
            try:
                values["celebs"] = json.dumps(json.loads(form["celebs_json"]),
                                              ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        with engine.begin() as conn:
            if values:
                conn.execute(db.projects.update()
                             .where(db.projects.c.id == project_id).values(**values))
            for plat in ("weibo", "xhs", "wechat_mp", "wechat_channels", "douyin"):
                key = f"platform_{plat}"
                if key in form or form.get("_platforms_submitted"):
                    db.upsert(conn, db.platform_matches,
                              {"project_id": project_id, "platform": plat,
                               "present": key in form}, ["project_id", "platform"])
        return RedirectResponse(f"/review/{month}/projects", status_code=303)

    @app.post("/review/{month}/projects/{project_id}/status")
    def project_status(month: str, project_id: int, value: str = Form(...)):
        engine = db.get_engine()
        with engine.begin() as conn:
            conn.execute(db.projects.update()
                         .where(db.projects.c.id == project_id)
                         .values(status=value))
        return RedirectResponse(f"/review/{month}/projects", status_code=303)

    @app.post("/review/{month}/orphans/{post_id:path}/resolve")
    def orphan_resolve(month: str, post_id: str, action: str = Form(...)):
        engine = db.get_engine()
        with engine.begin() as conn:
            if action == "promote":
                row = conn.execute(select(db.posts)
                                   .where(db.posts.c.post_id == post_id)).mappings().first()
                if row:
                    res = conn.execute(db.projects.insert().values(
                        month=month, brand=row["brand"],
                        title=(row["caption"] or "ORPHAN PROJECT")[:60].upper(),
                        phase_suffix=None,
                        date_start=(row["created_at"] or f"{month}-01")[:10],
                        date_end=(row["created_at"] or f"{month}-01")[:10],
                        ongoing=False, assets="PHOTO",
                        description=(row["caption"] or "")[:90].upper(),
                        celebs="[]", hero_media="[]", status="draft"))
                    pid = res.inserted_primary_key[0]
                    db.upsert(conn, db.project_posts,
                              {"project_id": pid, "post_id": post_id,
                               "role": "member"}, ["project_id", "post_id"])
                    db.upsert(conn, db.platform_matches,
                              {"project_id": pid, "platform": row["platform"],
                               "present": True, "matched_url": row["url"],
                               "matched_date": row["created_at"],
                               "confidence": 1.0}, ["project_id", "platform"])
            conn.execute(db.orphans.update()
                         .where(db.orphans.c.post_id == post_id)
                         .values(resolution="promoted" if action == "promote"
                                 else "ignored"))
        return RedirectResponse(f"/review/{month}/projects", status_code=303)

    @app.post("/review/{month}/render")
    def render_deck(month: str, visuals: str = Form("live")):
        pipeline.confirm_projects_review(month)
        _spawn(month, "render", pipeline.run_render, month, visuals_mode=visuals)
        return RedirectResponse(f"/review/{month}/projects", status_code=303)

    # ---------- Phase R ----------

    @app.get("/resolve/{brand_key}/{platform}", response_class=HTMLResponse)
    def resolve_candidates(request: Request, brand_key: str, platform: str):
        cfg = BrandsConfig.load()
        brand = cfg.brand(brand_key)
        acct = brand.account(platform)
        query = (acct.lookup_query if acct and acct.lookup_query
                 else brand.display_name)
        error, candidates = None, []
        try:
            client = TikHubClient(Settings.load())
            engine = db.get_engine()
            with engine.begin() as conn:
                candidates = lookup_candidates(client, platform, query, conn)
            client.close()
        except Exception as e:
            error = str(e)
        return TEMPLATES.TemplateResponse(request, "_resolve.html", {
            "brand": brand, "platform": platform, "query": query,
            "candidates": candidates, "error": error,
            "manual_note": ("WeChat Channels IDs are not web-discoverable — "
                            "paste the finder username (v2_…@finder) from the "
                            "WeChat app." if platform == "wechat_channels" else None)})

    @app.post("/resolve/{brand_key}/{platform}/confirm")
    def resolve_confirm(brand_key: str, platform: str, uid: str = Form(...),
                        name: str = Form("")):
        cfg = BrandsConfig.load()
        confirm_account(cfg, brand_key, platform, uid.strip(), name.strip() or None)
        return RedirectResponse("/", status_code=303)

    # ---------- assets ----------

    @app.get("/media")
    def media(path: str):
        p = Path(path).resolve()
        allowed = (p.is_relative_to(DATA_DIR.resolve())
                   or p.is_relative_to(OUTPUT_DIR.resolve()))
        if not allowed:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(p))

    @app.get("/download/{name}")
    def download(name: str):
        p = (OUTPUT_DIR / name).resolve()
        if not p.is_relative_to(OUTPUT_DIR.resolve()) or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(p), filename=name)

    return app
