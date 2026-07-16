"""Working evidence links for douyin/xhs (owner report: none of them worked).

Live-verified traps these tests codify:
- douyin: www.douyin.com/video/{id} hits a login/verification wall; the API's
  share_info.share_url (iesdouyin.com) opens for outsiders — prefer it.
- xhs: app_v2 timeline note ids are NOT the canonical web ids and carry no
  xsec_token, so explore/{id} is dead; only the note-detail share link
  (canonical id + xsec_token) works. hydrate_xhs_links swaps it in for every
  post a human sees as a link.
"""
import json

import pytest
from sqlalchemy import select


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


def _post(mdb, post_id, *, platform, url, keep=None, brand="lv"):
    with mdb.get_engine().begin() as conn:
        mdb.upsert(conn, mdb.posts, {
            "post_id": post_id, "month": "2026-06", "brand": brand,
            "platform": platform, "url": url,
            "created_at": "2026-06-05T12:00:00+08:00", "caption": "c",
            "at_tags": "[]", "hashtags": "[]", "media": "[]",
            "is_repost": False, "repost_ambiguous": False}, ["post_id"])
        if keep is not None:
            mdb.upsert(conn, mdb.verdicts, {
                "post_id": post_id, "keep": keep, "confidence": 0.9,
                "reasons": "[]", "celebs_tagged": "[]", "category": "event",
                "media_focus": "photo", "needs_review": False}, ["post_id"])


# ── normalizers build links that open for outsiders ──────────────────────────

def test_normalize_douyin_prefers_official_share_url():
    from mm.normalize import normalize_douyin
    aweme = {"aweme_id": "777", "create_time": 1750000000, "desc": "d",
             "share_info": {"share_url":
                            "https://www.iesdouyin.com/share/video/777/?region=US&mid=1 "}}
    assert normalize_douyin(aweme)["url"] == \
        "https://www.iesdouyin.com/share/video/777/?region=US&mid=1"
    # no share_info → constructed fallback
    assert normalize_douyin({"aweme_id": "778"})["url"] == \
        "https://www.douyin.com/video/778"


def test_normalize_xhs_uses_any_depth_token():
    from mm.normalize import normalize_xhs
    note = {"note_id": "abc", "desc": "d",
            "note_info": {"xsec_token": "TOK="}}
    assert normalize_xhs(note)["url"] == \
        "https://www.xiaohongshu.com/explore/abc?xsec_token=TOK=&xsec_source=pc_search"
    assert normalize_xhs({"note_id": "abc"})["url"] == \
        "https://www.xiaohongshu.com/explore/abc"


# ── share-link extraction from a note-detail payload ─────────────────────────

def test_xhs_share_link_normalizes_official_link():
    from mm.crosscheck import _xhs_share_link
    payload = {"data": {"note_list": [{
        "id": "CANONICAL",
        "share_info": {"link": (
            "https://www.xiaohongshu.com/discovery/item/CANONICAL"
            "?app_platform=ios&app_version=9.28.1&share_from_user_hidden=true"
            "&xsec_source=app_share&type=normal&xsec_token=CBIs=")}}]}}
    assert _xhs_share_link(payload) == (
        "https://www.xiaohongshu.com/discovery/item/CANONICAL"
        "?xsec_source=app_share&xsec_token=CBIs=")
    assert _xhs_share_link({"data": {}}) is None
    # tokenless links never win
    assert _xhs_share_link({"u": "https://www.xiaohongshu.com/explore/x"}) is None


# ── hydration: only visible posts, both endpoints, idempotent ────────────────

def test_hydrate_xhs_links_updates_posts(tmp_db):
    from mm import crosscheck as xc
    _post(tmp_db, "xhs:IMG", platform="xhs",
          url="https://www.xiaohongshu.com/explore/IMG")
    _post(tmp_db, "xhs:VID", platform="xhs",
          url="https://www.xiaohongshu.com/explore/VID")
    _post(tmp_db, "xhs:OK", platform="xhs",
          url="https://www.xiaohongshu.com/discovery/item/OK?xsec_source=app_share&xsec_token=T=")
    _post(tmp_db, "douyin:D", platform="douyin", url="https://x/d")

    calls = []

    class FakeClient:
        def call(self, ep, *, note_id=None, **kw):
            calls.append((ep, note_id))
            if note_id == "IMG" and ep == "xhs_note_detail_image":
                return {"share_info": {"link":
                        "https://www.xiaohongshu.com/discovery/item/IMG_CANON"
                        "?a=1&xsec_token=TI="}}
            if note_id == "VID":
                if ep == "xhs_note_detail_image":
                    raise RuntimeError("not an image note")
                return {"share_info": {"link":
                        "https://www.xiaohongshu.com/discovery/item/VID_CANON"
                        "?xsec_token=TV="}}
            raise AssertionError(f"unexpected {ep} {note_id}")

    stats = xc.hydrate_xhs_links(
        tmp_db.get_engine(), FakeClient(), "2026-06", "lv",
        {"xhs:IMG", "xhs:VID", "xhs:OK", "douyin:D"})
    assert stats == {"hydrated": 2, "failed": 0, "capped": 0}
    with tmp_db.get_engine().connect() as conn:
        urls = {r[0]: r[1] for r in conn.execute(
            select(tmp_db.posts.c.post_id, tmp_db.posts.c.url))}
    assert urls["xhs:IMG"] == ("https://www.xiaohongshu.com/discovery/item/"
                               "IMG_CANON?xsec_source=app_share&xsec_token=TI=")
    assert urls["xhs:VID"] == ("https://www.xiaohongshu.com/discovery/item/"
                               "VID_CANON?xsec_source=app_share&xsec_token=TV=")
    # already-tokened post and non-xhs post were never re-billed
    assert urls["xhs:OK"].endswith("xsec_token=T=")
    assert ("xhs_note_detail_image", "OK") not in calls
    assert all(nid != "D" for _, nid in calls)
    # video fallback happened exactly once
    assert ("xhs_note_detail_video", "VID") in calls


def test_crosscheck_phase_hydrates_matched_and_kept_orphans(tmp_db, monkeypatch, tmp_path):
    from mm import crosscheck as xc, filtering, pipeline
    monkeypatch.setattr(pipeline, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(pipeline, "TikHubClient", lambda s: type(
        "C", (), {"close": lambda self: None})())
    monkeypatch.setattr(pipeline, "LLM", lambda *a, **k: object())
    _post(tmp_db, "xhs:M", platform="xhs",
          url="https://www.xiaohongshu.com/explore/M", brand="lv")
    _post(tmp_db, "xhs:K", platform="xhs",
          url="https://www.xiaohongshu.com/explore/K", brand="lv", keep=True)
    with tmp_db.get_engine().begin() as conn:
        conn.execute(tmp_db.orphans.insert().values(
            post_id="xhs:K", month="2026-06", resolution="pending"))

    monkeypatch.setattr(xc, "pull_all", lambda *a, **k: {})
    monkeypatch.setattr(
        xc, "crosscheck_brand",
        lambda *a, **k: {"orphans": 0, "matches": {"weibo:W": {"xhs": {
            "post_id": "xhs:M", "url": "https://www.xiaohongshu.com/explore/M",
            "date": "2026-06-05", "confidence": 0.8}}}})
    hydrated = {}

    def fake_hydrate(engine, client, month, brand_key, post_ids):
        hydrated.setdefault(brand_key, set()).update(post_ids)
        with engine.begin() as conn:
            conn.execute(tmp_db.posts.update()
                         .where(tmp_db.posts.c.post_id == "xhs:M")
                         .values(url="https://fixed/M?xsec_token=Z="))
        return {"hydrated": 1, "failed": 0, "capped": 0}

    monkeypatch.setattr(xc, "hydrate_xhs_links", fake_hydrate)
    monkeypatch.setattr(filtering, "filter_orphans",
                        lambda *a, **k: {"kept": 0})
    pipeline.run_crosscheck("2026-06", brand_keys=["lv"])
    # both the matched post and the kept orphan were in scope
    assert hydrated["lv"] == {"xhs:M", "xhs:K"}
    # the persisted matches file carries the FIXED url for enrich to store
    saved = json.loads((tmp_path / "runs" / "2026-06" / "lv"
                        / "crosscheck_matches.json").read_text())
    assert saved["weibo:W"]["xhs"]["url"] == "https://fixed/M?xsec_token=Z="


def test_projects_view_prefers_live_url_for_evidence(tmp_db, monkeypatch):
    monkeypatch.setenv("CONSOLE_PASSPHRASE", "team-pass-123")
    monkeypatch.setenv("MM_SECRET_KEY", "f" * 64)
    from fastapi.testclient import TestClient
    from mm.console import create_app
    c = TestClient(create_app())
    c.post("/login", data={"name": "A", "passphrase": "team-pass-123"},
           follow_redirects=False)
    _post(tmp_db, "weibo:W", platform="weibo", url="https://x/w", keep=True)
    _post(tmp_db, "xhs:E", platform="xhs",
          url="https://www.xiaohongshu.com/discovery/item/E?xsec_source=app_share&xsec_token=NEW=")
    with tmp_db.get_engine().begin() as conn:
        pid = conn.execute(tmp_db.projects.insert().values(
            month="2026-06", brand="lv", title="T", phase_suffix=None,
            date_start="2026-06-05", date_end="2026-06-05", ongoing=False,
            assets="PHOTO", description="T", celebs="[]", hero_media="[]",
            status="draft")).inserted_primary_key[0]
        conn.execute(tmp_db.project_posts.insert().values(
            project_id=pid, post_id="weibo:W", role="member"))
        tmp_db.upsert(conn, tmp_db.platform_matches, {
            "project_id": pid, "platform": "xhs", "present": True,
            "matched_url": "https://www.xiaohongshu.com/explore/E",  # stale
            "matched_post_id": "xhs:E", "confidence": 0.8},
            ["project_id", "platform"])
    page = c.get("/review/2026-06/projects").text
    assert "xsec_token=NEW=" in page               # live url wins
    assert 'href="https://www.xiaohongshu.com/explore/E"' not in page
