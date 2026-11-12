"""
app/rag/retriever.py
====================
RBAC-enforced retrieval from ChromaDB.

Security design:
  1. PRIMARY FILTER: ChromaDB `where` clause restricts retrieval to chunks
     where min_role <= user_role (using role hierarchy index). This prevents
     the vector store from even returning restricted chunks.
  2. POST-RETRIEVAL ASSERTION: Every returned chunk is re-checked against
     the user's role. This is defense in depth — if the primary filter
     ever fails (bug, misconfiguration), this catch ensures leakage is
     impossible at the retrieval layer.
  3. The user's role comes from the JWT (server-validated), never from the query.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import role_level, settings
from app.rag.embedder import get_embedder
from app.schemas import SourceChunk
from app.store.vector_store import get_vector_store

logger = logging.getLogger(__name__)


def _build_rbac_filter(
    user_role: str,
    user_department: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a ChromaDB `where` filter that returns only chunks accessible
    to the given user_role.

    ChromaDB doesn't support "role >= user_role" natively, so we enumerate
    all roles the user CAN access (i.e., roles at or below their level).
    """
    accessible_roles = [
        r for r in settings.roles_hierarchy
        if role_level(r) <= role_level(user_role)
    ]
    role_filter = {"min_role": {"$in": accessible_roles}}
    if not user_department:
        return {
            "$and": [
                role_filter,
                {"classification": {"$eq": "Public"}},
            ]
        }
    return {
        "$and": [
            role_filter,
            {
                "$or": [
                    {"classification": {"$eq": "Public"}},
                    {"department": {"$eq": user_department}},
                ]
            },
        ]
    }


def _can_access_chunk(
    metadata: Dict[str, Any],
    user_role: str,
    user_department: Optional[str],
) -> bool:
    """Enforce role and simulated department policy; department denial wins."""
    minimum_role = metadata.get("min_role", "Administrator")
    if role_level(user_role) < role_level(minimum_role):
        return False

    classification = metadata.get("classification", "Public")
    document_department = metadata.get("department", "")
    if classification != "Public":
        return bool(document_department and user_department) and (
            user_department.casefold() == document_department.casefold()
        )
    return True


def retrieve(
    query: str,
    user_role: str,
    n_results: int = 5,
    include_injection_fixtures: bool = False,
    user_department: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve relevant chunks for `query` that are accessible to `user_role`.

    Args:
        query: The user's question text.
        user_role: The authenticated role from the JWT (never from query).
        user_department: The authenticated user's simulated department.
        n_results: Max number of chunks to return.
        include_injection_fixtures: If False, skip docs labeled as test fixtures.

    Returns:
        List of chunk dicts: {id, document, metadata, distance}
    """
    embedder = get_embedder()
    store = get_vector_store()

    # Embed the query
    query_embedding = embedder.embed_one(query)

    # Build RBAC where clause (PRIMARY filter)
    where_clause = _build_rbac_filter(user_role, user_department)

    # Execute RBAC-filtered query
    raw_results = store.query(
        query_embedding=query_embedding,
        n_results=n_results,
        where=where_clause,
    )

    # POST-RETRIEVAL ASSERTION (defense in depth)
    safe_results = []
    for chunk in raw_results:
        if not _can_access_chunk(chunk["metadata"], user_role, user_department):
            # This should NEVER happen given the primary filter — log as security event
            logger.error(
                "SECURITY VIOLATION: RBAC primary filter bypass detected! "
                "user_role=%s chunk_min_role=%s chunk_id=%s",
                user_role, chunk["metadata"].get("min_role", "Administrator"), chunk["id"],
            )
            continue  # Drop the chunk

        # Optionally skip injection test fixtures from normal retrieval
        if not include_injection_fixtures and chunk["metadata"].get("is_injection_fixture"):
            continue

        safe_results.append(chunk)

    logger.debug(
        "Retrieved %d chunks (of %d raw) for role=%s",
        len(safe_results), len(raw_results), user_role,
    )
    return safe_results


def format_sources(chunks: List[Dict[str, Any]]) -> List[SourceChunk]:
    """Convert raw chunk dicts to SourceChunk schema objects."""
    sources = []
    for chunk in chunks:
        meta = chunk["metadata"]
        sources.append(
            SourceChunk(
                doc_id=meta.get("doc_id", "unknown"),
                title=meta.get("title", "unknown"),
                source=meta.get("source", ""),
                min_role=meta.get("min_role", "Analyst"),
                chunk_index=meta.get("chunk_index", 0),
                content_preview=chunk["document"][:200],
            )
        )
    return sources
