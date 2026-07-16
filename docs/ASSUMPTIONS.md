# ASSUMPTIONS — judgment calls made during the one-shot build

Everything the Decision Record locked is implemented as specified — except
where a later owner directive superseded it; those reversals are called out
explicitly (see #46: perfume is now OUT of scope). The calls below were mine;
each is easy to revisit.

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
    OpenAPI doc lists `page`, but the live endpoint 400s on it.) The TikHub
    envelope echoes the request at top-level `params` *before* the payload,
    so cursors are read from the response body only (`normalize.next_cursor`)
    — a whole-response search returns the cursor just sent and freezes
    pagination on one page. Ingest also dedupes post_ids within a run and
    stops on an unmoved cursor or two pages with nothing new.
13. **Pure-repost rule**: a post with `retweeted_status` and ≤4 chars of own
    commentary (or the literal 转发微博) is skipped; a repost *with* commentary
    is ingested and flagged `repost_ambiguous` → always `needs_review` at
    checkpoint #1 (flag-not-drop per the handoff).
14. **Deterministic beauty keyword signal** runs beside the LLM filter as
    defense-in-depth (Chanel's mixed account). Since the 2026-07 owner
    directive (see #46), perfume/fragrance AND makeup/skincare are both
    policy DROP: the keyword signal forces `needs_review` when the LLM kept
    such a post, and the recall-bias flip never resurrects a beauty post the
    LLM dropped — the human remains the final decider either way.
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

## Post-review hardening (adversarial multi-agent review, 28 confirmed findings fixed)

26. **SQLite runs in WAL mode with a 60s busy timeout**, and no phase holds a
    write transaction across network work: ingest/pulls commit per page,
    filter commits per post, enrichment writes in one short transaction at
    the end. A crash never loses paid LLM work.
27. **Brand table slides split after 7 project rows** (continuation table
    slide) so the table can't run off the 7.5" canvas; VIDEO projects chunk
    at 6 stills per slide (the video layouts' maximum).
28. **A post pulled by two adjacent months' padded windows keeps its first
    month assignment**; cross-check candidates are queried by date window,
    not month.
29. **Transient LLM failures during filtering record nothing** — the post
    stays unfiltered, the phase reports `error: N posts failed`, and the next
    run picks up only the missing posts.
30. **Re-running enrich never regresses a confirmed review**: the checkpoint
    only reopens when enrichment actually produced drafts, and confirmed/
    rendered projects are never overwritten (dropped drafts are replaced).

## Change Order 01 — hosted (Fly.io) judgment calls

31. **Auth is keyed off `CONSOLE_PASSPHRASE`, not `MM_ENV`**: login is enforced
    whenever a passphrase is set (hosted mode fails fast if it isn't), so the
    hosted auth behavior is fully testable on a laptop, while a bare local
    checkout keeps the old no-login Console ("local dev unchanged").
32. **`Secure` cookies only in hosted mode** — a Secure cookie over plain
    `http://127.0.0.1` would break local logins when a passphrase is set.
33. **brands.yaml stays the single source of truth everywhere**: hosted Phase R
    confirmations persist as a small per-account overlay
    (`$MM_DATA_DIR/account_overrides.yaml`, merged at load) so they survive
    deploys while repo edits (new brands, filters, labels) always take effect
    on the next deploy — a full-file volume copy would shadow them forever.
34. **`config/default.env` and `.env` are dockerignored** (§6 "never baked into
    the image") — hosted secrets come exclusively from `fly secrets set`.
35. **Screenshot push tagging**: a pushed shot is stored as
    `live_<post_id>.png` (the filename prefix is the source tag, mirroring the
    existing live/card naming) plus a `screenshot_push` audit row; the card
    stays on disk as fallback. No extra DB column — renders always prefer an
    existing `live_*.png` regardless of visuals mode.
36. **Bearer-passphrase API auth** for `mm screenshots --push` (the order says
    "authenticate with the passphrase"); it is accepted on any route, audited
    as actor `api-client`.
37. **Volume ownership**: Fly mounts `/data` as root, so the container starts
    as root only to `chown` the mount, then drops to the non-root `mm` user
    via gosu (the Dockerfile's non-root requirement, reconciled with volumes).
38. **LibreOffice in the image is `libreoffice-impress`** (+ poppler-utils) —
    the QA loop only converts pptx→pdf; the full `libreoffice` meta-package
    would add Writer/Calc/etc. for nothing ("trim where free, never by
    dropping the QA loop").
39. **`mm deploy` generates missing `CONSOLE_PASSPHRASE`/`MM_SECRET_KEY` into
    `.env`** on first run (echoing names, never values) so the first deploy
    can't ship without them; app-name collisions get a random suffix written
    back into fly.toml.
40. **Backup**: the Console button streams a `sqlite3 .backup`-consistent
    snapshot (WAL-safe), not a raw copy of a live DB file.
41. **Post-review hardening (14 confirmed findings fixed)**: sessions are
    bound to the passphrase (rotation = instant sign-out everywhere);
    `mm deploy` is idempotent — validates keys before touching Fly, ensures
    the volume, and re-syncs secrets via stdin on every run (region read from
    fly.toml); the screenshots manifest only lists effective-keep posts and
    the push endpoint streams with a hard 20MB cap; human decisions upsert
    (a post whose LLM verdict failed can still be decided) and audit rows are
    only written for changes that actually landed; hosted phase threads are
    non-daemon so fly.toml's kill_timeout actually buys checkpoint time.

42. **Long phases must be observably alive**: ingest/filter/crosscheck/enrich
    stream their live position (brand · page · counts) into the Runs page
    poller line; per-brand phase failures are written into the phase status
    itself (visible in the pills) instead of a buried result dict; and a phase
    left at "running" by a mid-run process restart is flagged as interrupted
    with a resume hint (re-running is always safe — every phase is idempotent).
    A post's media files download concurrently (deduped per post, 10s connect
    timeout) since downloads dominate ingest wall-clock.

43. **Stop is cooperative, not preemptive**: the Runs page Stop button sets a
    flag that ingest checks between pages, filter between posts, and
    cross-check/enrich between brands — the current item always completes, so
    no paid API call is wasted and nothing is left half-written. A stopped
    phase reads `stopped — … resumes` and resuming = pressing Start month (or
    re-confirming the checkpoint that launched it). Render is not
    interruptible (it's minutes at most and has no safe midpoint). Each
    Start-month press is audited (`start_month`) so the Runs page shows who
    started the last run and when; a rolling per-month activity feed (in
    memory, 200 lines) narrates every page/post step, and review pages show
    an "in progress" banner — the lists they display are the last completed
    state — and auto-refresh when the run pauses.

44. **Archive & reset is the fresh-start mechanism.** Re-running a month
    deliberately *adds to* its dataset (idempotent upserts; human decisions
    persist) — that's what makes stop/resume safe. When a truly clean search
    is wanted (bad early runs, changed accounts), the Runs page's
    **Archive & reset** snapshots every row for the month (posts, verdicts,
    projects, matches, orphans) into `archives`/`archive_rows` as JSON —
    schema-proof, zero impact on live queries — deletes them from the live
    tables in one transaction, and resets the month's phases. Archives are
    listed under the month with counts/actor/time. Media files stay on disk
    and are reused by URL hash, so a re-search costs no re-downloads.
    Restore is deliberately manual (the rows sit in the DB as JSON).

45. **Brand ingest runs in parallel** (one worker per brand): TikHub calls
    and media downloads are I/O-bound, page batches commit in short WAL
    transactions, per-brand failures stay isolated, and the progress line
    shows a combined per-brand state. Pages within a brand remain sequential
    (cursor pagination is inherently serial).

46. **OWNER DIRECTIVE 2026-07 — supersedes the Decision Record's "fragrance
    IN" rule**: anything perfume/fragrance related is DROPPED, same as
    makeup/skincare. The filter prompt's core test was reframed to match the
    owner's stated logic: *China-market-specific → KEEP; global campaign with
    no China angle → DROP*, with CN celebrities (especially @-tagged) and
    specific China locations as strong keep signals. The deterministic
    keyword layer flags (never silently drops) LLM-kept perfume/beauty posts
    as needs_review — flag-not-drop still governs machine overrides; the drop
    itself is the rubric's job.

47. **Self-tuning filter loop**: every human keep/drop/restore is catalogued
    in `filter_feedback` with the LLM's stance and rationale at decision
    time. Before each filter run (and on demand from the Learning page), new
    corrections are distilled (prompts/learn.md) into a short learned-
    guidance block — appended to the filter prompt via `{{learned_rules}}`,
    capped at 4 KB, replaced wholesale each synthesis (history kept in
    `learned_rules`). The base rubric is fixed; the learner is instructed it
    may refine but never contradict it, may not invent rules unsupported by
    corrections, and synthesis failures never block a filter run. The model
    also now returns a `rationale` (3–5 sentences of its thinking), stored on
    the verdict and shown in review ("why?") and in archives.

48. **Learning-loop hardening (adversarial review round 3, 11 distinct
    findings fixed)**: the feedback watermark drains oldest-first in batches
    (a >200 backlog can no longer skip corrections); synthesis is serialized
    per process with a commit-time watermark re-check (concurrent runs can't
    double-count or regress); a schema-drifted learner response fails loudly
    without consuming corrections or wiping guidance; learner output is
    sanitized (bullets only, ≤15 lines, `{{ }}` stripped) and prompt
    substitution is single-pass (values can never inject other variables'
    slots — closes the caption→prompt injection route); the recall-bias flip
    exempts beauty posts (a low-confidence perfume drop stays dropped);
    drop-then-restore feeds only the final decision to the learner; the
    archive viewer groups by snapshot brands (removed brands stay visible)
    and never greys never-filtered posts; the Learning page's pending count
    is a real DB count, not capped by the display window.

49. **HQ images & human image selection (owner change order)**: ingest now
    downloads the *largest* variant weibo offers (chosen by pixel area, not
    variant name). In Review · Posts, every kept post shows its images with a
    checkbox — **ticked images render directly on the slide** (label on the
    first; no ticks → the automatic card/screenshot as before, so the old
    flow is the default) — plus a drop zone to upload manually-downloaded
    originals (JPEG/PNG/GIF/WebP by magic bytes, 30MB streamed cap, stored
    content-hashed, auto-selected, audited). Fonts per owner spec: the
    projects xlsx is Futura Lt BT 11 throughout; the celeb relationship +
    name labels under slide visuals are Calibre 9 (link lines keep their
    original size).

50. **The filter runs its LLM calls in parallel** (default 12 in flight,
    `MM_FILTER_WORKERS` to tune; owner directive — the model stays
    claude-sonnet-5, never a smaller tier: speed comes from concurrency, not
    downgrading judgment): posts are independent, the Anthropic client is
    thread-safe (max_retries=4 so bursts of 429s are absorbed by backoff),
    verdicts still commit per post in short transactions, and the
    stats/progress line updates under a lock. Stop semantics with workers:
    no NEW posts are dispatched once Stop is pressed; those already in
    flight complete and are saved. If a tight rate limit still fails posts,
    they stay unfiltered and the next run picks them up.

## Testing

25. The ~15 caption fixtures test the deterministic layers (@-tag extraction,
    CN-title mapping, cosmetics/fragrance policy signal, keep-policy
    annotations); LLM rubric behavior itself was smoke-tested live (real LV
    July posts: 3/3 correct keeps, correct celeb extraction, correct
    caption-stated BRAND AMBASSADOR for 王楚钦).
