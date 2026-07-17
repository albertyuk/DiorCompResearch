"""Split-screen grouping board: projects left, unplaced posts right, drags
carry review semantics (drop-in = keep, drag-out = drop/orphan)."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

PASS = "team-pass-123"


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import mm.db as mdb
    monkeypatch.setattr(mdb, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(mdb, "_engine", None)
    import mm.media as mmedia
    monkeypatch.setattr(mmedia, "RUNS_DIR", tmp_path / "runs")
    yield mdb
    if mdb._engine is not None:
        mdb._engine.dispose()


@pytest.fixture
def client(tmp_db, monkeypatch):
    monkeypatch.setenv("CONSOLE_PASSPHRASE", PASS)
    monkeypatch.setenv("MM_SECRET_KEY", "f" * 64)
    from mm.console import create_app
    c = TestClient(create_app())
    r = c.post("/login", data={"name": "Alice", "passphrase": PASS},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _post(mdb, post_id, *, platform="weibo", brand="lv", caption="上海快闪",
          keep=None, human=None, created="2026-06-05T12:00:00+08:00"):
    with mdb.get_engine().begin() as conn:
        mdb.upsert(conn, mdb.posts, {
            "post_id": post_id, "month": "2026-06", "brand": brand,
            "platform": platform, "url": f"https://x/{post_id}",
            "created_at": created, "caption": caption,
            "at_tags": "[]", "hashtags": "[]", "media": "[]",
            "is_repost": False, "repost_ambiguous": False}, ["post_id"])
        if keep is not None:
            mdb.upsert(conn, mdb.verdicts, {
                "post_id": post_id, "keep": keep, "confidence": 0.9,
                "reasons": "[]", "rationale": "r", "celebs_tagged": "[]",
                "category": "event", "media_focus": "photo",
                "needs_review": False, "human_decision": human}, ["post_id"])


def _project(mdb, title, post_ids=(), roles=None):
    with mdb.get_engine().begin() as conn:
        pid = conn.execute(mdb.projects.insert().values(
            month="2026-06", brand="lv", title=title, phase_suffix=None,
            date_start="2026-06-05", date_end="2026-06-05", ongoing=False,
            assets="PHOTO", description=title, celebs="[]", hero_media="[]",
            status="draft")).inserted_primary_key[0]
        for p in post_ids:
            conn.execute(mdb.project_posts.insert().values(
                project_id=pid, post_id=p, role=(roles or {}).get(p, "member")))
    return pid


def _decision(conn, mdb, post_id):
    return conn.execute(select(mdb.verdicts.c.human_decision).where(
        mdb.verdicts.c.post_id == post_id)).scalar()


def test_board_page_shows_both_panes(client, tmp_db):
    _post(tmp_db, "weibo:M1", keep=True)
    _post(tmp_db, "weibo:D1", keep=False, caption="dropped 香水")
    _post(tmp_db, "weibo:U1", keep=True, caption="kept unassigned")
    _post(tmp_db, "douyin:O1", platform="douyin", caption="orphan douyin",
          keep=True)
    with tmp_db.get_engine().begin() as conn:
        conn.execute(tmp_db.orphans.insert().values(
            post_id="douyin:O1", month="2026-06", resolution="pending"))
    _project(tmp_db, "POP UP", ("weibo:M1",))
    page = client.get("/review/2026-06/board").text
    assert "Grouping board" in page and 'data-guide="board"' in page
    assert "POP UP" in page and "data-newproject" in page
    assert "orphan douyin" in page and "dropped 香水" in page
    assert "kept unassigned" in page
    assert "data-pool" in page and "pool-filter" in page
    # member post is placed → not in the pool (its chip + the chip's
    # "Move to…" button are the only carriers of the id)
    assert page.count('data-post="weibo:M1"') == 2


def test_pool_drag_out_weibo_marks_dropped(client, tmp_db):
    _post(tmp_db, "weibo:P1", keep=True)
    pid = _project(tmp_db, "SHOW", ("weibo:P1",))
    r = client.post("/review/2026-06/board/pool", data={"post_id": "weibo:P1"})
    assert r.status_code == 200
    with tmp_db.get_engine().connect() as conn:
        assert conn.execute(select(tmp_db.project_posts).where(
            tmp_db.project_posts.c.project_id == pid)).first() is None
        assert _decision(conn, tmp_db, "weibo:P1") == "drop"
        fb = conn.execute(select(tmp_db.filter_feedback)).mappings().first()
    assert fb["human_decision"] == "drop" and fb["decided_by"] == "Alice"


def test_pool_drag_out_match_returns_to_orphans(client, tmp_db):
    _post(tmp_db, "weibo:P2", keep=True)
    _post(tmp_db, "douyin:P3", platform="douyin")
    pid = _project(tmp_db, "SHOW2", ("weibo:P2", "douyin:P3"),
                   roles={"douyin:P3": "match"})
    with tmp_db.get_engine().begin() as conn:
        tmp_db.upsert(conn, tmp_db.platform_matches, {
            "project_id": pid, "platform": "douyin", "present": True,
            "matched_url": "u", "matched_post_id": "douyin:P3",
            "confidence": 0.8}, ["project_id", "platform"])
    r = client.post("/review/2026-06/board/pool", data={"post_id": "douyin:P3"})
    assert r.status_code == 200
    with tmp_db.get_engine().connect() as conn:
        orphan = conn.execute(select(tmp_db.orphans).where(
            tmp_db.orphans.c.post_id == "douyin:P3")).mappings().first()
        pm = conn.execute(select(tmp_db.platform_matches).where(
            tmp_db.platform_matches.c.project_id == pid,
            tmp_db.platform_matches.c.platform == "douyin")).first()
    assert orphan["resolution"] == "pending" and pm is None


def test_adopt_dropped_weibo_marks_kept(client, tmp_db):
    _post(tmp_db, "weibo:A1", keep=True)
    _post(tmp_db, "weibo:A2", keep=False)          # filter dropped it
    pid = _project(tmp_db, "CAMPAIGN", ("weibo:A1",))
    r = client.post(f"/review/2026-06/projects/{pid}/adopt",
                    data={"post_id": "weibo:A2"})
    assert r.status_code == 200
    with tmp_db.get_engine().connect() as conn:
        members = {r["post_id"] for r in conn.execute(
            select(tmp_db.project_posts).where(
                tmp_db.project_posts.c.project_id == pid)).mappings()}
        assert _decision(conn, tmp_db, "weibo:A2") == "keep"
        fb = conn.execute(select(tmp_db.filter_feedback)).mappings().all()
    assert members == {"weibo:A1", "weibo:A2"}
    assert [f["human_decision"] for f in fb] == ["keep"]   # learning fed


def test_new_project_from_dropped_post_keeps_it(client, tmp_db):
    _post(tmp_db, "weibo:N1", keep=False, caption="快闪店 开幕")
    r = client.post("/review/2026-06/board/new_project",
                    data={"post_id": "weibo:N1"})
    assert r.status_code == 200
    pid = r.json()["project_id"]
    with tmp_db.get_engine().connect() as conn:
        proj = conn.execute(select(tmp_db.projects).where(
            tmp_db.projects.c.id == pid)).mappings().first()
        member = conn.execute(select(tmp_db.project_posts).where(
            tmp_db.project_posts.c.project_id == pid)).mappings().first()
        assert _decision(conn, tmp_db, "weibo:N1") == "keep"
    assert proj["title"] == "快闪店 开幕"
    assert "grouping board" in proj["rationale"]
    assert member["post_id"] == "weibo:N1" and member["role"] == "member"


def test_new_project_from_orphan_promotes_it(client, tmp_db):
    _post(tmp_db, "xhs:N2", platform="xhs", caption="种草笔记", keep=True)
    with tmp_db.get_engine().begin() as conn:
        conn.execute(tmp_db.orphans.insert().values(
            post_id="xhs:N2", month="2026-06", resolution="pending"))
    r = client.post("/review/2026-06/board/new_project",
                    data={"post_id": "xhs:N2"})
    assert r.status_code == 200
    with tmp_db.get_engine().connect() as conn:
        orphan = conn.execute(select(tmp_db.orphans).where(
            tmp_db.orphans.c.post_id == "xhs:N2")).mappings().first()
        pm = conn.execute(select(tmp_db.platform_matches).where(
            tmp_db.platform_matches.c.project_id == r.json()["project_id"]
        )).mappings().first()
    assert orphan["resolution"] == "promoted"
    assert pm["platform"] == "xhs" and pm["matched_post_id"] == "xhs:N2"


def test_board_unknown_post_404s(client, tmp_db):
    assert client.post("/review/2026-06/board/pool",
                       data={"post_id": "weibo:GHOST"}).status_code == 404
    assert client.post("/review/2026-06/board/new_project",
                       data={"post_id": "weibo:GHOST"}).status_code == 404
