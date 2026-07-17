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
    from mm.config import BrandsConfig
    page = client.get("/").text
    assert "brands to search:" in page
    for b in BrandsConfig.load().brands:
        assert f'name="brands" value="{b.key}" checked' in page, b.key


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


def test_start_with_all_brands_means_no_restriction(client, tmp_db,
                                                    monkeypatch):
    from mm.config import BrandsConfig
    got = {}
    _wire_capture(monkeypatch, got)
    everything = [b.key for b in BrandsConfig.load().brands]
    r = client.post("/runs/2026-07/start", data={"brands": everything},
                    follow_redirects=False)
    assert r.status_code == 303
    _wait(got, "filter_keys")
    assert got["ingest_keys"] is None and got["filter_keys"] is None


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
