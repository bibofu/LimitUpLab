"""Owner-scoped public run summaries, independent of the SSE connection."""

import json


def public_run_state(lifecycle, row):
    run_id, status = row["run_id"], row["run_status"]
    active, cancelled = bool(row["active"]), bool(row["cancelled"])
    with lifecycle.connection() as db:
        cursor = db.execute("SELECT COALESCE(MAX(seq),0) FROM react_events WHERE run_id=?",
                            (run_id,)).fetchone()[0]
    return {
        "run_id": run_id, "session_id": row["session_id"], "active": active,
        "cancel_requested": cancelled, "run_status": status,
        "state_revision": row.get("control_revision", 0),
        "recovery_count": row["recovery_count"], "resume_count": row.get("resume_count", 0),
        "paused_at": row.get("paused_at"), "last_event_id": cursor,
        "can_pause": active and not cancelled and status in {"pending", "running", "interrupted"},
        "can_resume": active and not cancelled and status == "paused",
        "can_cancel": active and not cancelled,
        "interrupted": active and status not in {"pausing", "paused", "resuming"}
                       and not lifecycle.executing(run_id),
        "response": json.loads(row["response_json"]) if row["response_json"] else None,
    }


def with_active_run(session, owner_id):
    if session is None:
        return None
    from app.agents.run_lifecycle import RunLifecycle

    lifecycle = RunLifecycle()
    with lifecycle.connection() as db:
        row = db.execute("SELECT * FROM react_runs WHERE session_id=? AND owner_id=? AND active=1",
                         (session.session_id, owner_id)).fetchone()
    return session.model_copy(update={"active_run": public_run_state(lifecycle, dict(row)) if row else None})
