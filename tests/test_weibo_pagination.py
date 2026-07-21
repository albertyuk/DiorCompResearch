"""Weibo pagination against the real TikHub envelope shape.

The envelope echoes the request parameters at `params.<key>` BEFORE the
payload (`data.data.since_id`) in iteration order — a whole-response
find_key returns the cursor we just sent, freezing pagination on one page
(observed live: "403 posts in window" for a brand that posts twice a day).
"""
import pytest

from mm import db as mdb, ingest, normalize
from mm.config import BrandsConfig


def _mblog(mid: str, created: str, text: str = "六月 campaign 内容"):
    return {"mblogid": mid, "mid": mid, "id": mid, "created_at": created,
            "text_raw": text, "isLongText": False, "pic_ids": [],
            "pic_infos": {}, "user": {"idstr": "1892475055"}}


def _envelope(mblogs, since_id, echo_since_id=None):
    """Real TikHub shape: top-level params echo BEFORE the data payload."""
    env = {"code": 200, "router": "/api/v1/weibo/web_v2/fetch_user_posts"}
    if echo_since_id is not None:
        env["params"] = {"uid": "1892475055", "since_id": echo_since_id}
    env["data"] = {"data": {"since_id": since_id, "list": mblogs}, "ok": 1}
    return env


JUNE = [_mblog(f"J{i}", f"Mon Jun {9 + i:02d} 12:00:00 +0800 2026")
        for i in range(6)]
MAY = [_mblog(f"M{i}", f"Thu May {20 + i:02d} 12:00:00 +0800 2026")
       for i in range(4)]


class FakeClient:
    """Scripted per-request responses keyed by the since_id we send."""

    def __init__(self, pages: dict):
        self.pages = pages          # {since_id_sent_or_None: envelope}
        self.calls = []

    def call(self, key, *, conn=None, brand=None, month=None, **kw):
        assert key == "weibo_user_posts"
        self.calls.append(kw.get("since_id"))
        return self.pages[kw.get("since_id")]


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(mdb, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(mdb, "_engine", None)
    import mm.media as mmedia
    monkeypatch.setattr(mmedia, "RUNS_DIR", tmp_path / "runs")
    yield mdb.get_engine()
    mdb._engine.dispose()


def test_next_cursor_ignores_params_echo():
    env = _envelope(JUNE, since_id="REAL_NEXT", echo_since_id="SENT_CURSOR")
    assert normalize.next_cursor(env, "since_id") == "REAL_NEXT"
    # the buggy whole-response search proves the trap exists in the shape
    assert normalize.find_key(env, "since_id") == "SENT_CURSOR"


def test_frozen_cursor_terminates_without_recounting(engine):
    # server re-serves the same page with an unmoved since_id forever
    pages = {
        None: _envelope(JUNE[:3], since_id="X"),
        "X": _envelope(JUNE[3:], since_id="X", echo_since_id="X"),
    }
    client = FakeClient(pages)
    res = ingest.ingest_weibo(engine, client, BrandsConfig.load(),
                              "2026-06", "chanel")
    assert client.calls == [None, "X"]          # stopped as soon as X repeated
    assert res["posts"] == 6                     # every post counted once


def test_healthy_pagination_advances_past_echo(engine):
    # params echoes the sent cursor but data.since_id moves on — must page on
    pages = {
        None: _envelope(JUNE[:3], since_id="X"),
        "X": _envelope(JUNE[3:], since_id="Y", echo_since_id="X"),
        "Y": _envelope(MAY, since_id="Z", echo_since_id="Y"),
    }
    client = FakeClient(pages)
    res = ingest.ingest_weibo(engine, client, BrandsConfig.load(),
                              "2026-06", "chanel")
    assert client.calls == [None, "X", "Y"]      # reached the May page…
    assert res["posts"] == 6                     # …and stopped on older posts


# ── web_v2 outage → app-timeline fallback (live-verified 2026-07-21) ─────────

def _app_envelope(mblogs):
    """weibo/app/fetch_user_timeline shape: items[] of {category, data} with
    header cards mixed in; the mblog is item.data (or item.data.mblog)."""
    items = [{"category": "card", "data": {"card_type": 216}}]
    for i, m in enumerate(mblogs):
        # exercise both nestings
        items.append({"category": "feed",
                      "data": m if i % 2 else {"mblog": m}})
    return {"code": 200, "data": {"items": items, "moreInfo": {}}}


def test_app_timeline_shape_is_extracted():
    env = _app_envelope(JUNE[:3])
    posts = normalize.weibo_posts_from_response(env)
    assert [p["mblogid"] for p in posts] == ["J0", "J1", "J2"]
    # the header card contributed nothing
    assert all("card_type" not in p for p in posts)


def test_ingest_falls_back_to_app_timeline_on_outage(engine):
    from mm.tikhub import TikHubError

    class OutageClient:
        """web_v2 errors like the 2026-07-21 outage; the app timeline
        serves the same posts page-numbered."""

        def __init__(self):
            self.calls = []
            self.app_pages = {1: _app_envelope(JUNE[:4]),
                              2: _app_envelope(JUNE[4:]),
                              3: _app_envelope(MAY)}

        def call(self, key, *, conn=None, brand=None, month=None, **kw):
            self.calls.append((key, kw.get("page") or kw.get("since_id")))
            if key == "weibo_user_posts":
                raise TikHubError("weibo_user_posts: retries exhausted")
            assert key == "weibo_user_timeline_app"
            return self.app_pages.get(kw["page"], _app_envelope([]))

    client = OutageClient()
    res = ingest.ingest_weibo(engine, client, BrandsConfig.load(),
                              "2026-06", "chanel")
    assert res["posts"] == 6                     # full June window ingested
    assert client.calls[0] == ("weibo_user_posts", None)
    assert ("weibo_user_timeline_app", 1) in client.calls
    assert ("weibo_user_timeline_app", 3) in client.calls  # stopped on May
    # web_v2 was not retried after the flip
    assert [c for c in client.calls[1:] if c[0] == "weibo_user_posts"] == []


def test_overlapping_pages_count_unique_posts_only(engine):
    # cursor moves but pages overlap (common in cursor pagination)
    pages = {
        None: _envelope(JUNE[:4], since_id="X"),
        "X": _envelope(JUNE[2:], since_id="Y", echo_since_id="X"),
        "Y": _envelope(MAY, since_id="Z", echo_since_id="Y"),
    }
    client = FakeClient(pages)
    res = ingest.ingest_weibo(engine, client, BrandsConfig.load(),
                              "2026-06", "chanel")
    assert res["posts"] == 6                     # J2/J3 not double-counted
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = conn.execute(select(mdb.posts.c.post_id)).all()
    assert len(rows) == 6
