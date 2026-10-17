"""Quality schema and atomic final-answer outbox helpers; no commits here."""

from datetime import datetime, timezone
import hashlib
import json
import time


DETECTOR_VERSION = "deterministic-v1"


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def answer_hash(response):
    return hashlib.sha256(str(response.get("answer", "")).encode("utf-8")).hexdigest()


def initialize_quality_schema(db):
    """Use individual statements to preserve a caller's active transaction."""
    statements = [
        """CREATE TABLE IF NOT EXISTS quality_feedback (
            run_id TEXT PRIMARY KEY REFERENCES react_runs(run_id) ON DELETE CASCADE,
            owner_id TEXT NOT NULL, vote TEXT, reasons_json TEXT NOT NULL,
            comment TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS quality_delivery (
            run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
            answer_hash TEXT NOT NULL, event TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(run_id,answer_hash,event)
        )""",
        """CREATE TABLE IF NOT EXISTS quality_cases (
            case_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE REFERENCES react_runs(run_id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending', severity TEXT NOT NULL,
            symptom TEXT NOT NULL DEFAULT '', root_cause TEXT NOT NULL DEFAULT '',
            assignee TEXT NOT NULL DEFAULT '', fix_version TEXT NOT NULL DEFAULT '',
            duplicate_of TEXT REFERENCES quality_cases(case_id) ON DELETE SET NULL,
            version TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS quality_signals (
            signal_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES quality_cases(case_id) ON DELETE CASCADE,
            source TEXT NOT NULL, code TEXT NOT NULL, severity TEXT NOT NULL,
            summary TEXT NOT NULL, evidence_json TEXT NOT NULL, detector_version TEXT NOT NULL,
            answer_hash TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(case_id,source,code,detector_version,answer_hash)
        )""",
        """CREATE TABLE IF NOT EXISTS quality_reviews (
            review_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES quality_cases(case_id) ON DELETE CASCADE,
            actor_id TEXT NOT NULL, from_status TEXT NOT NULL, to_status TEXT NOT NULL,
            revision INTEGER NOT NULL, note TEXT NOT NULL, changes_json TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(case_id,revision)
        )""",
        """CREATE TABLE IF NOT EXISTS quality_jobs (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
            answer_hash TEXT NOT NULL, detector_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3, available_at REAL NOT NULL,
            lease_token TEXT, lease_until REAL, worker_id TEXT, last_error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(run_id,answer_hash,detector_version)
        )""",
        """CREATE TABLE IF NOT EXISTS quality_trace_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
            stage TEXT NOT NULL, captured_at TEXT NOT NULL, payload_json TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS quality_cases_status ON quality_cases(status,updated_at)",
        "CREATE INDEX IF NOT EXISTS quality_jobs_ready ON quality_jobs(status,available_at)",
        "CREATE INDEX IF NOT EXISTS quality_trace_run ON quality_trace_snapshots(run_id,id)",
        """CREATE TRIGGER IF NOT EXISTS quality_reviews_no_update BEFORE UPDATE ON quality_reviews
            BEGIN SELECT RAISE(ABORT, 'Review audit is append-only'); END""",
    ]
    for statement in statements:
        db.execute(statement)


def enqueue_completed(db, run_row, response, detector_version=DETECTOR_VERSION):
    """Outbox entry shares the answer's transaction and stable content identity."""
    digest = answer_hash(response)
    key = hashlib.sha256(encode([run_row["run_id"], digest, detector_version]).encode()).hexdigest()
    stamp = now()
    return db.execute("""INSERT OR IGNORE INTO quality_jobs
        (id,run_id,answer_hash,detector_version,available_at,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?)""", (key, run_row["run_id"], digest, detector_version,
                                  time.time(), stamp, stamp)).rowcount
