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


def _install_exit_backstop(grace: float = 20.0) -> None:
    """Hosted only: a stop signal (deploy, restart) must never wait out
    fly.toml's kill_timeout on a wedged worker thread — after `grace`
    seconds of graceful shutdown, exit hard. Observed 2026-07-21: a render
    thread stuck in a C-level HEIC decode blocked interpreter exit, so the
    machine swap served nothing for the full 5-minute kill_timeout."""
    import os
    import threading
    import uvicorn

    orig = uvicorn.Server.handle_exit

    def handle_exit(self, sig, frame):
        t = threading.Timer(grace, os._exit, args=(0,))
        t.daemon = True
        t.start()
        return orig(self, sig, frame)

    uvicorn.Server.handle_exit = handle_exit


@app.command()
def console(host: str = typer.Option(None, help="default: 127.0.0.1 local, "
                                     "0.0.0.0 hosted"),
            port: int = typer.Option(None, help="default: 8377 local, 8080 hosted"),
            open_browser: bool = typer.Option(True, "--open/--no-open")):
    """Launch the web Console (binding is MM_ENV-driven)."""
    import uvicorn
    from .config import IS_HOSTED
    from .console import create_app
    ensure_dirs()
    if IS_HOSTED:
        _install_exit_backstop()
    host = host or ("0.0.0.0" if IS_HOSTED else "127.0.0.1")
    port = port or (8080 if IS_HOSTED else 8377)
    url = f"http://{host}:{port}"
    typer.echo(f"Maison Monitor Console → {url}")
    if open_browser and not IS_HOSTED:
        import threading
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=host, port=port,
                log_level="info" if IS_HOSTED else "warning")


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
    _echo(pipeline.run_render(month, progress=typer.echo))
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
           visuals: str = typer.Option(None, help="live | card "
                                       "(default: live local, card hosted)"),
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
    _echo(pipeline.run_render(month, visuals_mode=visuals,
                              include_drafts=drafts, progress=typer.echo))


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


@app.command()
def screenshots(month: str = typer.Option(..., help="YYYY-MM"),
                push: str = typer.Option(..., help="hosted Console URL, e.g. "
                                         "https://maison-monitor.fly.dev"),
                passphrase: str = typer.Option(None, help="team passphrase "
                                               "(default: CONSOLE_PASSPHRASE)")):
    """Capture live Weibo screenshots on THIS machine and push them to a hosted
    Console (datacenter IPs get friction from m.weibo.cn; laptops don't).
    Pushed shots replace the card images for matching posts at render time."""
    import os
    from urllib.parse import quote

    import httpx

    from .config import load_env
    from .render.visuals import VisualFactory

    load_env()
    passphrase = passphrase or os.environ.get("CONSOLE_PASSPHRASE", "")
    if not passphrase:
        raise typer.BadParameter("no passphrase — pass --passphrase or set "
                                 "CONSOLE_PASSPHRASE in .env")
    base = push.rstrip("/")
    headers = {"Authorization": f"Bearer {passphrase}"}
    r = httpx.get(f"{base}/api/screenshots/{month}/manifest",
                  headers=headers, timeout=60)
    if r.status_code == 401:
        typer.echo("✗ rejected — wrong passphrase")
        raise typer.Exit(1)
    r.raise_for_status()
    posts = r.json().get("posts", [])
    typer.echo(f"{len(posts)} weibo posts for {month} on {base}")
    ok = failed = 0
    with VisualFactory(month, mode="live") as vf:
        for p in posts:
            shot = vf.live_screenshot(p["brand"], {"post_id": p["post_id"],
                                                   "url": p["url"]})
            if shot is None:
                failed += 1
                typer.echo(f"  ✗ capture {p['post_id']} (its card stays)")
                continue
            resp = httpx.post(
                f"{base}/api/screenshots/{month}/{quote(p['post_id'], safe='')}",
                headers={**headers, "Content-Type": "image/png"},
                content=shot.read_bytes(), timeout=120)
            if resp.status_code == 200:
                ok += 1
                typer.echo(f"  ✓ {p['post_id']}")
            else:
                failed += 1
                typer.echo(f"  ✗ upload {p['post_id']}: {resp.status_code} "
                           f"{resp.text[:120]}")
    typer.echo(f"pushed {ok} live screenshots, {failed} kept their cards")


@app.command()
def deploy():
    """Deploy the hosted Console to Fly.io. Idempotent on every run: ensures
    the app exists, the 10GB volume exists, and SYNCS all secrets from .env
    (names echoed, never values — so rotating CONSOLE_PASSPHRASE in .env and
    re-running `mm deploy` really rotates it), then `fly deploy`."""
    import json as _json
    import os
    import re
    import secrets as pysecrets
    import shutil
    import subprocess

    from .config import ROOT, load_env

    fly = shutil.which("fly") or shutil.which("flyctl")
    if not fly:
        typer.echo("flyctl is not installed → https://fly.io/docs/flyctl/install/")
        raise typer.Exit(1)
    load_env()
    env = dict(os.environ)

    # authenticated? (fly auth login session or FLY_API_TOKEN from .env)
    who = subprocess.run([fly, "auth", "whoami"], env=env, capture_output=True)
    if who.returncode != 0 and not env.get("FLY_API_TOKEN"):
        typer.echo("Not logged in to Fly. Run:\n  fly auth login\nthen\n  mm deploy")
        raise typer.Exit(1)

    # fail fast on required API keys — BEFORE creating anything on Fly
    missing = [k for k in ("TIKHUB_API_KEY", "ANTHROPIC_API_KEY")
               if not env.get(k)]
    if missing:
        typer.echo(f"missing in .env: {', '.join(missing)} — add them, then "
                   f"re-run `mm deploy`")
        raise typer.Exit(1)

    # ensure console secrets exist locally (generated once, echoed by NAME only)
    env_path = ROOT / ".env"
    generated = []

    def ensure(key: str, gen):
        if not env.get(key):
            value = gen()
            with env_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{key}={value}\n")
            env[key] = value
            generated.append(key)

    ensure("MM_SECRET_KEY", lambda: pysecrets.token_hex(32))
    ensure("CONSOLE_PASSPHRASE",
           lambda: "-".join(pysecrets.token_hex(3) for _ in range(3)))
    if generated:
        typer.echo(f"generated into .env: {', '.join(generated)} (values withheld "
                   f"— the passphrase is in your .env; share it out-of-band)")

    toml_path = ROOT / "fly.toml"
    toml = toml_path.read_text(encoding="utf-8")
    app_name = re.search(r'^app\s*=\s*"([^"]+)"', toml, re.M).group(1)
    region_m = re.search(r'^primary_region\s*=\s*"([^"]+)"', toml, re.M)
    region = region_m.group(1) if region_m else "hkg"

    # validate the region against what THIS Fly account can actually use —
    # some regions (e.g. hkg) are unavailable to new accounts. Preference
    # order keeps the mainland-China-adjacency rationale: HK → SG → Tokyo.
    regions_r = subprocess.run([fly, "platform", "regions", "--json"], env=env,
                               capture_output=True, text=True)
    available: set[str] = set()
    if regions_r.returncode == 0:
        try:
            for item in _json.loads(regions_r.stdout or "null") or []:
                if isinstance(item, dict):
                    code = item.get("Code") or item.get("code")
                    if code:
                        available.add(code)
        except ValueError:
            pass
    if available and region not in available:
        fallback = next((r for r in ("hkg", "sin", "nrt", "sjc", "iad")
                         if r in available), sorted(available)[0])
        typer.echo(f"region {region!r} isn't available on this Fly account — "
                   f"using {fallback!r} instead (regions you can use: "
                   f"{', '.join(sorted(available))}; edit primary_region in "
                   f"fly.toml to change)")
        toml = re.sub(r'^primary_region\s*=\s*"[^"]+"',
                      f'primary_region = "{fallback}"', toml, flags=re.M)
        toml_path.write_text(toml, encoding="utf-8")
        region = fallback

    # existence via the account's app list — never confuse an API hiccup or a
    # name owned by someone else with "doesn't exist yet"
    lst = subprocess.run([fly, "apps", "list", "--json"], env=env,
                         capture_output=True, text=True)
    if lst.returncode != 0:
        typer.echo(f"couldn't reach the Fly API (fly apps list failed):\n"
                   f"{(lst.stderr or '').strip()[:300]}\nNothing was changed — "
                   f"re-run `mm deploy` when it's back.")
        raise typer.Exit(1)
    try:
        # a brand-new Fly account yields the literal JSON `null`
        apps_json = _json.loads(lst.stdout or "null") or []
    except ValueError:
        apps_json = []
    my_apps = {(a.get("Name") or a.get("name"))
               for a in apps_json if isinstance(a, dict)}

    if app_name not in my_apps:
        name = None
        for candidate in (app_name, f"{app_name}-{pysecrets.token_hex(2)}"):
            r = subprocess.run([fly, "apps", "create", candidate], env=env,
                               capture_output=True, text=True)
            if r.returncode == 0:
                name = candidate
                break
            typer.echo(f"  app name {candidate!r} unavailable "
                       f"({(r.stderr or '').strip()[:120]})")
        if name is None:
            raise typer.Exit(1)
        if name != app_name:
            toml_path.write_text(toml.replace(f'app = "{app_name}"',
                                              f'app = "{name}"'),
                                 encoding="utf-8")
            app_name = name
        typer.echo(f"created app {app_name}")

    # volume: ensured on EVERY run, so an interrupted first bootstrap heals
    vols = subprocess.run([fly, "volumes", "list", "-a", app_name, "--json"],
                          env=env, capture_output=True, text=True)
    have_volume = vols.returncode == 0 and '"mm_data"' in (vols.stdout or "")
    if not have_volume:
        # try the configured region first, then the preference chain —
        # `fly platform regions` isn't a guarantee, and a "region not found"
        # from the create call means this account can't use it regardless
        chain = [region] + [r for r in ("hkg", "sin", "nrt", "sjc", "iad")
                            if r != region]
        created, err = False, ""
        for cand in chain:
            typer.echo(f"creating 10GB volume mm_data in {cand}…")
            vol = subprocess.run([fly, "volumes", "create", "mm_data",
                                  "--region", cand, "--size", "10",
                                  "-a", app_name, "--yes"],
                                 env=env, capture_output=True, text=True)
            if vol.returncode == 0:
                created = True
                if cand != region:
                    typer.echo(f"volume landed in {cand!r} ({region!r} was "
                               f"rejected by Fly) — updating primary_region "
                               f"in fly.toml to match, so the machine deploys "
                               f"next to its volume")
                    toml = toml_path.read_text(encoding="utf-8")
                    toml = re.sub(r'^primary_region\s*=\s*"[^"]+"',
                                  f'primary_region = "{cand}"', toml, flags=re.M)
                    toml_path.write_text(toml, encoding="utf-8")
                    region = cand
                break
            err = (vol.stderr or vol.stdout or "").strip()
            if "not found" not in err.lower():
                break  # not a region problem — don't shotgun other regions
        if not created:
            typer.echo(f"volume creation failed:\n{err[:300]}\n"
                       f"Fix the cause and re-run `mm deploy`; every step is "
                       f"safe to repeat.")
            raise typer.Exit(1)

    # secrets: synced on EVERY run, via stdin (never argv → never in the
    # process list or a traceback)
    secret_names = ["TIKHUB_API_KEY", "ANTHROPIC_API_KEY",
                    "CONSOLE_PASSPHRASE", "MM_SECRET_KEY"]
    typer.echo(f"syncing fly secrets: {', '.join(secret_names)}, MM_ENV")
    payload = "".join(f"{n}={env[n]}\n" for n in secret_names) + "MM_ENV=hosted\n"
    imp = subprocess.run([fly, "secrets", "import", "--stage", "-a", app_name],
                         env=env, input=payload, text=True, capture_output=True)
    if imp.returncode != 0:
        typer.echo(f"fly secrets import failed:\n{(imp.stderr or '').strip()[:300]}")
        raise typer.Exit(1)

    typer.echo("deploying…")
    subprocess.run([fly, "deploy", "-a", app_name], check=True, env=env)
    typer.echo(f"\nlive → https://{app_name}.fly.dev")


if __name__ == "__main__":
    app()
