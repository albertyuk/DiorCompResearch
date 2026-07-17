"""Enrichment parallelism (brands / relation.md / describe.md pools), the
registry write-lock, and batch-level media download dedupe."""
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


def _kept_post(mdb, post_id, caption, *, at_tags="[]", brand="lv"):
    with mdb.get_engine().begin() as conn:
        mdb.upsert(conn, mdb.posts, {
            "post_id": post_id, "month": "2026-06", "brand": brand,
            "platform": "weibo", "url": f"https://x/{post_id}",
            "created_at": "2026-06-05T12:00:00+08:00", "caption": caption,
            "at_tags": at_tags, "hashtags": "[]", "media": "[]",
            "is_repost": False, "repost_ambiguous": False}, ["post_id"])
        mdb.upsert(conn, mdb.verdicts, {
            "post_id": post_id, "keep": True, "confidence": 0.9,
            "reasons": "[]", "celebs_tagged": "[]", "category": "event",
            "media_focus": "photo", "needs_review": False}, ["post_id"])


def test_run_enrich_brands_in_parallel(tmp_db, monkeypatch, tmp_path):
    from mm import enrich as enrich_mod, pipeline
    monkeypatch.setattr(pipeline, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(pipeline, "LLM", lambda *a, **k: object())

    barrier = threading.Barrier(5, timeout=8)

    def fake_brand(engine, llm, cfg, month, brand_key, matches=None):
        barrier.wait()
        return {"projects": 1, "celebs": 0}

    monkeypatch.setattr(enrich_mod, "enrich_brand", fake_brand)
    notes = []
    five = ["chanel", "lv", "tiffany", "gucci", "fendi"]
    res = pipeline.run_enrich("2026-06", brand_keys=five,
                              progress=notes.append)
    assert set(res) == set(five)
    with tmp_db.get_engine().connect() as conn:
        phases = tmp_db.get_run(conn, "2026-06")["phases"]
    assert phases["enrich"] == "done"
    assert phases["review_projects"] == "waiting"    # drafts produced
    assert any("enrich ·" in n for n in notes)


def test_relation_calls_run_in_parallel(tmp_db, monkeypatch):
    monkeypatch.setenv("MM_WEB_CONFIRM", "0")
    from mm import enrich
    from mm.config import BrandsConfig
    for i in range(3):
        _kept_post(tmp_db, f"weibo:R{i}", f"品牌大使@王一博 活动现场 {i}",
                   at_tags='["王一博"]')
    with tmp_db.get_engine().connect() as conn:
        from mm.filtering import kept_posts
        posts = kept_posts(conn, "2026-06", "lv")

    barrier = threading.Barrier(3, timeout=8)

    class BarrierLLM:
        def call_json(self, name, variables, **kw):
            assert name == "relation"
            barrier.wait()
            return {"celebs": [{"name_cn": "王一博", "relation": "BRAND AMBASSADOR",
                                "raw_cn_title": "品牌大使", "occupation": "ACTOR"}]}

    celebs = enrich.extract_caption_relations(
        tmp_db.get_engine(), BarrierLLM(), BrandsConfig.load(),
        "2026-06", "lv", posts)
    assert celebs["王一博"]["relation"] == "BRAND AMBASSADOR"
    assert celebs["王一博"]["verified"] is True
    reg = enrich.registry_get(tmp_db.get_engine(), "王一博")
    assert json.loads(reg["relations_json"])["lv"]["relation"] == "BRAND AMBASSADOR"


def test_enrich_brand_describes_in_parallel(tmp_db, monkeypatch):
    monkeypatch.setenv("MM_WEB_CONFIRM", "0")
    from mm import enrich
    from mm.config import BrandsConfig
    for i in range(3):
        _kept_post(tmp_db, f"weibo:P{i}", f"campaign {i} showcase")

    barrier = threading.Barrier(3, timeout=8)

    class FakeLLM:
        def call_json(self, name, variables, **kw):
            if name == "consolidate":
                return {"projects": [
                    {"title": f"PROJECT {i}", "post_ids": [f"weibo:P{i}"],
                     "category": "event"} for i in range(3)]}
            if name == "describe":
                barrier.wait()          # deadlocks unless 3 run concurrently
                return {"description": f"DESCRIBED {variables['title']}"}
            raise AssertionError(name)

    res = enrich.enrich_brand(tmp_db.get_engine(), FakeLLM(),
                              BrandsConfig.load(), "2026-06", "lv")
    assert res["projects"] == 3
    with tmp_db.get_engine().connect() as conn:
        rows = conn.execute(select(tmp_db.projects)
                            .order_by(tmp_db.projects.c.title)).mappings().all()
    assert len(rows) == 3
    assert all(r["description"].startswith("DESCRIBED PROJECT") for r in rows)
    assert all(r["status"] == "draft" for r in rows)


def test_describe_failure_keeps_fallback_title(tmp_db, monkeypatch):
    monkeypatch.setenv("MM_WEB_CONFIRM", "0")
    from mm import enrich
    from mm.config import BrandsConfig
    _kept_post(tmp_db, "weibo:F1", "solo campaign")

    class HalfLLM:
        def call_json(self, name, variables, **kw):
            if name == "consolidate":
                return {"projects": [{"title": "SOLO SHOW",
                                      "post_ids": ["weibo:F1"]}]}
            raise RuntimeError("describe down")

    res = enrich.enrich_brand(tmp_db.get_engine(), HalfLLM(),
                              BrandsConfig.load(), "2026-06", "lv")
    assert res["projects"] == 1
    with tmp_db.get_engine().connect() as conn:
        row = conn.execute(select(tmp_db.projects)).mappings().first()
    assert row["description"] == "SOLO SHOW"       # formatted-title fallback


def test_batch_media_download_dedupes_across_posts(tmp_path, monkeypatch):
    from mm import ingest
    from mm.media import MediaStore
    calls = []

    def fake_download(self, brand, url, kind="img", referer=None, timeout=30.0):
        calls.append((url, kind))
        return tmp_path / f"{kind}_{url.rsplit('/', 1)[-1]}"

    monkeypatch.setattr(MediaStore, "download", fake_download)
    posts = [
        {"media": [{"url": "http://cdn/a.jpg"}, {"url": "http://cdn/b.jpg"}],
         "author_avatar": "http://cdn/av.jpg"},
        {"media": [{"url": "http://cdn/a.jpg"}, {"url": None}],
         "author_avatar": "http://cdn/av.jpg"},   # same avatar + shared image
    ]
    ingest._download_batch_media(MediaStore("2026-06"), "lv", posts, "http://r/")
    # each unique (url, kind) downloaded exactly once across the whole batch
    assert calls.count(("http://cdn/a.jpg", "img")) == 1
    assert calls.count(("http://cdn/av.jpg", "avatar")) == 1
    assert len(calls) == 3                          # a, b, avatar
    assert posts[0]["media"][0]["local_path"] == posts[1]["media"][0]["local_path"]
    assert posts[1]["media"][1]["local_path"] is None
    assert posts[0]["author_avatar_path"].endswith("avatar_av.jpg")
    assert posts[1]["author_avatar_path"].endswith("avatar_av.jpg")
