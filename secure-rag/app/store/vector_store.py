"""
app/store/vector_store.py
=========================
ChromaDB wrapper for the RAG document collection.

Design notes:
  - A single ChromaDB client is shared across the app (singleton pattern).
  - Documents are stored with rich metadata for RBAC filtering.
  - Embeddings are generated externally and passed in — this module is
    storage-only so embedding providers are swappable.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings

logger = logging.getLogger(__name__)


class VectorStore:
    """
    Thin wrapper around a ChromaDB collection for RAG document chunks.

    Each chunk has metadata:
      doc_id, title, source, min_role, chunk_index, is_injection_fixture
    """

    def __init__(self) -> None:
        self._client = chromadb.PersistentClient(
            path=settings.chroma_persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=settings.chroma_rag_collection,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "VectorStore initialised: collection=%s, docs=%d",
            settings.chroma_rag_collection,
            self._collection.count(),
        )

    @property
    def count(self) -> int:
        return self._collection.count()

    def add_chunks(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
    ) -> None:
        """Upsert chunks (safe to call repeatedly on re-ingest)."""
        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.debug("Upserted %d chunks into RAG collection", len(ids))

    def query(
        self,
        query_embedding: List[float],
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query the collection and return a list of result dicts.
        The `where` clause is used for RBAC metadata filtering.

        Returns list of dicts with keys: id, document, metadata, distance.
        """
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        # Unpack chromadb's nested lists (single-query batch)
        ids = results["ids"][0] if results["ids"] else []
        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []
        dists = results["distances"][0] if results["distances"] else []

        return [
            {"id": i, "document": d, "metadata": m, "distance": dist}
            for i, d, m, dist in zip(ids, docs, metas, dists)
        ]

    def get_all_metadata(self) -> List[Dict[str, Any]]:
        """Return all chunk metadata (used by output guard for restricted content)."""
        result = self._collection.get(include=["metadatas", "embeddings"])
        return [
            {"metadata": m, "embedding": e}
            for m, e in zip(
                result.get("metadatas", []),
                result.get("embeddings", []),
            )
        ]

    def delete_collection(self) -> None:
        """Drop and recreate the collection (used in tests)."""
        self._client.delete_collection(settings.chroma_rag_collection)
        self._collection = self._client.get_or_create_collection(
            name=settings.chroma_rag_collection,
            metadata={"hnsw:space": "cosine"},
        )


# Module-level singleton
_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store
