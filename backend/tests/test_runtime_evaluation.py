"""A small real runtime sample and fail-closed database selection checks."""

import os
from contextlib import closing

import pytest
import sqlalchemy as sa

from app.services.runtime_evaluation import CASES, evaluate, evaluation_schema, representation_comparison
from app.storage.isolated import isolated_database_url
from app.database import connect


@pytest.mark.parametrize("url", [
    "sqlite:///local.db", "postgresql://user@host/limituplab", "postgresql://user@host/postgres",
    "postgresql://user@host/template1", "postgresql://user@host/eval?host=other",
    "postgresql://user@host/eval?service=business", "postgresql://user@host,other/eval",
])
def test_evaluation_database_rejects_business_and_routing_overrides(tmp_path, url):
    with pytest.raises(ValueError):
        isolated_database_url(url, runtime_urls={}, env_path=tmp_path / "none")


def test_evaluation_rejects_configured_business_name_even_on_another_host(tmp_path):
    env = tmp_path / "local.env"
    env.write_text("LIMITUPLAB_DATABASE_URL=postgresql://user:secret@business/custom_business\n", encoding="utf-8")
    with pytest.raises(ValueError, match="separate"):
        isolated_database_url("postgresql://other@elsewhere/custom_business", runtime_urls={}, env_path=env)
    with pytest.raises(ValueError, match="separate"):
        isolated_database_url("postgresql://other@elsewhere/current_business",
                              runtime_urls={"LIMITUPLAB_DATABASE_URL": "postgresql://host/current_business"}, env_path=env)


def test_evaluation_discards_supplied_search_path(tmp_path):
    selected = isolated_database_url("postgresql://user@host/eval?options=-csearch_path%3Dpublic",
                                     runtime_urls={}, env_path=tmp_path / "none")
    assert "options" not in selected.query


def test_schema_is_removed_on_failure_and_unrelated_objects_survive(monkeypatch, postgresql_database):
    with closing(connect(postgresql_database)) as db, db:
        db.execute("CREATE TABLE evaluation_safety_sentinel(value TEXT)")
        db.execute("INSERT INTO evaluation_safety_sentinel VALUES('preserve unrelated test data')")
    monkeypatch.delenv("LIMITUPLAB_DATABASE_URL", raising=False)
    raw = os.environ["LIMITUPLAB_TEST_DATABASE_URL"]
    base = isolated_database_url(raw)
    engine = sa.create_engine(base, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as db:
            before = set(sa.inspect(db).get_schema_names())
        with pytest.raises(RuntimeError, match="injected"):
            with evaluation_schema(raw) as url:
                assert "limituplab_eval_" in url
                raise RuntimeError("injected fixture failure")
        with engine.connect() as db:
            assert set(sa.inspect(db).get_schema_names()) == before
        with closing(connect(postgresql_database)) as db:
            assert db.execute("SELECT value FROM evaluation_safety_sentinel").fetchone()[0] == "preserve unrelated test data"
    finally:
        engine.dispose()


def test_small_deterministic_runtime_evaluation_uses_actual_graph_and_cleans_schema(monkeypatch):
    monkeypatch.delenv("LIMITUPLAB_DATABASE_URL", raising=False)
    report = evaluate(os.environ["LIMITUPLAB_TEST_DATABASE_URL"], trials=1)
    assert report["sample_count"] == len(CASES) == 5
    assert report["external_model_calls"] == 0 and report["schema_cleaned"]
    assert all(row["contract_pass"] and row["delivery_success"] for row in report["samples"]), report["samples"]
    recovery = next(row for row in report["samples"] if row["case"] == "interrupted_recovery")
    assert recovery["recovery_success"] and recovery["actual_business_tool_calls"] == 1
    for sample in report["samples"]:
        assert sample["estimated_model_input_tokens"] > 0
        assert sample["storage_growth"]["checkpoints"]["rows"] > 0
        assert sample["storage_growth"]["chat_messages"]["rows"] == sample["turn_count"] * 2
        assert sample["storage_growth"]["checkpoint_writes"]["logical_row_bytes"] > 0
    assert [row["cross_session_preference_present"] for row in report["memory_ablation"]["rows"]] == [False, False, True]
    assert report["representations"]["all_rows_roundtrip"]
    assert "not IO" in report["metric_definitions"]["storage_growth"]


def test_representation_comparison_counts_all_pages_without_claiming_equal_initial_coverage():
    comparison = representation_comparison()
    assert comparison["all_rows_roundtrip"] and comparison["page_count"] == 4
    assert comparison["paged_all_rows_tokens_sum"] > comparison["paged_initial_tokens"]


@pytest.mark.parametrize("trials", [0, -1, 101, True])
def test_invalid_trial_count_fails_before_any_database_access(trials):
    with pytest.raises(ValueError, match="trials"):
        evaluate("not-a-database", trials=trials)
