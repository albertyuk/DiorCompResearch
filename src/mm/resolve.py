"""Phase R — first-run account resolution.

For each `status: resolve` account, query the platform's TikHub lookup with
`lookup_query`, keep verified/corporate-looking candidates, and surface them
for one-click confirmation in the Console. Confirmation writes the internal ID
back into config/brands.yaml and flips the account to `verified`.

WeChat Channels IDs are not web-discoverable — the Console asks the user to
paste/confirm the finder username (v2_…@finder) from the WeChat app; the
wechat_search endpoint is still tried to offer best-effort candidates.
"""
from __future__ import annotations

import re
from datetime import datetime

from . import normalize
from .config import BrandsConfig
from .dates import CST
from .tikhub import TikHubClient


def _candidates_weibo(client: TikHubClient, query: str, conn=None) -> list[dict]:
    data = client.call("weibo_user_search", conn=conn, query=query, page=1,
                       auth="org_vip")
    users = normalize.find_post_list(data, {"screen_name", "followers_count"})
    out = []
    for u in users[:8]:
        out.append({
            "uid": str(u.get("idstr") or u.get("id") or ""),
            "name": u.get("screen_name"),
            "followers": u.get("followers_count"),
            "verified_reason": u.get("verified_reason") or u.get("description"),
            "profile_url": f"https://weibo.com/u/{u.get('idstr') or u.get('id')}",
        })
    return [c for c in out if c["uid"]]


def _candidates_douyin(client: TikHubClient, query: str, conn=None) -> list[dict]:
    data = client.call("douyin_user_search", conn=conn, keyword=query)
    users = normalize.find_post_list(data, {"sec_uid", "unique_id"})
    if not users:
        # live shape (2026-07): data.user_list[] with user_id holding the
        # sec-uid ("MS4wLjAB…") and nick_name/fans_cnt
        users = normalize.find_post_list(data, {"nick_name", "fans_cnt"})
    out = []
    for u in users[:8]:
        info = u.get("user_info") or u
        sec = info.get("sec_uid") or info.get("user_id")
        if not sec:
            continue
        out.append({
            "uid": sec,
            "name": info.get("nickname") or info.get("nick_name"),
            "followers": info.get("follower_count") or info.get("fans_cnt"),
            "verified_reason": (info.get("custom_verify")
                                or info.get("enterprise_verify_reason") or ""),
            "profile_url": f"https://www.douyin.com/user/{sec}",
        })
    return out


def _candidates_xhs(client: TikHubClient, query: str, conn=None) -> list[dict]:
    data = client.call("xhs_user_search", conn=conn, keyword=query, page=1)
    users = normalize.find_post_list(data, {"red_id", "user_id", "nickname"})
    out = []
    for u in users[:8]:
        uid = u.get("user_id") or u.get("id")
        if not uid:
            continue
        out.append({
            "uid": str(uid),
            "name": u.get("nickname") or u.get("name"),
            "followers": u.get("fans") or u.get("fans_count"),
            "verified_reason": u.get("red_official_verify_content")
                               or u.get("sub_title"),
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{uid}",
        })
    return out


_TAG_RE = re.compile(r"<[^>]+>")     # wechat search highlights terms with <em>

def _candidates_wechat(client: TikHubClient, query: str, business_type: str,
                       conn=None) -> list[dict]:
    data = client.call("wechat_search", conn=conn, keyword=query,
                       business_type=business_type, raw=False)
    items = (data.get("data") or {}).get("items") \
        if isinstance(data.get("data"), dict) else None
    if not items:
        items = normalize.find_post_list(
            data, {"username", "gh_username", "finder_username", "nickname"})
    out = []
    for u in items[:12]:
        ji = u.get("jumpInfo") or {}
        uid = (u.get("gh_username") or u.get("username")
               or u.get("finder_username") or ji.get("userName") or "")
        acc_type = u.get("accTypeName") or ""
        if business_type == "account":
            # real MP accounts only — mini-programs carry gh_…@app usernames
            if uid.endswith("@app") or \
                    (acc_type and acc_type not in ("服务号", "公众号", "订阅号")):
                continue
        elif business_type == "video" and uid and not uid.endswith("@finder"):
            continue                   # channels = finder usernames only
        if not uid:
            continue
        out.append({
            "uid": str(uid),
            "name": _TAG_RE.sub("", str(u.get("nickname") or u.get("title") or "")),
            "followers": u.get("fans_count"),
            "verified_reason": (u.get("authInfo") or u.get("verify_info")
                                or _TAG_RE.sub("", str(u.get("desc") or ""))[:90]),
            "profile_url": "",
        })
    return out


def lookup_candidates(client: TikHubClient, platform: str, query: str,
                      conn=None) -> list[dict]:
    if platform == "weibo":
        return _candidates_weibo(client, query, conn)
    if platform == "douyin":
        return _candidates_douyin(client, query, conn)
    if platform == "xhs":
        return _candidates_xhs(client, query, conn)
    if platform == "wechat_mp":
        return _candidates_wechat(client, query, "account", conn)
    if platform == "wechat_channels":
        return _candidates_wechat(client, query, "video", conn)
    raise ValueError(platform)


def unresolved_accounts(cfg: BrandsConfig) -> list[dict]:
    out = []
    for brand in cfg.brands:
        for platform, acct in brand.accounts.items():
            if not (acct.status == "verified" and acct.uid):
                out.append({"brand": brand.key, "brand_display": brand.display_name,
                            "platform": platform,
                            "lookup_query": acct.lookup_query or acct.screen_name
                                            or brand.display_name,
                            "note": acct.note,
                            "blocking": platform == "weibo"
                                        and not can_ingest_weibo(acct)})
    return out


def can_ingest_weibo(acct) -> bool:
    """A Weibo account is ingestable if it has a uid, or is 'verified' with a
    vanity_url we can auto-resolve the uid from at first ingest. Only accounts
    that fail both genuinely need Phase R confirmation before a run."""
    if acct is None:
        return False
    return bool(acct.uid) or (acct.status == "verified" and bool(acct.vanity_url))


def weibo_blockers(cfg: BrandsConfig) -> list[dict]:
    """Weibo accounts that actually block a run (neither resolved nor
    auto-resolvable) — e.g. a 'resolve'-status account with no vanity_url."""
    out = []
    for brand in cfg.brands:
        acct = brand.account("weibo")
        if not can_ingest_weibo(acct):
            out.append({"brand": brand.key, "brand_display": brand.display_name,
                        "lookup_query": (acct.lookup_query or acct.screen_name
                                         or brand.display_name) if acct
                                        else brand.display_name})
    return out


def confirm_account(cfg: BrandsConfig, brand_key: str, platform: str,
                    uid: str, screen_name: str | None) -> None:
    cfg.save_account_resolution(
        brand_key, platform, uid, screen_name,
        datetime.now(CST).date().isoformat())
