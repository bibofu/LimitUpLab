"""Durable feedback, review audit and leased deterministic quality jobs."""

from contextlib import contextmanager
import json
from pathlib import Path
from uuid import uuid4

from app.database import begin_write, connect
from app.quality_models import DeliveryInput, DetectionSignal, FeedbackInput, QualityConflict, ReviewInput
from app.quality_schema import DETECTOR_VERSION, answer_hash, encode, now
from app.repositories.quality_evaluation_repository import EvaluationRepositoryMixin
from app.repositories.quality_judge_repository import JudgeRepositoryMixin
from app.repositories.storage_sql import insertion_order
from app.services.quality_trace import scrub_private_data


TRANSITIONS = {
    "pending": {"confirmed", "duplicate", "not_a_bug", "insufficient_evidence"},
    "confirmed": {"pending", "in_progress", "verifying", "duplicate", "not_a_bug", "insufficient_evidence"},
    "in_progress": {"pending", "confirmed", "verifying", "duplicate", "not_a_bug", "insufficient_evidence"},
    "verifying": {"in_progress", "confirmed", "closed"},
    "closed": {"pending", "confirmed"},
    "duplicate": {"pending"}, "not_a_bug": {"pending"}, "insufficient_evidence": {"pending"},
}
SEVERITIES = {name: rank for rank, name in enumerate(("low", "medium", "high", "critical"))}


def decoded(row, fields):
    result = dict(row)
    for field in fields:
        raw = result.pop(field + "_json", None)
        result[field] = json.loads(raw) if raw else None
    return result


def statuses(row, response, case_status):
    task_status = (response or {}).get("task_status")
    execution = "running" if row["active"] else "cancelled" if row["cancelled"] or task_status == "cancelled" else "failed" if task_status == "error" else "succeeded"
    quality = "fail" if case_status in {"confirmed", "in_progress", "verifying", "closed"} else "pass" if case_status == "not_a_bug" else "unreviewed" if case_status == "pending" else "unknown"
    return {"execution_status": execution, "task_status": task_status, "quality_status": quality}


class QualityRepository(JudgeRepositoryMixin, EvaluationRepositoryMixin):
    def __init__(self, path=None):
        from app.agents.react_runtime.lifecycle import Journal
        self.path = Path(path) if path else None
        Journal(self.path)

    @contextmanager
    def connection(self):
        db = connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _owned_run(self, db, run_id, owner_id):
        row = db.execute("SELECT * FROM react_runs WHERE run_id=? AND owner_id=?", (run_id, owner_id)).fetchone()
        if row is None:
            raise KeyError("Run not found")
        if not row["response_json"]:
            raise QualityConflict("回答尚未完成，暂不能提交反馈或交付回执")
        return row, json.loads(row["response_json"])

    def _signal(self, db, run_id, response, signal, *, evaluation_key=""):
        signal = DetectionSignal.model_validate(signal).model_dump()
        case = db.execute("SELECT * FROM quality_cases WHERE run_id=?", (run_id,)).fetchone()
        stamp = now()
        if case is None:
            version = response.get("quality_context", {}).get("code_version") or response.get("generated_by") or "unknown"
            provenance = db.execute("SELECT payload_json FROM quality_trace_snapshots WHERE run_id=? AND stage='provenance' ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
            if provenance:
                captured = json.loads(provenance[0]).get("payload") or {}
                version = captured.get("build_revision") or captured.get("code_sha256") or version
            case_id = "case_" + uuid4().hex
            db.execute("""INSERT INTO quality_cases(case_id,run_id,severity,version,created_at,updated_at)
                VALUES(?,?,?,?,?,?)""", (case_id, run_id, signal["severity"], version, stamp, stamp))
        else:
            case_id = case["case_id"]
        inserted = db.execute("""INSERT INTO quality_signals
            (signal_id,case_id,source,code,severity,summary,evidence_json,detector_version,answer_hash,created_at,evaluation_key)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""", ("sig_" + uuid4().hex, case_id, signal["source"], signal["code"],
            signal["severity"], scrub_private_data(signal["summary"]), encode(scrub_private_data(signal["evidence"])), signal["detector_version"],
            answer_hash(response), stamp, evaluation_key)).rowcount
        if inserted:
            db.execute("UPDATE quality_cases SET updated_at=? WHERE case_id=?", (stamp, case_id))
            reviewed = db.execute("SELECT 1 FROM quality_reviews WHERE case_id=? LIMIT 1", (case_id,)).fetchone()
            if case and not reviewed and case["status"] == "pending" and SEVERITIES[signal["severity"]] > SEVERITIES[case["severity"]]:
                db.execute("UPDATE quality_cases SET severity=?,revision=revision+1 WHERE case_id=?", (signal["severity"], case_id))
        return case_id

    def save_feedback(self, run_id, owner_id, payload):
        data = scrub_private_data(FeedbackInput.model_validate(payload).model_dump())
        with self.connection() as db:
            begin_write(db)
            _, response = self._owned_run(db, run_id, owner_id)
            stamp = now()
            db.execute("""INSERT INTO quality_feedback(run_id,owner_id,vote,reasons_json,comment,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET vote=excluded.vote,
                reasons_json=excluded.reasons_json,comment=excluded.comment,updated_at=excluded.updated_at""",
                (run_id, owner_id, data["vote"], encode(data["reasons"]), data["comment"], stamp, stamp))
            if data["vote"] == "down":
                self._signal(db, run_id, response, {"source": "feedback", "code": "downvote", "severity": "medium",
                    "summary": "用户反馈回答有问题，等待人工复核", "evidence": data, "detector_version": "feedback-v1"})
            return decoded(db.execute("SELECT * FROM quality_feedback WHERE run_id=?", (run_id,)).fetchone(), ["reasons"])

    def _delivery_status(self, db, run_id, digest):
        events = {r[0] for r in db.execute("""SELECT event FROM quality_delivery WHERE run_id=?
            AND (answer_hash=? OR (answer_hash='' AND event='interrupted'))""", (run_id, digest))}
        return next((state for state in ("displayed", "received", "interrupted") if state in events), "unknown")

    def session_state(self, session_id, owner_id):
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM chat_sessions WHERE session_id=? AND owner_id=?", (session_id, owner_id)).fetchone():
                raise KeyError("Session not found")
            states = {}
            for row in db.execute("SELECT run_id,response_json FROM react_runs WHERE session_id=? AND owner_id=?", (session_id, owner_id)):
                digest = answer_hash(json.loads(row["response_json"])) if row["response_json"] else None
                feedback = db.execute("SELECT * FROM quality_feedback WHERE run_id=?", (row["run_id"],)).fetchone()
                states[row["run_id"]] = {"feedback": decoded(feedback, ["reasons"]) if feedback else None,
                    "answer_hash": digest, "delivery_status": self._delivery_status(db, row["run_id"], digest)}
            return {"runs": states}

    def record_delivery(self, run_id, owner_id, event, answer_hash):
        data = DeliveryInput(event=event, answer_hash=answer_hash)
        with self.connection() as db:
            begin_write(db)
            row = db.execute("SELECT * FROM react_runs WHERE run_id=? AND owner_id=?", (run_id, owner_id)).fetchone()
            if row is None:
                raise KeyError("Run not found")
            response = json.loads(row["response_json"]) if row["response_json"] else None
            from app.quality_schema import answer_hash as digest_of
            digest = digest_of(response) if response else None
            if data.answer_hash is not None and data.answer_hash != digest:
                raise QualityConflict("回执与已保存的答案版本不匹配")
            # Before completion, the client knows only the accepted run identity.
            # Empty hash is reserved internally for its transport interruption.
            db.execute("INSERT INTO quality_delivery(run_id,answer_hash,event,created_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                       (run_id, data.answer_hash or "", data.event, now()))
            delivery_status = self._delivery_status(db, run_id, digest)
            if (event == "interrupted" and response and response.get("task_status") != "cancelled"
                    and delivery_status == "interrupted"):
                self._signal(db, run_id, response, {"source": "delivery", "code": "interrupted", "severity": "low",
                    "summary": "客户端报告回答交付中断，需结合后续回执复核", "evidence": {
                        "event": event, "status": "unknown", "quality_verdict": "unknown",
                        "scope": "answer" if data.answer_hash else "run",
                    }, "detector_version": DETECTOR_VERSION})
            return {"run_id": run_id, "answer_hash": data.answer_hash, "delivery_status": delivery_status}

    def list_cases(self, *, status=None, source=None, severity=None, symptom=None, version=None, limit=50, offset=0):
        conditions, params = [], []
        for field, value in (("status", status), ("severity", severity), ("symptom", symptom), ("version", version)):
            if value:
                conditions.append(f"c.{field}=?")
                params.append(value)
        if source:
            conditions.append("EXISTS(SELECT 1 FROM quality_signals s WHERE s.case_id=c.case_id AND s.source=?)")
            params.append(source)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as db:
            total = db.execute("SELECT count(*) FROM quality_cases c" + where, params).fetchone()[0]
            rows = db.execute("""SELECT c.*,r.started_at,r.request_json,r.response_json,r.active,r.cancelled,
                (SELECT group_concat(DISTINCT source) FROM quality_signals s WHERE s.case_id=c.case_id) AS sources,
                (SELECT count(*) FROM quality_signals s WHERE s.case_id=c.case_id) AS signal_count
                FROM quality_cases c JOIN react_runs r ON r.run_id=c.run_id""" + where +
                " ORDER BY c.updated_at DESC,c.case_id LIMIT ? OFFSET ?", (*params, max(1, min(limit, 100)), max(0, offset)))
            items = []
            for row in rows:
                item = decoded(row, ["request", "response"])
                item["question"] = item.pop("request").get("message", "")
                response = item.pop("response")
                item.update(statuses(row, response, item["status"]))
                item["delivery_status"] = self._delivery_status(db, item["run_id"], answer_hash(response))
                item["sources"] = item["sources"].split(",") if item["sources"] else []
                items.append(item)
            return scrub_private_data({"items": items, "total": total})

    def _run_material(self, db, row):
        run = decoded(row, ["request", "response", "checkpoint"])
        run["calls"] = [decoded(r, ["result"]) for r in db.execute(f"SELECT * FROM react_calls WHERE run_id=? ORDER BY {insertion_order(db)}", (row["run_id"],))]
        run["trace_snapshots"] = [decoded(r, ["payload"]) for r in db.execute("SELECT * FROM quality_trace_snapshots WHERE run_id=? ORDER BY id", (row["run_id"],))]
        run["delivery_events"] = [dict(r) for r in db.execute("SELECT * FROM quality_delivery WHERE run_id=? ORDER BY created_at", (row["run_id"],))]
        return run

    def case_detail(self, case_id):
        with self.connection() as db:
            case = db.execute("SELECT * FROM quality_cases WHERE case_id=?", (case_id,)).fetchone()
            if case is None:
                raise KeyError("Case not found")
            row = db.execute("SELECT * FROM react_runs WHERE run_id=?", (case["run_id"],)).fetchone()
            run = self._run_material(db, row)
            run.update(statuses(row, run["response"], case["status"]))
            run["context"] = [dict(r) for r in db.execute(f"""SELECT role,content,created_at FROM chat_messages
                WHERE session_id=? AND created_at<=? ORDER BY created_at DESC,{insertion_order(db)} DESC LIMIT 30""", (row["session_id"], row["started_at"]))][::-1]
            feedback = db.execute("SELECT * FROM quality_feedback WHERE run_id=?", (case["run_id"],)).fetchone()
            run["feedback"] = decoded(feedback, ["reasons"]) if feedback else None
            run["delivery_status"] = self._delivery_status(db, case["run_id"], answer_hash(run["response"]))
            last_review = db.execute("SELECT max(created_at) FROM quality_reviews WHERE case_id=?", (case_id,)).fetchone()[0]
            new_signals = bool(last_review and db.execute("SELECT 1 FROM quality_signals WHERE case_id=? AND created_at>? LIMIT 1", (case_id, last_review)).fetchone())
            from app.repositories.quality_evaluation_repository import _unpack
            evaluations = [_unpack(r) for r in db.execute("SELECT * FROM quality_evaluations WHERE run_id=? ORDER BY started_at DESC LIMIT 20", (case["run_id"],))]
            evaluation_count = db.execute("SELECT count(*) FROM quality_evaluations WHERE run_id=?", (case["run_id"],)).fetchone()[0]
            return scrub_private_data({"case": {**dict(case), "has_new_signals_since_review": new_signals},
                "has_new_signals_since_review": new_signals, "evaluations": {"items": evaluations, "total": evaluation_count},
                "run": run, "trace_snapshots": run.pop("trace_snapshots"),
                "signals": [decoded(r, ["evidence"]) for r in db.execute("SELECT * FROM quality_signals WHERE case_id=? ORDER BY created_at", (case_id,))],
                "reviews": [decoded(r, ["changes"]) for r in db.execute("SELECT * FROM quality_reviews WHERE case_id=? ORDER BY revision", (case_id,))]})

    def review_case(self, case_id, payload):
        payload = dict(payload)
        actor_id = payload.pop("actor_id", "")
        if not actor_id or not isinstance(actor_id, str):
            raise ValueError("Reviewer identity is required")
        data = scrub_private_data(ReviewInput.model_validate(payload).model_dump(exclude_unset=True))
        if not data["note"].strip():
            raise ValueError("审核说明不能为空")
        with self.connection() as db:
            begin_write(db)
            row = db.execute("SELECT * FROM quality_cases WHERE case_id=?", (case_id,)).fetchone()
            if row is None:
                raise KeyError("Case not found")
            if row["revision"] != data["expected_revision"]:
                raise QualityConflict("案例已被更新，请刷新后重试")
            status = data["status"]
            if status != row["status"] and status not in TRANSITIONS[row["status"]]:
                raise QualityConflict("不允许该状态转换")
            merged = {**dict(row), **{k: v for k, v in data.items() if v is not None}}
            if status in {"confirmed", "in_progress", "verifying", "closed"} and not merged["symptom"].strip():
                raise ValueError("确认问题时需要填写症状")
            if status in {"verifying", "closed"} and (not merged["root_cause"].strip() or not merged["fix_version"].strip()):
                raise ValueError("验证和关闭前需要填写根因及修复版本")
            if status == "duplicate":
                target = db.execute("SELECT status FROM quality_cases WHERE case_id=?", (merged["duplicate_of"],)).fetchone()
                if merged["duplicate_of"] == case_id or not target or target["status"] == "duplicate":
                    raise ValueError("重复案例必须关联另一个非重复案例")
                if db.execute("SELECT 1 FROM quality_cases WHERE duplicate_of=?", (case_id,)).fetchone():
                    raise ValueError("已有其他案例关联此案例，不能形成重复案例链")
            else:
                merged["duplicate_of"] = None
            fields = ("status", "symptom", "root_cause", "severity", "assignee", "fix_version", "duplicate_of")
            revision, stamp = row["revision"] + 1, now()
            db.execute("UPDATE quality_cases SET " + ",".join(f"{f}=?" for f in fields) + ",revision=?,updated_at=? WHERE case_id=?",
                       (*[merged[f] for f in fields], revision, stamp, case_id))
            changes = {f: {"before": row[f], "after": merged[f]} for f in fields if row[f] != merged[f]}
            db.execute("""INSERT INTO quality_reviews(review_id,case_id,actor_id,from_status,to_status,revision,note,changes_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", ("review_" + uuid4().hex, case_id, actor_id, row["status"], status,
                revision, data["note"].strip(), encode(changes), stamp))
        return self.case_detail(case_id)
