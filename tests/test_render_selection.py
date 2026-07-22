"""Selective render + versioned deck files (owner request): the reviewer
picks exactly which projects render (Render all still exists), every render
writes NEW timestamped files (PARTIAL marks subsets), the Decks page shows
last-changed times and can delete versions."""
import os
import time

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


def _post(mdb, post_id, caption="上海快闪", media=None):
    import json
    with mdb.get_engine().begin() as conn:
        mdb.upsert(conn, mdb.posts, {
            "post_id": post_id, "month": "2026-06", "brand": "lv",
            "platform": "weibo", "url": f"https://x/{post_id}",
            "created_at": "2026-06-05T12:00:00+08:00", "caption": caption,
            "at_tags": "[]", "hashtags": "[]",
            "media": json.dumps(media or [], ensure_ascii=False),
            "is_repost": False, "repost_ambiguous": False}, ["post_id"])
        mdb.upsert(conn, mdb.verdicts, {
            "post_id": post_id, "keep": True, "confidence": 0.9,
            "reasons": "[]", "celebs_tagged": "[]", "category": "event",
            "media_focus": "photo", "needs_review": False}, ["post_id"])


def _project(mdb, title, post_id, status="confirmed"):
    with mdb.get_engine().begin() as conn:
        pid = conn.execute(mdb.projects.insert().values(
            month="2026-06", brand="lv", title=title, phase_suffix=None,
            date_start="2026-06-05", date_end="2026-06-05", ongoing=False,
            assets="PHOTO", description=title, celebs="[]", hero_media="[]",
            status=status)).inserted_primary_key[0]
        conn.execute(mdb.project_posts.insert().values(
            project_id=pid, post_id=post_id, role="member"))
    return pid


def _wire_fake_render(monkeypatch, tmp_path, captured):
    from pathlib import Path
    import mm.render.deck as deck_mod
    import mm.render.qa as qa_mod
    import mm.render.visuals as vis_mod
    import mm.render.xlsx as xlsx_mod
    from mm import pipeline

    class FakeFactory:
        def __init__(self, month, mode=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def visual_for_post(self, brand, post):
            f = tmp_path / "card.png"
            f.write_bytes(b"\x89PNG")
            return f

    class SpyBuilder:
        def build(self, spec, out):
            captured["titles"] = [p.title for b in spec for p in b.projects]
            Path(out).write_bytes(b"pptx")

    def spy_xlsx(spec, out):
        Path(out).write_bytes(b"xlsx")
        return out

    monkeypatch.setattr(vis_mod, "VisualFactory", FakeFactory)
    monkeypatch.setattr(deck_mod, "DeckBuilder", lambda: SpyBuilder())
    monkeypatch.setattr(xlsx_mod, "write_projects_xlsx", spy_xlsx)
    monkeypatch.setattr(qa_mod, "run_qa", lambda p, d: {"ok": True})
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path)


def test_only_ids_renders_a_subset_and_marks_the_file_partial(
        tmp_db, monkeypatch, tmp_path):
    from mm import pipeline
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    _post(tmp_db, "weibo:R1", media=[{"kind": "image",
                                      "local_path": str(img)}])
    _post(tmp_db, "weibo:R2", media=[{"kind": "image",
                                      "local_path": str(img)}])
    p1 = _project(tmp_db, "SHOW ONE", "weibo:R1")
    _project(tmp_db, "SHOW TWO", "weibo:R2")
    captured = {}
    _wire_fake_render(monkeypatch, tmp_path, captured)
    res = pipeline.run_render("2026-06", visuals_mode="card", only_ids=[p1])
    assert captured["titles"] == ["SHOW ONE"]
    assert "_PARTIAL_" in res["pptx"] and "_PARTIAL_" in res["xlsx"]
    # a full render is not marked partial, and never reuses the same name
    res_full = pipeline.run_render("2026-06", visuals_mode="card")
    assert captured["titles"] == ["SHOW ONE", "SHOW TWO"]
    assert "_PARTIAL_" not in res_full["pptx"]
    assert res_full["pptx"] != res["pptx"]           # versions coexist
    assert os.path.exists(res["pptx"]) and os.path.exists(res_full["pptx"])


def test_render_reports_dropped_unreadable_images(tmp_db, monkeypatch,
                                                  tmp_path):
    """An unreadable image costs its own grid cell, and the render SAYS so —
    result carries dropped_images and the progress log warns."""
    from mm import pipeline
    good = tmp_path / "good.jpg"
    from PIL import Image
    Image.new("RGB", (50, 50)).save(good)
    junk = tmp_path / "junk.heic"
    junk.write_bytes(b"not an image")
    _post(tmp_db, "weibo:D1", media=[
        {"kind": "image", "local_path": str(good), "selected": True},
        {"kind": "image", "local_path": str(junk), "selected": True}])
    _project(tmp_db, "DROP TEST", "weibo:D1")
    captured = {}
    _wire_fake_render(monkeypatch, tmp_path, captured)
    notes = []
    res = pipeline.run_render("2026-06", visuals_mode="card",
                              progress=notes.append)
    assert res["dropped_images"] == 1
    assert any("unreadable" in n for n in notes)
    assert captured["titles"] == ["DROP TEST"]     # the render still finished


def test_render_needs_no_browser_when_posts_have_images(tmp_db, monkeypatch,
                                                        tmp_path):
    """OOM postmortem (owner: 'the pptx does not render and just crashes'):
    the render used to launch a Chromium PER WORKER even when every post
    already had a photo — ~700MB of the 2GB box for nothing. The factory is
    now lazy: a photo-rich month must render with no browser at all."""
    from pathlib import Path
    from PIL import Image
    import mm.render.deck as deck_mod
    import mm.render.qa as qa_mod
    import mm.render.visuals as vis_mod
    import mm.render.xlsx as xlsx_mod
    from mm import pipeline
    img = tmp_path / "a.jpg"
    Image.new("RGB", (40, 40)).save(img)
    _post(tmp_db, "weibo:L1", media=[{"kind": "image",
                                      "local_path": str(img)}])
    _project(tmp_db, "LAZY ONE", "weibo:L1")

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("VisualFactory constructed — browser "
                                 "launches must be lazy")

    class SpyBuilder:
        def build(self, spec, out):
            Path(out).write_bytes(b"pptx")

    monkeypatch.setattr(vis_mod, "VisualFactory", Boom)
    monkeypatch.setattr(deck_mod, "DeckBuilder", lambda: SpyBuilder())
    monkeypatch.setattr(xlsx_mod, "write_projects_xlsx",
                        lambda spec, out: Path(out).write_bytes(b"x") or out)
    monkeypatch.setattr(qa_mod, "run_qa", lambda p, d: {"ok": True})
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path)
    res = pipeline.run_render("2026-06", visuals_mode="card")
    assert res["qa"]["ok"]


def test_render_route_passes_the_ticked_selection(client, tmp_db,
                                                  monkeypatch):
    from mm import pipeline
    _post(tmp_db, "weibo:R1")
    p1 = _project(tmp_db, "SHOW ONE", "weibo:R1", status="draft")
    got = {}

    def fake_render(month, **kw):
        got["month"] = month
        got["only_ids"] = kw.get("only_ids")
        return {"ok": True}

    monkeypatch.setattr(pipeline, "run_render", fake_render)
    r = client.post("/review/2026-06/render",
                    data={"visuals": "card", "scope": "selected",
                          "only": f"{p1}"}, follow_redirects=False)
    assert r.status_code == 303
    for _ in range(100):
        if "only_ids" in got:
            break
        time.sleep(0.05)
    assert got["only_ids"] == [p1]
    # Render all ignores the ticks entirely
    got.clear()
    import mm.console as console_mod
    console_mod.TASKS.clear()
    r = client.post("/review/2026-06/render",
                    data={"visuals": "card", "scope": "all",
                          "only": f"{p1}"}, follow_redirects=False)
    assert r.status_code == 303
    for _ in range(100):
        if "only_ids" in got:
            break
        time.sleep(0.05)
    assert got["only_ids"] is None


def test_projects_page_offers_render_ticks_and_render_all(client, tmp_db):
    _post(tmp_db, "weibo:R1")
    _post(tmp_db, "weibo:R2")
    p1 = _project(tmp_db, "SHOW ONE", "weibo:R1", status="draft")
    p2 = _project(tmp_db, "SHOW TWO", "weibo:R2", status="dropped")
    page = client.get("/review/2026-06/projects").text
    assert f'class="render-sel" value="{p1}" checked' in page
    assert f'class="render-sel" value="{p2}"' not in page   # dropped: no tick
    assert 'id="render-only"' in page
    assert 'name="scope" value="selected"' in page
    assert 'name="scope" value="all"' in page
    assert "Render all" in page and "include in render" in page


def test_decks_page_lists_versions_with_mtime_and_delete(client, tmp_db,
                                                         monkeypatch,
                                                         tmp_path):
    import mm.console as console_mod
    monkeypatch.setattr(console_mod, "OUTPUT_DIR", tmp_path)
    old = tmp_path / "_CREATIVE_2026_JUNE_old_20260701-090000.pptx"
    new = tmp_path / "_CREATIVE_2026_JUNE_new_20260715-090000.pptx"
    old.write_bytes(b"pptx-old")
    new.write_bytes(b"pptx-new")
    os.utime(old, (1_750_000_000, 1_750_000_000))    # force distinct mtimes
    os.utime(new, (1_752_000_000, 1_752_000_000))
    page = client.get("/decks").text
    assert "Last changed" in page
    assert old.name in page and new.name in page
    assert page.index(new.name) < page.index(old.name)   # newest first
    assert 'action="/decks/delete"' in page and "Delete" in page
    # delete removes exactly the named version
    r = client.post("/decks/delete", data={"name": old.name},
                    follow_redirects=False)
    assert r.status_code == 303
    assert not old.exists() and new.exists()


def test_deck_delete_rejects_traversal_and_foreign_files(client, tmp_db,
                                                         monkeypatch,
                                                         tmp_path):
    import mm.console as console_mod
    monkeypatch.setattr(console_mod, "OUTPUT_DIR", tmp_path)
    keep = tmp_path / "keep.pptx"
    keep.write_bytes(b"pptx")
    secret = tmp_path.parent / "secret.pptx"
    secret.write_bytes(b"pptx")
    for bad in ("../secret.pptx", "keep.py", "missing.pptx", "a/b.pptx"):
        r = client.post("/decks/delete", data={"name": bad},
                        follow_redirects=False)
        assert r.status_code == 404, bad
    assert keep.exists() and secret.exists()
