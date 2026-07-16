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
                               RedirectResponse, Response)
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.background import BackgroundTask

from .. import db, naming, pipeline
from ..config import (DATA_DIR, DB_PATH, DEFAULT_VISUALS, IS_HOSTED, OUTPUT_DIR,
                      BrandsConfig, Settings, console_auth_config)
from ..dates import previous_month
from ..resolve import (confirm_account, lookup_candidates, unresolved_accounts,
                       weibo_blockers)
from ..tikhub import TikHubClient
from .auth import PUBLIC_PATHS, SessionAuth

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# background task registry: {(month, phase): {"state": .., "detail": ..}}
TASKS: dict = {}
# posts.media is a JSON blob edited read-modify-write by media_select and
# media_upload — concurrent edits (two reviewers, or a double-click racing an
# upload) must serialize or one edit is silently lost. In-process lock is
# sufficient: the console process is the only writer by design.
_MEDIA_LOCK = threading.Lock()
# cooperative-stop flags per task key; kept out of TASKS so the status
# endpoint's JSON never has to serialize an Event
STOP_EVENTS: dict[str, threading.Event] = {}
# per-month rolling activity feed shown on the Runs page
ACTIVITY: dict[str, object] = {}
_LOCK = threading.Lock()


def _log_activity(month: str, msg: str) -> None:
    from collections import deque
    from datetime import datetime
    with _LOCK:
        log = ACTIVITY.setdefault(month, deque(maxlen=200))
    log.append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")


def _spawn(month: str, name: str, fn, *args, **kwargs):
    key = f"{month}:{name}"
    with _LOCK:
        if TASKS.get(key, {}).get("state") == "running":
            return False
        TASKS[key] = {"state": "running", "detail": ""}
        STOP_EVENTS[key] = threading.Event()
    _log_activity(month, f"{name} started")

    def worker():
        try:
            result = fn(*args, **kwargs)
            state = "stopped" if STOP_EVENTS[key].is_set() else "done"
            TASKS[key] = {"state": state,
                          "detail": json.dumps(result, ensure_ascii=False,
                                               default=str)[:2000]}
            _log_activity(month, f"{name} {state}")
        except Exception as e:
            traceback.print_exc()
            TASKS[key] = {"state": "error", "detail": str(e)[:500]}
            _log_activity(month, f"{name} error: {str(e)[:200]}")

    # hosted: non-daemon so a deploy's SIGINT lets the phase reach its next
    # checkpoint within fly.toml's kill_timeout; local Ctrl-C stays instant
    from ..config import IS_HOSTED as _hosted
    threading.Thread(target=worker, daemon=not _hosted).start()
    return True


def _task_note(month: str, name: str):
    """Progress callback: streams a phase's live position into TASKS (for the
    Runs page poller) and the month's activity feed."""
    key = f"{month}:{name}"

    def note(msg: str):
        t = TASKS.get(key)
        if t is not None:
            t["detail"] = msg
        _log_activity(month, msg)
    return note


def _stop_flag(month: str, name: str):
    ev = STOP_EVENTS.get(f"{month}:{name}")
    return ev.is_set if ev is not None else (lambda: False)


def _ingest_and_filter(month):
    note = _task_note(month, "ingest_filter")
    stop = _stop_flag(month, "ingest_filter")
    r1 = pipeline.run_ingest(month, progress=note, should_stop=stop)
    if stop():
        return {"ingest": r1, "stopped": True}
    r2 = pipeline.run_filter(month, progress=note, should_stop=stop)
    return {"ingest": r1, "filter": r2}


def _crosscheck_and_enrich(month):
    note = _task_note(month, "crosscheck_enrich")
    stop = _stop_flag(month, "crosscheck_enrich")
    r1 = pipeline.run_crosscheck(month, progress=note, should_stop=stop)
    if stop():
        return {"crosscheck": r1, "stopped": True}
    r2 = pipeline.run_enrich(month, progress=note, should_stop=stop)
    return {"crosscheck": r1, "enrich": r2}


def _render_task(month, visuals_mode):
    # note/stop resolve inside the worker: _spawn has registered the task
    # and its stop event by the time this runs
    return pipeline.run_render(month, visuals_mode=visuals_mode,
                               progress=_task_note(month, "render"),
                               should_stop=_stop_flag(month, "render"))


def _spinoff_project(conn, month: str, proj, post, rationale: str,
                     taken: set) -> int:
    """A single-post draft project for a weibo post leaving `proj` (used by
    Ungroup and per-post Remove). `taken` = (title, phase_suffix) pairs
    already used this month·brand; the new title is deduped into it."""
    import re as _re
    from .. import naming as _naming
    caption = _re.sub(r"\s+", " ", (post["caption"] or "")).strip()
    base = (caption[:60].upper() or proj["title"])
    title, n = base, 2
    while (title, None) in taken:
        title, n = f"{base[:54]} ({n})", n + 1
    taken.add((title, None))
    media = json.loads(post["media"] or "[]")
    has_video = any(x.get("kind") == "video_cover" for x in media)
    has_photo = any(x.get("kind") == "image" for x in media)
    celebs = json.loads(proj["celebs"] or "[]")
    res = conn.execute(db.projects.insert().values(
        month=month, brand=proj["brand"], title=title, phase_suffix=None,
        date_start=(post["created_at"] or f"{month}-01")[:10],
        date_end=(post["created_at"] or f"{month}-01")[:10],
        ongoing=False,
        assets=_naming.assets_label(has_photo or not has_video, has_video),
        description=caption[:90].upper() or title,
        celebs=json.dumps([c for c in celebs if c.get("name_cn")
                           and c["name_cn"] in (post["caption"] or "")],
                          ensure_ascii=False),
        hero_media=json.dumps([x["local_path"] for x in media
                               if x.get("local_path")][:3],
                              ensure_ascii=False),
        status="draft", rationale=rationale))
    pid = res.inserted_primary_key[0]
    db.upsert(conn, db.project_posts,
              {"project_id": pid, "post_id": post["post_id"],
               "role": "member"}, ["project_id", "post_id"])
    db.upsert(conn, db.platform_matches,
              {"project_id": pid, "platform": "weibo", "present": True,
               "matched_url": post["url"], "matched_date": post["created_at"],
               "matched_post_id": post["post_id"], "confidence": 1.0},
              ["project_id", "platform"])
    return pid


def create_app() -> FastAPI:
    app = FastAPI(title="Maison Monitor Console")
    auth_cfg = console_auth_config()
    auth = (SessionAuth(auth_cfg["passphrase"], auth_cfg["secret"],
                        secure_cookie=IS_HOSTED)
            if auth_cfg["enabled"] else None)

    def _actor(request: Request) -> str:
        return getattr(request.state, "actor", None) or "local"

    @app.middleware("http")
    async def auth_and_headers(request: Request, call_next):
        path = request.url.path
        if auth is not None and path not in PUBLIC_PATHS:
            actor = auth.actor_from_request(request)
            if actor is None:
                wants_json = (request.method != "GET"
                              or path.startswith(("/api/", "/media", "/download",
                                                  "/backup")))
                if wants_json:
                    resp = JSONResponse({"error": "authentication required"},
                                        status_code=401)
                else:
                    resp = RedirectResponse("/login", status_code=303)
                resp.headers["X-Robots-Tag"] = "noindex"
                return resp
            request.state.actor = actor
        else:
            request.state.actor = None if (auth is not None) else "local"
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex"
        return response

    # ---------- auth ----------

    @app.get("/healthz")
    def healthz():
        return Response(status_code=200)     # no data, for platform checks

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if auth is None:
            return RedirectResponse("/", status_code=303)
        return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login", response_class=HTMLResponse)
    def login_submit(request: Request, name: str = Form(""),
                     passphrase: str = Form("")):
        if auth is None:
            return RedirectResponse("/", status_code=303)
        name = name.strip()[:80]
        if not name or not auth.check_passphrase(passphrase):
            return TEMPLATES.TemplateResponse(request, "login.html", {
                "error": "Wrong passphrase (or missing name) — ask the deck "
                         "owner for the current team passphrase."},
                status_code=401)
        resp = RedirectResponse("/", status_code=303)
        auth.set_cookie(resp, name)
        db.audit(db.get_engine(), name, "login")
        return resp

    @app.post("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie("mm_session", path="/")
        return resp

    # ---------- pages ----------

    @app.get("/", response_class=HTMLResponse)
    def runs_view(request: Request, msg: str = ""):
        cfg = BrandsConfig.load()
        engine = db.get_engine()
        months = []
        with engine.connect() as conn:
            for row in conn.execute(select(db.runs).order_by(db.runs.c.month.desc())).mappings():
                months.append({**dict(row), "phases": json.loads(row["phase_status"] or "{}"),
                               "cost": db.cost_summary(conn, row["month"]),
                               "started_by": db.last_audit(conn, "start_month",
                                                           "month", row["month"]),
                               "archives": db.list_archives(conn, row["month"])})
        return TEMPLATES.TemplateResponse(request, "runs.html", {
            "months": months, "default_month": previous_month(),
            "unresolved": unresolved_accounts(cfg), "msg": msg, "tasks": TASKS})

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
            started_by = db.last_audit(conn, "start_month", "month", month)
        busy = any(k.startswith(f"{month}:") and v.get("state") == "running"
                   for k, v in TASKS.items())
        return TEMPLATES.TemplateResponse(request, "posts.html", {
            "month": month, "groups": groups, "phases": run["phases"],
            "busy": busy, "started_by": started_by})

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
                    # evidence links prefer the post's LIVE url — hydration /
                    # re-pulls fix posts.url in place, while matched_url is
                    # frozen at enrich time (and dead for old xhs matches)
                    for pm in p["platforms"].values():
                        if pm.get("matched_post_id"):
                            live = conn.execute(
                                select(db.posts.c.url).where(
                                    db.posts.c.post_id == pm["matched_post_id"])
                            ).scalar()
                            if live:
                                pm["matched_url"] = live
                    # the consolidated posts behind this project — weibo
                    # members first, then cross-platform matches
                    members = []
                    for r in conn.execute(
                            select(db.posts, db.project_posts.c.role)
                            .join(db.project_posts,
                                  db.project_posts.c.post_id == db.posts.c.post_id)
                            .where(db.project_posts.c.project_id == p["id"])
                            .order_by(db.posts.c.platform != "weibo",
                                      db.posts.c.created_at)).mappings():
                        d = dict(r)
                        d["media_list"] = json.loads(d.get("media") or "[]")
                        d["thumb"] = next((m.get("local_path")
                                           for m in d["media_list"]
                                           if m.get("local_path")), None)
                        members.append(d)
                    p["member_posts"] = members
                groups.append({"brand": brand, "projects": rows})
            for r in conn.execute(
                    select(db.orphans, db.posts, db.verdicts.c.keep,
                           db.verdicts.c.confidence, db.verdicts.c.rationale,
                           db.verdicts.c.reasons)
                    .join(db.posts, db.posts.c.post_id == db.orphans.c.post_id)
                    .join(db.verdicts,
                          db.verdicts.c.post_id == db.orphans.c.post_id,
                          isouter=True)
                    .where(db.orphans.c.month == month,
                           db.orphans.c.resolution == "pending")).mappings():
                d = dict(r)
                d["media_list"] = json.loads(d.get("media") or "[]")
                d["thumb"] = next((m.get("local_path") for m in d["media_list"]
                                   if m.get("local_path")), None)
                # filtered like review #1: dropped orphans grey out, keeps
                # (and not-yet-filtered) stay prominent
                d["effective_keep"] = d["keep"] is None or bool(d["keep"])
                orphans.append(d)
            orphans.sort(key=lambda o: (not o["effective_keep"],
                                        o.get("created_at") or ""))
            run = db.get_run(conn, month)
            registry = [dict(r) for r in conn.execute(select(db.celeb_registry)).mappings()]
            confirmed_by = db.last_audit(conn, "confirm_posts", "month", month)
            rendered_by = db.last_audit(conn, "render", "month", month)
        busy = any(k.startswith(f"{month}:") and v.get("state") == "running"
                   for k, v in TASKS.items())
        return TEMPLATES.TemplateResponse(request, "projects.html", {
            "month": month, "groups": groups, "orphans": orphans,
            "phases": run["phases"], "registry": registry, "tasks": TASKS,
            "confirmed_by": confirmed_by, "rendered_by": rendered_by,
            "default_visuals": DEFAULT_VISUALS, "busy": busy})

    @app.get("/archives", response_class=HTMLResponse)
    def archives_view(request: Request):
        with db.get_engine().connect() as conn:
            items = db.list_archives(conn)
        return TEMPLATES.TemplateResponse(request, "archives.html",
                                          {"items": items})

    @app.get("/archives/{archive_id}", response_class=HTMLResponse)
    def archive_detail(request: Request, archive_id: int):
        cfg = BrandsConfig.load()
        with db.get_engine().connect() as conn:
            meta = conn.execute(select(db.archives).where(
                db.archives.c.id == archive_id)).mappings().first()
            if meta is None:
                return HTMLResponse("archive not found", status_code=404)
            rows = conn.execute(select(db.archive_rows).where(
                db.archive_rows.c.archive_id == archive_id)).mappings().all()
        by_tbl: dict[str, list] = {}
        for r in rows:
            by_tbl.setdefault(r["tbl"], []).append(json.loads(r["row"]))
        verdicts_by_post = {v["post_id"]: v for v in by_tbl.get("verdicts", [])}
        # group by the brand keys IN THE SNAPSHOT — the archive must stay
        # complete even if a brand is later removed/renamed in brands.yaml
        display = {b.key: b.display_name for b in cfg.brands}
        order = {b.key: i for i, b in enumerate(cfg.brands)}
        by_brand: dict[str, list] = {}
        for p in by_tbl.get("posts", []):
            v = verdicts_by_post.get(p["post_id"], {})
            media = json.loads(p.get("media") or "[]")
            decision = v.get("human_decision")
            # a post that never got a verdict was not dropped — don't grey it
            keep = (True if not v else
                    v.get("keep") if decision is None else decision == "keep")
            by_brand.setdefault(p.get("brand") or "unknown", []).append({
                **p, "verdict": v,
                "thumb": next((m.get("local_path") for m in media
                               if m.get("local_path")), None),
                "images": [m["local_path"] for m in media
                           if m.get("local_path")],
                "effective_keep": keep})
        groups = []
        for bk in sorted(by_brand, key=lambda k: (order.get(k, 99), k)):
            posts = sorted(by_brand[bk], key=lambda p: p.get("created_at") or "")
            groups.append({"brand_key": bk,
                           "display": display.get(bk, bk.upper()),
                           "posts": posts})
        return TEMPLATES.TemplateResponse(request, "archive_detail.html", {
            "meta": dict(meta), "groups": groups,
            "projects": by_tbl.get("projects", [])})

    @app.get("/learning", response_class=HTMLResponse)
    def learning_view(request: Request, msg: str = ""):
        from .. import learn
        with db.get_engine().connect() as conn:
            rules = learn.current_rules(conn)
            feedback = [dict(r) for r in conn.execute(
                select(db.filter_feedback)
                .order_by(db.filter_feedback.c.id.desc())
                .limit(100)).mappings()]
            history = [dict(r) for r in conn.execute(
                select(db.learned_rules)
                .order_by(db.learned_rules.c.id.desc())
                .limit(10)).mappings()]
            from sqlalchemy import func
            since = rules["feedback_through"] if rules else 0
            pending = conn.execute(
                select(func.count()).select_from(db.filter_feedback)
                .where(db.filter_feedback.c.id > since)).scalar() or 0
        return TEMPLATES.TemplateResponse(request, "learning.html", {
            "rules": rules, "feedback": feedback, "history": history,
            "pending": pending, "msg": msg})

    @app.post("/learning/update")
    def learning_update(request: Request):
        from urllib.parse import quote
        from .. import learn
        from ..llm import LLM
        try:
            upd = learn.synthesize_rules(db.get_engine(), LLM(),
                                         actor=_actor(request))
        except Exception as e:
            return RedirectResponse(
                f"/learning?msg={quote(f'Update failed: {str(e)[:200]}')}",
                status_code=303)
        msg = (f"Learned rules updated from {upd['corrections']} corrections: "
               f"{upd['summary']}" if upd else
               "No new corrections since the last update.")
        return RedirectResponse(f"/learning?msg={quote(msg)}", status_code=303)

    @app.get("/decks", response_class=HTMLResponse)
    def decks_view(request: Request):
        files = sorted(OUTPUT_DIR.glob("*.pptx")) + sorted(OUTPUT_DIR.glob("*.xlsx"))
        return TEMPLATES.TemplateResponse(request, "decks.html", {
            "files": [{"name": f.name, "size_mb": round(f.stat().st_size / 1e6, 1)}
                      for f in files]})

    # ---------- actions ----------

    @app.post("/runs/{month}/start")
    def start_run(request: Request, month: str):
        from urllib.parse import quote
        cfg = BrandsConfig.load()
        blockers = weibo_blockers(cfg)
        if blockers:
            names = ", ".join(b["brand_display"] for b in blockers)
            msg = quote(f"Can't start {month} yet — confirm the Weibo account "
                        f"for {names} in the list below, then Start again.")
            return RedirectResponse(f"/?msg={msg}", status_code=303)
        if _spawn(month, "ingest_filter", _ingest_and_filter, month):
            db.audit(db.get_engine(), _actor(request), "start_month",
                     "month", month)
        return RedirectResponse("/", status_code=303)

    @app.post("/runs/{month}/stop")
    def stop_run(request: Request, month: str):
        from urllib.parse import quote
        hit = []
        for key, ev in list(STOP_EVENTS.items()):
            if (key.startswith(f"{month}:")
                    and TASKS.get(key, {}).get("state") == "running"):
                ev.set()
                hit.append(key.split(":", 1)[1])
        if hit:
            db.audit(db.get_engine(), _actor(request), "stop_requested",
                     "month", month)
            _log_activity(month, f"stop requested by {_actor(request)}")
            msg = ("Stop requested — the run finishes its current item and "
                   "pauses. Start month (or re-confirming a checkpoint) "
                   "resumes it; nothing already fetched or decided is lost.")
        else:
            msg = f"Nothing is running for {month}."
        return RedirectResponse(f"/?msg={quote(msg)}", status_code=303)

    @app.post("/runs/{month}/archive")
    def archive_run(request: Request, month: str):
        from urllib.parse import quote
        busy = any(k.startswith(f"{month}:") and v.get("state") == "running"
                   for k, v in TASKS.items())
        if busy:
            msg = (f"A run is working on {month} — press Stop first, "
                   f"then archive.")
        else:
            summary = db.archive_month(db.get_engine(), month, _actor(request))
            if any(summary.values()):
                _log_activity(month, f"archived by {_actor(request)} "
                                     f"({summary['posts']} posts, "
                                     f"{summary['projects']} projects)")
                msg = (f"Archived {summary['posts']} posts and "
                       f"{summary['projects']} projects for {month}. "
                       f"Start month now runs a completely fresh search.")
            else:
                msg = f"Nothing to archive for {month}."
        return RedirectResponse(f"/?msg={quote(msg)}", status_code=303)

    @app.get("/api/runs/{month}/status")
    def run_status(month: str):
        st = pipeline.status(month)
        st["tasks"] = {k: v for k, v in TASKS.items() if k.startswith(month)}
        st["activity"] = list(ACTIVITY.get(month, []))[-40:]
        # a phase stuck at "running" with no live task means the process was
        # restarted mid-run (TASKS is in-memory) — tell the user how to resume
        task_for = {"ingest": "ingest_filter", "filter": "ingest_filter",
                    "crosscheck": "crosscheck_enrich",
                    "enrich": "crosscheck_enrich", "render": "render"}
        live = {k.split(":", 1)[1] for k, v in TASKS.items()
                if k.startswith(f"{month}:") and v.get("state") == "running"}
        st["stalled"] = [ph for ph, s in st["phases"].items()
                         if s == "running" and task_for.get(ph) not in live]
        return st

    @app.post("/review/{month}/posts/{post_id}/decision")
    def post_decision(request: Request, month: str, post_id: str,
                      decision: str = Form(...)):
        from ..learn import record_feedback
        engine = db.get_engine()
        with engine.begin() as conn:
            post_row = conn.execute(select(db.posts).where(
                db.posts.c.post_id == post_id)).mappings().first()
            if post_row is None:
                return JSONResponse({"error": f"unknown post {post_id}"},
                                    status_code=404)
            verdict_row = conn.execute(select(db.verdicts).where(
                db.verdicts.c.post_id == post_id)).mappings().first()
            value = None if decision == "restore" else decision
            # upsert: a post whose LLM verdict failed (no verdicts row yet)
            # must still take a human decision — and the audit row must only
            # ever describe a change that actually landed
            db.upsert(conn, db.verdicts, {
                "post_id": post_id, "human_decision": value,
                "decided_at": db.now_iso(), "decided_by": _actor(request),
            }, ["post_id"])
            # the self-tuning catalogue: what the LLM said vs what the human did
            record_feedback(conn, post_row, verdict_row, decision,
                            _actor(request))
            db.audit(conn, _actor(request), f"post_{decision}", "post", post_id)
        return RedirectResponse(f"/review/{month}/posts", status_code=303)

    @app.post("/review/{month}/posts/{post_id}/media/select")
    def media_select(request: Request, month: str, post_id: str,
                     idx: int = Form(...), selected: str = Form(...)):
        """Checkbox next to an image: exactly the ticked images render into
        the slide for this post (untick all → automatic card/screenshot)."""
        engine = db.get_engine()
        with _MEDIA_LOCK, engine.begin() as conn:
            row = conn.execute(select(db.posts).where(
                db.posts.c.post_id == post_id)).mappings().first()
            if row is None:
                return JSONResponse({"error": "unknown post"}, status_code=404)
            media = json.loads(row["media"] or "[]")
            if not 0 <= idx < len(media):
                return JSONResponse({"error": "bad media index"}, status_code=400)
            media[idx]["selected"] = selected in ("true", "1", "on")
            conn.execute(db.posts.update()
                         .where(db.posts.c.post_id == post_id)
                         .values(media=json.dumps(media, ensure_ascii=False)))
            db.audit(conn, _actor(request),
                     "media_select" if media[idx]["selected"] else "media_deselect",
                     "post", f"{post_id}#{idx}")
        return JSONResponse({"ok": True, "selected": media[idx]["selected"]})

    _UPLOAD_CAP = 30 * 1024 * 1024

    def _image_ext(data: bytes) -> str | None:
        if data.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if data.startswith(b"\x89PNG"):
            return ".png"
        if data.startswith(b"GIF8"):
            return ".gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return ".webp"
        return None

    @app.post("/review/{month}/posts/{post_id}/upload")
    async def media_upload(request: Request, month: str, post_id: str):
        """Drop zone: the reviewer downloads the original image from the post
        and drops it here — stored as a selected, upload-sourced media item."""
        engine = db.get_engine()
        with engine.connect() as conn:
            row = conn.execute(select(db.posts).where(
                db.posts.c.post_id == post_id)).mappings().first()
        if row is None:
            return JSONResponse({"error": "unknown post"}, status_code=404)
        form = await request.form()
        files = [v for v in form.getlist("files") if hasattr(v, "read")]
        if not files:
            return JSONResponse({"error": "no files"}, status_code=400)
        import hashlib as _hashlib
        from ..media import MediaStore
        store = MediaStore(row["month"])
        added = 0
        new_items = []
        for f in files:
            # stream with a running cap — a huge file must not reach memory
            data = b""
            while chunk := await f.read(1 << 20):
                data += chunk
                if len(data) > _UPLOAD_CAP:
                    return JSONResponse({"error": f"{f.filename}: over 30MB"},
                                        status_code=413)
            ext = _image_ext(data)
            if ext is None:
                return JSONResponse(
                    {"error": f"{f.filename}: not a JPEG/PNG/GIF/WebP image"},
                    status_code=400)
            name = f"upload_{_hashlib.sha1(data).hexdigest()[:16]}{ext}"
            path = store.media_dir(row["brand"]) / name
            path.write_bytes(data)
            new_items.append({"kind": "image", "url": None,
                              "local_path": str(path), "source": "upload",
                              "selected": True})
            added += 1
        from starlette.concurrency import run_in_threadpool
        actor = _actor(request)

        def merge():
            # posts.media is RMW-shared with media_select — same lock; runs
            # in the threadpool so the event loop never blocks on it
            with _MEDIA_LOCK, engine.begin() as conn:
                fresh = conn.execute(select(db.posts.c.media).where(
                    db.posts.c.post_id == post_id)).scalar()
                media = json.loads(fresh or "[]")
                have = {m.get("local_path") for m in media}
                media.extend(m for m in new_items
                             if m["local_path"] not in have)
                conn.execute(db.posts.update()
                             .where(db.posts.c.post_id == post_id)
                             .values(media=json.dumps(media,
                                                      ensure_ascii=False)))
                db.audit(conn, actor, "media_upload", "post",
                         f"{post_id} (+{added})")

        await run_in_threadpool(merge)
        return JSONResponse({"ok": True, "added": added})

    @app.post("/review/{month}/posts/confirm")
    def posts_confirm(request: Request, month: str):
        pipeline.confirm_posts_review(month)
        db.audit(db.get_engine(), _actor(request), "confirm_posts", "month", month)
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
        elif "celeb_0_display" in form or form.get("celeb_new_display", "").strip():
            # structured celeb rows: name + relation per row, remove ticks a
            # row out, and one blank row adds a new celeb
            celebs, i = [], 0
            while f"celeb_{i}_display" in form:
                display = form[f"celeb_{i}_display"].strip()
                if display and form.get(f"celeb_{i}_remove") != "on":
                    rel = form.get(f"celeb_{i}_relation", "").strip()
                    celebs.append({
                        "name_cn": form.get(f"celeb_{i}_name_cn", "").strip() or None,
                        "name_en": form.get(f"celeb_{i}_name_en", "").strip() or None,
                        "display": display.upper(),
                        "relation_display": rel.upper() or "CELEBRITY ?",
                        "verified": form.get(f"celeb_{i}_verified") == "1",
                        "occupation": form.get(f"celeb_{i}_occupation") or None})
                i += 1
            new_display = form.get("celeb_new_display", "").strip()
            if new_display:
                celebs.append({
                    "name_cn": form.get("celeb_new_name_cn", "").strip() or None,
                    "name_en": None, "display": new_display.upper(),
                    "relation_display": (form.get("celeb_new_relation", "")
                                         .strip() or "CELEBRITY ?").upper(),
                    "verified": False, "occupation": None})
            values["celebs"] = json.dumps(celebs, ensure_ascii=False)
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
            db.audit(conn, _actor(request), "project_edit", "project", project_id)
        return RedirectResponse(f"/review/{month}/projects", status_code=303)

    @app.post("/review/{month}/projects/{project_id}/status")
    def project_status(request: Request, month: str, project_id: int,
                       value: str = Form(...)):
        engine = db.get_engine()
        with engine.begin() as conn:
            conn.execute(db.projects.update()
                         .where(db.projects.c.id == project_id)
                         .values(status=value))
            db.audit(conn, _actor(request), f"project_{value}", "project",
                     project_id)
        return RedirectResponse(f"/review/{month}/projects", status_code=303)

    @app.post("/review/{month}/projects/{project_id}/merge")
    def project_merge(request: Request, month: str, project_id: int,
                      target_id: int = Form(...)):
        """Drag one project onto another: the source's posts, platform
        evidence, celebs and hero media fold into the target; dates extend;
        the source project disappears."""
        engine = db.get_engine()
        with engine.begin() as conn:
            src = conn.execute(select(db.projects).where(
                db.projects.c.id == project_id)).mappings().first()
            tgt = conn.execute(select(db.projects).where(
                db.projects.c.id == target_id)).mappings().first()
            if src is None or tgt is None:
                return JSONResponse({"error": "unknown project"}, status_code=404)
            if src["id"] == tgt["id"]:
                return JSONResponse({"error": "cannot merge a project into itself"},
                                    status_code=400)
            if src["brand"] != tgt["brand"] or src["month"] != tgt["month"]:
                return JSONResponse(
                    {"error": "projects must belong to the same brand and month"},
                    status_code=400)
            for r in conn.execute(select(db.project_posts).where(
                    db.project_posts.c.project_id == src["id"])).mappings():
                db.upsert(conn, db.project_posts,
                          {"project_id": tgt["id"], "post_id": r["post_id"],
                           "role": r["role"]}, ["project_id", "post_id"],
                          no_update_cols=["role"])
            conn.execute(db.project_posts.delete().where(
                db.project_posts.c.project_id == src["id"]))
            tgt_pm = {r["platform"]: dict(r) for r in conn.execute(
                select(db.platform_matches).where(
                    db.platform_matches.c.project_id == tgt["id"])).mappings()}
            for r in conn.execute(select(db.platform_matches).where(
                    db.platform_matches.c.project_id == src["id"])).mappings():
                prev = tgt_pm.get(r["platform"])
                if prev is None or (r["present"] and not prev["present"]) \
                        or (r["confidence"] or 0) > (prev["confidence"] or 0):
                    db.upsert(conn, db.platform_matches,
                              {**dict(r), "project_id": tgt["id"]},
                              ["project_id", "platform"])
            conn.execute(db.platform_matches.delete().where(
                db.platform_matches.c.project_id == src["id"]))
            celebs = json.loads(tgt["celebs"] or "[]")
            seen = {(c.get("name_cn"), c.get("display")) for c in celebs}
            for c in json.loads(src["celebs"] or "[]"):
                if (c.get("name_cn"), c.get("display")) not in seen:
                    celebs.append(c)
            heroes = list(dict.fromkeys(json.loads(tgt["hero_media"] or "[]")
                                        + json.loads(src["hero_media"] or "[]")))
            starts = [d for d in (tgt["date_start"], src["date_start"]) if d]
            ends = [d for d in (tgt["date_end"], src["date_end"]) if d]
            why = (f"Merged '{naming.format_title(src['title'], src['phase_suffix'])}'"
                   f" into this project by {_actor(request)}.")
            rationale = " ".join(x for x in (tgt["rationale"], why,
                                             src["rationale"]) if x)
            conn.execute(db.projects.update()
                         .where(db.projects.c.id == tgt["id"])
                         .values(celebs=json.dumps(celebs, ensure_ascii=False),
                                 hero_media=json.dumps(heroes[:6],
                                                       ensure_ascii=False),
                                 date_start=min(starts) if starts else None,
                                 date_end=max(ends) if ends else None,
                                 ongoing=bool(src["ongoing"] or tgt["ongoing"]),
                                 assets=(tgt["assets"] if tgt["assets"] == src["assets"]
                                         else "PHOTO VIDEO"),
                                 rationale=rationale[:2000] or None))
            conn.execute(db.projects.delete().where(db.projects.c.id == src["id"]))
            db.audit(conn, _actor(request), "project_merge", "project",
                     f"{src['id']}->{tgt['id']}")
        return JSONResponse({"ok": True, "merged_into": tgt["id"]})

    @app.post("/review/{month}/projects/{project_id}/ungroup")
    def project_ungroup(request: Request, month: str, project_id: int):
        """Split a project back into one project per Weibo post; matched
        cross-platform posts return to the orphan list."""
        engine = db.get_engine()
        with engine.begin() as conn:
            proj = conn.execute(select(db.projects).where(
                db.projects.c.id == project_id)).mappings().first()
            if proj is None:
                return JSONResponse({"error": "unknown project"}, status_code=404)
            members = list(conn.execute(
                select(db.posts)
                .join(db.project_posts,
                      db.project_posts.c.post_id == db.posts.c.post_id)
                .where(db.project_posts.c.project_id == project_id)
                .order_by(db.posts.c.created_at)).mappings())
            weibo = [m for m in members if m["platform"] == "weibo"]
            if len(weibo) <= 1:
                return JSONResponse(
                    {"error": "nothing to ungroup — the project has a single post"},
                    status_code=400)
            proj_title = naming.format_title(proj["title"], proj["phase_suffix"])
            taken = {(r["title"], r["phase_suffix"]) for r in conn.execute(
                select(db.projects.c.title, db.projects.c.phase_suffix)
                .where(db.projects.c.month == month,
                       db.projects.c.brand == proj["brand"])).mappings()}
            conn.execute(db.project_posts.delete().where(
                db.project_posts.c.project_id == project_id))
            conn.execute(db.platform_matches.delete().where(
                db.platform_matches.c.project_id == project_id))
            conn.execute(db.projects.delete().where(
                db.projects.c.id == project_id))
            for m in weibo:
                _spinoff_project(
                    conn, month, proj, m,
                    f"Ungrouped from '{proj_title}' by {_actor(request)}.",
                    taken)
            # cross-platform members go back to the orphan pool
            for m in members:
                if m["platform"] != "weibo":
                    db.upsert(conn, db.orphans,
                              {"post_id": m["post_id"], "month": month,
                               "resolution": "pending"}, ["post_id"])
            db.audit(conn, _actor(request), "project_ungroup", "project",
                     f"{project_id} -> {len(weibo)} projects")
        return RedirectResponse(f"/review/{month}/projects", status_code=303)

    @app.post("/review/{month}/projects/{project_id}/eject")
    def project_eject(request: Request, month: str, project_id: int,
                      post_id: str = Form(...)):
        """Remove ONE chosen post from a group, leaving the rest intact:
        a weibo member becomes its own single-post project; a matched
        cross-platform post returns to the orphan list."""
        engine = db.get_engine()
        with engine.begin() as conn:
            proj = conn.execute(select(db.projects).where(
                db.projects.c.id == project_id)).mappings().first()
            member = conn.execute(select(db.project_posts).where(
                db.project_posts.c.project_id == project_id,
                db.project_posts.c.post_id == post_id)).mappings().first()
            post = conn.execute(select(db.posts).where(
                db.posts.c.post_id == post_id)).mappings().first()
            if proj is None or member is None or post is None:
                return JSONResponse({"error": "post is not in this project"},
                                    status_code=404)
            proj_title = naming.format_title(proj["title"], proj["phase_suffix"])
            if post["platform"] == "weibo":
                n_weibo = conn.execute(
                    select(func.count()).select_from(
                        db.project_posts.join(
                            db.posts,
                            db.posts.c.post_id == db.project_posts.c.post_id))
                    .where(db.project_posts.c.project_id == project_id,
                           db.posts.c.platform == "weibo")).scalar() or 0
                if n_weibo <= 1:
                    return JSONResponse(
                        {"error": "this is the project's last weibo post — "
                                  "drop the whole project instead"},
                        status_code=400)
                taken = {(r["title"], r["phase_suffix"]) for r in conn.execute(
                    select(db.projects.c.title, db.projects.c.phase_suffix)
                    .where(db.projects.c.month == month,
                           db.projects.c.brand == proj["brand"])).mappings()}
                conn.execute(db.project_posts.delete().where(
                    db.project_posts.c.project_id == project_id,
                    db.project_posts.c.post_id == post_id))
                _spinoff_project(
                    conn, month, proj, post,
                    f"Removed from '{proj_title}' by {_actor(request)}.",
                    taken)
            else:
                conn.execute(db.project_posts.delete().where(
                    db.project_posts.c.project_id == project_id,
                    db.project_posts.c.post_id == post_id))
                # drop the evidence row only when it points at this very post
                conn.execute(db.platform_matches.delete().where(
                    db.platform_matches.c.project_id == project_id,
                    db.platform_matches.c.platform == post["platform"],
                    db.platform_matches.c.matched_post_id == post_id))
                db.upsert(conn, db.orphans,
                          {"post_id": post_id, "month": month,
                           "resolution": "pending"}, ["post_id"])
            db.audit(conn, _actor(request), "project_eject", "project",
                     f"{post_id}<-{project_id}")
        return RedirectResponse(f"/review/{month}/projects", status_code=303)

    @app.post("/review/{month}/projects/{project_id}/adopt")
    def project_adopt(request: Request, month: str, project_id: int,
                      post_id: str = Form(...)):
        """Drag a single post (project member or orphan) onto a project:
        membership moves there; orphans count as resolved."""
        engine = db.get_engine()
        with engine.begin() as conn:
            tgt = conn.execute(select(db.projects).where(
                db.projects.c.id == project_id)).mappings().first()
            post = conn.execute(select(db.posts).where(
                db.posts.c.post_id == post_id)).mappings().first()
            if tgt is None or post is None:
                return JSONResponse({"error": "unknown project or post"},
                                    status_code=404)
            if post["brand"] != tgt["brand"]:
                return JSONResponse(
                    {"error": "post and project belong to different brands"},
                    status_code=400)
            month_pids = select(db.projects.c.id).where(
                db.projects.c.month == tgt["month"],
                db.projects.c.brand == tgt["brand"])
            conn.execute(db.project_posts.delete().where(
                db.project_posts.c.post_id == post_id,
                db.project_posts.c.project_id.in_(month_pids)))
            role = "member" if post["platform"] == "weibo" else "match"
            db.upsert(conn, db.project_posts,
                      {"project_id": tgt["id"], "post_id": post_id,
                       "role": role}, ["project_id", "post_id"])
            if post["platform"] != "weibo":
                db.upsert(conn, db.platform_matches,
                          {"project_id": tgt["id"],
                           "platform": post["platform"], "present": True,
                           "matched_url": post["url"],
                           "matched_date": post["created_at"],
                           "matched_post_id": post_id, "confidence": 1.0},
                          ["project_id", "platform"])
                conn.execute(db.orphans.update()
                             .where(db.orphans.c.post_id == post_id)
                             .values(resolution="promoted"))
            db.audit(conn, _actor(request), "project_adopt", "project",
                     f"{post_id}->{tgt['id']}")
        return JSONResponse({"ok": True})

    @app.post("/review/{month}/orphans/{post_id:path}/resolve")
    def orphan_resolve(request: Request, month: str, post_id: str,
                       action: str = Form(...)):
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
                        celebs="[]", hero_media="[]", status="draft",
                        rationale=(f"Promoted from a {row['platform']} orphan "
                                   f"by {_actor(request)}.")))
                    pid = res.inserted_primary_key[0]
                    db.upsert(conn, db.project_posts,
                              {"project_id": pid, "post_id": post_id,
                               "role": "member"}, ["project_id", "post_id"])
                    db.upsert(conn, db.platform_matches,
                              {"project_id": pid, "platform": row["platform"],
                               "present": True, "matched_url": row["url"],
                               "matched_date": row["created_at"],
                               "matched_post_id": post_id,
                               "confidence": 1.0}, ["project_id", "platform"])
            conn.execute(db.orphans.update()
                         .where(db.orphans.c.post_id == post_id)
                         .values(resolution="promoted" if action == "promote"
                                 else "ignored"))
            db.audit(conn, _actor(request), f"orphan_{action}", "orphan", post_id)
        return RedirectResponse(f"/review/{month}/projects", status_code=303)

    @app.post("/review/{month}/render")
    def render_deck(request: Request, month: str,
                    visuals: str = Form(DEFAULT_VISUALS)):
        # never audit/confirm a render that did not start (e.g. double-click
        # while one is already running)
        if TASKS.get(f"{month}:render", {}).get("state") == "running":
            return RedirectResponse(f"/review/{month}/projects", status_code=303)
        pipeline.confirm_projects_review(month)
        if _spawn(month, "render", _render_task, month, visuals):
            db.audit(db.get_engine(), _actor(request), "render", "month", month)
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

    # ---------- celebs ----------

    _CELEB_DIR = DATA_DIR / "celebs"

    @app.get("/celebs", response_class=HTMLResponse)
    def celebs_view(request: Request, msg: str = ""):
        cfg = BrandsConfig.load()
        with db.get_engine().connect() as conn:
            rows = [dict(r) for r in conn.execute(
                select(db.celeb_registry)
                .order_by(db.celeb_registry.c.name_cn)).mappings()]
        from urllib.parse import quote
        for r in rows:
            r["relations"] = json.loads(r["relations_json"] or "{}")
            r["images"] = json.loads(r["images_json"] or "[]")
            r["gal"] = [{"src": f"/media?path={quote(p, safe='')}"}
                        for p in r["images"]]
        return TEMPLATES.TemplateResponse(request, "celebs.html", {
            "celebs": rows, "brands": cfg.brands, "msg": msg})

    @app.post("/celebs/new")
    def celeb_new(request: Request, name_cn: str = Form(...),
                  name_en: str = Form("")):
        from urllib.parse import quote
        name = name_cn.strip()
        if not name:
            return RedirectResponse(f"/celebs?msg={quote('name required')}",
                                    status_code=303)
        with db.get_engine().begin() as conn:
            db.upsert(conn, db.celeb_registry,
                      {"name_cn": name, "name_en": name_en.strip() or None,
                       "relations_json": "{}", "images_json": "[]"},
                      ["name_cn"],
                      no_update_cols=["relations_json", "images_json"])
            db.audit(conn, _actor(request), "celeb_new", "celeb", name)
        return RedirectResponse("/celebs", status_code=303)

    @app.post("/celebs/{celeb_id}/update")
    async def celeb_update(request: Request, celeb_id: int):
        from urllib.parse import quote
        from starlette.concurrency import run_in_threadpool
        from ..enrich import _REG_LOCK
        cfg = BrandsConfig.load()
        form = dict(await request.form())
        actor = _actor(request)

        # relations_json is read-modify-written here AND by enrichment
        # threads (which serialize behind _REG_LOCK) — a console edit landing
        # between an enrich read and write would be silently lost, so this
        # route joins the same lock. The locked section runs in the
        # threadpool: an async route must never block the event loop on a
        # threading.Lock.
        def commit() -> str:
            with _REG_LOCK, db.get_engine().begin() as conn:
                row = conn.execute(select(db.celeb_registry).where(
                    db.celeb_registry.c.id == celeb_id)).mappings().first()
                if row is None:
                    return "notfound"
                relations = json.loads(row["relations_json"] or "{}")
                for brand in cfg.brands:
                    key = f"relation_{brand.key}"
                    if key not in form:
                        continue
                    rel = form[key].strip()
                    if rel:
                        prev = relations.get(brand.key, {})
                        relations[brand.key] = {
                            **prev, "relation": rel.upper(),
                            "verified": form.get(f"verified_{brand.key}") == "on"}
                    else:
                        relations.pop(brand.key, None)
                values = {"name_en": form.get("name_en", "").strip() or None,
                          "occupation": form.get("occupation", "").strip() or None,
                          "relations_json": json.dumps(relations,
                                                       ensure_ascii=False)}
                new_name = form.get("name_cn", "").strip()
                if new_name and new_name != row["name_cn"]:
                    dup = conn.execute(select(db.celeb_registry.c.id).where(
                        db.celeb_registry.c.name_cn == new_name)).first()
                    if dup:
                        return f"/celebs?msg={quote(f'{new_name} already exists')}"
                    values["name_cn"] = new_name
                conn.execute(db.celeb_registry.update()
                             .where(db.celeb_registry.c.id == celeb_id)
                             .values(**values))
                db.audit(conn, actor, "celeb_edit", "celeb",
                         values.get("name_cn", row["name_cn"]))
                return "/celebs"

        outcome = await run_in_threadpool(commit)
        if outcome == "notfound":
            return JSONResponse({"error": "unknown celeb"}, status_code=404)
        return RedirectResponse(outcome, status_code=303)

    @app.post("/celebs/{celeb_id}/images/upload")
    async def celeb_image_upload(request: Request, celeb_id: int):
        """Photo library per celeb — same validation as post HQ uploads."""
        engine = db.get_engine()
        with engine.connect() as conn:
            row = conn.execute(select(db.celeb_registry).where(
                db.celeb_registry.c.id == celeb_id)).mappings().first()
        if row is None:
            return JSONResponse({"error": "unknown celeb"}, status_code=404)
        form = await request.form()
        files = [v for v in form.getlist("files") if hasattr(v, "read")]
        if not files:
            return JSONResponse({"error": "no files"}, status_code=400)
        import hashlib as _hashlib
        _CELEB_DIR.mkdir(parents=True, exist_ok=True)
        new_paths = []
        for f in files:
            data = b""
            while chunk := await f.read(1 << 20):
                data += chunk
                if len(data) > _UPLOAD_CAP:
                    return JSONResponse({"error": f"{f.filename}: over 30MB"},
                                        status_code=413)
            ext = _image_ext(data)
            if ext is None:
                return JSONResponse(
                    {"error": f"{f.filename}: not a JPEG/PNG/GIF/WebP image"},
                    status_code=400)
            path = _CELEB_DIR / (f"celeb{celeb_id}_"
                                 f"{_hashlib.sha1(data).hexdigest()[:16]}{ext}")
            path.write_bytes(data)
            new_paths.append(str(path))
        from starlette.concurrency import run_in_threadpool
        from ..enrich import _REG_LOCK
        actor = _actor(request)

        def merge():
            # registry rows are RMW-shared with enrichment — same lock; runs
            # in the threadpool so the event loop never blocks on it
            with _REG_LOCK, engine.begin() as conn:
                fresh = conn.execute(
                    select(db.celeb_registry.c.images_json).where(
                        db.celeb_registry.c.id == celeb_id)).scalar()
                images = json.loads(fresh or "[]")
                images.extend(p for p in new_paths if p not in images)
                conn.execute(db.celeb_registry.update()
                             .where(db.celeb_registry.c.id == celeb_id)
                             .values(images_json=json.dumps(images)))
                db.audit(conn, actor, "celeb_image_upload", "celeb",
                         f"{row['name_cn']} (+{len(new_paths)})")

        await run_in_threadpool(merge)
        return JSONResponse({"ok": True, "added": len(new_paths)})

    @app.post("/celebs/{celeb_id}/images/delete")
    def celeb_image_delete(request: Request, celeb_id: int,
                           idx: int = Form(...)):
        from ..enrich import _REG_LOCK
        with _REG_LOCK, db.get_engine().begin() as conn:
            row = conn.execute(select(db.celeb_registry).where(
                db.celeb_registry.c.id == celeb_id)).mappings().first()
            if row is None:
                return JSONResponse({"error": "unknown celeb"}, status_code=404)
            images = json.loads(row["images_json"] or "[]")
            if not 0 <= idx < len(images):
                return JSONResponse({"error": "bad image index"}, status_code=400)
            images.pop(idx)      # registry entry only; the file stays on disk
            conn.execute(db.celeb_registry.update()
                         .where(db.celeb_registry.c.id == celeb_id)
                         .values(images_json=json.dumps(images)))
            db.audit(conn, _actor(request), "celeb_image_delete", "celeb",
                     f"{row['name_cn']}#{idx}")
        return JSONResponse({"ok": True})

    # ---------- backup ----------

    @app.get("/backup/db")
    def backup_db(request: Request):
        """Stream a consistent snapshot of the SQLite DB (the crown jewels —
        media is re-fetchable and decks re-renderable)."""
        import os
        import sqlite3
        import tempfile
        from datetime import datetime
        from ..dates import CST
        ts = datetime.now(CST).strftime("%Y%m%d-%H%M%S")
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(tmp)
        try:
            with dst:
                src.backup(dst)          # WAL-safe consistent copy
        finally:
            src.close()
            dst.close()
        db.audit(db.get_engine(), _actor(request), "backup_download", "db", ts)
        return FileResponse(tmp, filename=f"maison-monitor-{ts}.db",
                            media_type="application/octet-stream",
                            background=BackgroundTask(os.unlink, tmp))

    # ---------- screenshot push (hosted: live Weibo shots come from a laptop) --

    @app.get("/api/screenshots/{month}/manifest")
    def screenshots_manifest(month: str):
        """Only effective-keep posts — no point capturing screenshots of posts
        that were LLM-dropped or human-rejected and can never reach the deck."""
        engine = db.get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                select(db.posts.c.post_id, db.posts.c.url, db.posts.c.brand,
                       db.verdicts.c.keep, db.verdicts.c.human_decision)
                .join(db.verdicts, db.verdicts.c.post_id == db.posts.c.post_id,
                      isouter=True)
                .where(db.posts.c.month == month,
                       db.posts.c.platform == "weibo")).mappings()
            posts = []
            for r in rows:
                keep = (r["keep"] if r["human_decision"] is None
                        else r["human_decision"] == "keep")
                if keep:
                    posts.append({"post_id": r["post_id"], "url": r["url"],
                                  "brand": r["brand"]})
            return {"month": month, "posts": posts}

    _PUSH_CAP = 20_000_000

    @app.post("/api/screenshots/{month}/{post_id:path}")
    async def screenshots_push(request: Request, month: str, post_id: str):
        # stream with a hard cap — never buffer an unbounded body
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > _PUSH_CAP:
            return JSONResponse({"error": "body too large (≤20MB)"},
                                status_code=413)
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > _PUSH_CAP:
                return JSONResponse({"error": "body too large (≤20MB)"},
                                    status_code=413)
            chunks.append(chunk)
        body = b"".join(chunks)
        if not body.startswith(b"\x89PNG"):
            return JSONResponse({"error": "expected a PNG body"},
                                status_code=400)
        engine = db.get_engine()
        with engine.connect() as conn:
            row = conn.execute(select(db.posts.c.brand)
                               .where(db.posts.c.post_id == post_id)).first()
        if row is None:
            return JSONResponse({"error": f"unknown post {post_id}"},
                                status_code=404)
        from ..media import MediaStore
        out = (MediaStore(month).visuals_dir(row[0])
               / f"live_{post_id.replace(':', '_')}.png")
        out.write_bytes(body)
        db.audit(engine, _actor(request), "screenshot_push", "post", post_id)
        return {"ok": True, "stored": out.name, "source": "live-pushed"}

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
