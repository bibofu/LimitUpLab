"""Private, bounded diagnostic snapshots; never part of the public answer trace."""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path


LOGGER = logging.getLogger(__name__)
MAX_SNAPSHOT_BYTES = 512_000
MAX_TEXT_CHARS = 64_000
SECRET_FIELDS = {"api_key", "apikey", "authorization", "cookie", "set-cookie",
                 "password", "secret", "access_token", "refresh_token", "credentials"}


def scrub_private_data(value):
    """Remove credential fields and configured secrets without rewriting answers."""
    secrets = [v for k, v in os.environ.items()
               if len(v) >= 8 and k.upper().endswith(("KEY", "SECRET", "TOKEN", "PASSWORD"))]

    def visit(item, depth=0):
        if depth > 30:
            return {"omitted": "depth_limit"}
        if isinstance(item, dict):
            return {str(key): "[redacted]" if str(key).lower().replace("-", "_") in {
                field.replace("-", "_") for field in SECRET_FIELDS
            } else visit(child, depth + 1) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [visit(child, depth + 1) for child in item]
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, "[redacted]")
            # ToolMessage content commonly contains a JSON object. Scrub its keys too.
            if item.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(item)
                    cleaned = visit(parsed, depth + 1)
                    return item if cleaned == parsed else json.dumps(cleaned, ensure_ascii=False)
                except (ValueError, RecursionError):
                    pass
            return item
        return item if item is None or isinstance(item, (int, float, bool)) else str(item)

    return visit(value)


def bounded_snapshot(payload):
    cleaned = scrub_private_data(payload)
    omissions = []

    def bound(value, path):
        if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
            omissions.append({"path": path, "original_chars": len(value),
                              "sha256": hashlib.sha256(value.encode()).hexdigest()})
            return value[:MAX_TEXT_CHARS]
        if isinstance(value, dict):
            return {key: bound(child, [*path, key]) for key, child in value.items()}
        if isinstance(value, list):
            return [bound(child, [*path, index]) for index, child in enumerate(value)]
        return value

    document = {"payload": bound(cleaned, []), "omissions": omissions,
                "capture_version": "quality-trace-v1"}
    serialized = json.dumps(document, ensure_ascii=False, default=str)
    if len(serialized.encode()) > MAX_SNAPSHOT_BYTES:
        document = {"payload": None, "capture_version": "quality-trace-v1", "omissions": [{
            "reason": "snapshot_size_limit", "original_bytes": len(serialized.encode()),
            "sha256": hashlib.sha256(serialized.encode()).hexdigest()}]}
        serialized = json.dumps(document)
    return serialized


@lru_cache(maxsize=1)
def source_fingerprint():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@lru_cache(maxsize=1)
def component_fingerprints():
    """Fingerprint replay code plus its local dependencies, never just the entry point."""
    root = Path(__file__).resolve().parents[1]
    shared = ["agents/react_runtime/evidence.py", "agents/react_runtime/contracts.py",
              "agents/kline_presentation.py", "agents/tool_schemas_market.py",
              "agents/tool_schemas_news.py", "agents/tool_schemas_research.py", "agents/tool_schemas_review.py"]
    components = {"compute_sha256": shared,
                  "renderer_sha256": shared + ["agents/react_runtime/rendering.py",
                      "agents/react_runtime/display_fields.py", "agents/react_runtime/task_contract.py"]}
    result = {}
    for name, paths in components.items():
        digest = hashlib.sha256()
        try:
            for relative in sorted(paths):
                digest.update(relative.encode())
                digest.update((root / relative).read_bytes())
            result[name] = digest.hexdigest()
        except OSError:
            result[name] = None
    return result


def provenance(provider, system_prompt, tool_contract_version):
    from app.agents.react_runtime.contracts import VERSION
    from app.agents.react_runtime.evidence import EVIDENCE_VERSION
    chat_model = getattr(provider, "chat_model", None)
    missing = ["scoring_version: consult individual tool evidence"]
    try:
        fingerprint = source_fingerprint()
    except OSError:
        fingerprint = None
        missing.append("code_sha256: source files unavailable")
    return {
        "code_sha256": fingerprint,
        "build_revision": os.getenv("LIMITUPLAB_BUILD_REVISION") or None,
        "model": getattr(provider, "model", None),
        "provider": type(provider).__name__,
        "base_temperature": getattr(chat_model, "temperature", None),
        "prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
        "tool_contract_version": tool_contract_version,
        "runtime_version": VERSION,
        "evidence_version": EVIDENCE_VERSION,
        **component_fingerprints(),
        "scoring_version": None,
        "missing": missing,
    }


def rendering_snapshot(final, store, output_contract, rendered_answer, *, origin):
    return {"finish_input": final.model_dump(mode="json"),
            "output_contract": output_contract.model_dump(mode="json"),
            "rendered_answer": rendered_answer, "origin": origin,
            "renderer_sha256": component_fingerprints()["renderer_sha256"],
            "evidence_rank_scopes": store._rank_scopes,
            "evidence_ranked_prefixes": store._ranked_prefixes}


def record_snapshot(control, stage, payload):
    """Diagnostics must not turn a successful query into an execution failure."""
    if control is None:
        return
    try:
        serialized = bounded_snapshot(payload() if callable(payload) else payload)
        with control.journal.connection() as db:
            db.execute("INSERT INTO quality_trace_snapshots(run_id,stage,captured_at,payload_json) VALUES(?,?,?,?)",
                       (control.row["run_id"], stage, datetime.now(timezone.utc).isoformat(), serialized))
    except Exception as error:
        # Never log payloads or exception text: either can contain user/provider secrets.
        LOGGER.warning("quality_snapshot_failed stage=%s error=%s", stage, type(error).__name__)


def visible_messages(messages):
    """Capture messages/tools, excluding provider credentials and hidden reasoning."""
    return [{"role": message.type, "content": message.content,
             "name": getattr(message, "name", None),
             "tool_call_id": getattr(message, "tool_call_id", None),
             "tool_calls": getattr(message, "tool_calls", [])} for message in messages]


def prune_trace_snapshots(db, retention_days=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    return db.execute("DELETE FROM quality_trace_snapshots WHERE captured_at < ?", (cutoff,)).rowcount
