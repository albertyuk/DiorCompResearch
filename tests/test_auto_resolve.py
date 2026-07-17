"""Owner-authorized automatic account resolution: strict exact-name +
platform-verification binding, retried at every cross-check, with confirmed
platform absences excluded from the unresolved list."""
import shutil

import pytest
import yaml

PASS = "team-pass-123"


@pytest.fixture
def tmp_cfg(tmp_path, monkeypatch):
    """A throwaway copy of brands.yaml so bindings never touch the repo."""
    import mm.config as mconfig
    dst = tmp_path / "brands.yaml"
    shutil.copy(mconfig.BRANDS_YAML, dst)
    monkeypatch.setattr(mconfig, "BRANDS_YAML", dst)
    return dst


class FakeClient:
    """Canned search + verification payloads keyed by endpoint."""

    def __init__(self, search_items=None, wechat_items=None):
        self.search_items = search_items or {}
        self.wechat_items = wechat_items or []
        self.calls = []

    def call(self, key, *, conn=None, **kw):
        self.calls.append((key, kw))
        if key == "wechat_search":
            return {"data": {"items": self.wechat_items}}
        if key == "douyin_user_search":
            return {"data": {"user_list": self.search_items.get("douyin", [])}}
        if key == "xhs_user_search":
            return {"data": {"users": self.search_items.get("xhs", [])}}
        if key == "weibo_user_search":
            return {"data": {"parsed_data": {
                "users": self.search_items.get("weibo", [])}}}
        if key == "douyin_user_profile":
            return {"data": {"user": {
                "enterprise_verify_reason": "官方账号"}}}
        if key == "xhs_user_info":
            return {"data": {"red_official_verify_content": "服饰鞋帽"}}
        raise AssertionError(key)


def _pending(cfg):
    return [f"{b.key}:{p}" for b in cfg.brands
            for p, a in b.accounts.items() if a.status == "resolve"]


def test_absent_accounts_leave_the_unresolved_list(tmp_cfg):
    from mm.config import BrandsConfig
    from mm.resolve import unresolved_accounts
    cfg = BrandsConfig.load()
    rows = {(u["brand"], u["platform"]) for u in unresolved_accounts(cfg)}
    # confirmed absences (no official douyin) are not nagged about
    for bk in ("valentino", "bottega", "hermes"):
        assert (bk, "douyin") not in rows, bk
    # genuinely pending wechat accounts still appear
    assert ("prada", "wechat_mp") in rows


def test_auto_resolve_binds_exact_verified_wechat_match(tmp_cfg):
    from mm.config import BrandsConfig
    from mm.resolve import auto_resolve_pending
    cfg = BrandsConfig.load()
    client = FakeClient(wechat_items=[
        {"gh_username": "gh_prada123", "nickname": "PRADA普拉达",
         "accTypeName": "服务号", "authInfo": "普拉达时装商业（上海）有限公司"},
        {"gh_username": "gh_fake", "nickname": "普拉达代购汇",
         "accTypeName": "订阅号", "authInfo": "某代购公司"},
    ])
    res = auto_resolve_pending(client, cfg, None)
    assert {"brand": "prada", "platform": "wechat_mp",
            "uid": "gh_prada123", "name": "PRADA普拉达"} \
        in [dict(r) for r in res["resolved"]]
    fresh = BrandsConfig.load()
    acct = fresh.brand("prada").account("wechat_mp")
    assert acct.status == "verified" and acct.uid == "gh_prada123"
    raw = yaml.safe_load(tmp_cfg.read_text())          # persisted to yaml
    prada = next(b for b in raw["brands"] if b["key"] == "prada")
    assert prada["wechat_mp"]["uid"] == "gh_prada123"


def test_auto_resolve_refuses_unverified_and_ambiguous(tmp_cfg):
    from mm.config import BrandsConfig
    from mm.resolve import auto_resolve_pending
    cfg = BrandsConfig.load()
    # exact name but NO verification text → refused
    client = FakeClient(wechat_items=[
        {"gh_username": "gh_x", "nickname": "PRADA普拉达",
         "accTypeName": "服务号", "authInfo": ""}])
    res = auto_resolve_pending(client, cfg, None)
    assert not any(r["brand"] == "prada" and r["platform"] == "wechat_mp"
                   for r in res["resolved"])
    # two verified exact-name candidates → ambiguous, refused
    cfg = BrandsConfig.load()
    client = FakeClient(wechat_items=[
        {"gh_username": "gh_a", "nickname": "PRADA普拉达",
         "accTypeName": "服务号", "authInfo": "公司A"},
        {"gh_username": "gh_b", "nickname": "PRADA普拉达",
         "accTypeName": "服务号", "authInfo": "公司B"}])
    res = auto_resolve_pending(client, cfg, None)
    assert not any(r["brand"] == "prada" and r["platform"] == "wechat_mp"
                   for r in res["resolved"])
    assert "prada:wechat_mp" in res["pending"]
    fresh = BrandsConfig.load()
    assert fresh.brand("prada").account("wechat_mp").status == "resolve"


def test_auto_resolve_survives_search_outage(tmp_cfg):
    from mm.config import BrandsConfig
    from mm.resolve import auto_resolve_pending

    class DownClient:
        def call(self, key, **kw):
            raise RuntimeError("upstream 500")

    cfg = BrandsConfig.load()
    before = _pending(cfg)
    res = auto_resolve_pending(DownClient(), cfg, None)
    assert res["resolved"] == []
    assert set(res["pending"]) == set(before)     # everything survives


def test_crosscheck_run_retries_pending_accounts(tmp_cfg, tmp_path,
                                                 monkeypatch):
    import mm.db as mdb
    monkeypatch.setattr(mdb, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(mdb, "_engine", None)
    from mm import pipeline, resolve as resolve_mod
    from mm import crosscheck as xc_mod, filtering
    called = {}
    monkeypatch.setattr(resolve_mod, "auto_resolve_pending",
                        lambda *a, **k: called.setdefault("yes", True)
                        or {"resolved": [], "pending": []})
    monkeypatch.setattr(xc_mod, "pull_all", lambda *a, **k: {})
    monkeypatch.setattr(xc_mod, "crosscheck_brand",
                        lambda *a, **k: {"matches": {}, "orphans": 0})
    monkeypatch.setattr(filtering, "filter_orphans",
                        lambda *a, **k: {"kept": 0})
    monkeypatch.setattr(pipeline, "TikHubClient",
                        type("C", (), {"__init__": lambda s, *a: None,
                                       "close": lambda s: None}))
    monkeypatch.setattr(pipeline, "LLM", lambda *a, **k: object())
    monkeypatch.setattr(pipeline, "Settings",
                        type("S", (), {"load": staticmethod(lambda: None)}))
    pipeline.run_crosscheck("2026-06", brand_keys=["lv"])
    assert called.get("yes")           # pending accounts exist → retried
    if mdb._engine is not None:
        mdb._engine.dispose()


def test_console_auto_resolve_button(tmp_cfg, tmp_path, monkeypatch):
    import mm.db as mdb
    monkeypatch.setattr(mdb, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(mdb, "_engine", None)
    monkeypatch.setenv("CONSOLE_PASSPHRASE", PASS)
    monkeypatch.setenv("MM_SECRET_KEY", "f" * 64)
    from fastapi.testclient import TestClient
    import mm.console as console_mod
    from mm import resolve as resolve_mod
    monkeypatch.setattr(console_mod, "TikHubClient",
                        type("C", (), {"__init__": lambda s, *a: None,
                                       "close": lambda s: None}))
    monkeypatch.setattr(console_mod, "Settings",
                        type("S", (), {"load": staticmethod(lambda: None)}))
    monkeypatch.setattr(
        resolve_mod, "auto_resolve_pending",
        lambda *a, **k: {"resolved": [{"brand": "prada",
                                       "platform": "wechat_mp",
                                       "uid": "gh_x", "name": "PRADA普拉达"}],
                         "pending": ["loewe:wechat_mp"]})
    c = TestClient(console_mod.create_app())
    c.post("/login", data={"name": "Alice", "passphrase": PASS},
           follow_redirects=False)
    page = c.get("/").text
    assert "Auto-resolve all" in page
    r = c.post("/accounts/auto_resolve", follow_redirects=False)
    assert r.status_code == 303
    assert "Auto-resolved%201" in r.headers["location"]
    if mdb._engine is not None:
        mdb._engine.dispose()
