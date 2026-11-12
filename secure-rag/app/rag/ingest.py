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
    Parse YAML frontmatter (---) or JSON comment metadata (<!-- metadata: {...} -->).
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
    elif content.startswith("<!-- metadata:"):
        first_line, _, rest = content.partition("\n")
        json_str = first_line.replace("<!-- metadata:", "").replace("-->", "").strip()
        try:
            m = json.loads(json_str)
            meta["doc_id"] = m.get("document_id", "")
            meta["title"] = m.get("title", "")
            meta["department"] = m.get("department", "")
            meta["source"] = m.get("source", "GitLab Handbook")
            classification = m.get("classification", "Public")
            if classification == "Restricted":
                meta["min_role"] = "Administrator"
            elif classification == "Confidential":
                meta["min_role"] = "Manager"
            else:
                meta["min_role"] = "Analyst"
        except Exception:
            pass
        body = rest.strip()

    return meta, body


def ingest_documents(docs_dir: Path = None, force: bool = False, max_chunks_per_doc: int = 5) -> int:
    """
    Ingest all documents from docs_dir AND the curated GitLab Handbook dataset into ChromaDB.
    Combines:
      - 21 Fictional confidential & fixture documents (salary bands, budgets, canaries)
      - ~182 Curated GitLab Handbook enterprise documents across HR, Finance, Engineering, Security
    """
    docs_dir = docs_dir or _DOCS_DIR
    embedder = get_embedder()
    store = get_vector_store()
    canary_registry = _load_canary_registry()

    if store.count > 300 and not force:
        logger.info("Vector store already has %d chunks; skipping ingest. Use force=True to re-ingest.", store.count)
        return store.count

    # 1. Collect confidential and fixture markdown documents
    doc_files = list(docs_dir.glob("**/*.md")) + list(docs_dir.glob("**/*.txt"))

    # 2. Collect curated GitLab Handbook enterprise documents across core departments
    processed_root = Path(__file__).resolve().parents[3] / "dataset_pipeline" / "data" / "processed"
    if processed_root.is_dir():
        for dept in ["people_hr", "finance", "engineering", "cybersecurity", "company", "operations_it", "product"]:
            dept_dir = processed_root / dept
            if dept_dir.is_dir():
                doc_files.extend(list(dept_dir.glob("*.md")))

    logger.info("Found %d total knowledge base document files", len(doc_files))

    all_ids: List[str] = []
    all_texts: List[str] = []
    all_metas: List[Dict[str, Any]] = []

    for doc_path in sorted(doc_files):
        try:
            content = doc_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        meta, body = parse_doc_frontmatter(content)

        doc_id = meta.get("doc_id") or doc_path.stem
        title = meta.get("title") or doc_path.stem
        min_role = meta.get("min_role", "Analyst")
        source = meta.get("source") or doc_path.parent.name
        is_injection_fixture = str(meta.get("is_injection_fixture", "false")).lower() == "true"

        # Validate min_role
        if min_role not in settings.roles_hierarchy:
            min_role = "Analyst"

        raw_chunks = chunk_text(body)
        # For large GitLab docs take top substantive chunks; for fixture docs take all
        chunks = raw_chunks if len(raw_chunks) <= max_chunks_per_doc or "data/docs" in str(doc_path).replace("\\", "/") else raw_chunks[:max_chunks_per_doc]

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

    logger.info("Embedding %d total chunks across %d knowledge base documents...", len(all_texts), len(doc_files))

    # Batch embedding in chunks of 100 for safety and speed
    batch_size = 100
    for i in range(0, len(all_texts), batch_size):
        end = min(i + batch_size, len(all_texts))
        batch_ids = all_ids[i:end]
        batch_texts = all_texts[i:end]
        batch_metas = all_metas[i:end]

        batch_embeddings = embedder.embed(batch_texts)

        store.add_chunks(
            ids=batch_ids,
            embeddings=batch_embeddings,
            documents=batch_texts,
            metadatas=batch_metas,
        )
        logger.info("  Indexed chunks %d to %d / %d", i + 1, end, len(all_texts))

    logger.info("Ingest complete: %d chunks indexed from %d documents into ChromaDB", len(all_ids), len(doc_files))
    return len(all_ids)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    force = "--force" in sys.argv
    n = ingest_documents(force=force)
    print(f"\nIngested {n} chunks into ChromaDB")
