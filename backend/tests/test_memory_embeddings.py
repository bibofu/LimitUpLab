"""pgvector storage and local embedding helpers."""

from hashlib import sha256

import pytest

from app.memory_models import MemoryWrite
from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from app.services.memory_embeddings import embedding_source_hash, memory_embedding_text
from app.services.memory_retrieval import retrieve_memories


def vector(seed=0):
    values = [0.0] * 512
    values[seed] = 1.0
    return values


def test_embedding_text_is_deterministic_and_includes_scope():
    text = memory_embedding_text(
        key="response_style", value="detailed", scope_type="tool",
        scope={"tool_name": "stock_financials"},
        structured={"display_columns": ["name"]},
    )
    assert "response_style" in text and "detailed" in text
    assert "stock_financials" in text and "display_columns" in text
    assert embedding_source_hash(text) == sha256(text.encode("utf-8")).hexdigest()


def test_hybrid_retrieval_uses_vector_when_lexical_overlap_is_absent(
    postgresql_database, monkeypatch,
):
    import app.services.memory_embeddings as embeddings

    repository = LongTermMemoryRepository()
    owner = "visitor_" + "a2" * 16
    saved = repository.put(owner, MemoryWrite(
        memory_type="research_context", key="research:financials",
        value="财务报告研究背景", source_user_message="记录财报研究背景",
    ), "vector-retrieval-put")
    repository.save_embedding(
        owner, saved.current.memory_id, vector(0), "test-model", "source-hash",
    )
    monkeypatch.setenv("LIMITUPLAB_MEMORY_EMBEDDINGS", "on")
    monkeypatch.setattr(embeddings, "embed_query", lambda text: vector(0))
    result = retrieve_memories(repository, owner_id=owner, query="完全无关的行情问题")
    assert result.diagnostics["memory_vector_status"] == "ready"
    assert [item["value"] for item in result.items] == ["财务报告研究背景"]
    assert result.items[0]["vector_similarity"] == 1.0


def test_repository_stores_vectors_and_returns_cosine_distance(postgresql_database):
    repository = LongTermMemoryRepository()
    owner = "visitor_" + "a1" * 16
    saved = repository.put(owner, MemoryWrite(
        memory_type="research_context", key="research:financials",
        value="财务报告研究背景",
        source_user_message="记录财报研究背景",
    ), "embedding-put")
    assert repository.save_embedding(
        owner, saved.current.memory_id, vector(0), "test-model", "source-hash",
    ) is True
    snapshot = repository.read_context(
        owner, query_embedding=vector(0), memory_types=("research_context",),
    )
    item = snapshot["items"][0]
    assert item["vector_distance"] == 0.0
    plain = repository.read_context(owner, memory_types=("research_context",))
    assert plain["items"][0]["vector_distance"] is None
    rows = repository.embedding_backfill_rows()
    assert rows[0]["has_embedding"] is True
    assert rows[0]["embedding_model"] == "test-model"


def test_backfill_worker_embeds_only_missing_or_stale_rows(
    postgresql_database, monkeypatch,
):
    from scripts import run_memory_embeddings as worker

    repository = LongTermMemoryRepository()
    owner = "visitor_" + "a3" * 16
    saved = repository.put(owner, MemoryWrite(
        memory_type="research_context", key="research:financials",
        value="财务报告研究背景",
        source_user_message="记录财报研究背景",
    ), "embedding-worker-put")
    batches = []

    def embed(texts):
        batches.append(list(texts))
        return [vector(0) for _ in texts]

    monkeypatch.setattr(worker, "embed_texts", embed)
    monkeypatch.setattr(worker, "embedding_model_name", lambda: "test-model")
    assert worker.process_batch(repository, limit=10) == 1
    assert len(batches) == 1 and "财务报告研究背景" in batches[0][0]
    current = repository.list_active(owner)[0]
    assert current["revision"] == saved.current.revision
    assert current["vector_distance"] is None  # list_active does not compute distance
    assert repository.embedding_backfill_rows()[0]["embedding_model"] == "test-model"
    assert worker.process_batch(repository, limit=10) == 0

    monkeypatch.setattr(worker, "embedding_model_name", lambda: "test-model-v2")
    assert worker.process_batch(repository, limit=10) == 1
    assert repository.embedding_backfill_rows()[0]["embedding_model"] == "test-model-v2"


def test_embedding_worker_main_exits_without_claiming_lease_when_disabled(
    monkeypatch, capsys,
):
    from scripts import run_memory_embeddings as worker

    monkeypatch.setenv("LIMITUPLAB_MEMORY_EMBEDDINGS", "off")
    monkeypatch.setattr(worker.WorkerLifecycle, "claim", lambda *args, **kwargs: pytest.fail("lease claimed"))
    assert worker.main(["--once"]) == 0
    assert "disabled" in capsys.readouterr().out
