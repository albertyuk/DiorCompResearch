"""TikHub API client.

Endpoint paths below were read from the live OpenAPI spec (api.tikhub.io,
cached at data/tikhub_openapi.json) — never guessed. `verify_endpoints()`
re-checks them against the cached/live spec and warns on drift.

Platform notes (from the spec):
- Weibo web_v2: user timeline, post detail, advanced search with timescope.
- Xiaohongshu: App V2 series (Web V2 deprecated 2026-06-19; ~$0.01/request,
  responses carry a 24h cache_url).
- WeChat: MP article endpoints (gh_… username) + Channels (v2_…@finder).
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import httpx

from .config import DATA_DIR, Settings

BASE_URL = "https://api.tikhub.io"
SPEC_PATH = DATA_DIR / "tikhub_openapi.json"

# Verified endpoint registry (method, path)
EP = {
    # -- weibo (web_v2)
    "weibo_user_info":       ("GET", "/api/v1/weibo/web_v2/fetch_user_info"),
    "weibo_user_posts":      ("GET", "/api/v1/weibo/web_v2/fetch_user_posts"),
    "weibo_post_detail":     ("GET", "/api/v1/weibo/web_v2/fetch_post_detail"),
    "weibo_user_search":     ("GET", "/api/v1/weibo/web_v2/fetch_user_search"),
    "weibo_adv_search":      ("GET", "/api/v1/weibo/web_v2/fetch_advanced_search"),
    # -- douyin
    "douyin_user_profile":   ("GET", "/api/v1/douyin/web/handler_user_profile"),
    "douyin_user_posts":     ("GET", "/api/v1/douyin/web/fetch_user_post_videos"),
    "douyin_user_search":    ("POST", "/api/v1/douyin/search/fetch_user_search_v2"),
    # -- xiaohongshu (App V2; $0.01/request)
    "xhs_user_info":         ("GET", "/api/v1/xiaohongshu/app_v2/get_user_info"),
    "xhs_user_notes":        ("GET", "/api/v1/xiaohongshu/app_v2/get_user_posted_notes"),
    "xhs_user_search":       ("GET", "/api/v1/xiaohongshu/app_v2/search_users"),
    "xhs_note_detail_image": ("GET", "/api/v1/xiaohongshu/app_v2/get_image_note_detail"),
    "xhs_note_detail_video": ("GET", "/api/v1/xiaohongshu/app_v2/get_video_note_detail"),
    # -- wechat
    "wechat_mp_profile":     ("POST", "/api/v1/wechat_mp/v2/fetch_account_profile"),
    "wechat_mp_articles":    ("POST", "/api/v1/wechat_mp/v2/fetch_account_articles"),
    "wechat_ch_info":        ("POST", "/api/v1/wechat_channels/v2/fetch_channel_info"),
    "wechat_ch_videos":      ("POST", "/api/v1/wechat_channels/v2/fetch_user_videos"),
    "wechat_search":         ("POST", "/api/v1/wechat_search/v2/fetch_search"),
}

# Rough per-request $ estimates for the cost log. XHS App V2 is billed at
# $0.01/request per TikHub docs; other endpoints are ~ an order of magnitude
# cheaper on the standard pay-per-call plan. Refine from your TikHub invoice.
COST_ESTIMATE = {"xhs": 0.01, "default": 0.001}


def _cost_for(path: str) -> float:
    return COST_ESTIMATE["xhs"] if "/xiaohongshu/" in path else COST_ESTIMATE["default"]


class TikHubError(RuntimeError):
    pass


class TikHubClient:
    def __init__(self, settings: Settings | None = None, timeout: float = 60.0):
        self.settings = settings or Settings.load()
        self._client = httpx.Client(
            base_url=BASE_URL, timeout=timeout,
            headers={"Authorization": f"Bearer {self.settings.tikhub_api_key}",
                     "User-Agent": "maison-monitor/0.1"})

    # -- spec handling --------------------------------------------------------

    def ensure_spec(self, refresh: bool = False) -> dict | None:
        try:
            if refresh or not SPEC_PATH.exists():
                SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
                r = httpx.get(f"{BASE_URL}/openapi.json", timeout=60)
                r.raise_for_status()
                SPEC_PATH.write_bytes(r.content)
            return json.loads(SPEC_PATH.read_text())
        except Exception:
            return None

    def verify_endpoints(self) -> list[str]:
        """Return endpoint keys whose path is missing from the current spec."""
        spec = self.ensure_spec()
        if not spec:
            return []
        paths = spec.get("paths", {})
        return [k for k, (_, p) in EP.items() if p not in paths]

    # -- request core ---------------------------------------------------------

    def call(self, key: str, *, conn=None, brand: str | None = None,
             month: str | None = None, max_retries: int = 5, **kwargs) -> dict:
        """Call a registered endpoint. GET → query params; POST → JSON body.
        Exponential backoff on 429/5xx. Only successful calls hit the cost log."""
        method, path = EP[key]
        params = {k: v for k, v in kwargs.items() if v is not None}
        last = None
        for attempt in range(max_retries):
            try:
                if method == "GET":
                    r = self._client.get(path, params=params)
                else:
                    r = self._client.post(path, json=params)
            except httpx.HTTPError as e:
                last = e
                time.sleep(min(30, 2 ** attempt + random.random()))
                continue
            # TikHub signals transient upstream failures as 400 with an
            # explicit "Please retry" / uncharged message — treat as retryable
            transient_400 = (r.status_code == 400
                             and ("Please retry" in r.text[:500]
                                  or "请重试" in r.text[:500]))
            if r.status_code == 429 or r.status_code >= 500 or transient_400:
                last = TikHubError(f"{key}: HTTP {r.status_code} {r.text[:200]}")
                delay = min(30, 2 ** attempt + random.random())
                retry_after = r.headers.get("retry-after")
                if retry_after:
                    try:
                        delay = min(60.0, float(retry_after))
                    except ValueError:
                        pass          # HTTP-date form — keep the backoff delay
                time.sleep(delay)
                continue
            if r.status_code >= 400:
                if conn is not None:
                    from . import db
                    db.log_api_call(conn, "tikhub", path, brand=brand, month=month,
                                    status=r.status_code, ok=False)
                raise TikHubError(f"{key}: HTTP {r.status_code} {r.text[:300]}")
            data = r.json()
            if conn is not None:
                from . import db
                db.log_api_call(conn, "tikhub", path, brand=brand, month=month,
                                status=r.status_code, ok=True,
                                cost_usd=_cost_for(path))
            return data
        raise TikHubError(f"{key}: retries exhausted ({last})")

    def close(self) -> None:
        self._client.close()
