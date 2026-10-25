"""Run the exact same fixed tool world under three context configurations."""

import json
import os
from pathlib import Path

from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from long_term_memory_ablation import run_ablation
from long_term_memory_world import LongTermToolWorld


def test_three_memory_modes_have_reproducible_context_contract_metrics(postgresql_database):
    world = LongTermToolWorld(postgresql_database)
    report = run_ablation(world, LongTermMemoryRepository(postgresql_database))
    assert len(report["rows"]) == 27 and report["external_model_calls"] == 0
    assert [report["aggregates"][mode]["context_contract_passes"] for mode in (
        "no_memory", "short_term", "short_and_long_term")] == [4, 4, 9]
    assert all(row["estimated_input_tokens"] <= 8000 for row in report["rows"])
    report_path = os.getenv("LIMITUPLAB_MEMORY_ABLATION_REPORT")
    if report_path:
        path = Path(report_path).resolve()
        root = Path(__file__).resolve().parents[2] / "output"
        assert path.is_relative_to(root), "evaluation reports must stay in ignored workspace output"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
