"""Brand selection before the search phase (owner request): pick which
brands a month's search covers; downstream cross-check/enrich stay limited
to brands that actually have posts."""
import time

import pytest
from fastapi.testclient import TestClient

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
    import mm.console as console_mod
    console_mod.TASKS.clear()
    from mm.console import create_app
    c = TestClient(create_app())
    r = c.post("/login", data={"name": "Alice", "passphrase": PASS},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _wire_capture(monkeypatch, got):
    from mm import pipeline

    def fake_ingest(month, brand_keys=None, **kw):
        got["ingest_keys"] = brand_keys
        return {}

    def fake_filter(month, brand_keys=None, **kw):
        got["filter_keys"] = brand_keys
        return {}

    monkeypatch.setattr(pipeline, "run_ingest", fake_ingest)
    monkeypatch.setattr(pipeline, "run_filter", fake_filter)


def _wait(got, key, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if key in got:
            return
        time.sleep(0.05)
    raise AssertionError(f"{key} never captured")


def test_runs_page_offers_a_checkbox_per_brand(client, tmp_db):
    """Every configured brand gets a checkbox; ready brands (verified Weibo)
    start ticked, brands still awaiting account confirmation start unticked
    so they never block a search by default."""
    from mm.config import BrandsConfig
    from mm.resolve import weibo_blockers
    cfg = BrandsConfig.load()
    blocked = {b["brand"] for b in weibo_blockers(cfg)}
    page = client.get("/").text
    assert "brands to search:" in page
    for b in cfg.brands:
        assert f'name="brands" value="{b.key}"' in page, b.key
        row = page.split(f'value="{b.key}"')[1][:40]
        if b.key in blocked:
            assert "checked" not in row, b.key
        else:
            assert "checked" in row, b.key


def test_new_brands_are_configured_and_pool_is_capped():
    from mm import pipeline
    from mm.config import BrandsConfig
    keys = [b.key for b in BrandsConfig.load().brands]
    for k in ("prada", "loewe", "valentino", "bottega", "cartier",
              "hermes", "bvlgari"):
        assert k in keys
    assert len(keys) == 12
    # concurrent brand processing is capped at 10 (MM_BRAND_WORKERS overrides)
    assert pipeline._brand_pool(len(keys)) == 10
    assert pipeline._brand_pool(3) == 3
    import os
    os.environ["MM_BRAND_WORKERS"] = "4"
    try:
        assert pipeline._brand_pool(12) == 4
    finally:
        del os.environ["MM_BRAND_WORKERS"]


def test_start_with_a_subset_limits_ingest_and_filter(client, tmp_db,
                                                      monkeypatch):
    got = {}
    _wire_capture(monkeypatch, got)
    r = client.post("/runs/2026-06/start", data={"brands": ["lv", "gucci"]},
                    follow_redirects=False)
    assert r.status_code == 303
    _wait(got, "filter_keys")
    assert got["ingest_keys"] == ["lv", "gucci"]
    assert got["filter_keys"] == ["lv", "gucci"]


def test_bare_start_searches_every_ready_brand(client, tmp_db, monkeypatch):
    """No checkboxes posted (or the default form state) = every brand whose
    Weibo account is confirmed. Newly added, unconfirmed brands never block
    a search unless explicitly ticked."""
    from mm.config import BrandsConfig
    from mm.resolve import weibo_blockers
    cfg = BrandsConfig.load()
    blocked = {b["brand"] for b in weibo_blockers(cfg)}
    ready = [b.key for b in cfg.brands if b.key not in blocked]
    got = {}
    _wire_capture(monkeypatch, got)
    r = client.post("/runs/2026-07/start", follow_redirects=False)
    assert r.status_code == 303 and "msg=" not in r.headers["location"]
    _wait(got, "filter_keys")
    assert got["ingest_keys"] == ready


def test_ticking_an_unconfirmed_brand_blocks_with_guidance(client, tmp_db,
                                                           monkeypatch):
    got = {}
    _wire_capture(monkeypatch, got)
    r = client.post("/runs/2026-09/start", data={"brands": ["lv", "prada"]},
                    follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    time.sleep(0.3)
    assert "ingest_keys" not in got            # nothing started


def test_start_with_only_unknown_brands_is_rejected(client, tmp_db,
                                                    monkeypatch):
    got = {}
    _wire_capture(monkeypatch, got)
    r = client.post("/runs/2026-08/start", data={"brands": ["nonsense"]},
                    follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    time.sleep(0.3)
    assert "ingest_keys" not in got                # nothing was started


def test_confirm_limits_crosscheck_to_brands_with_posts(client, tmp_db,
                                                        monkeypatch):
    from mm import pipeline
    with tmp_db.get_engine().begin() as conn:
        tmp_db.upsert(conn, tmp_db.posts, {
            "post_id": "weibo:B1", "month": "2026-06", "brand": "lv",
            "platform": "weibo", "url": "https://x/1",
            "created_at": "2026-06-05T12:00:00+08:00", "caption": "c",
            "at_tags": "[]", "hashtags": "[]", "media": "[]",
            "is_repost": False, "repost_ambiguous": False}, ["post_id"])
    got = {}

    def fake_xc(month, brand_keys=None, **kw):
        got["xc_keys"] = brand_keys
        return {}

    def fake_enrich(month, brand_keys=None, **kw):
        got["enrich_keys"] = brand_keys
        return {}

    monkeypatch.setattr(pipeline, "run_crosscheck", fake_xc)
    monkeypatch.setattr(pipeline, "run_enrich", fake_enrich)
    r = client.post("/review/2026-06/posts/confirm", follow_redirects=False)
    assert r.status_code == 303
    _wait(got, "enrich_keys")
    assert got["xc_keys"] == ["lv"]                # only the searched brand
    assert got["enrich_keys"] == ["lv"]
