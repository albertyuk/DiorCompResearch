"""Self-tuning filter loop.

Every human keep/drop/restore lands in `filter_feedback` together with what
the LLM had said. `synthesize_rules` distills the corrections into a short
learned-guidance block (prompts/learn.md) that filter_month appends to the
filter prompt — so the filter improves from review to review. The base rubric
in prompts/filter.md stays fixed; learned rules only refine it and are
replaced wholesale on each synthesis (append-only history in `learned_rules`).
"""
from __future__ import annotations

import json

from sqlalchemy import select

from . import db

FEEDBACK_BATCH = 200          # most recent corrections fed to the learner
RULES_CAP = 4000              # chars — a runaway rules block must not eat the prompt


def record_feedback(conn, post_row, verdict_row, decision: str, actor: str) -> None:
    """Append one human decision (keep|drop|restore) with the LLM's stance."""
    conn.execute(db.filter_feedback.insert().values(
        post_id=post_row["post_id"], month=post_row["month"],
        brand=post_row["brand"], caption=(post_row["caption"] or "")[:500],
        llm_keep=None if verdict_row is None else verdict_row["keep"],
        llm_rationale=None if verdict_row is None
                      else (verdict_row["rationale"] or ""),
        human_decision=decision, decided_by=actor, at=db.now_iso()))


def current_rules(conn) -> dict | None:
    row = conn.execute(select(db.learned_rules)
                       .order_by(db.learned_rules.c.id.desc())
                       .limit(1)).mappings().first()
    return dict(row) if row else None


def learned_rules_block(conn) -> str:
    """The text substituted into filter.md's {{learned_rules}} slot."""
    row = current_rules(conn)
    if not row or not (row["rules_md"] or "").strip():
        return ""
    return ("## Learned guidance (from past human corrections — follow "
            "unless it contradicts the rubric above)\n\n"
            + row["rules_md"].strip()[:RULES_CAP])


def new_feedback(conn, since_id: int) -> list[dict]:
    rows = conn.execute(
        select(db.filter_feedback)
        .where(db.filter_feedback.c.id > since_id)
        .order_by(db.filter_feedback.c.id.desc())
        .limit(FEEDBACK_BATCH)).mappings().all()
    return [dict(r) for r in rows]


def synthesize_rules(engine, llm, actor: str = "auto") -> dict | None:
    """Distill new corrections into a fresh learned-guidance block.
    Returns the stored row's summary info, or None when nothing new."""
    with engine.connect() as conn:
        cur = current_rules(conn)
        since = cur["feedback_through"] if cur else 0
        fresh = new_feedback(conn, since)
        if not fresh:
            return None
        last_id = max(f["id"] for f in fresh)
        lines = []
        for f in reversed(fresh):        # chronological for the learner
            llm_said = ("no verdict" if f["llm_keep"] is None
                        else ("keep" if f["llm_keep"] else "drop"))
            lines.append(
                f"- [{f['brand']}] llm said {llm_said} → human said "
                f"{f['human_decision']} (by {f['decided_by']})\n"
                f"  caption: {(f['caption'] or '')[:200]}\n"
                f"  llm rationale: {(f['llm_rationale'] or '—')[:200]}")
    out = llm.call_json("learn", {
        "current_rules": (cur["rules_md"] if cur else "") or "(none yet)",
        "feedback": "\n".join(lines),
    }, conn=engine, month=None)
    rules_md = str(out.get("rules_md") or "").strip()[:RULES_CAP]
    summary = str(out.get("summary") or "")[:300]
    with engine.begin() as conn:
        conn.execute(db.learned_rules.insert().values(
            created_at=db.now_iso(), created_by=actor, rules_md=rules_md,
            summary=summary, feedback_through=last_id))
        db.audit(conn, actor, "learned_rules_update", "filter",
                 f"{len(fresh)} corrections")
    return {"rules_md": rules_md, "summary": summary, "corrections": len(fresh)}
