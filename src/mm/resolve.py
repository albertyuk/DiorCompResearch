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
    if not users:
        # live shape (2026-07): data.parsed_data.users[] carries uid/name/
        # fans/profile_url (fans is the truncated display number — the
        # user-info endpoint has the real count, checked at confirmation)
        users = normalize.find_post_list(data, {"uid", "profile_url"})
    out = []
    for u in users[:8]:
        uid = u.get("idstr") or u.get("id") or u.get("uid") or ""
        out.append({
            "uid": str(uid),
            "name": u.get("screen_name") or u.get("name"),
            "followers": u.get("followers_count") or u.get("fans"),
            "verified_reason": u.get("verified_reason")
                               or u.get("description") or "",
            "profile_url": f"https://weibo.com/u/{uid}",
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
            if acct.status == "absent":
                # confirmed: the brand has no official account on this
                # platform — nothing to resolve, keep it out of the list
                continue
            if not (acct.status == "verified" and acct.uid):
                out.append({"brand": brand.key, "brand_display": brand.display_name,
                            "platform": platform,
                            "lookup_query": acct.lookup_query or acct.screen_name
                                            or brand.display_name,
                            "note": acct.note,
                            "blocking": platform == "weibo"
                                        and not can_ingest_weibo(acct)})
    return out


# -- owner-authorized automatic resolution ---------------------------------------

_NORM_RE = re.compile(r"[\s\-_·•.。,，/|]+")


def _norm_name(s: str | None) -> str:
    return _NORM_RE.sub("", (s or "")).lower()


def _accept_names(brand) -> set[str]:
    """Normalized names a candidate must EXACTLY match to be auto-bound:
    the display name, the verified Weibo screen name, each lookup-query
    token, and two-token combinations in both orders (Prada普拉达 /
    普拉达Prada). Anything fuzzier stays for a human."""
    names = {brand.display_name}
    wb = brand.account("weibo")
    if wb and wb.screen_name:
        names.add(wb.screen_name)
    tokens: list[str] = []
    for acct in brand.accounts.values():
        if acct.lookup_query:
            tokens.extend(acct.lookup_query.split())
    out = {_norm_name(n) for n in names}
    toks = [_norm_name(t) for t in tokens if _norm_name(t)]
    out.update(toks)
    for a in toks:
        for b in toks:
            if a != b:
                out.add(a + b)
    return {x for x in out if len(x) >= 2}


def _is_official(client: TikHubClient, platform: str, cand: dict,
                 conn=None) -> bool:
    """A candidate may only auto-bind when the platform itself marks it as a
    verified organization — never on name similarity alone."""
    import json as _json
    try:
        if platform == "weibo":
            d = client.call("weibo_user_info", conn=conn, uid=cand["uid"])
            u = ((d.get("data") or {}).get("user")) or {}
            return bool(u.get("verified")) and u.get("verified_type") == 2
        if platform == "douyin":
            d = client.call("douyin_user_profile", conn=conn,
                            sec_user_id=cand["uid"])
            s = _json.dumps(d.get("data") or {}, ensure_ascii=False)
            return bool(re.search(
                r'"(?:enterprise_verify_reason|custom_verify)":\s*"[^"]+"', s))
        if platform == "xhs":
            d = client.call("xhs_user_info", conn=conn, user_id=cand["uid"])
            s = _json.dumps(d.get("data") or {}, ensure_ascii=False)
            return bool(re.search(
                r'"red_official_verify_content":\s*"[^"]+"', s))
        if platform in ("wechat_mp", "wechat_channels"):
            # the search payload carries the account's verification text
            return bool((cand.get("verified_reason") or "").strip())
    except Exception:
        return False
    return False


def auto_resolve_pending(client: TikHubClient, cfg: BrandsConfig,
                         engine=None, note=None) -> dict:
    """Resolve every account still at status=resolve WITHOUT a human in the
    loop — pre-authorized by the owner (2026-07-17) for the brand roster.
    Binding rules are deliberately strict: the platform search must yield
    exactly ONE candidate whose normalized name exactly matches the brand's
    known official names AND that the platform marks as a verified
    organization. Anything else stays pending for the Console's Resolve
    flow. Safe to call repeatedly (e.g. every cross-check run) — it no-ops
    once nothing is pending."""
    resolved, pending = [], []
    for brand in cfg.brands:
        for platform, acct in brand.accounts.items():
            if acct.status != "resolve":
                continue
            query = (acct.lookup_query or acct.screen_name
                     or brand.display_name)
            try:
                cands = lookup_candidates(client, platform, query, engine)
            except Exception:
                pending.append(f"{brand.key}:{platform}")
                continue
            accept = _accept_names(brand)
            hits: dict[str, dict] = {}
            for c in cands:
                if _norm_name(c.get("name")) not in accept:
                    continue
                if _is_official(client, platform, c, engine):
                    hits.setdefault(str(c["uid"]), c)
            if len(hits) == 1:
                c = next(iter(hits.values()))
                cfg.save_account_resolution(
                    brand.key, platform, str(c["uid"]), c.get("name"),
                    datetime.now(CST).date().isoformat())
                resolved.append({"brand": brand.key, "platform": platform,
                                 "uid": str(c["uid"]), "name": c.get("name")})
                if note:
                    note(f"auto-resolved {brand.key}·{platform} "
                         f"→ {c.get('name')}")
            else:
                pending.append(f"{brand.key}:{platform}")
    return {"resolved": resolved, "pending": pending}


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
