"""Local text embeddings (fastembed / ONNX, no PyTorch, no API key).

Shared by the vector store and by Graphiti so both index the same semantic space.
"""
from __future__ import annotations

import threading

from .config import settings

_model = None
_lock = threading.Lock()
EMBEDDING_DIM = 384  # bge-small-en-v1.5


def _get():
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding
            _model = TextEmbedding(model_name=settings.embedding_model)
        return _model


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    # batch_size matters: attention memory grows with batch x seq^2. The library default (256) peaked at
    # ~2.7 GB here; 8 keeps the process within a small host's memory at negligible speed cost.
    return [[float(x) for x in v] for v in _get().embed(texts, batch_size=8)]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
