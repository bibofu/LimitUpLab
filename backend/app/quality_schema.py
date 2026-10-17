"""Quality schema and atomic final-answer outbox helpers; no commits here."""

from datetime import datetime, timezone
import hashlib
import json
import math
import os
import time


DETECTOR_VERSION = "deterministic-v2"


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def answer_hash(response):
    return hashlib.sha256(str(response.get("answer", "")).encode("utf-8")).hexdigest()


def initialize_quality_schema(db):
    """Use individual statements to preserve a caller's active transaction."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
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
    initialize_evaluation_schema(db)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def default_config():
    raw = os.getenv("LIMITUPLAB_QUALITY_NORMAL_SAMPLE_RATE", "0.05")
    try:
        rate = float(raw)
        if not math.isfinite(rate) or not 0 <= rate <= 1:
            raise ValueError("invalid sampling rate")
        return {"normal_sample_rate": rate}
    except ValueError:
        # Invalid evaluator configuration must not prevent answer persistence.
        return {"normal_sample_rate": None, "configuration_error": "invalid_normal_sample_rate"}


def initialize_evaluation_schema(db):
    """Upgrade the v1 outbox atomically, preserving historical job identities."""
    db.execute("""CREATE TABLE IF NOT EXISTS quality_evidence_bundles (
        bundle_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
        answer_hash TEXT NOT NULL, content_hash TEXT NOT NULL, capture_mode TEXT NOT NULL,
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id,content_hash))""")
    if "evaluation_key" not in {row[1] for row in db.execute("PRAGMA table_info(quality_signals)")}:
        db.execute("ALTER TABLE quality_signals RENAME TO quality_signals_legacy_v1")
        db.execute("""CREATE TABLE quality_signals (
            signal_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES quality_cases(case_id) ON DELETE CASCADE,
            source TEXT NOT NULL, code TEXT NOT NULL, severity TEXT NOT NULL, summary TEXT NOT NULL,
            evidence_json TEXT NOT NULL, detector_version TEXT NOT NULL, answer_hash TEXT NOT NULL,
            created_at TEXT NOT NULL, evaluation_key TEXT NOT NULL DEFAULT '',
            UNIQUE(case_id,source,code,detector_version,answer_hash,evaluation_key))""")
        db.execute("""INSERT INTO quality_signals(signal_id,case_id,source,code,severity,summary,evidence_json,
            detector_version,answer_hash,created_at) SELECT * FROM quality_signals_legacy_v1""")
        db.execute("DROP TABLE quality_signals_legacy_v1")
    legacy = "job_kind" not in {row[1] for row in db.execute("PRAGMA table_info(quality_jobs)")}
    if legacy:
        db.execute("ALTER TABLE quality_jobs RENAME TO quality_jobs_legacy_v1")
        db.execute("""CREATE TABLE quality_jobs (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
            answer_hash TEXT NOT NULL, detector_version TEXT NOT NULL, job_kind TEXT NOT NULL DEFAULT 'deterministic',
            config_hash TEXT NOT NULL, config_json TEXT NOT NULL,
            bundle_id TEXT REFERENCES quality_evidence_bundles(bundle_id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3, available_at REAL NOT NULL,
            lease_token TEXT, lease_until REAL, worker_id TEXT, last_error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(job_kind,detector_version,config_hash,bundle_id))""")
        db.execute("""INSERT INTO quality_jobs
            (id,run_id,answer_hash,detector_version,config_hash,config_json,status,attempts,max_attempts,
             available_at,worker_id,last_error,created_at,updated_at)
            SELECT id,run_id,answer_hash,detector_version,?,'{}',
                CASE WHEN status='done' THEN 'done' ELSE 'superseded' END,
                attempts,max_attempts,available_at,worker_id,last_error,created_at,updated_at
            FROM quality_jobs_legacy_v1""", (canonical_hash({}),))
    db.execute("""CREATE TABLE IF NOT EXISTS quality_evaluations (
        evaluation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES quality_jobs(id) ON DELETE CASCADE,
        run_id TEXT NOT NULL REFERENCES react_runs(run_id) ON DELETE CASCADE,
        bundle_id TEXT REFERENCES quality_evidence_bundles(bundle_id) ON DELETE CASCADE,
        attempt INTEGER NOT NULL, evaluator_kind TEXT NOT NULL, evaluator_version TEXT NOT NULL,
        config_hash TEXT NOT NULL, answer_hash TEXT NOT NULL, execution_status TEXT NOT NULL,
        verdict TEXT NOT NULL DEFAULT 'unknown', report_json TEXT, error_type TEXT,
        started_at TEXT NOT NULL, finished_at TEXT, UNIQUE(job_id,attempt))""")
    if legacy:
        for job in db.execute("SELECT * FROM quality_jobs_legacy_v1").fetchall():
            report = {"evaluator_kind": "deterministic", "evaluator_version": job["detector_version"],
                      "execution_status": "skipped", "verdict": "unknown", "checks": [],
                      "metadata": {"reason": "legacy_report_unavailable" if job["status"] == "done" else "legacy_evaluator_superseded",
                                   "legacy_job_status": job["status"], "legacy_attempts": job["attempts"]}}
            db.execute("""INSERT INTO quality_evaluations
                (evaluation_id,job_id,run_id,attempt,evaluator_kind,evaluator_version,config_hash,answer_hash,
                 execution_status,report_json,started_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", ("legacy_" + job["id"], job["id"], job["run_id"],
                job["attempts"], "deterministic", job["detector_version"], canonical_hash({}), job["answer_hash"],
                "skipped", encode(report), job["created_at"], now()))
        db.execute("DROP TABLE quality_jobs_legacy_v1")
    db.execute("CREATE INDEX IF NOT EXISTS quality_jobs_ready ON quality_jobs(job_kind,status,available_at)")
    db.execute("CREATE INDEX IF NOT EXISTS quality_evaluations_run ON quality_evaluations(run_id,started_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS quality_budget_usage (
        job_kind TEXT NOT NULL, period TEXT NOT NULL, metric TEXT NOT NULL,
        reserved_units INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job_kind,period,metric))""")
    db.execute("""CREATE TABLE IF NOT EXISTS quality_budget_reservations (
        job_id TEXT NOT NULL REFERENCES quality_jobs(id) ON DELETE CASCADE, attempt INTEGER NOT NULL,
        reservation_key TEXT NOT NULL, job_kind TEXT NOT NULL, period TEXT NOT NULL,
        metric TEXT NOT NULL, units INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(job_id,attempt,reservation_key))""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS quality_bundles_no_update BEFORE UPDATE ON quality_evidence_bundles
        BEGIN SELECT RAISE(ABORT, 'Evidence bundle is immutable'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS quality_evaluations_terminal_no_update BEFORE UPDATE ON quality_evaluations
        WHEN OLD.execution_status != 'running' BEGIN SELECT RAISE(ABORT, 'Evaluation attempt is immutable'); END""")


def freeze_bundle(db, run_row, response, *, capture_mode="completion"):
    try:
        from app.services.quality_evidence import capture_evidence_bundle
        bundle = capture_evidence_bundle(db, run_row, response, capture_mode=capture_mode)
        hash_input = {**bundle, "manifest": {k: v for k, v in bundle["manifest"].items()
                                              if k not in {"captured_at", "material_hash"}}}
        digest = canonical_hash(hash_input)
        if digest != bundle["manifest"]["material_hash"]:
            raise ValueError("Evidence bundle content hash mismatch")
        serialized = encode(bundle)
    except Exception as error:
        # Diagnostic collection is optional; never lose an otherwise valid answer.
        # A failed scrubber must not force us to persist raw user/provider payloads.
        bundle = {"run_id": run_row["run_id"], **{key: None for key in (
            "request", "response", "checkpoint", "calls", "assistant_messages", "context",
            "diagnostic_snapshots", "model_calls", "delivery_events", "provenance", "renderings")},
            "manifest": {"schema_version": "quality-evidence-v1", "answer_hash": answer_hash(response),
                "captured_answer_hash": None, "capture_mode": capture_mode,
                "missing": [{"path": "", "reason": "evidence_capture_failed"}], "omissions": [],
                "capture_error_type": type(error).__name__, "delivery_scope": "unknown"}}
        digest = canonical_hash(bundle)
        bundle["manifest"].update(material_hash=digest, captured_at=now())
        serialized = encode(bundle)
    bundle_id = "bundle_" + digest
    db.execute("""INSERT OR IGNORE INTO quality_evidence_bundles
        (bundle_id,run_id,answer_hash,content_hash,capture_mode,payload_json,created_at) VALUES(?,?,?,?,?,?,?)""",
        (bundle_id, run_row["run_id"], answer_hash(response), digest, capture_mode, serialized, now()))
    return bundle_id


def enqueue_completed(db, run_row, response, detector_version=DETECTOR_VERSION, *,
                      job_kind="deterministic", config=None, bundle_id=None, capture_mode="completion"):
    """Freeze evidence and enqueue under the same transaction as the answer."""
    from app.services.quality_trace import scrub_private_data
    if job_kind not in {"deterministic", "llm_judge"}:
        raise ValueError("Unsupported evaluator kind")
    config = scrub_private_data(default_config() if config is None else config)
    config_hash = canonical_hash(config)
    bundle_id = bundle_id or freeze_bundle(db, run_row, response, capture_mode=capture_mode)
    bundle = db.execute("SELECT run_id,answer_hash FROM quality_evidence_bundles WHERE bundle_id=?", (bundle_id,)).fetchone()
    if bundle is None or bundle["run_id"] != run_row["run_id"] or bundle["answer_hash"] != answer_hash(response):
        raise ValueError("Evidence bundle does not belong to this completed answer")
    key = canonical_hash([job_kind, detector_version, config_hash, bundle_id])
    stamp = now()
    return db.execute("""INSERT OR IGNORE INTO quality_jobs
        (id,run_id,answer_hash,detector_version,job_kind,config_hash,config_json,bundle_id,available_at,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (key, run_row["run_id"], answer_hash(response), detector_version,
        job_kind, config_hash, encode(config), bundle_id, time.time(), stamp, stamp)).rowcount
