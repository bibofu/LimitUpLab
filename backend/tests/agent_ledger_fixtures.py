"""Insert historical trace fixtures without exposing a production overwrite API."""

from contextlib import closing
import json

from app.database import connect


def insert_historical_run(database_url, run):
    """Seed a pre-durable record; duplicate fixture IDs fail instead of updating."""
    with closing(connect(database_url)) as db, db:
        db.execute("""INSERT INTO agent_runs (
            run_id,session_id,run_type,status,intent,tool_calls_json,input_json,
            output_json,error_message,started_at,finished_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                run.run_id, run.session_id, run.run_type, run.status, run.intent,
                json.dumps(run.tool_calls), json.dumps(run.input_json),
                json.dumps(run.output_json) if run.output_json is not None else None,
                run.error_message, run.started_at.isoformat(), run.finished_at.isoformat(),
            ))
