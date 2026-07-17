"""Per-post cross-check (owner request: check per post, not per project).

db.post_matches is the source of truth: crosscheck persists each accepted
(weibo post ↔ platform post) pair; enrich derives the project SOCIAL ticks
from member posts and attaches EVERY matched post; when a post moves between
projects its evidence follows it; a reviewer separating a pair vetoes it for
all future runs — hashtag tier included."""
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


def _match(mdb, ref, cand, platform, conf=0.8, reason="test match"):
    with mdb.get_engine().begin() as conn:
        mdb.upsert(conn, mdb.post_matches, {
            "ref_post_id": ref, "cand_post_id": cand, "platform": platform,
            "month": "2026-06", "confidence": conf, "reason": reason,
            "at": "2026-06-06T00:00:00+08:00"}, ["ref_post_id", "cand_post_id"])


def _project(mdb, title, members=(), matches=()):
    """members = weibo post ids; matches = platform post ids (role=match)."""
    with mdb.get_engine().begin() as conn:
        pid = conn.execute(mdb.projects.insert().values(
            month="2026-06", brand="lv", title=title, phase_suffix=None,
            date_start="2026-06-05", date_end="2026-06-05", ongoing=False,
            assets="PHOTO", description=title, celebs="[]", hero_media="[]",
            status="draft")).inserted_primary_key[0]
        for p in members:
            conn.execute(mdb.project_posts.insert().values(
                project_id=pid, post_id=p, role="member"))
        for p in matches:
            conn.execute(mdb.project_posts.insert().values(
                project_id=pid, post_id=p, role="match"))
    return pid


class NeverLLM:
    def call_json(self, *a, **kw):
        raise AssertionError("no LLM call expected")


def _tick(conn, mdb, pid, platform):
    return conn.execute(select(mdb.platform_matches).where(
        mdb.platform_matches.c.project_id == pid,
        mdb.platform_matches.c.platform == platform)).mappings().first()


# ── crosscheck persists per post + honors vetoes ─────────────────────────────

def test_crosscheck_persists_per_post_matches(tmp_db):
    from mm import crosscheck as xc
    from mm.config import BrandsConfig
    _post(tmp_db, "weibo:W1", "weibo", "SPEEDY 大片",
          hashtags='["SpeedyP9"]', keep=True)
    _post(tmp_db, "douyin:D1", "douyin", "同款", hashtags='["speedy p9"]')
    xc.crosscheck_brand(tmp_db.get_engine(), NeverLLM(),
                        BrandsConfig.load(), "2026-06", "lv")
    with tmp_db.get_engine().connect() as conn:
        row = conn.execute(select(tmp_db.post_matches)).mappings().one()
    assert row["ref_post_id"] == "weibo:W1"
    assert row["cand_post_id"] == "douyin:D1"
    assert row["platform"] == "douyin" and row["month"] == "2026-06"
    assert row["confidence"] == xc.HASHTAG_CONFIDENCE
    assert "hashtag" in row["reason"]


def test_human_veto_blocks_even_the_hashtag_tier(tmp_db):
    from mm import crosscheck as xc
    from mm.config import BrandsConfig
    _post(tmp_db, "weibo:W1", "weibo", "SPEEDY 大片",
          hashtags='["SpeedyP9"]', keep=True)
    _post(tmp_db, "douyin:D1", "douyin", "同款", hashtags='["speedy p9"]')
    with tmp_db.get_engine().begin() as conn:
        tmp_db.upsert(conn, tmp_db.match_judgments, {
            "ref_post_id": "weibo:W1", "cand_post_id": "douyin:D1",
            "same_event": False, "confidence": 1.0,
            "reason": "pair separated by Alice at review #2",
            "at": "x"}, ["ref_post_id", "cand_post_id"])
    res = xc.crosscheck_brand(tmp_db.get_engine(), NeverLLM(),
                              BrandsConfig.load(), "2026-06", "lv")
    assert res["matches"]["weibo:W1"] == {}
    assert res["orphans"] == 1                    # candidate went to orphans
    with tmp_db.get_engine().connect() as conn:
        assert conn.execute(select(tmp_db.post_matches)).first() is None


# ── enrich: every matched post joins; the tick is derived ────────────────────

def test_enrich_attaches_every_matched_post_not_just_the_best(tmp_db,
                                                              monkeypatch):
    monkeypatch.setenv("MM_WEB_CONFIRM", "0")
    from mm import enrich
    from mm.config import BrandsConfig
    _post(tmp_db, "weibo:W1", "weibo", "show day one", keep=True)
    _post(tmp_db, "weibo:W2", "weibo", "show day two", keep=True)
    _post(tmp_db, "douyin:D1", "douyin", "dy echo one")
    _post(tmp_db, "douyin:D2", "douyin", "dy echo two")
    _match(tmp_db, "weibo:W1", "douyin:D1", "douyin", conf=0.75)
    _match(tmp_db, "weibo:W2", "douyin:D2", "douyin", conf=0.9)

    class FakeLLM:
        def call_json(self, name, variables, **kw):
            if name == "consolidate":
                return {"projects": [{"title": "THE SHOW",
                                      "post_ids": ["weibo:W1", "weibo:W2"],
                                      "rationale": "same show"}]}
            if name == "describe":
                return {"description": "THE SHOW"}
            raise AssertionError(name)

    res = enrich.enrich_brand(tmp_db.get_engine(), FakeLLM(),
                              BrandsConfig.load(), "2026-06", "lv")
    assert res["projects"] == 1
    with tmp_db.get_engine().connect() as conn:
        pid = conn.execute(select(tmp_db.projects.c.id)).scalar()
        roles = {r["post_id"]: r["role"] for r in conn.execute(
            select(tmp_db.project_posts).where(
                tmp_db.project_posts.c.project_id == pid)).mappings()}
        tick = _tick(conn, tmp_db, pid, "douyin")
    # BOTH matched douyin posts are members now — per-post, not best-per-
    # platform-per-project
    assert roles == {"weibo:W1": "member", "weibo:W2": "member",
                     "douyin:D1": "match", "douyin:D2": "match"}
    # the tick (SOCIAL column) carries the best hit
    assert tick["matched_post_id"] == "douyin:D2"
    assert tick["confidence"] == 0.9


# ── evidence follows the post through review #2 moves ───────────────────────

def test_eject_weibo_post_takes_its_matches_along(client, tmp_db):
    _post(tmp_db, "weibo:W1", "weibo", "day one", keep=True)
    _post(tmp_db, "weibo:W2", "weibo", "day two", keep=True)
    _post(tmp_db, "douyin:D1", "douyin", "dy echo")
    _match(tmp_db, "weibo:W1", "douyin:D1", "douyin", conf=0.8)
    pid = _project(tmp_db, "SHOW", members=("weibo:W1", "weibo:W2"),
                   matches=("douyin:D1",))
    with tmp_db.get_engine().begin() as conn:
        tmp_db.upsert(conn, tmp_db.platform_matches, {
            "project_id": pid, "platform": "douyin", "present": True,
            "matched_url": "https://x/douyin:D1", "matched_date": "2026-06-05",
            "matched_post_id": "douyin:D1", "confidence": 0.8},
            ["project_id", "platform"])
    r = client.post(f"/review/2026-06/projects/{pid}/eject",
                    data={"post_id": "weibo:W1"}, follow_redirects=False)
    assert r.status_code == 303
    with tmp_db.get_engine().connect() as conn:
        new_pid = conn.execute(select(tmp_db.projects.c.id).where(
            tmp_db.projects.c.id != pid)).scalar()
        new_roles = {r["post_id"]: r["role"] for r in conn.execute(
            select(tmp_db.project_posts).where(
                tmp_db.project_posts.c.project_id == new_pid)).mappings()}
        old_tick = _tick(conn, tmp_db, pid, "douyin")
        new_tick = _tick(conn, tmp_db, new_pid, "douyin")
        judged = conn.execute(select(tmp_db.match_judgments)).first()
    # the match followed ITS weibo post into the spinoff
    assert new_roles == {"weibo:W1": "member", "douyin:D1": "match"}
    assert new_tick["matched_post_id"] == "douyin:D1"
    assert old_tick is None                       # no douyin evidence remains
    assert judged is None                         # moving is not a veto


def test_eject_matched_post_vetoes_pair_and_resyncs_tick(client, tmp_db):
    _post(tmp_db, "weibo:W1", "weibo", "day one", keep=True)
    _post(tmp_db, "douyin:D1", "douyin", "dy strong")
    _post(tmp_db, "douyin:D2", "douyin", "dy weak")
    _match(tmp_db, "weibo:W1", "douyin:D1", "douyin", conf=0.9)
    _match(tmp_db, "weibo:W1", "douyin:D2", "douyin", conf=0.75)
    pid = _project(tmp_db, "SHOW", members=("weibo:W1",),
                   matches=("douyin:D1", "douyin:D2"))
    r = client.post(f"/review/2026-06/projects/{pid}/eject",
                    data={"post_id": "douyin:D1"}, follow_redirects=False)
    assert r.status_code == 303
    with tmp_db.get_engine().connect() as conn:
        veto = conn.execute(select(tmp_db.match_judgments).where(
            tmp_db.match_judgments.c.cand_post_id == "douyin:D1")
        ).mappings().one()
        pm_rows = conn.execute(select(tmp_db.post_matches)).mappings().all()
        tick = _tick(conn, tmp_db, pid, "douyin")
        orphan = conn.execute(select(tmp_db.orphans).where(
            tmp_db.orphans.c.post_id == "douyin:D1")).mappings().one()
    assert veto["same_event"] is False and "Alice" in veto["reason"]
    assert [r["cand_post_id"] for r in pm_rows] == ["douyin:D2"]
    # the tick re-derived itself from the surviving per-post match
    assert tick["matched_post_id"] == "douyin:D2"
    assert tick["confidence"] == 0.75
    assert orphan["resolution"] == "pending"


def test_ungroup_matches_follow_their_own_weibo_posts(client, tmp_db):
    _post(tmp_db, "weibo:W1", "weibo", "day one", keep=True)
    _post(tmp_db, "weibo:W2", "weibo", "day two", keep=True)
    _post(tmp_db, "douyin:D1", "douyin", "dy for W1")
    _post(tmp_db, "xhs:X1", "xhs", "hand-placed, no ref")
    _match(tmp_db, "weibo:W1", "douyin:D1", "douyin", conf=0.8)
    pid = _project(tmp_db, "SHOW", members=("weibo:W1", "weibo:W2"),
                   matches=("douyin:D1", "xhs:X1"))
    r = client.post(f"/review/2026-06/projects/{pid}/ungroup",
                    follow_redirects=False)
    assert r.status_code == 303
    with tmp_db.get_engine().connect() as conn:
        placements = {}
        for row in conn.execute(
                select(tmp_db.project_posts, tmp_db.projects.c.title)
                .join(tmp_db.projects,
                      tmp_db.projects.c.id == tmp_db.project_posts.c.project_id)
        ).mappings():
            placements.setdefault(row["title"], set()).add(row["post_id"])
        orphan = conn.execute(select(tmp_db.orphans).where(
            tmp_db.orphans.c.post_id == "xhs:X1")).mappings().one()
        d1_orphan = conn.execute(select(tmp_db.orphans).where(
            tmp_db.orphans.c.post_id == "douyin:D1")).first()
    # D1 sits in the SAME new project as its weibo post W1
    w1_proj = next(v for v in placements.values() if "weibo:W1" in v)
    assert "douyin:D1" in w1_proj
    assert d1_orphan is None
    # the reviewer-placed xhs post had no per-post ref → orphan pool
    assert orphan["resolution"] == "pending"


def test_board_drag_out_vetoes_and_adopt_resyncs_both_sides(client, tmp_db):
    _post(tmp_db, "weibo:W1", "weibo", "day one", keep=True)
    _post(tmp_db, "weibo:W2", "weibo", "day two", keep=True)
    _post(tmp_db, "douyin:D1", "douyin", "dy echo")
    _match(tmp_db, "weibo:W1", "douyin:D1", "douyin", conf=0.8)
    p1 = _project(tmp_db, "ONE", members=("weibo:W1",), matches=("douyin:D1",))
    p2 = _project(tmp_db, "TWO", members=("weibo:W2",))
    with tmp_db.get_engine().begin() as conn:
        tmp_db.upsert(conn, tmp_db.platform_matches, {
            "project_id": p1, "platform": "douyin", "present": True,
            "matched_url": "u", "matched_date": "2026-06-05",
            "matched_post_id": "douyin:D1", "confidence": 0.8},
            ["project_id", "platform"])
    # adopt into the other project: tick moves — old side cleared, new side set
    assert client.post(f"/review/2026-06/projects/{p2}/adopt",
                       data={"post_id": "douyin:D1"}).status_code == 200
    with tmp_db.get_engine().connect() as conn:
        assert _tick(conn, tmp_db, p1, "douyin") is None
        t2 = _tick(conn, tmp_db, p2, "douyin")
        assert t2["matched_post_id"] == "douyin:D1"
    # drag out to the pool: veto recorded, tick cleared, orphaned
    assert client.post("/review/2026-06/board/pool",
                       data={"post_id": "douyin:D1"}).status_code == 200
    with tmp_db.get_engine().connect() as conn:
        assert _tick(conn, tmp_db, p2, "douyin") is None
        veto = conn.execute(select(tmp_db.match_judgments)).mappings().all()
        orphan = conn.execute(select(tmp_db.orphans).where(
            tmp_db.orphans.c.post_id == "douyin:D1")).mappings().one()
    assert orphan["resolution"] == "pending"
    # the pair (W1, D1) was NOT vetoed by the adopt, and the drag-out only
    # vetoes pairs whose ref sat in the project it left (W2 had none)
    assert all(v["cand_post_id"] == "douyin:D1" for v in veto)


def test_projects_page_shows_per_post_match_provenance(client, tmp_db):
    _post(tmp_db, "weibo:W1", "weibo", "day one", keep=True)
    _post(tmp_db, "douyin:D1", "douyin", "dy echo")
    _post(tmp_db, "xhs:X1", "xhs", "hand placed")
    _match(tmp_db, "weibo:W1", "douyin:D1", "douyin", conf=0.83,
           reason="shared campaign hashtag: #x#")
    _project(tmp_db, "SHOW", members=("weibo:W1",),
             matches=("douyin:D1", "xhs:X1"))
    page = client.get("/review/2026-06/projects").text
    assert "matched weibo post ↗" in page         # provenance link
    assert "0.83" in page
    assert "shared campaign hashtag: #x#" in page  # reason in the tooltip
    assert "placed by a reviewer" in page          # no-ref fallback
