"""Quality schema and atomic final-answer outbox helpers; no commits here."""

from datetime import datetime, timezone
import hashlib
import json
import math
import os

from app.database import begin_write, initialize_database
from app.repositories.storage_sql import database_clock


DETECTOR_VERSION = "deterministic-v2"


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def answer_hash(response):
    return hashlib.sha256(str(response.get("answer", "")).encode("utf-8")).hexdigest()


def initialize_quality_schema(db):
    """Check the Alembic schema without applying request-time DDL."""
    initialize_database(db)


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
    """Check the Alembic schema without applying request-time DDL."""
    initialize_database(db)


def freeze_bundle(db, run_row, response, *, capture_mode="completion"):
    begin_write(db)
    # A PostgreSQL statement failure aborts its transaction until rolled back.
    # Optional diagnostic capture must never poison final-answer persistence.
    db.execute("SAVEPOINT quality_evidence_capture")
    try:
        from app.services.quality_evidence import capture_evidence_bundle
        bundle = capture_evidence_bundle(db, run_row, response, capture_mode=capture_mode)
        hash_input = {**bundle, "manifest": {k: v for k, v in bundle["manifest"].items()
                                              if k not in {"captured_at", "material_hash"}}}
        digest = canonical_hash(hash_input)
        if digest != bundle["manifest"]["material_hash"]:
            raise ValueError("Evidence bundle content hash mismatch")
        serialized = encode(bundle)
        db.execute("RELEASE SAVEPOINT quality_evidence_capture")
    except Exception as error:
        db.execute("ROLLBACK TO SAVEPOINT quality_evidence_capture")
        db.execute("RELEASE SAVEPOINT quality_evidence_capture")
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
    db.execute("""INSERT INTO quality_evidence_bundles
        (bundle_id,run_id,answer_hash,content_hash,capture_mode,payload_json,created_at) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT DO NOTHING""",
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
    return db.execute("""INSERT INTO quality_jobs
        (id,run_id,answer_hash,detector_version,job_kind,config_hash,config_json,bundle_id,available_at,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""", (key, run_row["run_id"], answer_hash(response), detector_version,
        job_kind, config_hash, encode(config), bundle_id, database_clock(db), stamp, stamp)).rowcount
