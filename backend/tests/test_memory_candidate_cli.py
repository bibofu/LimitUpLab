"""Backend operator CLI for held candidates."""

import json
from uuid import uuid4

from app.memory_models import MemoryWrite
from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from scripts.review_memory_candidates import main


def candidate_write():
    return MemoryWrite(
        memory_type="preference",
        key="response_style",
        value="concise",
        source_user_message="以后回答简洁",
    )


def test_operator_cli_lists_and_confirms_by_owner(postgresql_database, capsys):
    owner = "visitor_" + uuid4().hex
    repository = LongTermMemoryRepository()
    created = repository.consolidate_candidate(owner, candidate_write(), "cli-candidate")
    assert main(["--owner", owner, "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["owner"] == owner
    assert listed["count"] == 1
    assert listed["items"][0]["memory_id"] == created["candidate_id"]

    assert main(["--owner", owner, "confirm", "--id", created["candidate_id"],
                 "--operation-id", "cli-confirm"]) == 0
    confirmed = json.loads(capsys.readouterr().out)
    assert confirmed["current"]["status"] == "active"
    assert repository.list_candidates(owner) == []


def test_operator_cli_rejects_and_reports_missing_candidate(postgresql_database, capsys):
    owner = "visitor_" + uuid4().hex
    repository = LongTermMemoryRepository()
    created = repository.consolidate_candidate(owner, candidate_write(), "cli-reject-candidate")
    assert main(["--owner", owner, "reject", "--id", created["candidate_id"],
                 "--revision", "1", "--operation-id", "cli-reject"]) == 0
    assert json.loads(capsys.readouterr().out)["current"]["status"] == "revoked"
    assert main(["--owner", owner, "confirm", "--id", "missing"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "MemoryNotFound"
