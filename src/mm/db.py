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
    Column("rationale", Text),                          # the model's detailed thinking
    Column("celebs_tagged", Text),                      # JSON list
    Column("category", String),
    Column("media_focus", String),
    Column("needs_review", Boolean, default=False),
    Column("human_decision", String),                   # keep|drop|None
    Column("decided_at", String),
    Column("decided_by", String),                       # actor display name
)

# Every human keep/drop/restore on a post, with what the LLM had said at the
# time — the training signal for the self-tuning filter prompt.
filter_feedback = Table(
    "filter_feedback", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("post_id", String, nullable=False),
    Column("month", String),
    Column("brand", String),
    Column("caption", Text),                            # snippet for the learner
    Column("llm_keep", Boolean),                        # None = no LLM verdict yet
    Column("llm_rationale", Text),
    Column("human_decision", String, nullable=False),   # keep|drop|restore
    Column("decided_by", String, nullable=False),
    Column("at", String, nullable=False),
)

# Learned filter guidance synthesized from filter_feedback; the newest row is
# appended to prompts/filter.md at run time. Append-only history.
learned_rules = Table(
    "learned_rules", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", String, nullable=False),
    Column("created_by", String, nullable=False),       # actor or "auto"
    Column("rules_md", Text, nullable=False),
    Column("summary", String),
    Column("feedback_through", Integer, nullable=False),  # last feedback id used
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
    Column("rationale", Text),                          # why these posts are one project
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
    Column("matched_post_id", String),                  # evidence post, when known
    Column("confidence", Float),
)

# cross-platform same-event judgments, cached per (reference, candidate) pair
# so re-running cross-check is consistent (a pair can never flip between
# runs) and free (no re-billing the match LLM for pairs already judged)
match_judgments = Table(
    "match_judgments", metadata,
    Column("ref_post_id", String, primary_key=True),
    Column("cand_post_id", String, primary_key=True),
    Column("same_event", Boolean),
    Column("confidence", Float),
    Column("reason", String),
    Column("at", String),
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
    Column("images_json", Text, default="[]"),          # [local paths] — celeb photo library
)

audit_log = Table(
    "audit_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("actor_name", String, nullable=False),
    Column("action", String, nullable=False),   # login|post_decision|confirm_posts|project_edit|…
    Column("entity_type", String),              # post|project|orphan|month|db|…
    Column("entity_id", String),
    Column("at", String, nullable=False),
)

# A month's "search" can be archived: rows are snapshotted as JSON (schema-
# proof, zero impact on live queries) and removed from the live tables so the
# next Start month repopulates from scratch. Media files stay on disk — a
# fresh ingest reuses them by URL hash for free.
archives = Table(
    "archives", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("month", String, nullable=False),
    Column("archived_at", String, nullable=False),
    Column("archived_by", String, nullable=False),
    Column("summary", Text, nullable=False, default="{}"),   # JSON {tbl: count}
)

archive_rows = Table(
    "archive_rows", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("archive_id", Integer, ForeignKey("archives.id"), nullable=False),
    Column("tbl", String, nullable=False),
    Column("row", Text, nullable=False),                     # full row as JSON
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
        _migrate(_engine)
    return _engine


def _migrate(engine: Engine) -> None:
    """Tiny additive migrations for pre-existing databases (create_all only
    creates missing tables, it never alters existing ones)."""
    with engine.begin() as c:
        cols = [r[1] for r in c.exec_driver_sql("PRAGMA table_info(verdicts)")]
        if cols and "decided_by" not in cols:
            c.exec_driver_sql("ALTER TABLE verdicts ADD COLUMN decided_by VARCHAR")
        if cols and "rationale" not in cols:
            c.exec_driver_sql("ALTER TABLE verdicts ADD COLUMN rationale TEXT")
        cols = [r[1] for r in c.exec_driver_sql("PRAGMA table_info(projects)")]
        if cols and "rationale" not in cols:
            c.exec_driver_sql("ALTER TABLE projects ADD COLUMN rationale TEXT")
        cols = [r[1] for r in c.exec_driver_sql("PRAGMA table_info(platform_matches)")]
        if cols and "matched_post_id" not in cols:
            c.exec_driver_sql(
                "ALTER TABLE platform_matches ADD COLUMN matched_post_id VARCHAR")
        cols = [r[1] for r in c.exec_driver_sql("PRAGMA table_info(celeb_registry)")]
        if cols and "images_json" not in cols:
            c.exec_driver_sql(
                "ALTER TABLE celeb_registry ADD COLUMN images_json TEXT DEFAULT '[]'")


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


def audit(conn, actor: str, action: str, entity_type: str | None = None,
          entity_id=None) -> None:
    """Attribution trail. `conn` may be a Connection or an Engine (opens its
    own short transaction), mirroring log_api_call."""
    stmt = audit_log.insert().values(
        actor_name=actor or "unknown", action=action, entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        at=now_iso())
    if isinstance(conn, Engine):
        with conn.begin() as c:
            c.execute(stmt)
    else:
        conn.execute(stmt)


def last_audit(conn, action: str, entity_type: str | None = None,
               entity_id=None) -> dict | None:
    q = select(audit_log).where(audit_log.c.action == action)
    if entity_type:
        q = q.where(audit_log.c.entity_type == entity_type)
    if entity_id is not None:
        q = q.where(audit_log.c.entity_id == str(entity_id))
    row = conn.execute(q.order_by(audit_log.c.id.desc()).limit(1)).mappings().first()
    return dict(row) if row else None


def archive_month(engine: Engine, month: str, actor: str) -> dict:
    """Snapshot a month's search into the archive store and clear the live
    tables, so the next Start month repopulates from scratch. One transaction:
    either the whole month moves or nothing does. Returns {tbl: count}."""
    with engine.begin() as conn:
        post_ids = [r[0] for r in conn.execute(
            select(posts.c.post_id).where(posts.c.month == month))]
        project_ids = [r[0] for r in conn.execute(
            select(projects.c.id).where(projects.c.month == month))]
        collected = {
            "posts": select(posts).where(posts.c.month == month),
            "verdicts": select(verdicts).where(verdicts.c.post_id.in_(post_ids)),
            "projects": select(projects).where(projects.c.month == month),
            "project_posts": select(project_posts)
                .where(project_posts.c.project_id.in_(project_ids)),
            "platform_matches": select(platform_matches)
                .where(platform_matches.c.project_id.in_(project_ids)),
            "orphans": select(orphans).where(orphans.c.month == month),
        }
        rows_by_tbl = {tbl: [dict(r) for r in conn.execute(q).mappings()]
                       for tbl, q in collected.items()}
        summary = {tbl: len(rows) for tbl, rows in rows_by_tbl.items()}
        if not any(summary.values()):
            return summary
        archive_id = conn.execute(archives.insert().values(
            month=month, archived_at=now_iso(), archived_by=actor or "unknown",
            summary=json.dumps(summary))).inserted_primary_key[0]
        conn.execute(archive_rows.insert(), [
            {"archive_id": archive_id, "tbl": tbl,
             "row": json.dumps(row, ensure_ascii=False, default=str)}
            for tbl, rows in rows_by_tbl.items() for row in rows])
        # delete children before parents (FKs)
        conn.execute(platform_matches.delete()
                     .where(platform_matches.c.project_id.in_(project_ids)))
        conn.execute(project_posts.delete()
                     .where(project_posts.c.project_id.in_(project_ids)))
        conn.execute(orphans.delete().where(orphans.c.month == month))
        from sqlalchemy import or_
        conn.execute(match_judgments.delete().where(or_(
            match_judgments.c.ref_post_id.in_(post_ids),
            match_judgments.c.cand_post_id.in_(post_ids))))
        conn.execute(verdicts.delete().where(verdicts.c.post_id.in_(post_ids)))
        conn.execute(projects.delete().where(projects.c.month == month))
        conn.execute(posts.delete().where(posts.c.month == month))
        conn.execute(runs.update().where(runs.c.month == month)
                     .values(phase_status="{}", updated_at=now_iso()))
        audit(conn, actor, "archive_month", "month", month)
    return summary


def list_archives(conn, month: str | None = None) -> list[dict]:
    q = select(archives)
    if month:
        q = q.where(archives.c.month == month)
    return [{**dict(r), "counts": json.loads(r["summary"] or "{}")}
            for r in conn.execute(q.order_by(archives.c.id.desc())).mappings()]


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
