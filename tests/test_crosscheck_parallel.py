"""Cross-check parallelism: brands concurrent, platform pulls concurrent
within a brand, match.md escalations pooled — with matching semantics
unchanged (heuristic hits, LLM hits, best-per-platform, orphans)."""
import json
import threading

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


def _post(mdb, post_id, platform, caption, *, keep=None, brand="lv",
          created="2026-06-05T12:00:00+08:00"):
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
                "reasons": "[]", "celebs_tagged": "[]", "category": "event",
                "media_focus": "photo", "needs_review": False}, ["post_id"])


def test_run_crosscheck_brands_in_parallel(tmp_db, monkeypatch):
    from mm import crosscheck as xc_mod, pipeline

    barrier = threading.Barrier(5, timeout=8)   # all 5 brands at once or bust

    def fake_pull_all(engine, client, cfg, month, brand_key, progress=None):
        barrier.wait()
        if progress:
            progress("pulled 4/4 platforms")
        return {"douyin": 1, "xhs": 1, "wechat_mp": 0, "wechat_channels": 0}

    def fake_brand(engine, llm, cfg, month, brand_key, should_stop=None):
        return {"matches": {}, "orphans": 0}

    monkeypatch.setattr(xc_mod, "pull_all", fake_pull_all)
    monkeypatch.setattr(xc_mod, "crosscheck_brand", fake_brand)
    monkeypatch.setattr(pipeline, "TikHubClient",
                        type("C", (), {"__init__": lambda s, *a: None,
                                       "close": lambda s: None}))
    monkeypatch.setattr(pipeline, "LLM", lambda *a, **k: object())
    monkeypatch.setattr(pipeline, "Settings",
                        type("S", (), {"load": staticmethod(lambda: None)}))

    notes = []
    res = pipeline.run_crosscheck("2026-06", progress=notes.append)
    assert set(res) == {"chanel", "lv", "tiffany", "gucci", "fendi"}
    assert all("pulls" in r for r in res.values())
    with tmp_db.get_engine().connect() as conn:
        assert tmp_db.get_run(conn, "2026-06")["phases"]["crosscheck"] == "done"
    assert any("crosscheck ·" in n for n in notes)


def test_pull_all_platforms_in_parallel(tmp_db, monkeypatch):
    from mm import crosscheck as xc_mod
    from mm.config import BrandsConfig

    barrier = threading.Barrier(4, timeout=8)   # all 4 platforms at once

    def fake_pull_platform(engine, client, cfg, month, brand_key, platform,
                           **kw):
        barrier.wait()
        if platform == "xhs":
            raise RuntimeError("xhs quota")
        return 3

    monkeypatch.setattr(xc_mod, "pull_platform", fake_pull_platform)
    counts = xc_mod.pull_all(tmp_db.get_engine(), object(),
                             BrandsConfig.load(), "2026-06", "lv")
    assert counts["douyin"] == 3 and counts["wechat_mp"] == 3
    assert str(counts["xhs"]).startswith("error:")   # isolated, not fatal


def test_escalations_judged_in_parallel_with_same_semantics(tmp_db):
    from mm import crosscheck as xc
    from mm.config import BrandsConfig
    # kept weibo post: latin keywords {ancora, showcase, evening, premiere,
    # runway, collection} (brand names are stopworded)
    _post(tmp_db, "weibo:K1", "weibo",
          "ANCORA showcase evening premiere runway collection", keep=True)
    # strong keyword overlap (6) — now LLM-verified like everything else
    _post(tmp_db, "douyin:D1", "douyin",
          "ancora showcase evening premiere runway collection tonight")
    # escalation band (2 shared keywords) on three platforms
    for pid, plat in (("xhs:X1", "xhs"), ("wechat_mp:W1", "wechat_mp"),
                      ("douyin:D2", "douyin")):
        _post(tmp_db, pid, plat, "ancora showcase behind scenes")
    # non-candidate: no shared keywords → orphan
    _post(tmp_db, "xhs:X9", "xhs", "totally unrelated brunch spot")

    barrier = threading.Barrier(4, timeout=8)   # all 4 pairs judged at once

    class BarrierLLM:
        def call_json(self, name, variables, **kw):
            assert name == "match"
            barrier.wait()
            return {"same_event": True, "confidence": 0.9, "reason": "same event"}

    res = xc.crosscheck_brand(tmp_db.get_engine(), BarrierLLM(),
                              BrandsConfig.load(), "2026-06", "lv")
    hits = res["matches"]["weibo:K1"]
    assert hits["douyin"]["confidence"] == 0.9
    assert hits["douyin"]["post_id"] == "douyin:D1"  # first judged wins ties
    assert hits["xhs"]["post_id"] == "xhs:X1"
    assert hits["wechat_mp"]["post_id"] == "wechat_mp:W1"
    assert res["orphans"] == 1                       # only the brunch post
    with tmp_db.get_engine().connect() as conn:
        orphan = conn.execute(select(tmp_db.orphans)).mappings().all()
    assert [o["post_id"] for o in orphan] == ["xhs:X9"]


def test_escalation_llm_failure_is_isolated(tmp_db):
    from mm import crosscheck as xc
    from mm.config import BrandsConfig
    _post(tmp_db, "weibo:K2", "weibo",
          "ancora showcase evening premiere", keep=True)
    _post(tmp_db, "xhs:E1", "xhs", "ancora showcase somewhere")

    class BoomLLM:
        def call_json(self, *a, **k):
            raise RuntimeError("api down")

    res = xc.crosscheck_brand(tmp_db.get_engine(), BoomLLM(),
                              BrandsConfig.load(), "2026-06", "lv")
    assert res["matches"]["weibo:K2"] == {}          # no hit, no crash
    assert res["orphans"] == 1                       # unmatched → orphan
