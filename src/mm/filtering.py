"""Phase 2 — LLM relevance filter (prompts/filter.md), one call per Weibo post."""
from __future__ import annotations

import json

from sqlalchemy import select

from . import db
from .config import BrandsConfig
from .llm import LLM

CONFIDENCE_REVIEW_THRESHOLD = 0.65   # bias to recall


def filter_month(engine, llm: LLM, cfg: BrandsConfig, month: str,
                 brand_key: str | None = None, progress=None) -> dict:
    """One LLM call per unfiltered post; each verdict commits in its own short
    transaction, so a mid-run crash loses nothing already paid for and the
    console stays writable while this runs."""
    q = (select(db.posts)
         .where(db.posts.c.month == month, db.posts.c.platform == "weibo"))
    if brand_key:
        q = q.where(db.posts.c.brand == brand_key)
    with engine.connect() as conn:
        rows = list(conn.execute(q).mappings())
        done = {r["post_id"]
                for r in conn.execute(select(db.verdicts.c.post_id)).mappings()}
    stats = {"total": len(rows), "filtered": 0, "kept": 0, "needs_review": 0,
             "errors": 0}
    for row in rows:
        if row["post_id"] in done:
            continue
        brand = cfg.brand(row["brand"])
        media = json.loads(row["media"] or "[]")
        media_summary = (f"{sum(1 for m in media if m['kind'] == 'image')} image(s), "
                         f"{sum(1 for m in media if m['kind'] == 'video_cover')} video(s)")
        try:
            verdict = llm.call_json("filter", {
                "brand_display": brand.display_name,
                "account_name": (brand.account("weibo").screen_name
                                 if brand.account("weibo") else ""),
                "created_at": row["created_at"] or "",
                "caption": row["caption"] or "",
                "at_tags": json.loads(row["at_tags"] or "[]"),
                "hashtags": json.loads(row["hashtags"] or "[]"),
                "media_summary": media_summary,
            }, conn=engine, brand=row["brand"], month=month)
        except Exception:
            # transient API failure: record nothing — the post stays
            # unfiltered and the next `mm filter` run picks it up cheaply
            stats["errors"] += 1
            continue
        keep = bool(verdict.get("keep"))
        conf = float(verdict.get("confidence") or 0)
        # ambiguous reposts always surface for human review
        needs_review = conf < CONFIDENCE_REVIEW_THRESHOLD or bool(row["repost_ambiguous"])
        if not keep and conf < CONFIDENCE_REVIEW_THRESHOLD:
            keep, needs_review = True, True    # bias to recall
        # deterministic cosmetics signal (fragrance stays IN) — disagreement
        # with the LLM verdict always surfaces for human review
        from .naming import cosmetics_signal
        signal = cosmetics_signal(row["caption"] or "")
        if signal == "makeup_skincare" and keep:
            needs_review = True
            verdict.setdefault("reasons", []).append(
                "keyword signal: makeup/skincare terms present")
        with engine.begin() as wconn:
            db.upsert(wconn, db.verdicts, {
                "post_id": row["post_id"], "keep": keep, "confidence": conf,
                "reasons": json.dumps(verdict.get("reasons") or [],
                                      ensure_ascii=False),
                "celebs_tagged": json.dumps(verdict.get("celebs_tagged") or [],
                                            ensure_ascii=False),
                "category": verdict.get("category") or "other",
                "media_focus": verdict.get("media_focus") or "photo",
                "needs_review": needs_review,
            }, ["post_id"])
        stats["filtered"] += 1
        stats["kept"] += int(keep)
        stats["needs_review"] += int(needs_review)
        if progress:
            progress(stats)
    return stats


def kept_posts(conn, month: str, brand_key: str) -> list[dict]:
    """Posts that survive filter + human review (human decision wins)."""
    q = (select(db.posts, db.verdicts)
         .join(db.verdicts, db.verdicts.c.post_id == db.posts.c.post_id)
         .where(db.posts.c.month == month, db.posts.c.brand == brand_key,
                db.posts.c.platform == "weibo"))
    out = []
    for r in conn.execute(q).mappings():
        decision = r["human_decision"]
        keep = r["keep"] if decision is None else (decision == "keep")
        if keep:
            out.append(dict(r))
    return out
