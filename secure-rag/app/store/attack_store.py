"""
app/store/attack_store.py
=========================
ChromaDB collection for KNOWN ATTACKS (the adaptive guardrail's memory).

Only successful TRAIN attacks are stored here with their embeddings so future
queries can be checked for similarity. Legacy and held-out entries are removed.

This is the core of the "self-hardening" loop:
  red-team finds attack → stored here → adaptive guard blocks similar future attacks
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings

logger = logging.getLogger(__name__)


class AttackStore:
    """
    Stores successful attacks as embeddings for similarity-based detection.

    Each stored attack has metadata:
      attack_id, category, source, source_split, round, timestamp, variant_text
    """

    def __init__(self) -> None:
        self._client = chromadb.PersistentClient(
            path=settings.chroma_persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=settings.chroma_attack_collection,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "AttackStore initialised: collection=%s, attacks=%d",
            settings.chroma_attack_collection,
            self._collection.count(),
        )
        removed = self.remove_nontraining_attacks()
        if removed:
            logger.warning("Removed %d attack entries without full TRAIN provenance", removed)

    @property
    def count(self) -> int:
        return self._collection.count()

    def add_attack(
        self,
        attack_id: str,
        embedding: List[float],
        variant_text: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Store a successful TRAIN attack with its embedding and provenance."""
        metadata["variant_text"] = variant_text[:500]  # truncate for storage
        metadata["timestamp"] = metadata.get("timestamp", time.time())
        self._collection.upsert(
            ids=[attack_id],
            embeddings=[embedding],
            documents=[variant_text],
            metadatas=[metadata],
        )
        logger.info("Stored attack in AttackStore: id=%s category=%s", attack_id, metadata.get("category"))

    def query_similar(
        self,
        query_embedding: List[float],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Find the most similar known attacks to a query embedding.
        Returns list of dicts with keys: id, document, metadata, distance.
        Distance is cosine distance (lower = more similar).
        """
        if self._collection.count() == 0:
            return []

        n = min(top_k, self._collection.count())
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=n,
            where={"source_split": {"$eq": "train"}},
            include=["documents", "metadatas", "distances"],
        )
        ids = results["ids"][0] if results["ids"] else []
        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []
        dists = results["distances"][0] if results["distances"] else []

        return [
            {"id": i, "document": d, "metadata": m, "distance": dist}
            for i, d, m, dist in zip(ids, docs, metas, dists)
        ]

    def get_all(self) -> List[Dict[str, Any]]:
        """Return all stored attacks (for dashboard display)."""
        result = self._collection.get(include=["documents", "metadatas"])
        ids = result.get("ids", [])
        docs = result.get("documents", [])
        metas = result.get("metadatas", [])
        return [
            {"id": i, "document": d, "metadata": m}
            for i, d, m in zip(ids, docs, metas)
        ]

    def remove_nontraining_attacks(self) -> int:
        """Remove legacy, validation, and test entries lacking full TRAIN provenance."""
        result = self._collection.get(include=["metadatas"])
        ids = result.get("ids", [])
        metadatas = result.get("metadatas", [])
        nontraining_ids = [
            attack_id
            for attack_id, metadata in zip(ids, metadatas)
            if (
                not metadata
                or metadata.get("source_split") != "train"
                or not metadata.get("source")
            )
        ]
        if nontraining_ids:
            self._collection.delete(ids=nontraining_ids)
        return len(nontraining_ids)

    def get_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Return all attacks of a specific category."""
        result = self._collection.get(
            where={"category": category},
            include=["documents", "metadatas"],
        )
        return [
            {"id": i, "document": d, "metadata": m}
            for i, d, m in zip(
                result.get("ids", []),
                result.get("documents", []),
                result.get("metadatas", []),
            )
        ]

    def clear(self) -> None:
        """Clear all stored attacks (used between experiment rounds)."""
        self._client.delete_collection(settings.chroma_attack_collection)
        self._collection = self._client.get_or_create_collection(
            name=settings.chroma_attack_collection,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("AttackStore cleared")


# Module-level singleton
_attack_store: Optional[AttackStore] = None


def get_attack_store() -> AttackStore:
    global _attack_store
    if _attack_store is None:
        _attack_store = AttackStore()
    return _attack_store
