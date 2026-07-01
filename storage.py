"""SQLite-backed storage for Provenance Guard.

Two tables:
  * ``submissions``  — current state of each piece of content (keyed by content_id).
  * ``audit_log``    — append-only event log (classifications and appeals).

The audit log is the canonical, structured record graders rely on; ``GET /log`` surfaces it.
"""

import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "audit_log.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                content_id        TEXT PRIMARY KEY,
                creator_id        TEXT,
                text              TEXT,
                attribution       TEXT,
                ai_probability    REAL,
                confidence        REAL,
                llm_score         REAL,
                stylometric_score REAL,
                status            TEXT,
                created_at        TEXT,
                appeal_reasoning  TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id        TEXT,
                creator_id        TEXT,
                timestamp         TEXT,
                event_type        TEXT,   -- 'classification' | 'appeal'
                attribution       TEXT,
                ai_probability    REAL,
                confidence        REAL,
                llm_score         REAL,
                stylometric_score REAL,
                status            TEXT,
                detail            TEXT     -- JSON blob (rationale, appeal text, etc.)
            )
            """
        )


def _now():
    # ISO-8601 UTC with millisecond precision + trailing Z.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def record_classification(content_id, creator_id, text, result):
    """Persist a new submission's current state and append a classification audit event.

    ``result`` is the dict returned by scoring.score() enriched with label text and rationale.
    """
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO submissions
              (content_id, creator_id, text, attribution, ai_probability, confidence,
               llm_score, stylometric_score, status, created_at, appeal_reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                content_id, creator_id, text,
                result["attribution"], result["ai_probability"], result["confidence"],
                result["llm_score"], result["stylometric_score"],
                "classified", ts,
            ),
        )
        conn.execute(
            """
            INSERT INTO audit_log
              (content_id, creator_id, timestamp, event_type, attribution, ai_probability,
               confidence, llm_score, stylometric_score, status, detail)
            VALUES (?, ?, ?, 'classification', ?, ?, ?, ?, ?, 'classified', ?)
            """,
            (
                content_id, creator_id, ts,
                result["attribution"], result["ai_probability"], result["confidence"],
                result["llm_score"], result["stylometric_score"],
                json.dumps({
                    "llm_rationale": result.get("llm_rationale"),
                    "llm_available": result.get("llm_available"),
                    "stylometric_metrics": result.get("stylometric_metrics"),
                }),
            ),
        )
    return ts


def get_submission(content_id):
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM submissions WHERE content_id = ?", (content_id,)
        ).fetchone()
    return dict(row) if row else None


def record_appeal(content_id, creator_reasoning):
    """Flip a submission to 'under_review', store the reasoning, and log an appeal event.

    Returns the updated submission dict, or None if the content_id is unknown.
    """
    sub = get_submission(content_id)
    if sub is None:
        return None
    ts = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE submissions SET status = 'under_review', appeal_reasoning = ? "
            "WHERE content_id = ?",
            (creator_reasoning, content_id),
        )
        conn.execute(
            """
            INSERT INTO audit_log
              (content_id, creator_id, timestamp, event_type, attribution, ai_probability,
               confidence, llm_score, stylometric_score, status, detail)
            VALUES (?, ?, ?, 'appeal', ?, ?, ?, ?, ?, 'under_review', ?)
            """,
            (
                content_id, sub["creator_id"], ts,
                sub["attribution"], sub["ai_probability"], sub["confidence"],
                sub["llm_score"], sub["stylometric_score"],
                json.dumps({"appeal_reasoning": creator_reasoning}),
            ),
        )
    return get_submission(content_id)


def get_log(limit=50):
    """Return the most recent audit-log events, newest first, as plain dicts."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("detail"):
            try:
                d["detail"] = json.loads(d["detail"])
            except (ValueError, TypeError):
                pass
        out.append(d)
    return out


def get_appeals():
    """Reviewer queue: submissions currently under review."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM submissions WHERE status = 'under_review' "
            "ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
