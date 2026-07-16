# Maison Monitor

Local pipeline that produces Dior's monthly China competitive-intelligence
PowerPoint — `_CREATIVE_{YYYY}_{MONTH}_PR_COMPETITOR_REPORT_FASHION.pptx` —
tracking **Chanel, Louis Vuitton, Tiffany & Co., Gucci, Fendi**.

Pipeline: TikHub ingestion of each brand's official **Weibo** account → LLM
relevance filter → human review in a local web **Console** → cross-platform
verification (Douyin / RED / WeChat) → celebrity & relationship enrichment →
project consolidation → PPTX rendered to match the reference deck.

The filter's core test: **China-market-specific → keep; global campaign with
no China angle → drop.** Perfume and makeup/skincare are always out of scope.
Every keep/drop a reviewer makes is catalogued (with the model's own
rationale) on the **Learning** page and periodically distilled into learned
guidance appended to the filter prompt — the filter gets more accurate with
every review. **Archives** keeps browsable snapshots of past searches.

## Launch (Mac, zero setup)

1. Get the folder — either clone this repo, or on GitHub click
   **Code → Download ZIP** and unzip it.
2. **Double-click `start.command`.** The first time, macOS may block it:
   right-click the file → **Open** → **Open**. First run installs everything
   (a few minutes) and asks you to paste the team's `ANTHROPIC_API_KEY` once
   (get it from Albert / the team password manager) — it's remembered after
   that. After the first run it starts in seconds.
3. Your browser opens the Console at `http://127.0.0.1:8377`. Leave the
   Terminal window open while you work; close it (or Ctrl-C) to stop.

The TikHub key is already included (`config/default.env`, usage-limited team
key — rotate if repo access changes; GitHub push protection prevents
committing the Anthropic key, hence the one-time paste). A `.env` in the repo
root overrides any default.

<details><summary>Manual launch (any OS)</summary>

```bash
uv sync
uv run playwright install chromium   # for post screenshots / cards
uv run mm console                    # opens http://127.0.0.1:8377
```
</details>

A full month is drivable entirely from the browser: **Start month** → confirm
accounts (first run only) → review posts → confirm → edit projects → render →
download the deck from the **Decks** tab. The Runs page shows a live RUNNING/
idle indicator, a per-step activity log, and a **Stop** button (pauses at the
next safe point; Start month resumes). Re-running a month adds to it —
decisions persist; **Archive & reset** moves the whole month into the archive
and the next Start searches from scratch. Brands ingest in parallel.

Equivalent CLI: `uv run mm run --month 2026-07` (pauses at the two review
checkpoints with the Console URL). Also: `mm resolve`, `mm ingest`,
`mm filter`, `mm crosscheck`, `mm enrich`, `mm render`, `mm status`,
`mm costs`, `mm registry export`, `mm smoke`.

## Layout

```
config/brands.yaml     brand accounts (Phase R writes confirmed IDs back here)
prompts/*.md           LLM prompts — edit freely, loaded at runtime
template/              reference decks + extracted icons/logos
src/mm/                pipeline, renderer, console, CLI
fixtures/              hand-written July mimic for template-fidelity testing
data/                  runs, media, SQLite (gitignored)
output/                decks + xlsx + QA rasters (gitignored)
docs/                  template_map.md (measured geometry) · ASSUMPTIONS.md
```

## Hosted Console (Fly.io)

The Console can run at one stable URL for the whole team.

**Deploy (owner, one time):**

```bash
fly auth login          # once
uv run mm deploy        # creates app + 10GB volume + secrets, then deploys
```

`mm deploy` reads `TIKHUB_API_KEY` / `ANTHROPIC_API_KEY` from your `.env`,
generates `CONSOLE_PASSPHRASE` and `MM_SECRET_KEY` if missing (into `.env`),
and sets them all with `fly secrets set`. Re-run `mm deploy` any time to ship
updates. Runs in `hkg` (mainland-reachable without an ICP filing); flip
`primary_region` in `fly.toml` to `sjc`/`iad` if the team is US-based.

**Coworker onboarding:** open the URL → enter the team passphrase and your
name → review as usual. Your name is attached to every decision (audit trail).
The passphrase is shared out-of-band — never alongside the URL.

**Rotating access (someone leaves the team):** change `CONSOLE_PASSPHRASE` in
your `.env` and re-run `mm deploy` — secrets are re-synced on every deploy,
and all existing sessions are bound to the passphrase, so every logged-in
browser is signed out the moment the new passphrase is live.

**Weibo screenshots on hosted:** datacenter IPs get friction from m.weibo.cn,
so hosted renders default to post-cards. For live screenshots, run on any
laptop:

```bash
uv run mm screenshots --month 2026-07 --push https://maison-monitor.fly.dev
```

then render (or re-render) from the Console — pushed shots automatically
replace the cards for matching posts.

**If a mainland-China coworker can't reach the `*.fly.dev` URL:** shared
platform domains are common Great Firewall collateral. First fix to try —
put the app on a custom subdomain:

```bash
fly certs add monitor.<yourdomain>     # then add the CNAME it prints
```

**Backups:** Fly snapshots the volume daily. On top of that, the Decks page
has **Download database backup** — the SQLite DB (decisions, projects, celeb
registry, audit trail) is the unrecoverable part; media is re-fetchable and
decks re-renderable.

**Expected hosting cost:** ~$12/month (≈$10.70 always-on 1×shared-cpu/2GB
machine + ≈$1.50 for the 10GB volume). Always-on is deliberate — background
pipeline phases hold no HTTP connection, so scale-to-zero would kill ingests
mid-run.

## Notes

- **First-run account confirmation**: ingest refuses any account still marked
  `resolve` in `config/brands.yaml`. The Console's Runs page lists them with a
  one-click TikHub lookup; WeChat Channels IDs must be pasted from the WeChat
  app (not web-discoverable).
- **Media discipline**: every image is downloaded at ingest — at the largest
  resolution weibo offers — into `data/runs/{month}/{brand}/media/`;
  TikHub/CDN URLs expire and are never treated as source of truth. The PPTX
  embeds local files only. In Review · Posts, tick the checkbox on the images
  you want on the slide (none ticked → automatic post-card), and use each
  post's drop zone to upload a manually-downloaded original when the scraped
  file isn't good enough.
- **WeChat is the weakest column by design** (Channels has no keyword search;
  MP matching is title/date-based) — fix it at review checkpoint #2.
- **Costs**: `mm costs --month …` prints per-endpoint TikHub calls and LLM
  token spend. A full 5-brand month lands in the low single-digit dollars
  (Xiaohongshu App V2 calls at ~$0.01 dominate). The ~50 free TikHub requests
  cover smoke tests only (`mm smoke --brand lv`).
- **Remote access is out of scope.** The Console binds to localhost and holds
  competitor intelligence and celebrity imagery. If remote access is ever
  genuinely needed, put it behind a Cloudflare Access / Tailscale tunnel with
  a token gate — never expose the bare port.
- Tests: `uv run pytest`.
