"""
app/rag/ingest.py
=================
Document ingestion pipeline:
  1. Read markdown/text documents from data/docs/
  2. Chunk them with configurable size/overlap
  3. Embed chunks
  4. Upsert into ChromaDB with RBAC metadata

Run this once before starting the API:
  python -m app.rag.ingest
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.rag.embedder import get_embedder
from app.store.vector_store import get_vector_store

logger = logging.getLogger(__name__)

_DOCS_DIR = Path(__file__).parent.parent.parent / "data" / "docs"
_CANARIES_FILE = Path(__file__).parent.parent.parent / "data" / "canaries.json"


def _load_canary_registry() -> Dict[str, str]:
    """Load canary → min_role mapping from canaries.json."""
    if not _CANARIES_FILE.exists():
        return {}
    with open(_CANARIES_FILE, encoding="utf-8") as f:
        return json.load(f)


def chunk_text(
    text: str,
    chunk_size: int = None,
    chunk_overlap: int = None,
) -> List[str]:
    """
    Simple recursive character-based chunker.
    Tries to split on paragraph/sentence/word boundaries before hard-splitting.
    """
    chunk_size = chunk_size or settings.chunking_chunk_size
    overlap = chunk_overlap or settings.chunking_chunk_overlap

    if len(text) <= chunk_size:
        return [text.strip()] if text.strip() else []

    chunks: List[str] = []
    separators = ["\n\n", "\n", ". ", " "]
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end == len(text):
            chunks.append(text[start:].strip())
            break

        # Try to find a natural split point
        split_at = end
        for sep in separators:
            idx = text.rfind(sep, start, end)
            if idx > start:
                split_at = idx + len(sep)
                break

        chunk = text[start:split_at].strip()
        if chunk:
            chunks.append(chunk)
        start = split_at - overlap if split_at - overlap > start else split_at

    return [c for c in chunks if c]


def parse_doc_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    """
    Parse YAML-like frontmatter from document files.
    Format:
      ---
      title: My Doc
      min_role: Analyst
      doc_id: doc-001
      is_injection_fixture: false
      ---
      Body text here...
    """
    meta: Dict[str, Any] = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            body = parts[2].strip()
            for line in frontmatter.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()

    return meta, body


def ingest_documents(docs_dir: Path = None, force: bool = False) -> int:
    """
    Ingest all documents from docs_dir into the vector store.

    Returns number of chunks ingested.
    """
    docs_dir = docs_dir or _DOCS_DIR
    embedder = get_embedder()
    store = get_vector_store()
    canary_registry = _load_canary_registry()

    if store.count > 0 and not force:
        logger.info("Vector store already has %d chunks; skipping ingest. Use force=True to re-ingest.", store.count)
        return store.count

    doc_files = list(docs_dir.glob("**/*.md")) + list(docs_dir.glob("**/*.txt"))
    logger.info("Found %d document files in %s", len(doc_files), docs_dir)

    all_ids: List[str] = []
    all_embeddings: List[List[float]] = []
    all_texts: List[str] = []
    all_metas: List[Dict[str, Any]] = []

    for doc_path in sorted(doc_files):
        content = doc_path.read_text(encoding="utf-8")
        meta, body = parse_doc_frontmatter(content)

        doc_id = meta.get("doc_id", doc_path.stem)
        title = meta.get("title", doc_path.stem)
        min_role = meta.get("min_role", "Analyst")
        source = str(doc_path.relative_to(docs_dir.parent.parent))
        is_injection_fixture = meta.get("is_injection_fixture", "false").lower() == "true"

        # Validate min_role
        if min_role not in settings.roles_hierarchy:
            logger.warning("Unknown min_role '%s' in %s — defaulting to Analyst", min_role, doc_path.name)
            min_role = "Analyst"

        chunks = chunk_text(body)
        logger.info("  %s: %d chunks, min_role=%s", doc_path.name, len(chunks), min_role)

        for idx, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}__chunk_{idx}"
            chunk_meta: Dict[str, Any] = {
                "doc_id": doc_id,
                "title": title,
                "source": source,
                "min_role": min_role,
                "chunk_index": idx,
                "is_injection_fixture": is_injection_fixture,
            }

            all_ids.append(chunk_id)
            all_texts.append(chunk)
            all_metas.append(chunk_meta)

    # Batch embed
    logger.info("Embedding %d chunks...", len(all_texts))
    all_embeddings = embedder.embed(all_texts)

    # Upsert to store
    store.add_chunks(
        ids=all_ids,
        embeddings=all_embeddings,
        documents=all_texts,
        metadatas=all_metas,
    )

    logger.info("Ingest complete: %d chunks from %d documents", len(all_ids), len(doc_files))
    return len(all_ids)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    force = "--force" in sys.argv
    n = ingest_documents(force=force)
    print(f"\nIngested {n} chunks into ChromaDB")
