"""Platform response normalization → common post dicts.

TikHub responses nest platform payloads differently per endpoint version, so
normalizers are defensive: they locate the post list by structure, not by a
hardcoded envelope, and archive the raw JSON alongside.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .dates import CST

_AT_RE = re.compile(r"@([\w\-·一-鿿]+)")
_TAG_RE = re.compile(r"#([^#\n]{1,50})#")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    return _HTML_TAG_RE.sub("", text).strip()


def parse_weibo_time(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").astimezone(CST)
    except (ValueError, TypeError):
        return None


def parse_unix(ts) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(CST)
    except (ValueError, TypeError, OSError):
        return None


def find_post_list(data, marker_keys: set[str], max_depth: int = 8) -> list[dict]:
    """Find the first list of dicts where items carry any marker key.
    Checks the first few elements, not just [0] — feeds sometimes lead with
    ad cards / deleted-post placeholders."""
    if max_depth < 0:
        return []
    if isinstance(data, list):
        head = [d for d in data[:3] if isinstance(d, dict)]
        if any(marker_keys & set(d.keys()) for d in head):
            return [d for d in data if isinstance(d, dict)]
        for item in data:
            found = find_post_list(item, marker_keys, max_depth - 1)
            if found:
                return found
        return []
    if isinstance(data, dict):
        for v in data.values():
            found = find_post_list(v, marker_keys, max_depth - 1)
            if found:
                return found
    return []


def find_key(data, key: str, max_depth: int = 8):
    """First value for `key` anywhere in the nested structure."""
    if max_depth < 0:
        return None
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for v in data.values():
            r = find_key(v, key, max_depth - 1)
            if r is not None:
                return r
    elif isinstance(data, list):
        for v in data:
            r = find_key(v, key, max_depth - 1)
            if r is not None:
                return r
    return None


def next_cursor(data, key: str):
    """Next-page cursor from a TikHub response. The envelope echoes the
    REQUEST parameters at the top level (`params.<key>`) *before* the payload
    in iteration order, so a whole-response find_key returns the cursor we
    just sent and pagination silently freezes (live-verified on weibo
    fetch_user_posts). Only the response body may be searched."""
    body = data.get("data") if isinstance(data, dict) else None
    v = find_key(body, key) if body is not None else None
    return None if v in (None, "", 0) else v


# -- weibo ---------------------------------------------------------------------

def weibo_posts_from_response(data) -> list[dict]:
    return find_post_list(data, {"mblogid", "mblog_id", "text_raw", "isLongText"})


def _largest_pic_variant(info: dict) -> dict:
    """Highest-resolution variant of a weibo picture: start from the named
    preference chain, then let any variant with a strictly larger pixel area
    win — variant naming shifts (original/largest/large/mw2000) but width ×
    height doesn't lie. Slides need the biggest file weibo will give us."""
    def area(v) -> int:
        try:
            return int(v.get("width") or 0) * int(v.get("height") or 0)
        except (TypeError, ValueError):
            return 0
    best = (info.get("largest") or info.get("original") or info.get("large")
            or info.get("mw2000") or {})
    if not isinstance(best, dict):
        best = {}
    for v in info.values():
        if isinstance(v, dict) and v.get("url") and area(v) > area(best):
            best = v
    if not best.get("url"):     # unknown variant names, no dimensions
        best = next((v for v in info.values()
                     if isinstance(v, dict) and v.get("url")), {})
    return best


def normalize_weibo(mblog: dict, uid: str) -> dict | None:
    mid = str(mblog.get("mblogid") or mblog.get("bid") or mblog.get("mid")
              or mblog.get("id") or "")
    if not mid:
        return None
    created = parse_weibo_time(mblog.get("created_at", ""))
    text = mblog.get("text_raw") or strip_html(mblog.get("text", ""))
    retweet = mblog.get("retweeted_status")
    commentary = text.split("//@")[0].strip() if retweet else text
    # only the literal no-commentary forms count as pure reposts; short real
    # commentary is flagged ambiguous (surfaced at review), never dropped
    is_pure_repost = bool(retweet) and (
        not commentary or commentary in ("转发微博", "轉發微博", "Repost", "转发"))
    repost_ambiguous = bool(retweet) and not is_pure_repost

    media = []
    pic_infos = mblog.get("pic_infos") or {}
    pic_ids = mblog.get("pic_ids") or list(pic_infos.keys())
    for pid in pic_ids:
        info = pic_infos.get(pid) or {}
        best = _largest_pic_variant(info)
        url = best.get("url")
        if url:
            media.append({"kind": "image", "url": url,
                          "width": best.get("width"),
                          "height": best.get("height")})
    page_info = mblog.get("page_info") or {}
    if page_info.get("object_type") == "video" or "media_info" in page_info:
        cover = (page_info.get("page_pic") or {})
        cover_url = cover.get("url") if isinstance(cover, dict) else cover
        media.append({"kind": "video_cover", "url": cover_url,
                      "is_cover": True})
    user = mblog.get("user") or {}
    return {
        "native_id": mid,
        "post_id": f"weibo:{mid}",
        "platform": "weibo",
        "url": f"https://weibo.com/{uid}/{mid}",
        "created_at": created.isoformat() if created else None,
        "created_dt": created,
        "caption": text,
        "at_tags": _AT_RE.findall(text),
        "hashtags": _TAG_RE.findall(text),
        "media": media,
        "is_repost": is_pure_repost,
        "repost_ambiguous": repost_ambiguous,
        "author_name": user.get("screen_name"),
        "author_avatar": user.get("avatar_hd") or user.get("profile_image_url"),
        "is_top": bool(mblog.get("isTop") or (mblog.get("title") or {}).get("text") == "置顶"),
    }


# -- xiaohongshu ----------------------------------------------------------------

def xhs_notes_from_response(data) -> list[dict]:
    return find_post_list(data, {"note_id", "notes_count", "desc", "display_title"})


def normalize_xhs(note: dict) -> dict | None:
    nid = str(note.get("note_id") or note.get("id") or "")
    if not nid:
        return None
    created = parse_unix(note.get("time") or note.get("create_time"))
    title = note.get("display_title") or note.get("title") or ""
    desc = note.get("desc") or ""
    caption = f"{title}\n{desc}".strip()
    media = []
    cover = note.get("cover")
    if isinstance(cover, dict):
        url = cover.get("url") or cover.get("url_default")
        if url:
            media.append({"kind": "image", "url": url, "is_cover": True})
    for img in (note.get("images_list") or []):
        url = img.get("url") if isinstance(img, dict) else None
        if url:
            media.append({"kind": "image", "url": url})
    user = note.get("user") or {}
    # xiaohongshu gates note pages behind a per-note xsec_token, and the
    # app_v2 timeline notes carry none (live-verified) — grab one from any
    # depth if this source has it; otherwise the URL is provisional and
    # crosscheck.hydrate_xhs_links replaces it with the official share link
    xsec = note.get("xsec_token") or find_key(note, "xsec_token", 3)
    url = f"https://www.xiaohongshu.com/explore/{nid}"
    if xsec:
        url += f"?xsec_token={xsec}&xsec_source=pc_search"
    return {
        "native_id": nid,
        "post_id": f"xhs:{nid}",
        "platform": "xhs",
        "url": url,
        "created_at": created.isoformat() if created else None,
        "created_dt": created,
        "caption": caption,
        "at_tags": _AT_RE.findall(caption),
        "hashtags": _TAG_RE.findall(caption),
        "media": media,
        "is_repost": False,
        "repost_ambiguous": False,
        "author_name": user.get("nickname") or user.get("nick_name"),
        "author_avatar": user.get("avatar") or user.get("images"),
        "is_top": False,
    }


# -- douyin ----------------------------------------------------------------------

def douyin_posts_from_response(data) -> list[dict]:
    return find_post_list(data, {"aweme_id", "aweme_type"})


def normalize_douyin(aweme: dict) -> dict | None:
    aid = str(aweme.get("aweme_id") or "")
    if not aid:
        return None
    created = parse_unix(aweme.get("create_time"))
    caption = aweme.get("desc") or ""
    media = []
    video = aweme.get("video") or {}
    cover = video.get("cover") or video.get("origin_cover") or {}
    urls = cover.get("url_list") or []
    if urls:
        media.append({"kind": "video_cover", "url": urls[0], "is_cover": True})
    author = aweme.get("author") or {}
    # the official share link (iesdouyin.com) opens without login from any
    # region; the constructed www.douyin.com/video/{id} page often hits a
    # login/verification wall (live-verified owner report)
    share_url = ((aweme.get("share_info") or {}).get("share_url") or "").strip()
    return {
        "native_id": aid,
        "post_id": f"douyin:{aid}",
        "platform": "douyin",
        "url": share_url or f"https://www.douyin.com/video/{aid}",
        "created_at": created.isoformat() if created else None,
        "created_dt": created,
        "caption": caption,
        "at_tags": _AT_RE.findall(caption),
        "hashtags": _TAG_RE.findall(caption),
        "media": media,
        "is_repost": False,
        "repost_ambiguous": False,
        "author_name": author.get("nickname"),
        "author_avatar": ((author.get("avatar_thumb") or {}).get("url_list") or [None])[0],
        "is_top": bool(aweme.get("is_top")),
    }


# -- wechat MP -------------------------------------------------------------------

def wechat_mp_articles_from_response(data) -> list[dict]:
    return find_post_list(data, {"link", "cover_url", "digest", "appmsgid"})


def normalize_wechat_mp(article: dict) -> dict | None:
    link = article.get("link") or article.get("url") or ""
    aid = str(article.get("appmsgid") or article.get("aid") or "") or link
    if not aid:
        return None
    created = parse_unix(article.get("create_time") or article.get("update_time")
                         or article.get("publish_time"))
    title = article.get("title") or ""
    digest = article.get("digest") or ""
    caption = f"{title}\n{digest}".strip()
    media = []
    cover = article.get("cover_url") or article.get("cover")
    if cover:
        media.append({"kind": "image", "url": cover, "is_cover": True})
    return {
        "native_id": str(aid),
        "post_id": f"wechat_mp:{aid}",
        "platform": "wechat_mp",
        "url": link,
        "created_at": created.isoformat() if created else None,
        "created_dt": created,
        "caption": caption,
        "at_tags": _AT_RE.findall(caption),
        "hashtags": _TAG_RE.findall(caption),
        "media": media,
        "is_repost": False,
        "repost_ambiguous": False,
        "author_name": article.get("account_name"),
        "author_avatar": None,
        "is_top": False,
    }


# -- wechat channels ----------------------------------------------------------------

def wechat_ch_videos_from_response(data) -> list[dict]:
    return find_post_list(data, {"object_id", "objectId", "export_id", "media"})


def normalize_wechat_channels(video: dict) -> dict | None:
    vid = str(video.get("object_id") or video.get("objectId")
              or video.get("export_id") or video.get("id") or "")
    if not vid:
        return None
    created = parse_unix(video.get("create_time") or video.get("createtime"))
    caption = (video.get("desc") or video.get("description") or "")
    if isinstance(caption, dict):
        caption = caption.get("description") or ""
    media = []
    cover = (video.get("cover_url") or video.get("cover")
             or find_key(video, "thumb_url", 3) or find_key(video, "cover_url", 3))
    if cover and isinstance(cover, str):
        media.append({"kind": "video_cover", "url": cover, "is_cover": True})
    return {
        "native_id": vid,
        "post_id": f"wechat_channels:{vid}",
        "platform": "wechat_channels",
        "url": video.get("share_url") or "",
        "created_at": created.isoformat() if created else None,
        "created_dt": created,
        "caption": caption if isinstance(caption, str) else "",
        "at_tags": [],
        "hashtags": _TAG_RE.findall(caption if isinstance(caption, str) else ""),
        "media": media,
        "is_repost": False,
        "repost_ambiguous": False,
        "author_name": video.get("nickname"),
        "author_avatar": None,
        "is_top": False,
    }
