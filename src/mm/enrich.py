"""Phase 5 — enrichment: celebrity relations (strict cascade, never invent),
one-line descriptions, and consolidation into projects.

Relation cascade per celeb:
  a. caption extraction (prompts/relation.md) — Chinese captions usually state
     the title next to the @; a stated title counts as verified (source = post).
  b. registry lookup (celeb_registry, compounding across months).
  c. web-search confirm ONLY (Anthropic web_search tool) for suspected but
     unverified relations — never to conjure one.
  d. unresolved → occupation + ' ?' (or 'CELEBRITY ?' when occupation unknown;
     checkpoint #2's registry-backed dropdown is where the human fixes it).
"""
from __future__ import annotations

import json
import os
import re

from sqlalchemy import delete, select

from . import db, naming
from .config import BrandsConfig
from .dates import month_bounds, parse_iso
from .filtering import kept_posts
from .llm import LLM, parse_json_loose


# -- registry -----------------------------------------------------------------

_CN_RE = re.compile(r"[一-鿿]{2,}")


def norm_name_cn(raw: str) -> str:
    """'王羽佳Yuja' → '王羽佳' (Weibo handles often append latin suffixes)."""
    raw = (raw or "").strip().lstrip("@")
    m = _CN_RE.search(raw)
    return m.group(0) if m else raw


def registry_get(conn, name_cn: str) -> dict | None:
    from sqlalchemy.engine import Engine
    stmt = select(db.celeb_registry).where(db.celeb_registry.c.name_cn == name_cn)
    if isinstance(conn, Engine):
        with conn.connect() as c:
            row = c.execute(stmt).mappings().first()
    else:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def registry_update(conn, name_cn: str, *, name_en: str | None = None,
                    occupation: str | None = None, brand_key: str | None = None,
                    relation: str | None = None, raw_cn_title: str | None = None,
                    verified: bool = False, source_url: str | None = None,
                    date: str | None = None) -> None:
    row = registry_get(conn, name_cn)
    relations = json.loads(row["relations_json"]) if row else {}
    if brand_key and relation:
        prev = relations.get(brand_key, {})
        if verified or not prev.get("verified"):
            relations[brand_key] = {"relation": relation,
                                    "raw_cn_title": raw_cn_title,
                                    "verified": verified,
                                    "source_url": source_url, "date": date}
    values = {"name_cn": name_cn,
              "name_en": name_en or (row or {}).get("name_en"),
              "occupation": occupation or (row or {}).get("occupation"),
              "relations_json": json.dumps(relations, ensure_ascii=False)}
    from sqlalchemy.engine import Engine
    if isinstance(conn, Engine):
        with conn.begin() as c:
            db.upsert(c, db.celeb_registry, values, ["name_cn"])
    else:
        db.upsert(conn, db.celeb_registry, values, ["name_cn"])


def registry_export(conn) -> list[dict]:
    rows = conn.execute(select(db.celeb_registry)).mappings()
    return [{**dict(r), "relations": json.loads(r["relations_json"] or "{}")}
            for r in rows]


# -- relation cascade ------------------------------------------------------------

def _web_confirm(llm: LLM, brand_display: str, name_cn: str,
                 name_en: str | None) -> tuple[str | None, str | None]:
    """Confirm-only web search. Returns (relation, source_url) or (None, None)."""
    if os.environ.get("MM_WEB_CONFIRM", "1") == "0":
        return None, None
    try:
        resp = llm.client.messages.create(
            model=llm.model, max_tokens=1000,
            tools=[{"type": "web_search_20260209", "name": "web_search",
                    "max_uses": 3}],
            messages=[{"role": "user", "content":
                f"Verify (do not guess): is {name_en or name_cn} ({name_cn}) "
                f"currently an OFFICIAL {brand_display} brand ambassador "
                f"(代言人/品牌大使) or brand friend (品牌挚友)? Search "
                f"'{brand_display} {name_cn} 代言 大使'. Respond with strict JSON "
                f'only: {{"relation": "BRAND AMBASSADOR" | "BRAND FRIEND" | null, '
                f'"source_url": "https://..." | null}}. relation must be null '
                f"unless a source explicitly confirms it."}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        j = parse_json_loose(text)
        rel = j.get("relation")
        if rel in ("BRAND AMBASSADOR", "BRAND FRIEND") and j.get("source_url"):
            return rel, j["source_url"]
    except Exception:
        pass
    return None, None


def extract_caption_relations(conn, llm: LLM, cfg: BrandsConfig, month: str,
                              brand_key: str, posts: list[dict]) -> dict:
    """Run relation.md over kept posts; returns {name_cn: celeb info} and
    updates the registry with caption-stated titles."""
    brand = cfg.brand(brand_key)
    celebs: dict[str, dict] = {}
    for row in posts:
        at_tags = json.loads(row["at_tags"] or "[]")
        tagged = json.loads(row["celebs_tagged"] or "[]") if "celebs_tagged" in row else []
        if not at_tags and not tagged:
            continue
        try:
            j = llm.call_json("relation", {
                "brand_display": brand.display_name,
                "caption": (row["caption"] or "")[:2000],
                "at_tags": at_tags,
            }, conn=conn, brand=brand_key, month=month)
        except Exception:
            continue
        guesses = {norm_name_cn(c.get("name_cn") or ""): c for c in tagged}
        for c in j.get("celebs") or []:
            name_cn = norm_name_cn(c.get("name_cn") or "")
            if not name_cn:
                continue
            relation = c.get("relation")
            name_en = (guesses.get(name_cn) or {}).get("name_en_guess")
            entry = celebs.setdefault(name_cn, {
                "name_cn": name_cn, "name_en": name_en,
                "relation": None, "raw_cn_title": None, "verified": False,
                "occupation": c.get("occupation"), "source_url": None})
            if c.get("occupation") and not entry.get("occupation"):
                entry["occupation"] = c["occupation"]
            if relation and not entry["verified"]:
                entry.update({"relation": relation,
                              "raw_cn_title": c.get("raw_cn_title"),
                              "verified": True, "source_url": row["url"]})
                registry_update(conn, name_cn, name_en=name_en,
                                occupation=c.get("occupation"),
                                brand_key=brand_key, relation=relation,
                                raw_cn_title=c.get("raw_cn_title"), verified=True,
                                source_url=row["url"],
                                date=(row["created_at"] or "")[:10])
        # celebs the filter saw but relation.md did not resolve
        for c in tagged:
            name_cn = norm_name_cn(c.get("name_cn") or "")
            if name_cn and name_cn not in celebs:
                celebs[name_cn] = {"name_cn": name_cn,
                                   "name_en": c.get("name_en_guess"),
                                   "relation": None, "raw_cn_title": None,
                                   "verified": False, "occupation": None,
                                   "source_url": None}
    # registry + web-confirm passes
    for name_cn, entry in celebs.items():
        reg = registry_get(conn, name_cn)
        if reg:
            entry["name_en"] = entry["name_en"] or reg.get("name_en")
            entry["occupation"] = entry["occupation"] or reg.get("occupation")
            rel = (json.loads(reg["relations_json"] or "{}")).get(brand_key)
            if rel and not entry["verified"]:
                entry.update({"relation": rel.get("relation"),
                              "raw_cn_title": rel.get("raw_cn_title"),
                              "verified": bool(rel.get("verified")),
                              "source_url": rel.get("source_url")})
        if entry["relation"] and not entry["verified"]:
            rel, src = _web_confirm(llm, brand.display_name, name_cn,
                                    entry["name_en"])
            if rel:
                entry.update({"relation": rel, "verified": True,
                              "source_url": src})
                registry_update(conn, name_cn, name_en=entry["name_en"],
                                brand_key=brand_key, relation=rel, verified=True,
                                source_url=src)
        registry_update(conn, name_cn, name_en=entry["name_en"],
                        occupation=entry["occupation"])
    return celebs


def celeb_for_deck(entry: dict) -> dict:
    display = naming.display_name(entry.get("name_en"), entry.get("name_cn"))
    if entry.get("relation") and entry.get("verified"):
        label = entry["relation"]
    else:
        label = naming.relation_display(None, entry.get("occupation"), False)
    return {"name_cn": entry.get("name_cn"), "name_en": entry.get("name_en"),
            "display": display, "relation_display": label,
            "verified": bool(entry.get("verified")),
            "occupation": entry.get("occupation")}


# -- consolidation -----------------------------------------------------------------

def _hero_media(posts: list[dict], limit: int = 3) -> list[str]:
    """1–3 best local images — prefer Weibo originals, highest resolution."""
    from PIL import Image
    scored = []
    for row in posts:
        for m in json.loads(row["media"] or "[]"):
            p = m.get("local_path")
            if not p:
                continue
            try:
                with Image.open(p) as im:
                    w, h = im.size
            except Exception:
                continue
            score = w * h + (10**7 if row["platform"] == "weibo" else 0)
            scored.append((score, p))
    scored.sort(reverse=True)
    seen, out = set(), []
    for _, p in scored:
        if p not in seen:
            out.append(p)
            seen.add(p)
        if len(out) >= limit:
            break
    return out


def enrich_brand(engine, llm: LLM, cfg: BrandsConfig, month: str, brand_key: str,
                 crosscheck_matches: dict | None = None) -> dict:
    brand = cfg.brand(brand_key)
    with engine.connect() as conn:
        posts = kept_posts(conn, month, brand_key)
        existing_confirmed = conn.execute(select(db.projects).where(
            db.projects.c.month == month, db.projects.c.brand == brand_key,
            db.projects.c.status.in_(("confirmed", "rendered")))).mappings().first()
    if not posts:
        return {"projects": 0, "note": "no kept posts"}
    if existing_confirmed:
        return {"projects": -1, "note": "confirmed projects exist; not overwriting"}

    # LLM-heavy work happens with no transaction open
    celebs = extract_caption_relations(engine, llm, cfg, month, brand_key, posts)

    posts_json = [{"post_id": r["post_id"], "date": (r["created_at"] or "")[:10],
                   "caption": (r["caption"] or "")[:300],
                   "celebs": json.loads(r["celebs_tagged"] or "[]")
                   if "celebs_tagged" in r else []}
                  for r in posts]
    clusters = llm.call_json("consolidate", {
        "brand_display": brand.display_name,
        "posts_json": posts_json,
    }, conn=engine, brand=brand_key, month=month, max_tokens=8000)

    # merge clusters that collapse onto the same (title, phase_suffix) after
    # normalization — db.projects has a natural-key unique constraint
    merged: dict[tuple, dict] = {}
    for cluster in clusters.get("projects") or []:
        title, suffix = naming.split_phase_suffix(cluster.get("title") or "PROJECT")
        suffix = suffix or cluster.get("phase_suffix")
        key = (title.upper(), (suffix or "").upper() or None)
        if key in merged:
            merged[key]["post_ids"] = list(dict.fromkeys(
                (merged[key].get("post_ids") or []) + (cluster.get("post_ids") or [])))
            merged[key]["ongoing"] = merged[key].get("ongoing") or cluster.get("ongoing")
        else:
            cluster = dict(cluster)
            cluster["_title"], cluster["_suffix"] = title, suffix
            merged[key] = cluster

    by_id = {r["post_id"]: r for r in posts}
    month_start, month_end = month_bounds(month)
    prepared = []
    for cluster in merged.values():
        member_ids = [pid for pid in (cluster.get("post_ids") or []) if pid in by_id]
        if not member_ids:
            continue
        members = [by_id[pid] for pid in member_ids]
        # celebs present in this cluster's captions/tags
        proj_celebs = []
        for name_cn, entry in celebs.items():
            hit = any(name_cn in (m["caption"] or "")
                      or name_cn in (m["at_tags"] or "")
                      for m in members)
            if hit:
                proj_celebs.append(celeb_for_deck(entry))

        # dates across all platforms (weibo members + matched posts)
        dates = [parse_iso(m["created_at"]) for m in members if m["created_at"]]
        matches: dict[str, dict] = {}
        for pid in member_ids:
            for plat, hit in ((crosscheck_matches or {}).get(pid) or {}).items():
                prev = matches.get(plat)
                if prev is None or hit["confidence"] > prev["confidence"]:
                    matches[plat] = hit
                if hit.get("date"):
                    d = parse_iso(hit["date"])
                    if d:
                        dates.append(d)
        dates = [d for d in dates if d]
        date_start = min(dates) if dates else month_start.date()
        date_end = max(dates) if dates else date_start
        date_start = max(date_start, month_start.date())
        date_end = min(date_end, (month_end.date()))
        ongoing = bool(cluster.get("ongoing"))

        has_video = any(
            m2.get("kind") == "video_cover"
            for m in members for m2 in json.loads(m["media"] or "[]"))
        has_photo = any(
            m2.get("kind") == "image"
            for m in members for m2 in json.loads(m["media"] or "[]"))
        # union across matched platforms is refined by the human at checkpoint #2
        assets = naming.assets_label(has_photo or not has_video, has_video)

        title, suffix = cluster["_title"], cluster["_suffix"]
        try:
            desc = llm.call_json("describe", {
                "brand_display": brand.display_name,
                "title": title, "phase_suffix": suffix or "",
                "category": cluster.get("category") or "other",
                "celebs": [{ "name": c["display"], "relation": c["relation_display"]}
                           for c in proj_celebs],
                "captions": "\n---\n".join((m["caption"] or "")[:300] for m in members[:5]),
            }, conn=engine, brand=brand_key, month=month)
            description = naming.format_title(desc.get("description") or title, None)
            if len(description) > 90:
                description = naming.format_title(title, suffix)
        except Exception:
            description = naming.format_title(title, suffix)

        prepared.append({
            "values": dict(
                month=month, brand=brand_key, title=title.upper(),
                phase_suffix=suffix, date_start=date_start.isoformat(),
                date_end=date_end.isoformat(), ongoing=ongoing, assets=assets,
                description=description,
                celebs=json.dumps(proj_celebs, ensure_ascii=False),
                hero_media=json.dumps(_hero_media(members), ensure_ascii=False),
                status="draft"),
            "member_ids": member_ids,
            "first_member": members[0],
            "matches": matches,
        })

    # single short write transaction: replace previous draft/dropped projects
    # (confirmed ones were guarded against above) and insert the new set
    with engine.begin() as conn:
        old = conn.execute(select(db.projects.c.id).where(
            db.projects.c.month == month, db.projects.c.brand == brand_key,
            db.projects.c.status.in_(("draft", "dropped")))).scalars().all()
        if old:
            conn.execute(delete(db.project_posts)
                         .where(db.project_posts.c.project_id.in_(old)))
            conn.execute(delete(db.platform_matches)
                         .where(db.platform_matches.c.project_id.in_(old)))
            conn.execute(delete(db.projects).where(db.projects.c.id.in_(old)))
        n = 0
        for item in prepared:
            res = conn.execute(db.projects.insert().values(**item["values"]))
            project_id = res.inserted_primary_key[0]
            for pid in item["member_ids"]:
                db.upsert(conn, db.project_posts,
                          {"project_id": project_id, "post_id": pid,
                           "role": "member"}, ["project_id", "post_id"])
            first = item["first_member"]
            db.upsert(conn, db.platform_matches,
                      {"project_id": project_id, "platform": "weibo",
                       "present": True, "matched_url": first["url"],
                       "matched_date": first["created_at"], "confidence": 1.0},
                      ["project_id", "platform"])
            for plat, hit in item["matches"].items():
                db.upsert(conn, db.platform_matches,
                          {"project_id": project_id, "platform": plat,
                           "present": True, "matched_url": hit["url"],
                           "matched_date": hit["date"],
                           "confidence": hit["confidence"]},
                          ["project_id", "platform"])
            n += 1
    return {"projects": n, "celebs": len(celebs)}
