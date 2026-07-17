"""Hosted-mode verification (§9 of the change order): auth gating, stateless
sessions, audit attribution, screenshot push, and the decided_by migration."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

PASS = "team-pass-123"
SECRET = "f" * 64


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import mm.db as mdb
    monkeypatch.setattr(mdb, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(mdb, "_engine", None)
    yield mdb
    if mdb._engine is not None:
        mdb._engine.dispose()


@pytest.fixture
def authed_app(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setenv("CONSOLE_PASSPHRASE", PASS)
    monkeypatch.setenv("MM_SECRET_KEY", SECRET)
    import mm.media as mmedia
    monkeypatch.setattr(mmedia, "RUNS_DIR", tmp_path / "runs")
    from mm.console import create_app
    return create_app


def _seed_post(mdb, post_id="weibo:TEST1", month="2026-06", brand="lv"):
    with mdb.get_engine().begin() as conn:
        mdb.upsert(conn, mdb.posts, {
            "post_id": post_id, "month": month, "brand": brand,
            "platform": "weibo", "url": f"https://weibo.com/1/{post_id.split(':')[1]}",
            "created_at": f"{month}-05T12:00:00+08:00", "caption": "测试",
            "at_tags": "[]", "hashtags": "[]", "media": "[]",
            "is_repost": False, "repost_ambiguous": False,
        }, ["post_id"])
        mdb.upsert(conn, mdb.verdicts, {
            "post_id": post_id, "keep": True, "confidence": 0.9,
            "reasons": "[]", "celebs_tagged": "[]", "category": "event",
            "media_focus": "photo", "needs_review": False,
        }, ["post_id"])


def _login(client, name):
    r = client.post("/login", data={"name": name, "passphrase": PASS},
                    follow_redirects=False)
    assert r.status_code == 303
    return client


# ── §9.1 local unchanged ─────────────────────────────────────────────────────

def test_local_mode_no_login_required(tmp_db, monkeypatch):
    monkeypatch.delenv("CONSOLE_PASSPHRASE", raising=False)
    monkeypatch.delenv("MM_SECRET_KEY", raising=False)
    from mm.console import create_app
    c = TestClient(create_app())
    assert c.get("/").status_code == 200          # straight in, as before
    assert c.get("/healthz").status_code == 200


# ── §9.3 logged-out access ───────────────────────────────────────────────────

def test_logged_out_gets_login_only(authed_app):
    c = TestClient(authed_app())
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/login").status_code == 200
    # direct media URL and API route both rejected
    assert c.get("/media?path=/etc/passwd").status_code == 401
    assert c.get("/api/runs/2026-06/status").status_code == 401
    assert c.get("/download/x.pptx").status_code == 401
    assert c.get("/backup/db").status_code == 401
    assert c.post("/review/2026-06/posts/confirm").status_code == 401
    # healthz open, empty
    h = c.get("/healthz")
    assert h.status_code == 200 and h.content == b""


def test_wrong_passphrase_rejected(authed_app):
    c = TestClient(authed_app())
    r = c.post("/login", data={"name": "Mallory", "passphrase": "nope"})
    assert r.status_code == 401
    assert "mm_session" not in c.cookies


def test_session_survives_restart_and_robots_header(authed_app):
    c1 = TestClient(authed_app())
    _login(c1, "Albert")
    token = c1.cookies.get("mm_session")
    assert token
    r = c1.get("/")
    assert r.status_code == 200
    assert r.headers.get("x-robots-tag") == "noindex"
    # brand-new app instance (machine restart) — cookie is stateless
    c2 = TestClient(authed_app())
    c2.cookies.set("mm_session", token)
    assert c2.get("/").status_code == 200


# ── §9.5 two sessions, attribution ───────────────────────────────────────────

def test_two_actors_attributed(authed_app, tmp_db):
    _seed_post(tmp_db)
    ca = _login(TestClient(authed_app()), "Alice")
    cb = _login(TestClient(authed_app()), "Bob")
    assert ca.post("/review/2026-06/posts/weibo:TEST1/decision",
                   data={"decision": "drop"}, follow_redirects=False).status_code == 303
    with tmp_db.get_engine().connect() as conn:
        row = conn.execute(select(tmp_db.verdicts)).mappings().first()
        assert row["human_decision"] == "drop" and row["decided_by"] == "Alice"
    assert cb.post("/review/2026-06/posts/weibo:TEST1/decision",
                   data={"decision": "restore"}, follow_redirects=False).status_code == 303
    with tmp_db.get_engine().connect() as conn:
        row = conn.execute(select(tmp_db.verdicts)).mappings().first()
        assert row["human_decision"] is None and row["decided_by"] == "Bob"
        acts = {(r["actor_name"], r["action"])
                for r in conn.execute(select(tmp_db.audit_log)).mappings()}
    assert ("Alice", "login") in acts and ("Bob", "login") in acts
    assert ("Alice", "post_drop") in acts and ("Bob", "post_restore") in acts


# ── screenshots push (bearer passphrase) ─────────────────────────────────────

def test_screenshot_manifest_and_push(authed_app, tmp_db, tmp_path):
    _seed_post(tmp_db)
    c = TestClient(authed_app())
    hdr = {"Authorization": f"Bearer {PASS}"}
    assert c.get("/api/screenshots/2026-06/manifest").status_code == 401
    m = c.get("/api/screenshots/2026-06/manifest", headers=hdr)
    assert m.status_code == 200
    assert m.json()["posts"][0]["post_id"] == "weibo:TEST1"
    png = b"\x89PNG\r\n\x1a\n" + b"fixture-bytes"
    r = c.post("/api/screenshots/2026-06/weibo%3ATEST1", headers=hdr, content=png)
    assert r.status_code == 200, r.text
    stored = tmp_path / "runs" / "2026-06" / "lv" / "visuals" / "live_weibo_TEST1.png"
    assert stored.read_bytes() == png
    # non-PNG rejected
    assert c.post("/api/screenshots/2026-06/weibo%3ATEST1", headers=hdr,
                  content=b"not a png").status_code == 400
    # wrong bearer rejected
    assert c.get("/api/screenshots/2026-06/manifest",
                 headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_pushed_screenshot_wins_over_card(tmp_path, monkeypatch):
    import mm.media as mmedia
    monkeypatch.setattr(mmedia, "RUNS_DIR", tmp_path / "runs")
    from mm.render.visuals import VisualFactory
    vf = VisualFactory("2026-06", mode="card")     # no browser started
    live = vf.store.visuals_dir("lv") / "live_weibo_TEST1.png"
    live.write_bytes(b"\x89PNG pushed")
    out = vf.visual_for_post("lv", {"post_id": "weibo:TEST1", "platform": "weibo",
                                    "media": "[]"})
    assert out == live                              # no card render attempted


# ── decided_by migration on a pre-existing DB ────────────────────────────────

def test_decided_by_migration(tmp_path):
    import mm.db as mdb
    path = tmp_path / "old.db"
    eng = create_engine(f"sqlite:///{path}")
    with eng.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE verdicts (post_id VARCHAR PRIMARY KEY, keep BOOLEAN)")
    mdb._migrate(eng)
    with eng.connect() as c:
        cols = [r[1] for r in c.exec_driver_sql("PRAGMA table_info(verdicts)")]
    assert "decided_by" in cols
    eng.dispose()


# ── review-fix regressions ───────────────────────────────────────────────────

def test_passphrase_rotation_invalidates_sessions(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MM_SECRET_KEY", SECRET)
    monkeypatch.setenv("CONSOLE_PASSPHRASE", PASS)
    from mm.console import create_app
    c1 = TestClient(create_app())
    _login(c1, "Contractor")
    token = c1.cookies.get("mm_session")
    # owner rotates the passphrase (same signing key)
    monkeypatch.setenv("CONSOLE_PASSPHRASE", "rotated-pass-456")
    c2 = TestClient(create_app())
    c2.cookies.set("mm_session", token)
    r = c2.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_manifest_lists_only_effective_keeps(authed_app, tmp_db):
    _seed_post(tmp_db, post_id="weibo:KEPT")
    _seed_post(tmp_db, post_id="weibo:DROPPED")
    _seed_post(tmp_db, post_id="weibo:HUMANDROP")
    with tmp_db.get_engine().begin() as conn:
        conn.execute(tmp_db.verdicts.update()
                     .where(tmp_db.verdicts.c.post_id == "weibo:DROPPED")
                     .values(keep=False))
        conn.execute(tmp_db.verdicts.update()
                     .where(tmp_db.verdicts.c.post_id == "weibo:HUMANDROP")
                     .values(human_decision="drop"))
    c = TestClient(authed_app())
    m = c.get("/api/screenshots/2026-06/manifest",
              headers={"Authorization": f"Bearer {PASS}"})
    ids = {p["post_id"] for p in m.json()["posts"]}
    assert ids == {"weibo:KEPT"}


def test_decision_upserts_when_verdict_missing(authed_app, tmp_db):
    # post whose LLM verdict failed → no verdicts row, human decides anyway
    with tmp_db.get_engine().begin() as conn:
        tmp_db.upsert(conn, tmp_db.posts, {
            "post_id": "weibo:NOVERDICT", "month": "2026-06", "brand": "lv",
            "platform": "weibo", "url": "https://weibo.com/1/NOVERDICT",
            "created_at": "2026-06-05T12:00:00+08:00", "caption": "x",
            "at_tags": "[]", "hashtags": "[]", "media": "[]",
            "is_repost": False, "repost_ambiguous": False}, ["post_id"])
    c = _login(TestClient(authed_app()), "Alice")
    r = c.post("/review/2026-06/posts/weibo:NOVERDICT/decision",
               data={"decision": "keep"}, follow_redirects=False)
    assert r.status_code == 303
    with tmp_db.get_engine().connect() as conn:
        row = conn.execute(select(tmp_db.verdicts).where(
            tmp_db.verdicts.c.post_id == "weibo:NOVERDICT")).mappings().first()
    assert row is not None and row["human_decision"] == "keep"
    assert row["decided_by"] == "Alice"
    # unknown post: 404 and NO audit row
    r = c.post("/review/2026-06/posts/weibo:GHOST/decision",
               data={"decision": "keep"})
    assert r.status_code == 404
    with tmp_db.get_engine().connect() as conn:
        ghost = conn.execute(select(tmp_db.audit_log).where(
            tmp_db.audit_log.c.entity_id == "weibo:GHOST")).first()
    assert ghost is None


def test_status_reports_interrupted_phase(authed_app, tmp_db):
    # phase says "running" but the process restarted (no live task in TASKS)
    with tmp_db.get_engine().begin() as conn:
        tmp_db.set_phase(conn, "2026-06", "ingest", "running")
    c = _login(TestClient(authed_app()), "Albert")
    j = c.get("/api/runs/2026-06/status").json()
    assert j["stalled"] == ["ingest"]
    # a genuinely running task is NOT flagged
    from mm import console as mconsole
    mconsole.TASKS["2026-06:ingest_filter"] = {"state": "running", "detail": ""}
    try:
        j = c.get("/api/runs/2026-06/status").json()
        assert j["stalled"] == []
        assert j["tasks"]["2026-06:ingest_filter"]["state"] == "running"
    finally:
        del mconsole.TASKS["2026-06:ingest_filter"]


def test_filter_progress_fires_on_llm_errors(tmp_db):
    # every LLM call failing must still move the progress line (paid retries
    # were previously invisible: cost climbed while the UI froze)
    from mm import filtering
    from mm.config import BrandsConfig
    with tmp_db.get_engine().begin() as conn:
        tmp_db.upsert(conn, tmp_db.posts, {
            "post_id": "weibo:BOOM", "month": "2026-06", "brand": "lv",
            "platform": "weibo", "url": "https://weibo.com/1/BOOM",
            "created_at": "2026-06-05T12:00:00+08:00", "caption": "x",
            "at_tags": "[]", "hashtags": "[]", "media": "[]",
            "is_repost": False, "repost_ambiguous": False}, ["post_id"])

    class BoomLLM:
        def call_json(self, *a, **k):
            raise RuntimeError("api down")

    seen = []
    stats = filtering.filter_month(tmp_db.get_engine(), BoomLLM(),
                                   BrandsConfig.load(), "2026-06",
                                   progress=lambda s: seen.append(dict(s)))
    assert stats["errors"] == 1
    assert seen and seen[-1]["errors"] == 1


def test_stop_button_sets_flag_and_audits(authed_app, tmp_db):
    import threading
    from mm import console as mconsole
    c = _login(TestClient(authed_app()), "Albert")
    # nothing running → friendly no-op
    r = c.post("/runs/2026-05/stop", follow_redirects=False)
    assert r.status_code == 303 and "Nothing" in r.headers["location"]
    # fake a running task
    key = "2026-05:ingest_filter"
    mconsole.TASKS[key] = {"state": "running", "detail": ""}
    mconsole.STOP_EVENTS[key] = threading.Event()
    try:
        r = c.post("/runs/2026-05/stop", follow_redirects=False)
        assert r.status_code == 303 and "Stop%20requested" in r.headers["location"]
        assert mconsole.STOP_EVENTS[key].is_set()
        with tmp_db.get_engine().connect() as conn:
            row = conn.execute(select(tmp_db.audit_log).where(
                tmp_db.audit_log.c.action == "stop_requested")).mappings().first()
        assert row and row["actor_name"] == "Albert"
        # the activity feed narrates it via the status endpoint
        j = c.get("/api/runs/2026-05/status").json()
        assert any("stop requested by Albert" in line for line in j["activity"])
    finally:
        del mconsole.TASKS[key], mconsole.STOP_EVENTS[key]


def test_filter_month_cooperative_stop(tmp_db):
    from mm import filtering
    from mm.config import BrandsConfig
    for pid in ("weibo:S1", "weibo:S2"):
        with tmp_db.get_engine().begin() as conn:
            tmp_db.upsert(conn, tmp_db.posts, {
                "post_id": pid, "month": "2026-06", "brand": "lv",
                "platform": "weibo", "url": f"https://weibo.com/1/{pid}",
                "created_at": "2026-06-05T12:00:00+08:00", "caption": "x",
                "at_tags": "[]", "hashtags": "[]", "media": "[]",
                "is_repost": False, "repost_ambiguous": False}, ["post_id"])

    calls = []

    class OneCallLLM:
        def call_json(self, *a, **k):
            calls.append(1)
            return {"keep": True, "confidence": 0.9, "reasons": [],
                    "celebs_tagged": [], "category": "event",
                    "media_focus": "photo"}

    # max_workers=1 → deterministic "stop after the current post" semantics
    stats = filtering.filter_month(tmp_db.get_engine(), OneCallLLM(),
                                   BrandsConfig.load(), "2026-06",
                                   should_stop=lambda: len(calls) >= 1,
                                   max_workers=1)
    assert stats["stopped"] is True
    assert stats["filtered"] == 1 and len(calls) == 1   # paused, not lost


def test_ingest_weibo_stop_before_first_call(tmp_db, tmp_path, monkeypatch):
    import mm.media as mmedia
    from mm import ingest
    from mm.config import BrandsConfig
    monkeypatch.setattr(mmedia, "RUNS_DIR", tmp_path / "runs")

    class NoCallClient:
        def call(self, *a, **k):
            raise AssertionError("stopped ingest must not hit the API")

    res = ingest.ingest_weibo(tmp_db.get_engine(), NoCallClient(),
                              BrandsConfig.load(), "2026-06", "chanel",
                              should_stop=lambda: True)
    assert res["posts"] == 0


def test_start_month_audits_actor(authed_app, tmp_db, monkeypatch):
    import time
    from mm import console as mconsole
    monkeypatch.setattr(mconsole, "_ingest_and_filter", lambda month: {"ok": 1})
    c = _login(TestClient(authed_app()), "Albert")
    r = c.post("/runs/2026-04/start", follow_redirects=False)
    assert r.status_code == 303
    for _ in range(50):                       # wait out the worker thread
        if mconsole.TASKS.get("2026-04:ingest_filter", {}).get("state") != "running":
            break
        time.sleep(0.05)
    with tmp_db.get_engine().connect() as conn:
        row = conn.execute(select(tmp_db.audit_log).where(
            tmp_db.audit_log.c.action == "start_month")).mappings().first()
    assert row and row["actor_name"] == "Albert" and row["entity_id"] == "2026-04"


def test_archive_month_moves_everything_and_resets(tmp_db):
    _seed_post(tmp_db, post_id="weibo:A1")
    with tmp_db.get_engine().begin() as conn:
        tmp_db.set_phase(conn, "2026-06", "ingest", "done")
        pid = conn.execute(tmp_db.projects.insert().values(
            month="2026-06", brand="lv", title="T", status="draft",
            celebs="[]", hero_media="[]")).inserted_primary_key[0]
        conn.execute(tmp_db.project_posts.insert().values(
            project_id=pid, post_id="weibo:A1"))
        conn.execute(tmp_db.platform_matches.insert().values(
            project_id=pid, platform="xhs", present=True))
        conn.execute(tmp_db.orphans.insert().values(
            post_id="weibo:A1", month="2026-06"))
    summary = tmp_db.archive_month(tmp_db.get_engine(), "2026-06", "Albert")
    assert summary == {"posts": 1, "verdicts": 1, "projects": 1,
                       "project_posts": 1, "platform_matches": 1,
                       "post_matches": 0, "orphans": 1}
    with tmp_db.get_engine().connect() as conn:
        for tbl in (tmp_db.posts, tmp_db.verdicts, tmp_db.projects,
                    tmp_db.project_posts, tmp_db.platform_matches,
                    tmp_db.orphans):
            assert conn.execute(select(tbl)).first() is None, tbl.name
        arch = tmp_db.list_archives(conn, "2026-06")
        assert len(arch) == 1 and arch[0]["counts"]["posts"] == 1
        rows = conn.execute(select(tmp_db.archive_rows)).mappings().all()
        assert len(rows) == 6
        assert tmp_db.get_run(conn, "2026-06")["phases"] == {}   # fresh start
    # the same post can be re-ingested afterwards (no PK collision)…
    _seed_post(tmp_db, post_id="weibo:A1")
    tmp_db.archive_month(tmp_db.get_engine(), "2026-06", "Albert")
    # …and archiving an empty month is a no-op that creates no archive row
    assert not any(tmp_db.archive_month(
        tmp_db.get_engine(), "2026-06", "Albert").values())
    with tmp_db.get_engine().connect() as conn:
        assert len(tmp_db.list_archives(conn, "2026-06")) == 2


def test_archive_route_guards_running_month(authed_app, tmp_db):
    from mm import console as mconsole
    _seed_post(tmp_db, post_id="weibo:AR1", month="2026-03")
    c = _login(TestClient(authed_app()), "Albert")
    # busy month refuses
    mconsole.TASKS["2026-03:ingest_filter"] = {"state": "running", "detail": ""}
    try:
        r = c.post("/runs/2026-03/archive", follow_redirects=False)
        assert "Stop%20first" in r.headers["location"]
        with tmp_db.get_engine().connect() as conn:
            assert conn.execute(select(tmp_db.posts)).first() is not None
    finally:
        del mconsole.TASKS["2026-03:ingest_filter"]
    # idle month archives + audits
    r = c.post("/runs/2026-03/archive", follow_redirects=False)
    assert r.status_code == 303 and "Archived%201%20posts" in r.headers["location"]
    with tmp_db.get_engine().connect() as conn:
        assert conn.execute(select(tmp_db.posts)).first() is None
        row = tmp_db.last_audit(conn, "archive_month", "month", "2026-03")
    assert row and row["actor_name"] == "Albert"


def test_run_ingest_is_parallel_across_brands(tmp_db, monkeypatch):
    import threading
    from mm import ingest as ingest_mod, pipeline

    barrier = threading.Barrier(5, timeout=8)   # all 5 brands in flight at once

    def fake_ingest(engine, client, cfg, month, brand_key, *,
                    progress=None, should_stop=None, **kw):
        barrier.wait()                           # sequential execution deadlocks
        if progress:
            progress(brand_key, 1, 2)
        return {"brand": brand_key, "posts": 2, "skipped_reposts": 0}

    class DummyClient:
        def __init__(self, *a, **k): pass
        def close(self): pass

    monkeypatch.setattr(ingest_mod, "ingest_weibo", fake_ingest)
    monkeypatch.setattr(pipeline, "TikHubClient", DummyClient)
    monkeypatch.setattr(pipeline, "Settings",
                        type("S", (), {"load": staticmethod(lambda: None)}))
    notes = []
    res = pipeline.run_ingest("2026-06", progress=notes.append)
    assert set(res) == {"chanel", "lv", "tiffany", "gucci", "fendi"}
    assert all(r["posts"] == 2 for r in res.values())
    with tmp_db.get_engine().connect() as conn:
        assert tmp_db.get_run(conn, "2026-06")["phases"]["ingest"] == "done"
    # combined progress line mentions brands with their page state
    assert any("p1·2 in window" in n for n in notes)


def test_hosted_account_overrides_overlay(tmp_path, monkeypatch):
    import mm.config as mconfig
    monkeypatch.setattr(mconfig, "IS_HOSTED", True)
    monkeypatch.setattr(mconfig, "ACCOUNT_OVERRIDES_YAML",
                        tmp_path / "account_overrides.yaml")
    cfg = mconfig.BrandsConfig.load()
    repo_uid = cfg.brand("chanel").account("douyin").uid   # whatever ships
    assert repo_uid != "SEC_UID_X"
    cfg.save_account_resolution("chanel", "douyin", "SEC_UID_X", "CHANEL抖音",
                                "2026-07-15")
    # repo yaml untouched; overlay applied on every load
    import yaml as _y
    repo = _y.safe_load(mconfig.BRANDS_YAML.read_text())
    chanel = next(b for b in repo["brands"] if b["key"] == "chanel")
    assert chanel["douyin"].get("uid") == repo_uid        # repo unchanged
    fresh = mconfig.BrandsConfig.load()
    acct = fresh.brand("chanel").account("douyin")
    assert acct.resolved and acct.uid == "SEC_UID_X"      # overlay wins
    # repo remains authoritative for everything else
    assert fresh.filters["exclude_fragrance"] is True
