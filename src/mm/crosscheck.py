"""Phase 4 — cross-platform verification + orphan detection.

For each kept Weibo post: candidates = other-platform posts within ±5 days
sharing a celeb name, sharing campaign keywords, or judged same-event by
prompts/match.md with confidence ≥ 0.7. Accepted matches are persisted PER
POST in db.post_matches — the source of truth. Project-level SOCIAL ticks
are derived from these at enrich time, so a match always belongs to the
specific weibo post it was verified against, never to a whole project.
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
HASHTAG_CONFIDENCE = 0.9  # shared campaign hashtag — the platform-crossing marker
WINDOW_DAYS = 5
MATCH_LLM_WORKERS = 4     # concurrent match.md escalations per brand

_LATIN_RE = re.compile(r"[A-Za-z0-9]{3,}")
_CJK_RE = re.compile(r"[一-鿿]{2,}")


def _norm_tags(raw_json) -> set[str]:
    """Hashtags normalized for cross-platform comparison: strip #, collapse
    whitespace, lowercase. Campaign tags (#SpeedyP9#, #CocoCrush上海#) are the
    strongest same-event signal Chinese brand marketing offers."""
    tags = set()
    try:
        raw = json.loads(raw_json or "[]")
    except (TypeError, ValueError):
        raw = []
    for t in raw:
        t = re.sub(r"\s+", "", str(t)).strip("#").lower()
        if len(t) >= 2:
            tags.add(t)
    return tags


def _brand_generic_tags(brand) -> set[str]:
    """Tags that name the brand itself appear on nearly every post and prove
    nothing — excluded from the shared-hashtag signal (equality match only,
    so #lv龙年限定# stays specific while #louisvuitton# is generic)."""
    generic = {brand.key.lower(),
               re.sub(r"\s+", "", brand.display_name or "").lower()}
    acct = brand.account("weibo")
    if acct and acct.screen_name:
        generic.add(re.sub(r"\s+", "", acct.screen_name).lower())
    return {g for g in generic if g}


def _keywords(text: str) -> set[str]:
    """Latin words plus CJK bigrams (whole CJK runs are punctuation-delimited
    phrases and almost never match across platforms)."""
    stop = {"the", "and", "with", "for", "chanel", "gucci", "fendi", "tiffany",
            "louis", "vuitton", "prada", "loewe", "valentino", "bottega",
            "veneta", "cartier", "hermes", "bvlgari",
            "weibo", "video", "photo", "品牌", "全新",
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


# -- xiaohongshu link hydration -------------------------------------------------

# per brand·month safety cap on note-detail calls ($0.01 each)
XHS_HYDRATE_CAP = 40


def _xhs_share_link(data) -> str | None:
    """The official share link from a note-detail payload, normalized to
    path + xsec_token only. Live-verified traps this codifies: the app_v2
    timeline note ids are NOT the canonical web ids (the share link's path
    carries the real one), and any xiaohongshu.com URL without an
    xsec_token is login-walled for outsiders."""
    from urllib.parse import parse_qs, urlsplit
    raw = json.dumps(data, ensure_ascii=False)
    for m in re.finditer(r'https://www\.xiaohongshu\.com/[^"\s\\]+', raw):
        u = m.group(0)
        if "xsec_token=" in u:
            parts = urlsplit(u)
            token = (parse_qs(parts.query).get("xsec_token") or [None])[0]
            if token:
                return (f"https://www.xiaohongshu.com{parts.path}"
                        f"?xsec_source=app_share&xsec_token={token}")
    return None


def hydrate_xhs_links(engine, client: TikHubClient, month: str,
                      brand_key: str, post_ids: set[str]) -> dict:
    """Replace provisional xhs URLs (built from timeline ids, tokenless →
    dead for anyone clicking them) with the official tokened share link via
    a note-detail call. Only called for posts a human will actually see as
    links (matched evidence + filter-kept orphans), capped per brand."""
    todo = []
    if post_ids:
        with engine.connect() as conn:
            for r in conn.execute(
                    select(db.posts.c.post_id, db.posts.c.url)
                    .where(db.posts.c.post_id.in_(post_ids),
                           db.posts.c.platform == "xhs")).mappings():
                if "xsec_token=" not in (r["url"] or ""):
                    todo.append(r["post_id"])
    stats = {"hydrated": 0, "failed": 0,
             "capped": max(0, len(todo) - XHS_HYDRATE_CAP)}
    for post_id in todo[:XHS_HYDRATE_CAP]:
        note_id = post_id.split(":", 1)[1]
        link = None
        # image detail resolves image notes; video notes need the sibling
        # endpoint — try both, first tokened link wins
        for ep in ("xhs_note_detail_image", "xhs_note_detail_video"):
            try:
                d = client.call(ep, conn=engine, brand=brand_key,
                                month=month, note_id=note_id)
                link = _xhs_share_link(d)
            except Exception:
                link = None
            if link:
                break
        if link:
            with engine.begin() as conn:
                conn.execute(db.posts.update()
                             .where(db.posts.c.post_id == post_id)
                             .values(url=link))
            stats["hydrated"] += 1
        else:
            stats["failed"] += 1
    return stats


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

    # judgments already paid for — a pair can never flip between runs and is
    # never re-billed (owner report: matches inconsistent with reality; part
    # of the fix is making them at least consistent with themselves)
    with engine.connect() as conn:
        judged = {(r["ref_post_id"], r["cand_post_id"]): dict(r)
                  for r in conn.execute(
                      select(db.match_judgments).where(
                          db.match_judgments.c.ref_post_id.in_(
                              [r["post_id"] for r in kept]))).mappings()}

    # candidate features computed once, not once per kept post
    generic_tags = _brand_generic_tags(brand)
    cand_feats = [(cand, _keywords(cand["caption"]),
                   (cand["caption"] or "").lower(),
                   parse_iso(cand["created_at"]),
                   _norm_tags(cand["hashtags"]) - generic_tags)
                  for cand in others]

    # pass 1 — a shared campaign hashtag matches outright (the deterministic
    # platform-crossing marker); EVERY other signal — shared celeb, keyword
    # overlap — only nominates the pair for LLM judgment. Heuristics used to
    # auto-match at 0.85/0.75, which is exactly how a shared ambassador or
    # generic bigram overlap produced ticks inconsistent with reality.
    # A cached same_event=False judgment (an LLM verdict OR a reviewer
    # detaching the pair at checkpoint #2) vetoes even the hashtag tier —
    # a pair a human separated must never re-match itself.
    escalations = []          # (kept row, ref_celebs, candidate, evidence)
    for row in kept:
        ref_date = parse_iso(row["created_at"])
        ref_kw = _keywords(row["caption"])
        ref_celebs = _celeb_names(row)
        ref_tags = _norm_tags(row["hashtags"]) - generic_tags
        hits = {}
        for cand, ckw, cand_text, cdate, ctags in cand_feats:
            if ref_date and cdate and abs((cdate - ref_date).days) > WINDOW_DAYS:
                continue
            cached = judged.get((row["post_id"], cand["post_id"]))
            if cached is not None and not cached["same_event"]:
                continue
            shared_tags = ref_tags & ctags
            overlap_kw = ref_kw & ckw
            overlap_celeb = {n for n in ref_celebs if n and n in cand_text}
            if shared_tags:
                record_hit(hits, cand, HASHTAG_CONFIDENCE,
                           f"shared campaign hashtag: "
                           f"#{sorted(shared_tags)[0]}#")
            elif overlap_celeb or len(overlap_kw) >= 2:
                evidence = []
                if overlap_celeb:
                    evidence.append(f"shared celeb: {sorted(overlap_celeb)[:2]}")
                if overlap_kw:
                    evidence.append(f"shared keywords: {sorted(overlap_kw)[:6]}")
                escalations.append((row, ref_celebs, cand,
                                    "; ".join(evidence)))
        results[row["post_id"]] = hits

    # pass 2 — match.md judgments run concurrently (pairs are independent;
    # the hit tables only mutate after the pool completes). Cached verdicts
    # short-circuit without an LLM call.
    def judge(item):
        row, ref_celebs, cand, evidence = item
        cached = judged.get((row["post_id"], cand["post_id"]))
        if cached is not None:
            if cached["same_event"] and \
                    (cached["confidence"] or 0) >= MATCH_CONFIDENCE:
                return row, cand, float(cached["confidence"]), \
                    cached["reason"] or "llm match (cached)"
            return None
        if should_stop and should_stop():
            return None
        try:
            j = llm.call_json("match", {
                "brand_display": brand.display_name,
                "ref_date": row["created_at"] or "",
                "ref_caption": (row["caption"] or "")[:1500],
                "ref_hashtags": json.dumps(sorted(_norm_tags(row["hashtags"])),
                                           ensure_ascii=False),
                "ref_celebs": json.dumps(sorted(ref_celebs), ensure_ascii=False),
                "ref_title": "",
                "candidate_platform": cand["platform"],
                "candidate_date": cand["created_at"] or "",
                "candidate_caption": (cand["caption"] or "")[:1500],
                "candidate_hashtags": json.dumps(
                    sorted(_norm_tags(cand["hashtags"])), ensure_ascii=False),
                "candidate_at_tags": cand["at_tags"] or "[]",
                "heuristic_evidence": evidence,
            }, conn=engine, brand=brand_key, month=month)
        except Exception:
            return None
        same = bool(j.get("same_event"))
        conf = float(j.get("confidence") or 0)
        reason = str(j.get("reason") or "llm match")[:300]
        with engine.begin() as wconn:
            db.upsert(wconn, db.match_judgments, {
                "ref_post_id": row["post_id"],
                "cand_post_id": cand["post_id"],
                "same_event": same, "confidence": conf, "reason": reason,
                "at": db.now_iso()}, ["ref_post_id", "cand_post_id"])
        if not same:
            return None
        return row, cand, conf, reason

    if escalations:
        with ThreadPoolExecutor(
                max_workers=min(MATCH_LLM_WORKERS, len(escalations))) as ex:
            for out in ex.map(judge, escalations):
                if out is None:
                    continue
                row, cand, confidence, why = out
                if confidence >= MATCH_CONFIDENCE:
                    record_hit(results[row["post_id"]], cand, confidence, why)

    # persist the accepted matches PER POST — replace this brand·month's rows
    # wholesale so re-runs (and human vetoes) are reflected exactly
    with engine.begin() as conn:
        conn.execute(db.post_matches.delete().where(
            db.post_matches.c.ref_post_id.in_(
                [r["post_id"] for r in kept])))
        for ref_id, hits in results.items():
            for plat, hit in hits.items():
                db.upsert(conn, db.post_matches, {
                    "ref_post_id": ref_id, "cand_post_id": hit["post_id"],
                    "platform": plat, "month": month,
                    "confidence": hit["confidence"], "reason": hit["why"],
                    "at": db.now_iso()}, ["ref_post_id", "cand_post_id"])

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
