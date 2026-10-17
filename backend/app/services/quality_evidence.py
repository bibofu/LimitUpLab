"""Freeze owner-scoped run evidence without fetching or reinterpreting market data."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math

from app.services.quality_trace import scrub_private_data


SCHEMA_VERSION = "quality-evidence-v1"
MAX_BUNDLE_BYTES = 4_000_000
MAX_SECTION_BYTES = 2_000_000


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=str)


def content_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _decode(raw, path, missing, *, expected=dict):
    if raw is None:
        missing.append({"path": path, "reason": "not_recorded"})
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else deepcopy(raw)
        if not isinstance(value, expected):
            raise ValueError("unexpected JSON type")
        return value
    except (ValueError, TypeError, RecursionError):
        missing.append({"path": path, "reason": "invalid_recorded_json"})
        return None


def capture_evidence_bundle(db, run_row, response, *, capture_mode="completion"):
    """Read in the caller's transaction; never commit, query providers, or cross owners."""
    row, missing = dict(run_row), []
    identity = db.execute("""SELECT 1 FROM react_runs r JOIN chat_sessions s ON s.session_id=r.session_id
        WHERE r.run_id=? AND r.session_id=? AND r.owner_id=? AND s.owner_id=r.owner_id""",
        (row["run_id"], row["session_id"], row["owner_id"])).fetchone()
    if identity is None:
        raise KeyError("Run session owner mismatch")
    calls = []
    for call in db.execute("SELECT * FROM react_calls WHERE run_id=? ORDER BY rowid", (row["run_id"],)):
        call = dict(call)
        path = "/calls/" + str(len(calls))
        signature = _decode(call.get("signature"), path + "/signature", missing, expected=list)
        name, arguments = None, None
        try:
            if signature and len(signature) == 2 and isinstance(signature[0], str):
                name, arguments = signature[0], dict(signature[1])
        except (TypeError, ValueError):
            pass
        if arguments is None:
            missing.append({"path": path + "/arguments", "reason": "invalid_call_signature"})
        calls.append({"call_id": call["call_id"], "name": name, "arguments": arguments,
                      "signature": signature,
                      "result": _decode(call.get("result_json"), path + "/result", missing,
                                        expected=(dict, list))})
    snapshots = []
    for snapshot in db.execute("SELECT * FROM quality_trace_snapshots WHERE run_id=? ORDER BY id", (row["run_id"],)):
        document = _decode(snapshot["payload_json"], "/diagnostic_snapshots/" + str(len(snapshots)), missing) or {}
        snapshots.append({**document, "id": snapshot["id"], "stage": snapshot["stage"],
                          "captured_at": snapshot["captured_at"]})
    def message(record):
        # Metadata can duplicate provider traces. Content/run association is sufficient here.
        return {key: record[key] for key in ("message_id", "run_id", "role", "content", "status", "created_at")}
    assistant = [message(record) for record in db.execute("""SELECT * FROM chat_messages
        WHERE session_id=? AND run_id=? AND role='assistant' ORDER BY rowid""",
        (row["session_id"], row["run_id"]))]
    context = [message(record) for record in db.execute("""SELECT m.* FROM chat_messages m
        LEFT JOIN react_runs r ON r.run_id=m.run_id
        WHERE m.session_id=? AND julianday(m.created_at)<julianday(?)
          AND (m.run_id IS NULL OR (m.run_id<>? AND r.owner_id=? AND r.session_id=m.session_id))
        ORDER BY julianday(m.created_at),m.rowid""",
        (row["session_id"], row["started_at"], row["run_id"], row["owner_id"]))]
    material = {"run_id": row["run_id"], "session_id": row["session_id"], "started_at": row["started_at"],
                "request": _decode(row.get("request_json"), "/request", missing),
                "response": response,
                "checkpoint": _decode(row.get("checkpoint_json"), "/checkpoint", missing),
                "calls": calls, "assistant_messages": assistant, "context": context,
                "diagnostic_snapshots": snapshots,
                "delivery_events": [dict(record) for record in db.execute(
                    "SELECT answer_hash,event,created_at FROM quality_delivery WHERE run_id=? ORDER BY created_at,event",
                    (row["run_id"],))], "missing": missing}
    return build_evidence_bundle(material, capture_mode=capture_mode)


def _model_calls(snapshots):
    """Correlate explicit attempt IDs, never infer receipt from round numbers."""
    calls = []
    for snapshot in snapshots:
        if snapshot.get("stage") != "model_input":
            continue
        payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
        call_id = payload.get("model_call_id")
        matches = [item for item in snapshots if call_id and isinstance(item.get("payload"), dict)
                   and item["payload"].get("model_call_id") == call_id]
        inputs = [item for item in matches if item.get("stage") == "model_input"]
        outputs = [item for item in matches if item.get("stage") == "model_output"]
        errors = [item for item in matches if item.get("stage") == "model_error"]
        returned = len(outputs) == 1 and outputs[0]["payload"].get("status") == "returned"
        input_complete = not snapshot.get("omissions") and isinstance(snapshot.get("payload"), dict)
        calls.append({"model_call_id": call_id, "input_snapshot_id": snapshot.get("id"),
                      "output_snapshot_ids": [item.get("id") for item in outputs],
                      "error_snapshot_ids": [item.get("id") for item in errors],
                      "input_capture_complete": input_complete,
                      "response_returned": returned,
                      "input_status": "response_linked" if call_id and len(inputs) == 1 and returned and not errors
                                      else "attempted_unconfirmed"})
    return calls


def _finite_values(value, path, omissions, depth=0):
    if depth > 30:
        omissions.append({"path": path, "reason": "bundle_depth_limit"})
        return None
    if isinstance(value, float) and not math.isfinite(value):
        omissions.append({"path": path, "reason": "non_finite_recorded_number"})
        return None
    if isinstance(value, dict):
        return {key: _finite_values(child, path + "/" + str(key), omissions, depth + 1) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_values(child, path + "/" + str(index), omissions, depth + 1) for index, child in enumerate(value)]
    return value


def _derived_omissions(snapshots, omissions):
    dependencies = {"provenance": {"provenance"}, "renderings": {"answer_rendering"},
                    "model_calls": {"model_input", "model_output", "model_error"}}
    affected = set()
    for item in omissions:
        path = item["path"]
        if path == "/diagnostic_snapshots":
            affected.update(dependencies)
        elif path.startswith("/diagnostic_snapshots/"):
            index = path.split("/")[2]
            if index.isdigit() and int(index) < len(snapshots):
                stage = snapshots[int(index)].get("stage")
                affected.update(key for key, stages in dependencies.items() if stage in stages)
    return [{"path": "/" + key, "reason": "source_diagnostic_omitted_or_redacted"} for key in sorted(affected)]


def build_evidence_bundle(material, *, capture_mode="completion"):
    """Pure material transformation; timestamp is metadata, excluded from content identity."""
    if capture_mode not in {"completion", "historical"}:
        raise ValueError("Unknown evidence capture mode")
    missing, omissions = list(material.get("missing", [])), []
    material = _finite_values(material, "", omissions)
    answer = (material.get("response") or {}).get("answer", "")
    answer_digest = hashlib.sha256(str(answer).encode("utf-8")).hexdigest()
    checkpoint = material.get("checkpoint")
    if isinstance(checkpoint, dict) and isinstance(checkpoint.get("state"), dict):
        # Serialized provider messages may contain private reasoning/headers. The separate
        # model_input snapshots are the intentionally bounded, credential-free view.
        if "messages" in checkpoint["state"]:
            del checkpoint["state"]["messages"]
            omissions.append({"path": "/checkpoint/state/messages", "reason": "private_provider_metadata_excluded"})
    keys = ("run_id", "session_id", "started_at", "request", "response", "checkpoint", "calls",
            "assistant_messages", "context", "diagnostic_snapshots", "delivery_events")
    body = {key: material.get(key) for key in keys}
    for key in keys:
        if key not in material or material[key] is None:
            missing.append({"path": "/" + key, "reason": "not_recorded"})
        cleaned = scrub_private_data(body[key])
        if canonical_json(cleaned) != canonical_json(body[key]):
            omissions.append({"path": "/" + key, "reason": "credential_redaction_or_depth_limit"})
        body[key] = cleaned
    snapshots = body.get("diagnostic_snapshots") or []
    for index, snapshot in enumerate(snapshots):
        for omitted in snapshot.get("omissions") or []:
            omissions.append({"path": f"/diagnostic_snapshots/{index}/payload",
                              "reason": "trace_capture_omission", "detail": omitted})
    omissions.extend(_derived_omissions(snapshots, omissions))
    body["model_calls"] = _model_calls(snapshots)
    body["provenance"] = [item["payload"] for item in snapshots if item.get("stage") == "provenance"
                          and isinstance(item.get("payload"), dict) and not item.get("omissions")]
    body["renderings"] = [item.get("payload") for item in snapshots if item.get("stage") == "answer_rendering"
                          and isinstance(item.get("payload"), dict) and not item.get("omissions")]
    for key in ("assistant_messages", "diagnostic_snapshots", "model_calls", "provenance", "renderings"):
        if not body.get(key):
            missing.append({"path": "/" + key, "reason": "not_recorded"})
    if capture_mode == "historical":
        missing.append({"path": "/manifest/capture_mode", "reason": "historical_material_not_captured_at_completion"})
    sizes = {key: len(canonical_json(value).encode("utf-8")) for key, value in body.items()}
    for key in sorted(sizes, key=sizes.get, reverse=True):
        if sizes[key] > MAX_SECTION_BYTES or len(canonical_json(body).encode("utf-8")) > MAX_BUNDLE_BYTES:
            omissions.append({"path": "/" + key, "reason": "bundle_size_limit",
                              "original_bytes": sizes[key], "sha256": content_hash(body[key])})
            body[key] = None
    # Derived views cannot hide that their source capture was omitted.
    if body["diagnostic_snapshots"] is None:
        body["model_calls"] = body["provenance"] = body["renderings"] = None
        missing.extend({"path": "/" + key, "reason": "diagnostic_source_omitted"}
                       for key in ("model_calls", "provenance", "renderings"))
    # De-duplicate metadata while keeping deterministic order.
    missing = list({canonical_json(item): item for item in missing}.values())
    manifest = {"schema_version": SCHEMA_VERSION, "answer_hash": answer_digest,
                "captured_answer_hash": hashlib.sha256(str((body.get("response") or {}).get("answer", "")).encode("utf-8")).hexdigest()
                                        if body.get("response") is not None else None,
                "capture_mode": capture_mode, "missing": missing, "omissions": omissions,
                "delivery_scope": "events_present_at_capture", "context_scope": "same_session_before_run_start",
                "model_input_scope": "react_generate_messages_attempts_only",
                "redaction_policy": "credentials-and-configured-secrets-v1"}
    manifest["material_hash"] = content_hash({**body, "manifest": manifest})
    manifest["captured_at"] = material.get("captured_at") or datetime.now(timezone.utc).isoformat()
    return {**body, "manifest": manifest}
