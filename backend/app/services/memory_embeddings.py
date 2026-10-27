"""Local BGE embeddings for long-term memory hybrid retrieval."""

from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

EMBEDDINGS_ENV = "LIMITUPLAB_MEMORY_EMBEDDINGS"
EMBEDDING_MODEL_ENV = "LIMITUPLAB_MEMORY_EMBEDDING_MODEL"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
EMBEDDING_DIMENSION = 512
BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = BACKEND_ROOT / "data" / "memory_embeddings"
_MODEL = None
_MODEL_LOCK = Lock()


class MemoryEmbeddingUnavailable(RuntimeError):
    """The local embedding model is disabled or failed to load."""


def embeddings_enabled() -> bool:
    value = (os.getenv(EMBEDDINGS_ENV) or "on").strip().lower()
    return value not in {"0", "false", "off", "no"}


def embedding_model_name() -> str:
    return (os.getenv(EMBEDDING_MODEL_ENV) or DEFAULT_EMBEDDING_MODEL).strip()


def _model():
    global _MODEL
    if not embeddings_enabled():
        raise MemoryEmbeddingUnavailable("embeddings_disabled")
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            try:
                from fastembed import TextEmbedding
                cache_dir = Path(os.getenv("LIMITUPLAB_MEMORY_EMBEDDING_CACHE") or DEFAULT_CACHE_DIR)
                cache_dir.mkdir(parents=True, exist_ok=True)
                _MODEL = TextEmbedding(
                    model_name=embedding_model_name(),
                    cache_dir=str(cache_dir),
                )
            except Exception as error:
                raise MemoryEmbeddingUnavailable(type(error).__name__) from error
    return _MODEL


def _vector(values) -> list[float]:
    vector = [float(value) for value in values]
    if len(vector) != EMBEDDING_DIMENSION:
        raise MemoryEmbeddingUnavailable("unexpected_embedding_dimension")
    return vector


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    try:
        return [_vector(values) for values in _model().embed(texts)]
    except MemoryEmbeddingUnavailable:
        raise
    except Exception as error:
        raise MemoryEmbeddingUnavailable(type(error).__name__) from error


@lru_cache(maxsize=128)
def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]


def memory_embedding_text(
    *,
    key: str,
    value: str,
    scope_type: str,
    scope: dict[str, Any] | None,
    structured: dict[str, Any] | None,
) -> str:
    """Build the deterministic text embedded for one memory."""

    parts = [key, value]
    if scope_type != "global" and scope:
        parts.append(json.dumps(scope, ensure_ascii=False, sort_keys=True))
    if structured:
        parts.append(json.dumps(structured, ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)


def embedding_source_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()
