"""Review checkpoint #2 overhaul: consolidated-post listing with image
selection, grouping rationale, drag-and-drop merge/adopt + ungroup, the
orphan filter (same rubric as review #1), and the celeb registry page
(names, per-brand relations, photo libraries used by the renderer)."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

PASS = "team-pass-123"
SECRET = "f" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32


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
    monkeypatch.setenv("MM_SECRET_KEY", SECRET)
    from mm.console import create_app
    c = TestClient(create_app())
    r = c.post("/login", data={"name": "Alice", "passphrase": PASS},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _post(mdb, post_id, *, platform="weibo", brand="lv",
          caption="上海快闪 王一博", media=None,
          created="2026-06-05T12:00:00+08:00", keep=None):
    with mdb.get_engine().begin() as conn:
        mdb.upsert(conn, mdb.posts, {
            "post_id": post_id, "month": "2026-06", "brand": brand,
            "platform": platform, "url": f"https://x/{post_id}",
            "created_at": created, "caption": caption,
            "at_tags": "[]", "hashtags": "[]",
            "media": json.dumps(media or [], ensure_ascii=False),
            "is_repost": False, "repost_ambiguous": False}, ["post_id"])
        if keep is not None:
            mdb.upsert(conn, mdb.verdicts, {
                "post_id": post_id, "keep": keep, "confidence": 0.9,
                "reasons": "[]", "rationale": f"rationale for {post_id}",
                "celebs_tagged": "[]", "category": "event",
                "media_focus": "photo", "needs_review": False}, ["post_id"])


def _project(mdb, title, *, brand="lv", post_ids=(), roles=None,
             celebs="[]", rationale=None, date_start="2026-06-05",
             date_end="2026-06-08", heroes="[]", assets="PHOTO",
             matches=()):
    with mdb.get_engine().begin() as conn:
        pid = conn.execute(mdb.projects.insert().values(
            month="2026-06", brand=brand, title=title, phase_suffix=None,
            date_start=date_start, date_end=date_end, ongoing=False,
            assets=assets, description=title, celebs=celebs,
            hero_media=heroes, status="draft",
            rationale=rationale)).inserted_primary_key[0]
        for i, post_id in enumerate(post_ids):
            conn.execute(mdb.project_posts.insert().values(
                project_id=pid, post_id=post_id,
                role=(roles or {}).get(post_id, "member")))
        for m in matches:
            mdb.upsert(conn, mdb.platform_matches, {"project_id": pid, **m},
                       ["project_id", "platform"])
    return pid


# ── review #2 page: members, links, rationale, selection, evidence ──────────

def test_projects_page_members_rationale_and_selection(client, tmp_db, tmp_path):
    img = tmp_path / "m.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"x" * 8)
    _post(tmp_db, "weibo:A1", keep=True,
          media=[{"kind": "image", "local_path": str(img), "selected": True}])
    _post(tmp_db, "weibo:A2", keep=True, caption="第二个帖子")
    _post(tmp_db, "douyin:D1", platform="douyin", caption="同一活动")
    pid = _project(
        tmp_db, "SHANGHAI POP-UP",
        post_ids=("weibo:A1", "weibo:A2", "douyin:D1"),
        roles={"douyin:D1": "match"},
        rationale="All posts cover the same Shanghai pop-up within 3 days.",
        matches=[{"platform": "douyin", "present": True,
                  "matched_url": "https://x/douyin:D1",
                  "matched_date": "2026-06-06", "confidence": 0.85}])
    page = client.get("/review/2026-06/posts").text
    # selection checkboxes + HQ drop zones moved off review #1
    assert 'class="media-sel"' not in page
    assert "dropzone" not in page
    page = client.get("/review/2026-06/projects").text
    # consolidated posts listed, each with a link to the original post
    assert "Consolidated posts — 3" in page
    assert 'href="https://x/weibo:A1"' in page
    assert 'href="https://x/weibo:A2"' in page
    assert 'href="https://x/douyin:D1"' in page
    assert "douyin · matched" in page
    # grouping rationale is written out
    assert "Why these posts are grouped" in page
    assert "same Shanghai pop-up" in page
    # image selection + HQ drop zone now live here, with the lightbox
    assert "media-sel" in page and "dropzone" in page
    assert 'data-post="weibo:A1"' in page and "data-gallery='[" in page
    assert 'id="mmlb"' in page
    # crosscheck evidence is inspectable, not a bare checkbox
    assert "evidence ↗" in page
    # drag handles for merge (project) and move (post), ungroup available
    assert f'data-proj="{pid}"' in page
    assert 'data-post="weibo:A2"' in page and "drag-handle" in page
    assert "/ungroup" in page


# ── grouping: merge / ungroup / adopt ────────────────────────────────────────

def test_merge_projects_moves_everything(client, tmp_db):
    _post(tmp_db, "weibo:M1", keep=True)
    _post(tmp_db, "weibo:M2", keep=True, created="2026-06-12T10:00:00+08:00")
    src = _project(tmp_db, "TEASER DROP", post_ids=("weibo:M2",),
                   celebs=json.dumps([{"name_cn": "王一博", "display": "WANG YIBO",
                                       "relation_display": "BRAND AMBASSADOR"}]),
                   rationale="src why", date_start="2026-06-10",
                   date_end="2026-06-12",
                   matches=[{"platform": "xhs", "present": True,
                             "matched_url": "https://x/xhs:1", "confidence": 0.9}])
    tgt = _project(tmp_db, "MAIN CAMPAIGN", post_ids=("weibo:M1",),
                   celebs=json.dumps([{"name_cn": "王一博", "display": "WANG YIBO",
                                       "relation_display": "BRAND AMBASSADOR"}]),
                   rationale="tgt why", date_start="2026-06-05",
                   date_end="2026-06-06",
                   matches=[{"platform": "xhs", "present": True,
                             "matched_url": "https://x/xhs:2", "confidence": 0.4}])
    r = client.post(f"/review/2026-06/projects/{src}/merge",
                    data={"target_id": tgt})
    assert r.status_code == 200 and r.json()["ok"] is True
    with tmp_db.get_engine().connect() as conn:
        assert conn.execute(select(tmp_db.projects).where(
            tmp_db.projects.c.id == src)).first() is None
        merged = conn.execute(select(tmp_db.projects).where(
            tmp_db.projects.c.id == tgt)).mappings().first()
        members = {r["post_id"] for r in conn.execute(
            select(tmp_db.project_posts).where(
                tmp_db.project_posts.c.project_id == tgt)).mappings()}
        xhs = conn.execute(select(tmp_db.platform_matches).where(
            tmp_db.platform_matches.c.project_id == tgt,
            tmp_db.platform_matches.c.platform == "xhs")).mappings().first()
        audit = tmp_db.last_audit(conn, "project_merge", "project",
                                  f"{src}->{tgt}")
    assert members == {"weibo:M1", "weibo:M2"}
    assert merged["date_start"] == "2026-06-05"      # extended across both
    assert merged["date_end"] == "2026-06-12"
    assert len(json.loads(merged["celebs"])) == 1    # deduped by name
    assert xhs["confidence"] == 0.9                  # best evidence wins
    assert "Merged 'TEASER DROP'" in merged["rationale"]
    assert "src why" in merged["rationale"] and "tgt why" in merged["rationale"]
    assert audit["actor_name"] == "Alice"


def test_merge_rejects_cross_brand_and_self(client, tmp_db):
    _post(tmp_db, "weibo:B1", keep=True)
    _post(tmp_db, "weibo:B2", keep=True, brand="gucci")
    a = _project(tmp_db, "A", post_ids=("weibo:B1",))
    b = _project(tmp_db, "B", brand="gucci", post_ids=("weibo:B2",))
    assert client.post(f"/review/2026-06/projects/{a}/merge",
                       data={"target_id": b}).status_code == 400
    assert client.post(f"/review/2026-06/projects/{a}/merge",
                       data={"target_id": a}).status_code == 400
    assert client.post("/review/2026-06/projects/99999/merge",
                       data={"target_id": a}).status_code == 404


def test_ungroup_splits_per_post_and_reorphans_matches(client, tmp_db):
    img_media = [{"kind": "image", "local_path": None, "url": "u"}]
    _post(tmp_db, "weibo:U1", keep=True, caption="快闪店 开幕 王一博")
    _post(tmp_db, "weibo:U2", keep=True, caption="快闪店 второй",
          created="2026-06-07T10:00:00+08:00")
    _post(tmp_db, "douyin:U3", platform="douyin", media=img_media)
    pid = _project(tmp_db, "POP UP", post_ids=("weibo:U1", "weibo:U2", "douyin:U3"),
                   roles={"douyin:U3": "match"},
                   celebs=json.dumps([{"name_cn": "王一博",
                                       "display": "WANG YIBO",
                                       "relation_display": "BRAND AMBASSADOR"}]))
    r = client.post(f"/review/2026-06/projects/{pid}/ungroup",
                    follow_redirects=False)
    assert r.status_code == 303
    with tmp_db.get_engine().connect() as conn:
        projs = [dict(p) for p in conn.execute(select(tmp_db.projects)
                 .order_by(tmp_db.projects.c.date_start)).mappings()]
        orphan = conn.execute(select(tmp_db.orphans).where(
            tmp_db.orphans.c.post_id == "douyin:U3")).mappings().first()
        memberships = {r["post_id"]: r["project_id"] for r in conn.execute(
            select(tmp_db.project_posts)).mappings()}
    assert len(projs) == 2                        # one per weibo post
    assert all(p["title"] != "POP UP" for p in projs)   # original gone
    # (ids may be recycled by SQLite rowid reuse — titles are the identity)
    assert all("Ungrouped from 'POP UP'" in p["rationale"] for p in projs)
    # the celeb follows only the post whose caption names them
    by_start = {p["date_start"]: p for p in projs}
    assert json.loads(by_start["2026-06-05"]["celebs"])[0]["name_cn"] == "王一博"
    assert json.loads(by_start["2026-06-07"]["celebs"]) == []
    assert orphan["resolution"] == "pending"      # match returned to the pool
    assert set(memberships) == {"weibo:U1", "weibo:U2"}
    # single-post projects refuse to ungroup
    solo = memberships["weibo:U1"]
    assert client.post(f"/review/2026-06/projects/{solo}/ungroup",
                       follow_redirects=False).status_code == 400


def test_adopt_moves_post_and_resolves_orphans(client, tmp_db):
    _post(tmp_db, "weibo:C1", keep=True)
    _post(tmp_db, "weibo:C2", keep=True)
    _post(tmp_db, "douyin:C3", platform="douyin")
    with tmp_db.get_engine().begin() as conn:
        conn.execute(tmp_db.orphans.insert().values(
            post_id="douyin:C3", month="2026-06", resolution="pending"))
    a = _project(tmp_db, "A", post_ids=("weibo:C1", "weibo:C2"))
    b = _project(tmp_db, "B", post_ids=())
    # move a weibo post A → B
    r = client.post(f"/review/2026-06/projects/{b}/adopt",
                    data={"post_id": "weibo:C2"})
    assert r.status_code == 200
    # attach an orphan by dragging it onto B
    r = client.post(f"/review/2026-06/projects/{b}/adopt",
                    data={"post_id": "douyin:C3"})
    assert r.status_code == 200
    with tmp_db.get_engine().connect() as conn:
        members = {r["post_id"]: r for r in conn.execute(
            select(tmp_db.project_posts).where(
                tmp_db.project_posts.c.project_id == b)).mappings()}
        a_members = [r["post_id"] for r in conn.execute(
            select(tmp_db.project_posts).where(
                tmp_db.project_posts.c.project_id == a)).mappings()]
        orphan = conn.execute(select(tmp_db.orphans).where(
            tmp_db.orphans.c.post_id == "douyin:C3")).mappings().first()
        pm = conn.execute(select(tmp_db.platform_matches).where(
            tmp_db.platform_matches.c.project_id == b,
            tmp_db.platform_matches.c.platform == "douyin")).mappings().first()
    assert set(members) == {"weibo:C2", "douyin:C3"}
    assert members["douyin:C3"]["role"] == "match"
    assert a_members == ["weibo:C1"]
    assert orphan["resolution"] == "promoted"
    assert pm["matched_post_id"] == "douyin:C3" and pm["present"] is True


# ── orphans: filtered like review #1 ─────────────────────────────────────────

def test_filter_orphans_uses_same_rubric(tmp_db):
    from mm.config import BrandsConfig
    from mm.filtering import filter_orphans
    _post(tmp_db, "douyin:O1", platform="douyin", caption="上海活动现场")
    _post(tmp_db, "xhs:O2", platform="xhs", caption="香水上新")  # policy drop
    with tmp_db.get_engine().begin() as conn:
        for pid in ("douyin:O1", "xhs:O2"):
            conn.execute(tmp_db.orphans.insert().values(
                post_id=pid, month="2026-06", resolution="pending"))

    class FakeLLM:
        def call_json(self, name, variables, **kw):
            assert name == "filter"
            keep = "香水" not in variables["caption"]
            return {"keep": keep, "confidence": 0.9,
                    "reasons": ["r"], "rationale": "because",
                    "celebs_tagged": [], "category": "event",
                    "media_focus": "photo"}

    stats = filter_orphans(tmp_db.get_engine(), FakeLLM(),
                           BrandsConfig.load(), "2026-06")
    assert stats["filtered"] == 2 and stats["kept"] == 1
    with tmp_db.get_engine().connect() as conn:
        v = {r["post_id"]: dict(r) for r in conn.execute(
            select(tmp_db.verdicts)).mappings()}
    assert v["douyin:O1"]["keep"] is True
    assert v["xhs:O2"]["keep"] is False
    # idempotent: verdicted orphans are not re-billed
    assert filter_orphans(tmp_db.get_engine(), FakeLLM(),
                          BrandsConfig.load(), "2026-06")["pending"] == 0


def test_orphan_list_shows_verdicts_keeps_first(client, tmp_db):
    _post(tmp_db, "douyin:K1", platform="douyin", caption="活动 keep me",
          created="2026-06-09T10:00:00+08:00", keep=True)
    _post(tmp_db, "xhs:D1", platform="xhs", caption="drop me 香水",
          created="2026-06-02T10:00:00+08:00", keep=False)
    with tmp_db.get_engine().begin() as conn:
        for pid in ("douyin:K1", "xhs:D1"):
            conn.execute(tmp_db.orphans.insert().values(
                post_id=pid, month="2026-06", resolution="pending"))
    page = client.get("/review/2026-06/projects").text
    assert "sifted by the same filter" in page
    assert page.index("keep me") < page.index("drop me")   # keeps first
    assert "KEEP · 0.90" in page and "DROP · 0.90" in page
    assert "rationale for xhs:D1" in page                  # why? details


# ── structured celeb editing on the project card ─────────────────────────────

def test_project_update_structured_celeb_rows(client, tmp_db):
    _post(tmp_db, "weibo:S1", keep=True)
    pid = _project(tmp_db, "SHOW", post_ids=("weibo:S1",),
                   celebs=json.dumps([
                       {"name_cn": "王一博", "name_en": "Wang Yibo",
                        "display": "WANG YIBO",
                        "relation_display": "BRAND AMBASSADOR",
                        "verified": True, "occupation": "ACTOR"},
                       {"name_cn": "刘亦菲", "display": "LIU YIFEI",
                        "relation_display": "ACTRESS ?", "verified": False}]))
    r = client.post(f"/review/2026-06/projects/{pid}/update", data={
        "_platforms_submitted": "1", "title": "SHOW",
        "celeb_0_name_cn": "王一博", "celeb_0_name_en": "Wang Yibo",
        "celeb_0_verified": "1", "celeb_0_occupation": "ACTOR",
        "celeb_0_display": "WANG YIBO",
        "celeb_0_relation": "brand friend",         # edited relation
        "celeb_1_name_cn": "刘亦菲", "celeb_1_display": "LIU YIFEI",
        "celeb_1_relation": "ACTRESS ?", "celeb_1_remove": "on",  # removed
        "celeb_new_display": "gong jun", "celeb_new_relation": "",
        "celeb_new_name_cn": "龚俊",
    }, follow_redirects=False)
    assert r.status_code == 303
    with tmp_db.get_engine().connect() as conn:
        celebs = json.loads(conn.execute(select(tmp_db.projects.c.celebs).where(
            tmp_db.projects.c.id == pid)).scalar())
    assert [c["display"] for c in celebs] == ["WANG YIBO", "GONG JUN"]
    assert celebs[0]["relation_display"] == "BRAND FRIEND"
    assert celebs[0]["verified"] is True and celebs[0]["name_en"] == "Wang Yibo"
    assert celebs[1] == {"name_cn": "龚俊", "name_en": None,
                         "display": "GONG JUN",
                         "relation_display": "CELEBRITY ?",
                         "verified": False, "occupation": None}


# ── celeb registry page ──────────────────────────────────────────────────────

def test_celebs_page_add_rename_and_relations(client, tmp_db):
    r = client.post("/celebs/new", data={"name_cn": "王一博",
                                         "name_en": "Wang Yibo"},
                    follow_redirects=False)
    assert r.status_code == 303
    with tmp_db.get_engine().connect() as conn:
        row = conn.execute(select(tmp_db.celeb_registry)).mappings().first()
    cid = row["id"]
    r = client.post(f"/celebs/{cid}/update", data={
        "name_cn": "王一博", "name_en": "WANG YIBO", "occupation": "actor",
        "relation_lv": "brand ambassador", "verified_lv": "on",
        "relation_gucci": ""}, follow_redirects=False)
    assert r.status_code == 303
    with tmp_db.get_engine().connect() as conn:
        row = dict(conn.execute(select(tmp_db.celeb_registry)).mappings().first())
    rel = json.loads(row["relations_json"])
    assert rel["lv"] == {"relation": "BRAND AMBASSADOR", "verified": True}
    assert "gucci" not in rel
    page = client.get("/celebs").text
    assert "王一博" in page and "BRAND AMBASSADOR" in page
    # renaming onto an existing name is refused
    client.post("/celebs/new", data={"name_cn": "龚俊"})
    r = client.post(f"/celebs/{cid}/update",
                    data={"name_cn": "龚俊", "name_en": "", "occupation": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "already%20exists" in r.headers["location"]
    with tmp_db.get_engine().connect() as conn:
        names = {r["name_cn"] for r in conn.execute(
            select(tmp_db.celeb_registry)).mappings()}
    assert names == {"王一博", "龚俊"}


def test_celeb_image_library_upload_and_delete(client, tmp_db):
    client.post("/celebs/new", data={"name_cn": "王一博"})
    with tmp_db.get_engine().connect() as conn:
        cid = conn.execute(select(tmp_db.celeb_registry.c.id)).scalar()
    r = client.post(f"/celebs/{cid}/images/upload",
                    files={"files": ("hq.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["added"] == 1
    # duplicate content dedupes; junk is rejected
    client.post(f"/celebs/{cid}/images/upload",
                files={"files": ("again.png", PNG, "image/png")})
    assert client.post(f"/celebs/{cid}/images/upload",
                       files={"files": ("evil.png", b"#!/bin/sh", "image/png")}
                       ).status_code == 400
    with tmp_db.get_engine().connect() as conn:
        images = json.loads(conn.execute(
            select(tmp_db.celeb_registry.c.images_json)).scalar())
    assert len(images) == 1
    from pathlib import Path
    assert Path(images[0]).read_bytes() == PNG
    page = client.get("/celebs").text
    assert "thumb zoom" in page and "data-gallery='[" in page
    r = client.post(f"/celebs/{cid}/images/delete", data={"idx": 0})
    assert r.status_code == 200
    with tmp_db.get_engine().connect() as conn:
        images = json.loads(conn.execute(
            select(tmp_db.celeb_registry.c.images_json)).scalar())
    assert images == []
    assert client.post(f"/celebs/{cid}/images/delete",
                       data={"idx": 5}).status_code == 400


# ── renderer: celeb library images + platform members ────────────────────────

def test_visuals_use_celeb_library_and_skip_unticked_platform_posts(tmp_db, tmp_path):
    from mm.pipeline import _project_visuals
    sel = tmp_path / "sel.jpg"
    sel.write_bytes(b"\xff\xd8\xff")
    celeb_img = tmp_path / "celeb.jpg"
    celeb_img.write_bytes(b"\xff\xd8\xff")
    # weibo post: caption does NOT name the celeb → no label from posts
    _post(tmp_db, "weibo:V1", keep=True, caption="活动现场",
          media=[{"kind": "image", "local_path": str(sel), "selected": True}])
    # platform member with nothing ticked must not fall back to a card
    _post(tmp_db, "douyin:V2", platform="douyin", media=[])
    pid = _project(tmp_db, "SHOW", post_ids=("weibo:V1", "douyin:V2"),
                   roles={"douyin:V2": "match"})
    with tmp_db.get_engine().begin() as conn:
        tmp_db.upsert(conn, tmp_db.celeb_registry, {
            "name_cn": "王一博", "name_en": "Wang Yibo",
            "relations_json": "{}",
            "images_json": json.dumps([str(celeb_img)])}, ["name_cn"])

    class NoCardFactory:
        def visual_for_post(self, brand, post):
            raise AssertionError("factory must not run for platform members")

    celebs = [{"name_cn": "王一博", "display": "WANG YIBO",
               "relation_display": "BRAND AMBASSADOR"}]
    with tmp_db.get_engine().connect() as conn:
        vis = _project_visuals(conn, NoCardFactory(), "lv",
                               {"id": pid, "assets": "PHOTO"}, celebs)
    images = [v["image"] for v in vis]
    assert str(sel) in images
    assert str(celeb_img) in images          # library photo fills the label
    lib = [v for v in vis if v["image"] == str(celeb_img)][0]
    assert lib["label_name"] == "WANG YIBO"
    assert lib["label_top"] == "BRAND AMBASSADOR"


# ── render progress: narrated i/N + step notes ───────────────────────────────

def test_run_render_reports_progress(tmp_db, monkeypatch, tmp_path):
    from pathlib import Path
    import mm.render.deck as deck_mod
    import mm.render.qa as qa_mod
    import mm.render.visuals as vis_mod
    import mm.render.xlsx as xlsx_mod
    from mm import pipeline
    img = tmp_path / "s.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    _post(tmp_db, "weibo:R1", keep=True,
          media=[{"kind": "image", "local_path": str(img), "selected": True}])
    pid = _project(tmp_db, "SHOW", post_ids=("weibo:R1",))
    with tmp_db.get_engine().begin() as conn:
        conn.execute(tmp_db.projects.update()
                     .where(tmp_db.projects.c.id == pid)
                     .values(status="confirmed"))

    class FakeFactory:
        def __init__(self, month, mode=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def visual_for_post(self, brand, post):
            return None

    class FakeBuilder:
        def build(self, spec, out):
            Path(out).write_bytes(b"pptx")

    monkeypatch.setattr(vis_mod, "VisualFactory", FakeFactory)
    monkeypatch.setattr(deck_mod, "DeckBuilder", lambda: FakeBuilder())
    monkeypatch.setattr(xlsx_mod, "write_projects_xlsx", lambda spec, out: out)
    monkeypatch.setattr(qa_mod, "run_qa", lambda p, d: {"ok": True})
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path)
    notes = []
    res = pipeline.run_render("2026-06", visuals_mode="card",
                              progress=notes.append)
    assert res["qa"]["ok"] is True
    # countable progress (drives the determinate bar) plus step narration
    assert any("visuals 0/1" in n for n in notes)
    assert any("visuals 1/1" in n for n in notes)
    assert any("PPTX" in n for n in notes)
    assert any("XLSX" in n for n in notes)
    assert any("LibreOffice" in n for n in notes)
    assert notes[-1] == "render · done"
    with tmp_db.get_engine().connect() as conn:
        status = conn.execute(select(tmp_db.projects.c.status)).scalar()
        phases = tmp_db.get_run(conn, "2026-06")["phases"]
    assert status == "rendered" and phases["render"] == "done"


# ── enrichment: rationale stored, matches become members ─────────────────────

def test_enrich_stores_rationale_and_match_membership(tmp_db, monkeypatch):
    monkeypatch.setenv("MM_WEB_CONFIRM", "0")
    from mm import enrich
    from mm.config import BrandsConfig
    _post(tmp_db, "weibo:E1", keep=True, caption="campaign one")
    _post(tmp_db, "douyin:E2", platform="douyin", caption="campaign one dy")

    class FakeLLM:
        def call_json(self, name, variables, **kw):
            if name == "consolidate":
                return {"projects": [{
                    "title": "CAMPAIGN ONE", "post_ids": ["weibo:E1"],
                    "category": "campaign",
                    "rationale": "Single post; matched douyin echo same day."}]}
            if name == "describe":
                return {"description": "CAMPAIGN ONE"}
            raise AssertionError(name)

    matches = {"weibo:E1": {"douyin": {
        "post_id": "douyin:E2", "url": "https://x/douyin:E2",
        "date": "2026-06-05T13:00:00+08:00", "confidence": 0.8,
        "why": "shared keywords"}}}
    res = enrich.enrich_brand(tmp_db.get_engine(), FakeLLM(),
                              BrandsConfig.load(), "2026-06", "lv", matches)
    assert res["projects"] == 1
    with tmp_db.get_engine().connect() as conn:
        proj = conn.execute(select(tmp_db.projects)).mappings().first()
        roles = {r["post_id"]: r["role"] for r in conn.execute(
            select(tmp_db.project_posts).where(
                tmp_db.project_posts.c.project_id == proj["id"])).mappings()}
        pm = conn.execute(select(tmp_db.platform_matches).where(
            tmp_db.platform_matches.c.project_id == proj["id"],
            tmp_db.platform_matches.c.platform == "douyin")).mappings().first()
    assert proj["rationale"] == "Single post; matched douyin echo same day."
    assert roles == {"weibo:E1": "member", "douyin:E2": "match"}
    assert pm["matched_post_id"] == "douyin:E2"
