"""Phase 4 — cross-platform verification + orphan detection.

For each kept Weibo post: candidates = other-platform posts within ±5 days
sharing a celeb name, sharing campaign keywords, or judged same-event by
prompts/match.md with confidence ≥ 0.7. Matches are recorded per project later
(consolidation joins them); here we record per-post platform hits and orphans.
"""
from __future__ import annotations

import json
import re
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

_WORD_RE = re.compile(r"[A-Za-z0-9]{3,}|[一-鿿]{2,}")


def _keywords(text: str) -> set[str]:
    stop = {"the", "and", "with", "for", "chanel", "gucci", "fendi", "tiffany",
            "louis", "vuitton", "weibo", "video", "photo"}
    return {w.lower() for w in _WORD_RE.findall(text or "") if w.lower() not in stop}


def _celeb_names(verdict_row: dict) -> set[str]:
    names = set()
    for c in json.loads(verdict_row.get("celebs_tagged") or "[]"):
        for k in ("name_cn", "name_en_guess", "handle"):
            v = (c.get(k) or "").strip().lstrip("@")
            if v:
                names.add(v.lower())
    return names


def pull_all(conn, client: TikHubClient, cfg: BrandsConfig, month: str,
             brand_key: str) -> dict:
    """Pull each cross-check platform once per run (cached by idempotent upserts)."""
    counts = {}
    for platform in ("douyin", "xhs", "wechat_mp", "wechat_channels"):
        try:
            counts[platform] = pull_platform(conn, client, cfg, month,
                                             brand_key, platform)
        except Exception as e:
            counts[platform] = f"error: {e}"
    return counts


def crosscheck_brand(conn, llm: LLM, cfg: BrandsConfig, month: str,
                     brand_key: str) -> dict:
    brand = cfg.brand(brand_key)
    kept = kept_posts(conn, month, brand_key)
    others = list(conn.execute(
        select(db.posts).where(db.posts.c.month == month,
                               db.posts.c.brand == brand_key,
                               db.posts.c.platform != "weibo")).mappings())
    matched_other_ids: set[str] = set()
    results = {}
    for row in kept:
        ref_date = parse_iso(row["created_at"])
        ref_kw = _keywords(row["caption"])
        ref_celebs = _celeb_names(row)
        hits = {}
        for cand in others:
            cdate = parse_iso(cand["created_at"])
            if ref_date and cdate and abs((cdate - ref_date).days) > WINDOW_DAYS:
                continue
            ckw = _keywords(cand["caption"])
            overlap_kw = ref_kw & ckw
            cand_text = (cand["caption"] or "").lower()
            overlap_celeb = {n for n in ref_celebs if n and n in cand_text}
            confidence, why = 0.0, ""
            if overlap_celeb:
                confidence, why = 0.85, f"shared celeb: {sorted(overlap_celeb)[:2]}"
            elif len(overlap_kw) >= 3:
                confidence, why = 0.75, f"shared keywords: {sorted(overlap_kw)[:4]}"
            elif len(overlap_kw) == 2:
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
                    }, conn=conn, brand=brand_key, month=month)
                    if j.get("same_event"):
                        confidence = float(j.get("confidence") or 0)
                        why = j.get("reason") or "llm match"
                except Exception:
                    pass
            if confidence >= MATCH_CONFIDENCE:
                plat = cand["platform"]
                prev = hits.get(plat)
                if prev is None or confidence > prev["confidence"]:
                    hits[plat] = {"post_id": cand["post_id"], "url": cand["url"],
                                  "date": cand["created_at"],
                                  "confidence": confidence, "why": why}
                matched_other_ids.add(cand["post_id"])
        results[row["post_id"]] = hits

    # orphans: pulled cross-platform posts matching no kept Weibo post
    n_orphans = 0
    for cand in others:
        if cand["post_id"] in matched_other_ids:
            continue
        db.upsert(conn, db.orphans, {"post_id": cand["post_id"], "month": month,
                                     "resolution": "pending"}, ["post_id"])
        n_orphans += 1
    return {"matches": results, "orphans": n_orphans}
