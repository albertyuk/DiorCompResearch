"""SQLite schema and access via SQLAlchemy Core. Boring by design.

Idempotency: natural keys everywhere (post_id, (month, brand, title), …) with
sqlite upserts, so re-running any phase never duplicates rows.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, Float, ForeignKey, Integer, MetaData,
                        String, Table, Text, UniqueConstraint, create_engine, select)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from .config import DB_PATH, ensure_dirs

metadata = MetaData()

runs = Table(
    "runs", metadata,
    Column("month", String, primary_key=True),          # 'YYYY-MM'
    Column("phase_status", Text, nullable=False, default="{}"),  # JSON {phase: status}
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

posts = Table(
    "posts", metadata,
    Column("post_id", String, primary_key=True),        # '<platform>:<native id>'
    Column("month", String, nullable=False),
    Column("brand", String, nullable=False),
    Column("platform", String, nullable=False),         # weibo|douyin|xhs|wechat_mp|wechat_channels
    Column("url", String),
    Column("created_at", String),                       # ISO8601, CST
    Column("caption", Text),
    Column("at_tags", Text),                            # JSON list of handles
    Column("hashtags", Text),                           # JSON list
    Column("media", Text),                              # JSON [{kind, local_path, width, height, is_cover}]
    Column("is_repost", Boolean, default=False),
    Column("repost_ambiguous", Boolean, default=False),
    Column("author_name", String),
    Column("author_avatar_path", String),
    Column("raw_path", String),                         # archived raw API JSON on disk
)

verdicts = Table(
    "verdicts", metadata,
    Column("post_id", String, ForeignKey("posts.post_id"), primary_key=True),
    Column("keep", Boolean),
    Column("confidence", Float),
    Column("reasons", Text),                            # JSON list
    Column("celebs_tagged", Text),                      # JSON list
    Column("category", String),
    Column("media_focus", String),
    Column("needs_review", Boolean, default=False),
    Column("human_decision", String),                   # keep|drop|None
    Column("decided_at", String),
)

projects = Table(
    "projects", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("month", String, nullable=False),
    Column("brand", String, nullable=False),
    Column("title", String, nullable=False),            # canonical, ALL CAPS, no suffix
    Column("phase_suffix", String),                     # TEASER|CELEBS|... or NULL
    Column("date_start", String),                       # ISO date
    Column("date_end", String),
    Column("ongoing", Boolean, default=False),
    Column("assets", String),                           # PHOTO|VIDEO|PHOTO VIDEO
    Column("description", String),                      # PROJECT cell line
    Column("celebs", Text),                             # JSON [{name_cn,name_en,relation,verified,occupation}]
    Column("hero_media", Text),                         # JSON [local paths]
    Column("status", String, default="draft"),          # draft|confirmed|rendered
    UniqueConstraint("month", "brand", "title", "phase_suffix", name="uq_project"),
)

project_posts = Table(
    "project_posts", metadata,
    Column("project_id", Integer, ForeignKey("projects.id"), primary_key=True),
    Column("post_id", String, ForeignKey("posts.post_id"), primary_key=True),
    Column("role", String, default="member"),           # member|hero
)

platform_matches = Table(
    "platform_matches", metadata,
    Column("project_id", Integer, ForeignKey("projects.id"), primary_key=True),
    Column("platform", String, primary_key=True),       # weibo|xhs|douyin|wechat_mp|wechat_channels
    Column("present", Boolean, default=False),
    Column("matched_url", String),
    Column("matched_date", String),
    Column("confidence", Float),
)

orphans = Table(
    "orphans", metadata,
    Column("post_id", String, ForeignKey("posts.post_id"), primary_key=True),
    Column("month", String, nullable=False),
    Column("resolution", String, default="pending"),    # pending|promoted|ignored
)

celeb_registry = Table(
    "celeb_registry", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name_cn", String, unique=True),
    Column("name_en", String),
    Column("occupation", String),
    Column("relations_json", Text, default="{}"),       # {brand_key: {relation, raw_cn_title, verified, source_url, date}}
)

api_calls = Table(
    "api_calls", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", String, nullable=False),
    Column("kind", String, nullable=False),             # tikhub|anthropic
    Column("endpoint", String, nullable=False),
    Column("brand", String),
    Column("month", String),
    Column("status", Integer),
    Column("ok", Boolean, default=True),
    Column("cost_usd", Float, default=0.0),
    Column("tokens_in", Integer, default=0),
    Column("tokens_out", Integer, default=0),
)

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        ensure_dirs()
        # WAL + generous busy timeout: the console (reads + small writes) and
        # background phase threads share this file; readers must never block
        # on a writer.
        _engine = create_engine(f"sqlite:///{DB_PATH}", future=True,
                                connect_args={"timeout": 60})
        from sqlalchemy import event

        @event.listens_for(_engine, "connect")
        def _set_wal(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        metadata.create_all(_engine)
    return _engine


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert(conn, table: Table, values: dict, key_cols: list[str],
           no_update_cols: list[str] | None = None) -> None:
    stmt = sqlite_insert(table).values(**values)
    skip = set(key_cols) | set(no_update_cols or [])
    update_cols = {c: stmt.excluded[c] for c in values if c not in skip}
    if update_cols:
        stmt = stmt.on_conflict_do_update(index_elements=key_cols, set_=update_cols)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=key_cols)
    conn.execute(stmt)


# --- runs / phase status -----------------------------------------------------

PHASES = ["resolve", "ingest", "filter", "review_posts", "crosscheck",
          "enrich", "review_projects", "render"]


def get_run(conn, month: str) -> dict:
    row = conn.execute(select(runs).where(runs.c.month == month)).mappings().first()
    if row is None:
        values = {"month": month, "phase_status": "{}",
                  "created_at": now_iso(), "updated_at": now_iso()}
        upsert(conn, runs, values, ["month"])
        return {**values, "phases": {}}
    return {**dict(row), "phases": json.loads(row["phase_status"] or "{}")}


def set_phase(conn, month: str, phase: str, status: str) -> None:
    run = get_run(conn, month)
    phases = run["phases"]
    phases[phase] = status
    conn.execute(runs.update().where(runs.c.month == month)
                 .values(phase_status=json.dumps(phases), updated_at=now_iso()))


def log_api_call(conn, kind: str, endpoint: str, *, brand: str | None = None,
                 month: str | None = None, status: int | None = None,
                 ok: bool = True, cost_usd: float = 0.0,
                 tokens_in: int = 0, tokens_out: int = 0) -> None:
    """`conn` may be a Connection (joins its transaction) or an Engine
    (opens its own short transaction) — so API-call logging never forces a
    caller to hold a write transaction across network calls."""
    stmt = api_calls.insert().values(
        ts=now_iso(), kind=kind, endpoint=endpoint, brand=brand, month=month,
        status=status, ok=ok, cost_usd=cost_usd,
        tokens_in=tokens_in, tokens_out=tokens_out)
    if isinstance(conn, Engine):
        with conn.begin() as c:
            c.execute(stmt)
    else:
        conn.execute(stmt)


def cost_summary(conn, month: str | None = None) -> dict:
    from sqlalchemy import func
    q = select(api_calls.c.kind, api_calls.c.endpoint,
               func.count().label("n"),
               func.sum(api_calls.c.cost_usd).label("cost"),
               func.sum(api_calls.c.tokens_in).label("tin"),
               func.sum(api_calls.c.tokens_out).label("tout"))
    if month:
        q = q.where(api_calls.c.month == month)
    q = q.where(api_calls.c.ok.is_(True)).group_by(api_calls.c.kind, api_calls.c.endpoint)
    rows = [dict(r) for r in conn.execute(q).mappings()]
    total = sum(r["cost"] or 0 for r in rows)
    return {"rows": rows, "total_usd": round(total, 4)}
