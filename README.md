# Maison Monitor

Local pipeline that produces Dior's monthly China competitive-intelligence
PowerPoint — `_CREATIVE_{YYYY}_{MONTH}_PR_COMPETITOR_REPORT_FASHION.pptx` —
tracking **Chanel, Louis Vuitton, Tiffany & Co., Gucci, Fendi**.

Pipeline: TikHub ingestion of each brand's official **Weibo** account → LLM
relevance filter → human review in a local web **Console** → cross-platform
verification (Douyin / RED / WeChat) → celebrity & relationship enrichment →
project consolidation → PPTX rendered to match the reference deck.

## Launch

```bash
# 1. secrets (never committed)
cp .env.example .env        # fill in TIKHUB_API_KEY + ANTHROPIC_API_KEY

# 2. install
uv sync
uv run playwright install chromium   # for post screenshots / cards

# 3. go
uv run mm console            # opens http://127.0.0.1:8377
```

A full month is drivable entirely from the browser: **Start month** → confirm
accounts (first run only) → review posts → confirm → edit projects → render →
download the deck from the **Decks** tab.

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

## Notes

- **First-run account confirmation**: ingest refuses any account still marked
  `resolve` in `config/brands.yaml`. The Console's Runs page lists them with a
  one-click TikHub lookup; WeChat Channels IDs must be pasted from the WeChat
  app (not web-discoverable).
- **Media discipline**: every image is downloaded at ingest
  (`data/runs/{month}/{brand}/media/`); TikHub/CDN URLs expire and are never
  treated as source of truth. The PPTX embeds local files only.
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
