# ASSUMPTIONS — judgment calls made during the one-shot build

Everything the Decision Record locked is implemented as specified. The calls
below were mine; each is easy to revisit.

## Repo & environment

1. **Repo root = this repository** (`DiorCompResearch`), not a `maison-monitor/`
   subfolder — the project owns the whole repo, and the CLI/package name stays
   `mm` / `maison-monitor`.
2. **Reference decks are committed** to `template/` (the handoff's repo tree
   lists them outside the gitignored dirs). If the repo ever goes public they
   must be pulled — they contain competitor material.
3. **Default model is `claude-sonnet-5`.** The handoff §3 said default
   `claude-sonnet-4-6`, but the user-provided `.env` comment states
   `MM_MODEL=claude-sonnet-5` "(this is the default)" — the newer,
   user-authored instruction wins. Override any time via `MM_MODEL`.
4. **`ANTHROPIC_BASE_URL` from the ambient environment is ignored** — the LLM
   client pins `https://api.anthropic.com` (override deliberately with
   `MM_ANTHROPIC_BASE_URL`). A stray env var must not reroute API-key traffic.

## Template & rendering

5. **Brand logo sizes are per-brand, copied from the July deck's measured
   sizes** (not one standardized box) — a uniform fit box made the FENDI/GUCCI
   wordmarks visibly oversized relative to the reference.
6. **Platform icon row order is standardized to WEIBO → RED → WECHAT → DOUYIN**
   (same order as the SOCIAL column). The human decks varied (July slide 4 shows
   weibo/wechat/red).
7. **The "video marker" is the deck's little movie-camera glyph placed left of
   the first still** (as in both reference decks) — there is no play-triangle
   overlay on stills in the references, so none is added.
8. **DATE ordinals render superscript** (separate run, `baseline=30000`),
   matching the references, on top of the standardized `JULY 2ND – 3RD` text.
9. **Table style relies on PowerPoint's built-in "Medium Style 2"
   (`{073A0DAA-6AF3-43AB-8588-CEC1D06C72B9}`)** with `firstRow`/`bandRow`,
   exactly like the reference (black header, grey banded rows). No manual cell
   fills are written.
10. **One-row photo layouts auto-size**: common height chosen per count
    (≤4 → up to 4.23", 5 → ≤2.95", 6 → ≤2.45"), images width-scaled by aspect,
    row centered — reproducing the reference's varied-but-consistent look.
11. **Slide-2 analysis stub is cloned verbatim** from the July deck (its
    STRENGTHS:/WEAKNESSES: content is already empty there).

## Pipeline

12. **Weibo pagination**: live testing showed `fetch_user_posts` rejects an
    explicit `page` param; first call sends `uid` only, then `since_id`. (The
    OpenAPI doc lists `page`, but the live endpoint 400s on it.)
13. **Pure-repost rule**: a post with `retweeted_status` and ≤4 chars of own
    commentary (or the literal 转发微博) is skipped; a repost *with* commentary
    is ingested and flagged `repost_ambiguous` → always `needs_review` at
    checkpoint #1 (flag-not-drop per the handoff).
14. **Deterministic cosmetics keyword signal** runs beside the LLM filter as
    defense-in-depth (Chanel's mixed account): fragrance terms are checked
    first and never exclude; makeup/skincare terms merely force
    `needs_review` when the LLM kept the post — the LLM + human remain the
    deciders.
15. **Cross-platform matching heuristics before LLM**: shared celeb name
    (conf 0.85) or ≥3 shared keywords (conf 0.75) match directly; exactly 2
    shared keywords escalate to `match.md`. Threshold 0.7 per the handoff.
16. **Occupation labels**: relation.md only extracts occupations stated in the
    caption, and the registry compounds them across months. A celeb with no
    known occupation renders as `CELEBRITY ?` and is expected to be fixed at
    checkpoint #2 (deliberate: inventing occupations from model memory was
    ruled out alongside inventing relations).
17. **Web-search confirm** uses the Anthropic `web_search` server tool
    (confirm-only, max 3 searches, requires an explicit confirming source URL);
    disable with `MM_WEB_CONFIRM=0`.
18. **Re-running enrich replaces only `draft` projects.** If a brand/month has
    confirmed projects, enrichment refuses to overwrite them.
19. **Orphan promotion** creates a minimal draft project (caption-derived
    title) for the human to edit — it does not run LLM enrichment on the
    orphan.
20. **TikHub cost log uses estimates**: $0.01/request for Xiaohongshu App V2
    (documented) and $0.001/request elsewhere — refine against a real TikHub
    invoice; the log records exact call counts either way.

## Console

21. **Plain server-rendered forms + a tiny fetch-poller** instead of HTMX — the
    interaction surface is small enough that zero client dependencies beat one.
22. **Card visuals embed images as data: URIs** — headless Chromium blocks
    `file://` subresources on synthetic pages.
23. **Live m.weibo.cn screenshots validate the card's bounding box** and fall
    back to the HTML card if the page is a login/error wall (and a failed
    navigation recreates the browser page so it can't poison the fallback).
24. **EN gloss** on the Posts tab is approximated by the filter verdict's
    English `reasons` rather than a separate translation call per post.

## Testing

25. The ~15 caption fixtures test the deterministic layers (@-tag extraction,
    CN-title mapping, cosmetics/fragrance policy signal, keep-policy
    annotations); LLM rubric behavior itself was smoke-tested live (real LV
    July posts: 3/3 correct keeps, correct celeb extraction, correct
    caption-stated BRAND AMBASSADOR for 王楚钦).
