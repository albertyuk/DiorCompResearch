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
    assert "启动本月" in page and "归档并重置" in page    # runs page chrome
    assert ">运行<" in page and ">幻灯片<" in page       # nav
    assert "EN</button>" in page                         # switch back offered
    page = client.get("/review/2026-06/posts").text
    assert "审核检查点 #1 — 帖子" in page
    assert "确认并继续" in page
    page = client.get("/review/2026-06/projects").text
    assert "审核检查点 #2 — 项目" in page
    page = client.get("/celebs").text
    assert "名人档案" in page
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


def test_login_page_translates_too(tmp_db, monkeypatch):
    monkeypatch.setenv("CONSOLE_PASSPHRASE", PASS)
    monkeypatch.setenv("MM_SECRET_KEY", "f" * 64)
    from mm.console import create_app
    c = TestClient(create_app())
    assert "Team passphrase" in c.get("/login").text
    c.post("/lang", data={"lang": "zh", "next": "/login"})   # public route
    page = c.get("/login").text
    assert "团队口令" in page and "你的名字" in page
