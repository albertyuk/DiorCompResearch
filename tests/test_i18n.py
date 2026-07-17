"""EN/中文 console toggle + the spend display staying hidden."""
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
    from mm.console import create_app
    c = TestClient(create_app())
    r = c.post("/login", data={"name": "Alice", "passphrase": PASS},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _seed_month(mdb):
    with mdb.get_engine().begin() as conn:
        mdb.get_run(conn, "2026-06")


def test_cost_is_hidden_from_the_ui(client, tmp_db):
    _seed_month(tmp_db)
    page = client.get("/").text
    assert "cost" not in page.lower()
    assert "usd" not in page.lower() and "≈ $" not in page


def test_lang_toggle_translates_the_console(client, tmp_db):
    _seed_month(tmp_db)
    page = client.get("/").text
    assert "Start month" in page and "中文" in page      # EN default + switch
    r = client.post("/lang", data={"lang": "zh", "next": "/"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "mm_lang=zh" in r.headers.get("set-cookie", "")
    page = client.get("/").text
    assert 'lang="zh-CN"' in page
    assert "开始搜索" in page and "归档并重置" in page    # runs page chrome
    assert ">任务<" in page and ">报告<" in page         # nav
    assert "EN</button>" in page                         # switch back offered
    page = client.get("/review/2026-06/posts").text
    assert "帖子审核（第 1 步）" in page
    assert "确认并继续" in page
    page = client.get("/review/2026-06/projects").text
    assert "项目审核（第 2 步）" in page
    page = client.get("/celebs").text
    assert "明星档案" in page
    # back to English
    client.post("/lang", data={"lang": "en", "next": "/"})
    assert "Start month" in client.get("/").text


def test_lang_rejects_offsite_redirect_and_bad_codes(client):
    r = client.post("/lang", data={"lang": "zh", "next": "https://evil.example"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"
    r = client.post("/lang", data={"lang": "zh", "next": "//evil.example"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"
    r = client.post("/lang", data={"lang": "xx", "next": "/decks"},
                    follow_redirects=False)
    assert "mm_lang=en" in r.headers.get("set-cookie", "")


def test_theme_toggle_dark_mode(client, tmp_db):
    _seed_month(tmp_db)
    page = client.get("/").text
    assert 'data-theme="light"' in page and "Dark</button>" in page
    r = client.post("/theme", data={"theme": "dark", "next": "/decks"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/decks"
    assert "mm_theme=dark" in r.headers.get("set-cookie", "")
    page = client.get("/").text
    assert 'data-theme="dark"' in page and "Light</button>" in page
    # bad inputs fall back safely
    r = client.post("/theme", data={"theme": "neon", "next": "https://evil"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"
    assert "mm_theme=light" in r.headers.get("set-cookie", "")
    # login page is themed too
    assert 'data-theme=' in client.get("/login").text


def test_every_page_carries_a_guide(client, tmp_db):
    """Each page explains exactly what to do and what happens next."""
    _seed_month(tmp_db)
    pages = {
        "/": "runs",
        "/review/2026-06/posts": "posts",
        "/review/2026-06/projects": "projects",
        "/decks": "decks",
        "/celebs": "celebs",
        "/archives": "archives",
        "/learning": "learning",
    }
    for path, key in pages.items():
        page = client.get(path).text
        assert f'data-guide="{key}"' in page, path
        assert "How this page works" in page, path
        assert "What happens next" in page, path
    # the steps are concrete instructions, not lorem
    posts = client.get("/review/2026-06/posts").text
    assert "Confirm &amp; continue" in posts or "Confirm & continue" in posts
    projects = client.get("/review/2026-06/projects").text
    assert "Orphans" in projects and "Decks page" in projects
    # and they translate
    client.post("/lang", data={"lang": "zh", "next": "/"})
    page = client.get("/review/2026-06/posts").text
    assert "操作指南" in page and "后续流程" in page
    assert "按品牌逐一检查" in page
    client.post("/lang", data={"lang": "en", "next": "/"})


def test_review_tabs_always_in_nav(client, tmp_db):
    """The review tabs must not appear only on review pages — every page
    links them to the latest run's month (default month when none exist)."""
    _seed_month(tmp_db)
    for path in ("/", "/decks", "/celebs", "/archives", "/learning"):
        page = client.get(path).text
        assert '/review/2026-06/posts"' in page, path
        assert '/review/2026-06/projects"' in page, path
        assert '/review/2026-06/board"' in page, path
    # a month-scoped page keeps linking to its OWN month
    with tmp_db.get_engine().begin() as conn:
        tmp_db.get_run(conn, "2026-05")
    page = client.get("/review/2026-05/posts").text
    assert '/review/2026-05/projects"' in page
    page = client.get("/decks").text                # latest run wins elsewhere
    assert '/review/2026-06/posts"' in page


def test_every_page_shares_one_anatomy(client, tmp_db):
    """Cohesion contract: exactly one serif page title per page, the shared
    toast container, the nav workflow/library separator — and empty lists
    that say which action fills them."""
    _seed_month(tmp_db)
    for path in ("/", "/review/2026-06/posts", "/review/2026-06/projects",
                 "/review/2026-06/board", "/decks", "/celebs", "/archives",
                 "/learning"):
        page = client.get(path).text
        assert page.count('class="page-title"') == 1, path
        assert 'id="mmtoast"' in page, path
        assert 'class="nav-sep"' in page, path
    archives = client.get("/archives").text
    assert 'class="empty"' in archives
    assert "move a finished month here" in archives
    learning = client.get("/learning").text
    assert 'class="empty"' in learning
    assert "every correction lands here" in learning


def _set_phases(mdb, month, phases):
    with mdb.get_engine().begin() as conn:
        mdb.get_run(conn, month)
        for k, v in phases.items():
            mdb.set_phase(conn, month, k, v)


def test_stepper_points_at_the_next_action(client, tmp_db):
    """Every page shows where the month stands and ONE next action."""
    _seed_month(tmp_db)
    # fresh month → start
    page = client.get("/").text
    assert 'class="stepper"' in page
    assert "Start the month" in page
    # posts waiting → CTA links to review posts everywhere else…
    _set_phases(tmp_db, "2026-06", {"ingest": "done", "filter": "done",
                                    "review_posts": "waiting"})
    page = client.get("/").text
    assert "Decide keeps &amp; drops" in page or "Decide keeps & drops" in page
    assert 'href="/review/2026-06/posts"' in page
    # …but renders as an on-page hint on the posts page itself
    page = client.get("/review/2026-06/posts").text
    assert 'next-cta here' in page
    # projects waiting → CTA to projects; posts step shows done ✓
    _set_phases(tmp_db, "2026-06", {"review_posts": "confirmed",
                                    "crosscheck": "done", "enrich": "done",
                                    "review_projects": "waiting"})
    page = client.get("/").text
    assert "Check grouping &amp; images" in page or "Check grouping & images" in page
    assert page.count('<li class="done">') >= 3
    # render done → CTA to the decks page
    _set_phases(tmp_db, "2026-06", {"review_projects": "confirmed",
                                    "render": "done"})
    page = client.get("/review/2026-06/projects").text
    assert 'href="/decks"' in page
    assert "Download the report" in page
    # error state points at the fix
    _set_phases(tmp_db, "2026-06", {"render": "error"})
    page = client.get("/").text
    assert "Render failed" in page


def test_login_page_translates_too(tmp_db, monkeypatch):
    monkeypatch.setenv("CONSOLE_PASSPHRASE", PASS)
    monkeypatch.setenv("MM_SECRET_KEY", "f" * 64)
    from mm.console import create_app
    c = TestClient(create_app())
    assert "Team passphrase" in c.get("/login").text
    c.post("/lang", data={"lang": "zh", "next": "/login"})   # public route
    page = c.get("/login").text
    assert "团队口令" in page and "姓名" in page
