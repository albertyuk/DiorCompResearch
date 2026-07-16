"""Phase 4 — cross-platform verification + orphan detection.

For each kept Weibo post: candidates = other-platform posts within ±5 days
sharing a celeb name, sharing campaign keywords, or judged same-event by
prompts/match.md with confidence ≥ 0.7. Matches are recorded per project later
(consolidation joins them); here we record per-post platform hits and orphans.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select

from . import db
from .config import BrandsConfig
from .dates import parse_iso
from .filtering import kept_posts
from .ingest import pull_platform
from .llm import LLM
from .tikhub import TikHubClient

MATCH_CONFIDENCE = 0.7
WINDOW_DAYS = 5
MATCH_LLM_WORKERS = 4     # concurrent match.md escalations per brand

_LATIN_RE = re.compile(r"[A-Za-z0-9]{3,}")
_CJK_RE = re.compile(r"[一-鿿]{2,}")


def _keywords(text: str) -> set[str]:
    """Latin words plus CJK bigrams (whole CJK runs are punctuation-delimited
    phrases and almost never match across platforms)."""
    stop = {"the", "and", "with", "for", "chanel", "gucci", "fendi", "tiffany",
            "louis", "vuitton", "weibo", "video", "photo", "品牌", "全新",
            "系列", "查看", "点击", "官方"}
    words = {w.lower() for w in _LATIN_RE.findall(text or "")}
    for run in _CJK_RE.findall(text or ""):
        for i in range(len(run) - 1):
            words.add(run[i:i + 2])
    return {w for w in words if w not in stop}


def _celeb_names(verdict_row: dict) -> set[str]:
    names = set()
    for c in json.loads(verdict_row.get("celebs_tagged") or "[]"):
        for k in ("name_cn", "name_en_guess", "handle"):
            v = (c.get(k) or "").strip().lstrip("@")
            if v:
                names.add(v.lower())
    return names


def pull_all(engine, client: TikHubClient, cfg: BrandsConfig, month: str,
             brand_key: str, progress=None) -> dict:
    """Pull the four cross-check platforms concurrently (they're independent
    cursor streams writing distinct post_ids; upserts stay idempotent).
    Per-platform failures are captured, never fatal to the others."""
    platforms = ("douyin", "xhs", "wechat_mp", "wechat_channels")
    done = []

    def one(platform):
        try:
            n = pull_platform(engine, client, cfg, month, brand_key, platform)
        except Exception as e:
            n = f"error: {e}"
        done.append(platform)
        if progress:
            progress(f"pulled {len(done)}/{len(platforms)} platforms")
        return platform, n

    with ThreadPoolExecutor(max_workers=len(platforms)) as ex:
        return dict(ex.map(one, platforms))


def crosscheck_brand(engine, llm: LLM, cfg: BrandsConfig, month: str,
                     brand_key: str, should_stop=None) -> dict:
    from datetime import timedelta
    from .dates import month_bounds
    brand = cfg.brand(brand_key)
    start, end = month_bounds(month)
    lo = (start - timedelta(days=WINDOW_DAYS)).isoformat()
    hi = (end + timedelta(days=WINDOW_DAYS)).isoformat()
    with engine.connect() as conn:
        kept = kept_posts(conn, month, brand_key)
        # candidates by date window, not month — padded pulls from adjacent
        # months keep their first month assignment
        others = list(conn.execute(
            select(db.posts).where(db.posts.c.brand == brand_key,
                                   db.posts.c.platform != "weibo",
                                   db.posts.c.created_at >= lo,
                                   db.posts.c.created_at < hi)).mappings())
    matched_other_ids: set[str] = set()
    results = {}

    def record_hit(hits, cand, confidence, why):
        plat = cand["platform"]
        prev = hits.get(plat)
        if prev is None or confidence > prev["confidence"]:
            hits[plat] = {"post_id": cand["post_id"], "url": cand["url"],
                          "date": cand["created_at"],
                          "confidence": confidence, "why": why}
        matched_other_ids.add(cand["post_id"])

    # pass 1 — cheap heuristics inline; ambiguous pairs queue for the LLM
    escalations = []          # (kept row, ref_celebs, candidate)
    for row in kept:
        ref_date = parse_iso(row["created_at"])
        ref_kw = _keywords(row["caption"])
        ref_celebs = _celeb_names(row)
        hits = {}
        for cand in others:
            cdate = parse_iso(cand["created_at"])
            if ref_date and cdate and abs((cdate - ref_date).days) > WINDOW_DAYS:
                continue
            overlap_kw = ref_kw & _keywords(cand["caption"])
            cand_text = (cand["caption"] or "").lower()
            overlap_celeb = {n for n in ref_celebs if n and n in cand_text}
            if overlap_celeb:
                record_hit(hits, cand, 0.85,
                           f"shared celeb: {sorted(overlap_celeb)[:2]}")
            elif len(overlap_kw) >= 6:
                record_hit(hits, cand, 0.75,
                           f"shared keywords: {sorted(overlap_kw)[:4]}")
            elif len(overlap_kw) >= 2:
                escalations.append((row, ref_celebs, cand))
        results[row["post_id"]] = hits

    # pass 2 — match.md escalations run concurrently (pairs are independent;
    # the hit tables only mutate after the pool completes)
    def judge(item):
        row, ref_celebs, cand = item
        if should_stop and should_stop():
            return None
        try:
            j = llm.call_json("match", {
                "brand_display": brand.display_name,
                "ref_date": row["created_at"] or "",
                "ref_caption": (row["caption"] or "")[:1500],
                "ref_celebs": json.dumps(sorted(ref_celebs), ensure_ascii=False),
                "ref_title": "",
                "candidate_platform": cand["platform"],
                "candidate_date": cand["created_at"] or "",
                "candidate_caption": (cand["caption"] or "")[:1500],
            }, conn=engine, brand=brand_key, month=month)
        except Exception:
            return None
        if not j.get("same_event"):
            return None
        return row, cand, float(j.get("confidence") or 0), \
            (j.get("reason") or "llm match")

    if escalations:
        with ThreadPoolExecutor(
                max_workers=min(MATCH_LLM_WORKERS, len(escalations))) as ex:
            for out in ex.map(judge, escalations):
                if out is None:
                    continue
                row, cand, confidence, why = out
                if confidence >= MATCH_CONFIDENCE:
                    record_hit(results[row["post_id"]], cand, confidence, why)

    # orphans: pulled cross-platform posts matching no kept Weibo post
    n_orphans = 0
    with engine.begin() as conn:
        for cand in others:
            if cand["post_id"] in matched_other_ids:
                continue
            db.upsert(conn, db.orphans,
                      {"post_id": cand["post_id"], "month": month,
                       "resolution": "pending"}, ["post_id"],
                      no_update_cols=["month", "resolution"])
            n_orphans += 1
    return {"matches": results, "orphans": n_orphans}
