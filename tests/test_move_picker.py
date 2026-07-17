"""Click-based destination picker: every orphan/post/project gets a button
that opens a list of project names — the no-drag sibling of the board."""
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


def _post(mdb, post_id, *, platform="weibo", brand="lv", caption="上海快闪",
          keep=None, created="2026-06-05T12:00:00+08:00"):
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
                "reasons": "[]", "rationale": "r", "celebs_tagged": "[]",
                "category": "event", "media_focus": "photo",
                "needs_review": False, "human_decision": None}, ["post_id"])


def _project(mdb, title, post_ids=(), status="draft"):
    with mdb.get_engine().begin() as conn:
        pid = conn.execute(mdb.projects.insert().values(
            month="2026-06", brand="lv", title=title, phase_suffix=None,
            date_start="2026-06-05", date_end="2026-06-05", ongoing=False,
            assets="PHOTO", description=title, celebs="[]", hero_media="[]",
            status=status)).inserted_primary_key[0]
        for p in post_ids:
            conn.execute(mdb.project_posts.insert().values(
                project_id=pid, post_id=p, role="member"))
    return pid


def _seed(mdb):
    _post(mdb, "weibo:M1", keep=True, caption="秀场大事件")
    _post(mdb, "weibo:M2", keep=True, caption="第二个项目")
    _post(mdb, "douyin:O1", platform="douyin", caption="orphan douyin",
          keep=True)
    with mdb.get_engine().begin() as conn:
        conn.execute(mdb.orphans.insert().values(
            post_id="douyin:O1", month="2026-06", resolution="pending"))
    return (_project(mdb, "POP UP", ("weibo:M1",)),
            _project(mdb, "SHOW", ("weibo:M2",)))


def test_projects_page_offers_click_movers(client, tmp_db):
    p1, p2 = _seed(tmp_db)
    page = client.get("/review/2026-06/projects").text
    # the shared modal + destination data + wiring are on the page
    assert 'id="mmpick"' in page
    assert "window.MM_PROJECTS" in page and '"lv": [' in page
    assert 'label: "POP UP' in page and 'label: "SHOW' in page
    assert 'mmWireMovers("2026-06")' in page
    # orphans get a primary "Move to project…" button (no data-from → no
    # discard option in the picker; Ignore already covers that)
    assert "Move to project…" in page
    assert 'class="move-post" data-post="douyin:O1"' in page
    # member posts get "Move to…" with enough context for the discard leg
    assert "Move to…" in page
    assert (f'data-post="weibo:M1"\n                data-from="{p1}"'
            in page or f'data-from="{p1}"' in page)
    assert 'data-platform="weibo"' in page
    # each project card gets "Merge into…" carrying its own id + status
    assert "Merge into…" in page
    assert f'data-proj="{p1}"' in page and f'data-proj="{p2}"' in page
    assert 'data-status="draft"' in page


def test_board_page_offers_click_movers(client, tmp_db):
    p1, _ = _seed(tmp_db)
    page = client.get("/review/2026-06/board").text
    assert "window.MM_PROJECTS" in page and '"lv": [' in page
    assert 'mmWireMovers("2026-06")' in page
    assert "Move to project…" in page          # pool chips
    assert "Move to…" in page                  # member chips
    assert "Merge into…" in page               # project cards
    assert f'data-proj="{p1}"' in page


def test_dropped_project_is_labelled_and_not_rediscardable(client, tmp_db):
    _seed(tmp_db)
    pd = _project(tmp_db, "OLD CAMPAIGN", status="dropped")
    page = client.get("/review/2026-06/projects").text
    assert 'data-status="dropped"' in page     # JS hides the discard option
    assert 'label: "OLD CAMPAIGN' in page      # still a valid merge target


def test_picker_endpoints_accept_the_picker_call_shape(client, tmp_db):
    """The picker POSTs urlencoded bodies to existing endpoints — lock in
    the exact shapes mmWireMovers emits."""
    p1, p2 = _seed(tmp_db)
    # post → other project (adopt)
    r = client.post(f"/review/2026-06/projects/{p2}/adopt",
                    data={"post_id": "weibo:M1"})
    assert r.status_code == 200
    # project → discard (status endpoint, value=dropped)
    r = client.post(f"/review/2026-06/projects/{p1}/status",
                    data={"value": "dropped"})
    assert r.status_code in (200, 303)
    with tmp_db.get_engine().connect() as conn:
        import mm.db as mdb
        status = conn.execute(select(mdb.projects.c.status).where(
            mdb.projects.c.id == p1)).scalar()
        members = {m["post_id"] for m in conn.execute(
            select(mdb.project_posts).where(
                mdb.project_posts.c.project_id == p2)).mappings()}
    assert status == "dropped"
    assert members == {"weibo:M1", "weibo:M2"}


def test_picker_strings_translate(client, tmp_db):
    _seed(tmp_db)
    client.post("/lang", data={"lang": "zh", "next": "/"})
    page = client.get("/review/2026-06/projects").text
    assert "移入项目…" in page and "移动到…" in page
    assert "并入其他项目…" in page
    assert "取消" in page                       # the modal's Cancel button
    assert "这条帖子要移到哪里？" in page       # picker titles land in MM_T
    assert "这个项目要移到哪里？" in page
