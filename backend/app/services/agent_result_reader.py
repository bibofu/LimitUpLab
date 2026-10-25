"""Read immutable tool results with an owner/run boundary, including after delivery."""

from contextlib import closing

from app.agents.result_paging import read_result_page
from app.database import connect_readonly, initialize_database


def read_owned_result(database_url, *, owner_id, run_id, request, max_tokens=None):
    import json

    with closing(connect_readonly(database_url)) as db:
        initialize_database(db)
        row = db.execute("""SELECT result.payload_json FROM agent_tool_results AS result
            JOIN react_runs AS run ON run.run_id=result.run_id
            WHERE run.owner_id=? AND result.run_id=? AND result.call_id=? AND result.status='complete'""",
            (owner_id, run_id, request.result_id)).fetchone()
    if row is None:
        raise KeyError("Saved result not found")
    return read_result_page(json.loads(row["payload_json"]), request, max_tokens=max_tokens)
