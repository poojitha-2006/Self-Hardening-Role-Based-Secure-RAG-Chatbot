"""
app/rag/embedder.py
===================
Embedding provider abstraction.

Supports two backends:
  1. sentence-transformers (local, no server needed) — DEFAULT
  2. Ollama embedding endpoint (requires Ollama running)

All callers use get_embedder() to get the singleton instance.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import List

from app.config import settings

logger = logging.getLogger(__name__)


class BaseEmbedder(ABC):
    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts. Returns list of float vectors."""
        ...

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]


class SentenceTransformersEmbedder(BaseEmbedder):
    """Uses sentence-transformers library (fully local, no server)."""

    def __init__(self, model_name: str, device: str = "cpu") -> None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading sentence-transformers model: %s on %s", model_name, device)
        self._model = SentenceTransformer(model_name, device=device)
        self.dim = self._model.get_sentence_embedding_dimension()
        logger.info("Embedder ready: dim=%d", self.dim)

    def embed(self, texts: List[str]) -> List[List[float]]:
        vecs = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,  # unit vectors → cosine sim = dot product
            show_progress_bar=False,
        )
        return vecs.tolist()


class OllamaEmbedder(BaseEmbedder):
    """Uses Ollama's embedding endpoint."""

    def __init__(self, model_name: str, base_url: str) -> None:
        import ollama
        self._client = ollama.Client(host=base_url)
        self._model = model_name
        logger.info("OllamaEmbedder using model: %s at %s", model_name, base_url)
        # Probe to get dimension
        test = self._client.embeddings(model=model_name, prompt="test")
        self.dim = len(test["embedding"])
        logger.info("Embedder ready: dim=%d", self.dim)

    def embed(self, texts: List[str]) -> List[List[float]]:
        results = []
        for text in texts:
            resp = self._client.embeddings(model=self._model, prompt=text)
            results.append(resp["embedding"])
        return results


@lru_cache(maxsize=1)
def get_embedder() -> BaseEmbedder:
    """Return cached embedder instance based on config."""
    if settings.embedding_provider == "sentence_transformers":
        return SentenceTransformersEmbedder(
            model_name=settings.embedding_model,
            device=settings.embedding_device,
        )
    elif settings.embedding_provider == "ollama":
        return OllamaEmbedder(
            model_name=settings.embedding_ollama_model,
            base_url=settings.llm_base_url,
        )
    else:
        raise ValueError(f"Unknown embedding provider: {settings.embedding_provider}")
