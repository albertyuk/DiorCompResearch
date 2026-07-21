"""Phase 1 — Weibo ingestion, and cross-platform pulls used by Phase 4.

Every fetched post: raw JSON archived on disk, normalized row upserted into
`posts` (idempotent by post_id), media downloaded immediately (CDN URLs expire).

Transaction discipline: network work (TikHub calls, media downloads) happens
OUTSIDE any DB transaction; rows are committed in one short transaction per
page, so a crash never loses more than a page and the console stays writable.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import db, normalize
from .config import BrandsConfig, Brand
from .dates import CST, month_bounds
from .media import MediaStore
from .tikhub import TikHubClient, TikHubError


def _archive_raw(store: MediaStore, brand_key: str, name: str, payload) -> str:
    path = store.raw_dir(brand_key) / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _download_batch_media(store: MediaStore, brand_key: str, posts: list[dict],
                          referer: str, max_workers: int = 12) -> None:
    """Downloads dominate ingest wall-clock — fetch a whole page's files in
    one pool, deduped by (url, kind) across posts so shared files (the brand
    avatar repeats on every post) download once and no two threads ever share
    a target file."""
    jobs: dict[tuple[str, str], object] = {}
    for post in posts:
        for m in post["media"]:
            if m.get("url"):
                jobs[(m["url"], "img")] = None
            else:
                m["local_path"] = None
        if post.get("author_avatar"):
            jobs[(post["author_avatar"], "avatar")] = None
    if jobs:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as ex:
            futs = {key: ex.submit(store.download, brand_key, key[0],
                                   kind=key[1], referer=referer)
                    for key in jobs}
            for key, fut in futs.items():
                jobs[key] = fut.result()
    for post in posts:
        for m in post["media"]:
            if m.get("url"):
                local = jobs[(m["url"], "img")]
                m["local_path"] = str(local) if local else None
        avatar = post.get("author_avatar")
        got = jobs.get((avatar, "avatar")) if avatar else None
        post["author_avatar_path"] = str(got) if got else None


def _download_post_media(store: MediaStore, brand_key: str, post: dict,
                         referer: str) -> None:
    _download_batch_media(store, brand_key, [post], referer)


def _store_posts(engine, month: str, brand_key: str, posts: list[dict],
                 raw_path: str) -> None:
    if not posts:
        return
    with engine.begin() as conn:
        for post in posts:
            db.upsert(conn, db.posts, {
                "post_id": post["post_id"], "month": month, "brand": brand_key,
                "platform": post["platform"], "url": post["url"],
                "created_at": post["created_at"], "caption": post["caption"],
                "at_tags": json.dumps(post["at_tags"], ensure_ascii=False),
                "hashtags": json.dumps(post["hashtags"], ensure_ascii=False),
                "media": json.dumps(post["media"], ensure_ascii=False),
                "is_repost": post["is_repost"],
                "repost_ambiguous": post["repost_ambiguous"],
                "author_name": post.get("author_name"),
                "author_avatar_path": post.get("author_avatar_path"),
                "raw_path": raw_path,
            }, ["post_id"],
            # a post pulled by two adjacent months' padded windows keeps its
            # first month assignment (cross-check queries go by date, not month)
            no_update_cols=["month"])


def resolve_weibo_uid(client: TikHubClient, brand: Brand, engine=None,
                      month: str | None = None) -> str | None:
    """Fill in a missing Weibo uid from the vanity URL / screen name."""
    acct = brand.account("weibo")
    if acct is None:
        return None
    if acct.uid:
        return acct.uid
    custom = None
    if acct.vanity_url:
        custom = acct.vanity_url.rstrip("/").rsplit("/", 1)[-1]
    data = client.call("weibo_user_info", conn=engine, brand=brand.key, month=month,
                       custom=custom, uid=None)
    uid = normalize.find_key(data, "idstr") or normalize.find_key(data, "id")
    return str(uid) if uid else None


def ingest_weibo(engine, client: TikHubClient, cfg: BrandsConfig, month: str,
                 brand_key: str, *, max_pages: int = 40,
                 progress=None, should_stop=None) -> dict:
    """Page the official timeline for [month_start, month_end) CST."""
    brand = cfg.brand(brand_key)
    acct = brand.account("weibo")
    if acct is None or acct.status != "verified":
        raise RuntimeError(
            f"{brand_key}: weibo account not verified — run account resolution "
            f"(Phase R) first; refusing to ingest.")
    uid = acct.uid
    if not uid:
        uid = resolve_weibo_uid(client, brand, engine, month)
        if not uid:
            raise RuntimeError(f"{brand_key}: could not resolve weibo uid")
        cfg.save_account_resolution(brand.key, "weibo", uid, acct.screen_name,
                                    datetime.now(CST).date().isoformat())
    start, end = month_bounds(month)
    store = MediaStore(month)
    n_new, n_reposts, page, since_id = 0, 0, 1, None
    seen: set[str] = set()   # post_ids this run — later pages may overlap
    stale_pages = 0
    use_app = False          # flips when web_v2 errors (upstream outage)
    while page <= max_pages:
        if should_stop and should_stop():
            break                     # pause between pages; nothing is lost
        if not use_app:
            # live-verified param shape: first page takes uid only;
            # pagination is since_id from the previous response (an explicit
            # page=1 returns 400)
            try:
                data = client.call("weibo_user_posts", conn=engine,
                                   brand=brand_key, month=month, uid=uid,
                                   since_id=since_id)
            except TikHubError:
                # web_v2 timeline outage (first seen 2026-07-21): the app
                # timeline serves the same posts with the same mblogid ids,
                # page-numbered — restart this brand from its page 1 (posts
                # already stored dedupe via `seen` + idempotent upserts)
                use_app = True
                page, since_id, stale_pages = 1, None, 0
                if progress:
                    progress(brand_key, page, n_new)
        if use_app:
            data = client.call("weibo_user_timeline_app", conn=engine,
                               brand=brand_key, month=month, uid=uid,
                               page=page)
        raw_path = _archive_raw(store, brand_key, f"weibo_page{page:03d}", data)
        mblogs = normalize.weibo_posts_from_response(data)
        if not mblogs:
            break
        batch, older_seen, considered, fresh = [], 0, 0, 0
        for mblog in mblogs:
            post = normalize.normalize_weibo(mblog, uid)
            if post is None or post["created_dt"] is None:
                continue
            if post["post_id"] in seen:
                continue              # overlap with an earlier page
            seen.add(post["post_id"])
            fresh += 1
            dt = post["created_dt"]
            if post.get("is_top") and not (start <= dt < end):
                continue              # pinned out-of-window post; keep paging
            considered += 1
            if dt >= end:
                continue
            if dt < start:
                older_seen += 1
                continue
            if post["is_repost"]:
                n_reposts += 1
                continue              # pure repost without commentary
            batch.append(post)
        _download_batch_media(store, brand_key, batch, "https://weibo.com/")
        _store_posts(engine, month, brand_key, batch, raw_path)
        n_new += len(batch)
        if progress:
            progress(brand_key, page, n_new)
        # stop when the page is (almost) entirely older than the window, or
        # the timeline has no next page
        if considered and older_seen >= max(1, considered - 1):
            break
        # stall insurance: a timeline that re-serves itself (two pages with
        # nothing new, or an unmoved cursor) must terminate, not spin to
        # max_pages re-counting the same posts
        if fresh == 0:
            stale_pages += 1
            if stale_pages >= 2:
                break
        else:
            stale_pages = 0
        if use_app:
            page += 1                 # app timeline pages by number
            continue
        new_since = normalize.next_cursor(data, "since_id")
        new_since = str(new_since) if new_since is not None else None
        if new_since is None or new_since == since_id:
            break
        since_id = new_since
        page += 1
    return {"brand": brand_key, "posts": n_new, "skipped_reposts": n_reposts}


# -- Phase 4 pulls (other platforms), cached once per run ------------------------

def pull_platform(engine, client: TikHubClient, cfg: BrandsConfig, month: str,
                  brand_key: str, platform: str, *, max_pages: int = 15) -> int:
    """Pull a brand's timeline on douyin/xhs/wechat_mp/wechat_channels for
    [month_start − 5d, month_end + 5d]; normalize, download media, store."""
    brand = cfg.brand(brand_key)
    acct = brand.account(platform)
    if acct is None or not (acct.status == "verified" and acct.uid):
        return -1                     # unresolved — surfaced by the caller
    start, end = month_bounds(month)
    start -= timedelta(days=5)
    end += timedelta(days=5)
    store = MediaStore(month)
    n = 0
    cursor = None
    seen: set[str] = set()
    for page in range(max_pages):
        prev_cursor = cursor
        if platform == "douyin":
            data = client.call("douyin_user_posts", conn=engine, brand=brand_key,
                               month=month, sec_user_id=acct.uid,
                               max_cursor=cursor, count=20)
            items = normalize.douyin_posts_from_response(data)
            posts = [normalize.normalize_douyin(i) for i in items]
            cursor = normalize.next_cursor(data, "max_cursor")
            has_more = bool(normalize.next_cursor(data, "has_more"))
        elif platform == "xhs":
            data = client.call("xhs_user_notes", conn=engine, brand=brand_key,
                               month=month, user_id=acct.uid, cursor=cursor)
            items = normalize.xhs_notes_from_response(data)
            posts = [normalize.normalize_xhs(i) for i in items]
            cursor = normalize.next_cursor(data, "cursor")
            has_more = bool(normalize.next_cursor(data, "has_more"))
        elif platform == "wechat_mp":
            data = client.call("wechat_mp_articles", conn=engine, brand=brand_key,
                               month=month, username=acct.uid, offset=cursor,
                               raw=False)
            items = normalize.wechat_mp_articles_from_response(data)
            posts = [normalize.normalize_wechat_mp(i) for i in items]
            cursor = normalize.next_cursor(data, "next_offset")
            has_more = bool(normalize.next_cursor(data, "has_more")) or bool(cursor)
        elif platform == "wechat_channels":
            data = client.call("wechat_ch_videos", conn=engine, brand=brand_key,
                               month=month, username=acct.uid,
                               last_buffer=cursor, raw=False)
            items = normalize.wechat_ch_videos_from_response(data)
            posts = [normalize.normalize_wechat_channels(i) for i in items]
            cursor = normalize.next_cursor(data, "last_buffer")
            has_more = bool(cursor)
        else:
            raise ValueError(platform)

        raw_path = _archive_raw(store, brand_key, f"{platform}_page{page:03d}", data)
        batch, older_seen, considered = [], 0, 0
        referer = {"douyin": "https://www.douyin.com/",
                   "xhs": "https://www.xiaohongshu.com/",
                   "wechat_mp": "https://mp.weixin.qq.com/",
                   "wechat_channels": "https://channels.weixin.qq.com/"}[platform]
        for post in posts:
            if post is None:
                continue
            dt = post["created_dt"]
            if dt is None:
                continue
            if post["post_id"] in seen:
                continue              # overlap with an earlier page
            seen.add(post["post_id"])
            considered += 1
            if dt >= end:
                continue
            if dt < start:
                older_seen += 1       # pinned posts can't be detected on all
                continue              # platforms — stop only on a fully-old page
            batch.append(post)
        _download_batch_media(store, brand_key, batch, referer)
        _store_posts(engine, month, brand_key, batch, raw_path)
        n += len(batch)
        if (considered and older_seen >= considered) or not has_more or not items:
            break
        if cursor == prev_cursor:
            break                     # cursor didn't move — page would repeat
    return n
