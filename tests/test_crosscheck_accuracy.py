"""Cross-check accuracy rework (owner report: platform matches inconsistent
with reality): shared campaign hashtags match deterministically; celeb /
keyword overlap only NOMINATES a pair — the match LLM decides with rich
context; judgments are cached so runs are consistent and never re-billed."""
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


def _post(mdb, post_id, platform, caption, *, hashtags="[]", keep=None,
          brand="lv", created="2026-06-05T12:00:00+08:00"):
    with mdb.get_engine().begin() as conn:
        mdb.upsert(conn, mdb.posts, {
            "post_id": post_id, "month": "2026-06", "brand": brand,
            "platform": platform, "url": f"https://x/{post_id}",
            "created_at": created, "caption": caption,
            "at_tags": "[]", "hashtags": hashtags, "media": "[]",
            "is_repost": False, "repost_ambiguous": False}, ["post_id"])
        if keep is not None:
            mdb.upsert(conn, mdb.verdicts, {
                "post_id": post_id, "keep": keep, "confidence": 0.9,
                "reasons": "[]", "celebs_tagged": "[]", "category": "event",
                "media_focus": "photo", "needs_review": False}, ["post_id"])


class NeverLLM:
    def call_json(self, *a, **kw):
        raise AssertionError("no LLM call expected")


def test_shared_campaign_hashtag_matches_without_llm(tmp_db):
    from mm import crosscheck as xc
    from mm.config import BrandsConfig
    _post(tmp_db, "weibo:H1", "weibo", "SPEEDY 广告大片",
          hashtags='["SpeedyP9", "LouisVuitton"]', keep=True)
    _post(tmp_db, "douyin:H2", "douyin", "同款手袋",
          hashtags='["speedy p9"]')                 # spacing/case normalized
    res = xc.crosscheck_brand(tmp_db.get_engine(), NeverLLM(),
                              BrandsConfig.load(), "2026-06", "lv")
    hit = res["matches"]["weibo:H1"]["douyin"]
    assert hit["post_id"] == "douyin:H2"
    assert hit["confidence"] == xc.HASHTAG_CONFIDENCE
    assert "hashtag" in hit["why"]
    assert res["orphans"] == 0


def test_brand_name_hashtag_alone_never_matches(tmp_db):
    from mm import crosscheck as xc
    from mm.config import BrandsConfig
    # only the brand-generic tag is shared — proves nothing, no captions in
    # common → not even nominated for the LLM
    _post(tmp_db, "weibo:G1", "weibo", "七夕限定系列",
          hashtags='["LouisVuitton"]', keep=True)
    _post(tmp_db, "xhs:G2", "xhs", "brunch today",
          hashtags='["louis vuitton"]')
    res = xc.crosscheck_brand(tmp_db.get_engine(), NeverLLM(),
                              BrandsConfig.load(), "2026-06", "lv")
    assert res["matches"]["weibo:G1"] == {}
    assert res["orphans"] == 1


def test_shared_celeb_no_longer_auto_matches(tmp_db):
    """The old 0.85 auto-match on a shared ambassador is exactly how ticks
    diverged from reality — the LLM now decides, and a 'no' sticks."""
    from mm import crosscheck as xc
    from mm.config import BrandsConfig
    _post(tmp_db, "weibo:C1", "weibo", "王一博 出席快闪店开幕", keep=True)
    with tmp_db.get_engine().begin() as conn:
        conn.execute(tmp_db.verdicts.update()
                     .where(tmp_db.verdicts.c.post_id == "weibo:C1")
                     .values(celebs_tagged=json.dumps(
                         [{"name_cn": "王一博"}], ensure_ascii=False)))
    _post(tmp_db, "douyin:C2", "douyin", "王一博 全新腕表大片")   # different campaign
    calls = []

    class NoLLM:
        def call_json(self, name, variables, **kw):
            calls.append(variables)
            return {"same_event": False, "confidence": 0.9,
                    "reason": "different campaigns sharing an ambassador"}

    res = xc.crosscheck_brand(tmp_db.get_engine(), NoLLM(),
                              BrandsConfig.load(), "2026-06", "lv")
    assert len(calls) == 1                          # nominated, judged…
    assert res["matches"]["weibo:C1"] == {}         # …and rejected
    assert res["orphans"] == 1
    # the judge saw the rich context, not bare captions
    v = calls[0]
    assert "ref_hashtags" in v and "candidate_hashtags" in v
    assert "heuristic_evidence" in v and "shared celeb" in v["heuristic_evidence"]


def test_judgments_cached_across_runs(tmp_db):
    from mm import crosscheck as xc
    from mm.config import BrandsConfig
    _post(tmp_db, "weibo:J1", "weibo",
          "ancora showcase evening premiere", keep=True)
    _post(tmp_db, "xhs:J2", "xhs", "ancora showcase 到店打卡")
    calls = []

    class CountingLLM:
        def call_json(self, name, variables, **kw):
            calls.append(name)
            return {"same_event": True, "confidence": 0.8, "reason": "same pop-up"}

    engine = tmp_db.get_engine()
    cfg = BrandsConfig.load()
    r1 = xc.crosscheck_brand(engine, CountingLLM(), cfg, "2026-06", "lv")
    assert len(calls) == 1
    assert r1["matches"]["weibo:J1"]["xhs"]["confidence"] == 0.8
    with engine.connect() as conn:
        row = conn.execute(select(tmp_db.match_judgments)).mappings().first()
    assert row["same_event"] is True and row["confidence"] == 0.8
    # second run: same verdict, zero new LLM calls (and a NO also sticks)
    r2 = xc.crosscheck_brand(engine, NeverLLM(), cfg, "2026-06", "lv")
    assert r2["matches"]["weibo:J1"]["xhs"]["post_id"] == "xhs:J2"
    assert r2["matches"]["weibo:J1"]["xhs"]["why"] == "same pop-up"
    assert r2["matches"]["weibo:J1"]["xhs"]["confidence"] == 0.8


def test_cached_rejection_sticks(tmp_db):
    from mm import crosscheck as xc
    from mm.config import BrandsConfig
    _post(tmp_db, "weibo:N1", "weibo", "ancora showcase", keep=True)
    _post(tmp_db, "douyin:N2", "douyin", "ancora showcase 无关视频")
    with tmp_db.get_engine().begin() as conn:
        tmp_db.upsert(conn, tmp_db.match_judgments, {
            "ref_post_id": "weibo:N1", "cand_post_id": "douyin:N2",
            "same_event": False, "confidence": 0.9,
            "reason": "unrelated", "at": tmp_db.now_iso()},
            ["ref_post_id", "cand_post_id"])
    res = xc.crosscheck_brand(tmp_db.get_engine(), NeverLLM(),
                              BrandsConfig.load(), "2026-06", "lv")
    assert res["matches"]["weibo:N1"] == {}
    assert res["orphans"] == 1


def test_archive_cleans_judgments(tmp_db):
    _post(tmp_db, "weibo:A1", "weibo", "x", keep=True)
    _post(tmp_db, "douyin:A2", "douyin", "y")
    with tmp_db.get_engine().begin() as conn:
        tmp_db.upsert(conn, tmp_db.match_judgments, {
            "ref_post_id": "weibo:A1", "cand_post_id": "douyin:A2",
            "same_event": True, "confidence": 0.9, "reason": "r",
            "at": tmp_db.now_iso()}, ["ref_post_id", "cand_post_id"])
    tmp_db.archive_month(tmp_db.get_engine(), "2026-06", "Alice")
    with tmp_db.get_engine().connect() as conn:
        assert conn.execute(select(tmp_db.match_judgments)).first() is None
