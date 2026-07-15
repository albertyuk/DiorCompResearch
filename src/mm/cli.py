"""mm — Maison Monitor CLI. `mm run --month 2026-07` walks all phases, pausing
at the two review checkpoints with the Console URL."""
from __future__ import annotations

import json

import typer

from . import db, pipeline
from .config import BrandsConfig, Settings, ensure_dirs
from .dates import previous_month

app = typer.Typer(help="Maison Monitor — Dior China competitive-intelligence deck pipeline",
                  no_args_is_help=True, pretty_exceptions_enable=False)


def _month_opt(month: str | None) -> str:
    return month or previous_month()


def _echo(obj) -> None:
    typer.echo(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


@app.command()
def console(host: str = "127.0.0.1", port: int = 8377,
            open_browser: bool = typer.Option(True, "--open/--no-open")):
    """Launch the local web Console."""
    import uvicorn
    from .console import create_app
    ensure_dirs()
    url = f"http://{host}:{port}"
    typer.echo(f"Maison Monitor Console → {url}")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@app.command()
def run(month: str = typer.Option(None, help="YYYY-MM; default = previous month"),
        console_url: str = "http://127.0.0.1:8377"):
    """Walk all phases, pausing at the review checkpoints."""
    month = _month_opt(month)
    st = pipeline.status(month)["phases"]
    if st.get("ingest") != "done":
        typer.echo(f"[{month}] ingest…")
        _echo(pipeline.run_ingest(month))
    if st.get("filter") != "done":
        typer.echo(f"[{month}] LLM filter…")
        _echo(pipeline.run_filter(month))
    st = pipeline.status(month)["phases"]
    if st.get("review_posts") != "confirmed":
        typer.echo(f"\n[{month}] ⏸  Review checkpoint #1 — confirm posts in the "
                   f"Console:\n    {console_url}/review/{month}/posts\n"
                   f"    (run `mm console` if it isn't running, then re-run "
                   f"`mm run --month {month}`)")
        raise typer.Exit(0)
    if st.get("crosscheck") != "done":
        typer.echo(f"[{month}] cross-platform verification…")
        _echo(pipeline.run_crosscheck(month))
    if st.get("enrich") != "done":
        typer.echo(f"[{month}] enrichment…")
        _echo(pipeline.run_enrich(month))
    st = pipeline.status(month)["phases"]
    if st.get("review_projects") != "confirmed":
        typer.echo(f"\n[{month}] ⏸  Review checkpoint #2 — confirm projects in "
                   f"the Console:\n    {console_url}/review/{month}/projects")
        raise typer.Exit(0)
    typer.echo(f"[{month}] render…")
    _echo(pipeline.run_render(month))
    typer.echo(f"[{month}] cost summary:")
    costs(month)


@app.command()
def resolve():
    """List unresolved accounts (confirmation happens in the Console)."""
    cfg = BrandsConfig.load()
    _echo(pipeline.run_resolve_check(cfg))


@app.command()
def ingest(month: str = typer.Option(None), brand: str = typer.Option(None)):
    month = _month_opt(month)
    _echo(pipeline.run_ingest(month, [brand] if brand else None))


@app.command("filter")
def filter_cmd(month: str = typer.Option(None), brand: str = typer.Option(None)):
    month = _month_opt(month)
    _echo(pipeline.run_filter(month, [brand] if brand else None))


@app.command()
def crosscheck(month: str = typer.Option(None), brand: str = typer.Option(None)):
    month = _month_opt(month)
    _echo(pipeline.run_crosscheck(month, [brand] if brand else None))


@app.command()
def enrich(month: str = typer.Option(None), brand: str = typer.Option(None)):
    month = _month_opt(month)
    _echo(pipeline.run_enrich(month, [brand] if brand else None))


@app.command()
def render(month: str = typer.Option(None),
           visuals: str = typer.Option("live", help="live | card"),
           fixtures: bool = typer.Option(False, help="render fixtures/projects.json"),
           drafts: bool = typer.Option(False, help="include draft projects")):
    """Render the deck (+ companion xlsx) and run the QA loop."""
    if fixtures:
        from pathlib import Path
        from .config import FIXTURES_DIR, OUTPUT_DIR
        from .render.deck import DeckBuilder, load_spec
        from .render.qa import run_qa
        from .render.xlsx import write_projects_xlsx
        spec = json.loads((FIXTURES_DIR / "projects.json").read_text())
        brands = load_spec(spec)
        m = spec.get("month", "2026-07")
        from .dates import deck_month_token
        name = (f"_CREATIVE_{m[:4]}_{deck_month_token(m)}"
                f"_PR_COMPETITOR_REPORT_FASHION.pptx")
        out = DeckBuilder().build(brands, OUTPUT_DIR / name)
        write_projects_xlsx(brands, OUTPUT_DIR / f"{m}_projects.xlsx")
        _echo(run_qa(out, OUTPUT_DIR / f"{m}_qa"))
        return
    month = _month_opt(month)
    _echo(pipeline.run_render(month, visuals_mode=visuals, include_drafts=drafts))


@app.command()
def status(month: str = typer.Option(None)):
    _echo(pipeline.status(_month_opt(month)))


@app.command()
def costs(month: str = typer.Option(None)):
    """Per-run cost summary (TikHub calls + LLM tokens)."""
    month = _month_opt(month)
    engine = db.get_engine()
    with engine.connect() as conn:
        summary = db.cost_summary(conn, month)
    typer.echo(f"— {month} —")
    for r in sorted(summary["rows"], key=lambda r: -(r["cost"] or 0)):
        typer.echo(f"  {r['kind']:<9} {r['endpoint']:<55} n={r['n']:<4} "
                   f"${(r['cost'] or 0):.4f}"
                   + (f"  in/out tokens {r['tin']}/{r['tout']}"
                      if r["kind"] == "anthropic" else ""))
    typer.echo(f"  TOTAL ≈ ${summary['total_usd']:.4f}")


registry_app = typer.Typer(help="Celebrity registry")
app.add_typer(registry_app, name="registry")


@registry_app.command("export")
def registry_export_cmd(out: str = typer.Option(None, help="output JSON path")):
    from .enrich import registry_export
    engine = db.get_engine()
    with engine.connect() as conn:
        data = registry_export(conn)
    if out:
        from pathlib import Path
        Path(out).write_text(json.dumps(data, indent=1, ensure_ascii=False),
                             encoding="utf-8")
        typer.echo(f"wrote {len(data)} celebs → {out}")
    else:
        _echo(data)


@app.command()
def smoke(brand: str = "lv", days: int = 7,
          month: str = typer.Option(None, help="month to test (default: current "
                                    "month — its posts sit on the first pages)")):
    """One-brand/one-week end-to-end smoke: ingest a few pages, filter, print costs.
    Uses TikHub free credits; does not render."""
    from datetime import datetime
    from . import filtering
    from .dates import CST
    from .ingest import ingest_weibo
    from .llm import LLM
    from .tikhub import TikHubClient
    month = month or datetime.now(CST).strftime("%Y-%m")
    cfg = BrandsConfig.load()
    settings = Settings.load()
    client = TikHubClient(settings)
    missing = client.verify_endpoints()
    if missing:
        typer.echo(f"⚠ endpoints missing from live spec: {missing}")
    engine = db.get_engine()
    result = ingest_weibo(engine, client, cfg, month, brand, max_pages=3)
    typer.echo(f"ingest: {result}")
    llm = LLM(settings)
    from sqlalchemy import select
    with engine.connect() as conn:
        rows = list(conn.execute(
            select(db.posts.c.post_id).where(db.posts.c.month == month,
                                             db.posts.c.brand == brand)
            .limit(1000)).scalars())
    typer.echo(f"posts in db for {brand}/{month}: {len(rows)}")
    stats = filtering.filter_month(engine, llm, cfg, month, brand)
    typer.echo(f"filter: {stats}")
    client.close()
    costs(month)


if __name__ == "__main__":
    app()
