"""Build a bounded blind view from one immutable, owner-scoped evidence bundle."""

from copy import deepcopy

from app.quality_judge_models import JUDGE_INPUT_VERSION
from app.services.quality_evidence import SCHEMA_VERSION, canonical_json, content_hash
from app.services.quality_trace import scrub_private_data


POLICY = "仅做数据研究、解释与风险提示；不得输出买入、卖出、仓位、目标价、收益承诺或确定性预测。"
MAX_REFERENCES = 320
MAX_VALUE_CHARS = 5000


def _pointer(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def build_judge_input(bundle, *, max_input_chars=60000):
    """No feedback/review/diagnostic prose enters this allow-listed input."""
    if type(max_input_chars) is not int or not 4000 <= max_input_chars <= 240000:
        raise ValueError("max_input_chars must be between 4000 and 240000")
    manifest = bundle.get("manifest") or {}
    request, response = bundle.get("request") or {}, bundle.get("response") or {}
    question, answer = request.get("message"), response.get("answer")
    view = {"schema_version": JUDGE_INPUT_VERSION, "evidence_catalog": [],
            "limitations": [], "omitted_reference_count": 0,
            "capture_mode": manifest.get("capture_mode"),
            "scope": "Only supplied records; no external fact verification. References prove presence, not truth."}
    reason = None
    if manifest.get("schema_version") != SCHEMA_VERSION:
        reason = "unsupported_evidence_schema"
    elif not isinstance(question, str) or not question.strip():
        reason = "missing_user_request"
    elif not isinstance(answer, str) or not answer.strip():
        reason = "missing_final_answer"
    catalog = view["evidence_catalog"]
    omissions = view["limitations"]
    used = len(canonical_json(view))
    # Keep room for bounded omission metadata. Required texts are never cut halfway.
    remaining = max_input_chars - used - 1800
    sequence = 0

    def omit(path, cause):
        view["omitted_reference_count"] += 1
        if len(omissions) < 12:
            omissions.append({"path": str(path)[:100], "reason": cause})

    def add(kind, path, value, *, required=False):
        nonlocal remaining, sequence
        sequence += 1
        entry = {"ref_id": f"ref_{sequence:04d}", "kind": kind, "path": path, "value": value}
        size = len(canonical_json(entry)) + 1
        if len(catalog) >= MAX_REFERENCES or size > remaining or (not required and size > MAX_VALUE_CHARS):
            omit(path, "judge_input_limit")
            return False
        catalog.append(entry)
        remaining -= size
        return True

    if reason is None:
        if not add("requirement", "/request/message", question, required=True):
            reason = "request_exceeds_input_limit"
        if not add("answer", "/response/answer", answer, required=True):
            reason = "answer_exceeds_input_limit"
    add("policy", "/judge_policy/investment_boundary", POLICY, required=True)
    # Scrub again to cover credentials configured after the frozen bundle was captured.
    original_core = canonical_json(catalog)
    view["evidence_catalog"] = catalog = scrub_private_data(catalog)
    if canonical_json(catalog) != original_core:
        omit("/request_or_answer", "credential_redaction")
    for item in [*manifest.get("missing", []), *manifest.get("omissions", [])]:
        if isinstance(item, dict):
            path = str(item.get("path", ""))
            if path.startswith(("/request", "/response", "/context", "/checkpoint/runtime/evidence", "/calls")) or path == "/checkpoint":
                omit(path, str(item.get("reason", "bundle_omission"))[:70])
    if reason is None:
        context = bundle.get("context") or []
        if len(context) > 12:
            omit("/context", "older_context_omitted")
        for index, item in list(enumerate(context))[-12:]:
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"}:
                add("context_" + item["role"], f"/context/{index}/content", item.get("content", ""))
        for key in ("task_status", "stop_reason", "data_missing"):
            if key in response:
                add("execution", "/response/" + key, response[key])
        checkpoint = bundle.get("checkpoint") or {}
        runtime = checkpoint.get("runtime") or {}
        records = runtime.get("evidence") or {}
        finish = (checkpoint.get("state") or {}).get("finish") or {}
        selected = finish.get("evidence_ids") or []
        keys = list(dict.fromkeys([key for key in selected if isinstance(key, str)] + sorted(records)))

        def walk(value, path, kind):
            # Bounded traversal avoids spending time enumerating a multi-megabyte tail.
            if len(catalog) >= MAX_REFERENCES or remaining < 150:
                omit(path, "judge_input_limit")
                return
            if isinstance(value, dict) and value:
                for key in sorted(value):
                    child_kind = "tool_name" if key == "tool" else "tool_arguments" if key == "arguments" else kind
                    walk(value[key], path + "/" + _pointer(key), child_kind)
                    if len(catalog) >= MAX_REFERENCES or remaining < 150:
                        omit(path, "remaining_child_records_omitted")
                        break
            elif isinstance(value, list) and value:
                for index, child in enumerate(value):
                    walk(child, path + "/" + str(index), kind)
                    if len(catalog) >= MAX_REFERENCES or remaining < 150:
                        omit(path, "remaining_child_records_omitted")
                        break
            else:
                add(kind, path, value)

        for key in keys:
            record = records.get(key)
            if not isinstance(record, dict):
                omit("/checkpoint/runtime/evidence/" + _pointer(key), "invalid_evidence_record")
                continue
            # This is a visible record, not a replay. Preserve its schema and scope as evidence.
            cleaned_record = scrub_private_data(record)
            if cleaned_record != record:
                omit("/checkpoint/runtime/evidence/" + _pointer(key), "credential_redaction")
            walk(cleaned_record, "/checkpoint/runtime/evidence/" + _pointer(key), "evidence")
        for index, call in enumerate(bundle.get("calls") or []):
            if isinstance(call, dict):
                cleaned_call = scrub_private_data(call)
                if cleaned_call != call:
                    omit(f"/calls/{index}", "credential_redaction")
                for key, kind in (("name", "tool_name"), ("arguments", "tool_arguments"), ("result", "tool_result")):
                    if key in cleaned_call:
                        walk(cleaned_call[key], f"/calls/{index}/{key}", kind)
    cleaned = scrub_private_data(view)
    if cleaned != view:
        omit("/evidence_catalog", "credential_redaction")
        cleaned = scrub_private_data(view)
    # Deepcopy isolates the reusable frozen bundle from downstream caller mutation.
    cleaned = deepcopy(cleaned)
    if len(canonical_json(cleaned)) > max_input_chars:
        # A malicious/invalid manifest must not bypass the input bound.
        cleaned = {"schema_version": JUDGE_INPUT_VERSION, "evidence_catalog": [],
                   "limitations": [{"reason": "input_metadata_limit"}], "omitted_reference_count": 1}
        reason = "input_metadata_limit"
    return {"view": cleaned, "input_hash": content_hash(cleaned), "skip_reason": reason,
            "answer_hash": manifest.get("answer_hash"), "bundle_material_hash": manifest.get("material_hash")}
