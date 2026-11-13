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
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.rag.classification import classify_document
from app.rag.embedder import get_embedder
from app.store.vector_store import get_vector_store

logger = logging.getLogger(__name__)

_DOCS_DIR = Path(__file__).parent.parent.parent / "data" / "docs"
_CANARIES_FILE = Path(__file__).parent.parent.parent / "data" / "canaries.json"
_CURATED_DOCS_FILE = (
    Path(__file__).resolve().parents[3]
    / "dataset_pipeline"
    / "data"
    / "curated"
    / "documents.jsonl"
)


def _load_canary_registry() -> Dict[str, str]:
    """Load canary → min_role mapping from canaries.json."""
    if not _CANARIES_FILE.exists():
        return {}
    with open(_CANARIES_FILE, encoding="utf-8") as f:
        return json.load(f)


def chunk_text_with_offsets(
    text: str,
    chunk_size: int = None,
    chunk_overlap: int = None,
) -> List[tuple[int, int, str]]:
    """Return index-compatible chunks with their source-text offsets."""
    chunk_size = chunk_size or settings.chunking_chunk_size
    overlap = chunk_overlap or settings.chunking_chunk_overlap

    if len(text) <= chunk_size:
        chunk = text.strip()
        if not chunk:
            return []
        start = len(text) - len(text.lstrip())
        return [(start, start + len(chunk), chunk)]

    chunks: List[tuple[int, int, str]] = []
    separators = ["\n\n", "\n", ". ", " "]
    start = 0

    def append_chunk(chunk_start: int, chunk_end: int) -> None:
        raw = text[chunk_start:chunk_end]
        chunk = raw.strip()
        if chunk:
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw.rstrip())
            chunks.append((chunk_start + leading, chunk_start + trailing, chunk))

    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end == len(text):
            append_chunk(start, end)
            break

        # Try to find a natural split point
        split_at = end
        for sep in separators:
            idx = text.rfind(sep, start, end)
            if idx > start:
                split_at = idx + len(sep)
                break

        append_chunk(start, split_at)
        start = split_at - overlap if split_at - overlap > start else split_at

    return chunks


def chunk_text(
    text: str,
    chunk_size: int = None,
    chunk_overlap: int = None,
) -> List[str]:
    """
    Simple recursive character-based chunker.
    Tries to split on paragraph/sentence/word boundaries before hard-splitting.
    """
    return [
        chunk
        for _, _, chunk in chunk_text_with_offsets(text, chunk_size, chunk_overlap)
    ]


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


def _classification_for_role(role: str) -> str:
    return {
        "Analyst": "Public",
        "Manager": "Confidential",
        "Administrator": "Restricted",
    }.get(role, "Public")


def _minimum_role_for_classification(classification: str) -> str:
    return {
        "Public": "Analyst",
        "Internal": "Analyst",
        "Confidential": "Manager",
        "Restricted": "Administrator",
    }.get(classification, "Administrator")


def ingest_documents(
    docs_dir: Path = None,
    force: bool = False,
    max_chunks_per_doc: int = 5,
) -> int:
    """Ingest local simulated policy fixtures and the curated public corpus."""
    docs_dir = docs_dir or _DOCS_DIR
    if not _CURATED_DOCS_FILE.is_file():
        raise FileNotFoundError(f"Curated corpus not found: {_CURATED_DOCS_FILE}")

    embedder = get_embedder()
    store = get_vector_store()
    canary_registry = _load_canary_registry()

    if store.count > 300 and not force:
        logger.info("Vector store already has %d chunks; skipping ingest. Use force=True to re-ingest.", store.count)
        return store.count
    if force:
        store.delete_collection()

    doc_files = list(docs_dir.glob("**/*.md")) + list(docs_dir.glob("**/*.txt"))

    all_ids: List[str] = []
    all_texts: List[str] = []
    all_metas: List[Dict[str, Any]] = []
    document_counts: Counter = Counter()
    chunk_counts: Counter = Counter()
    documents_by_classification: Counter = Counter()
    chunks_by_classification: Counter = Counter()
    curated_documents_by_classification: Counter = Counter()
    curated_chunks_by_classification: Counter = Counter()
    ingested_documents: set[str] = set()

    for doc_path in sorted(doc_files):
        try:
            content = doc_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            logger.exception("Could not read document: %s", doc_path)
            raise
        meta, body = parse_doc_frontmatter(content)

        doc_id = meta.get("doc_id") or doc_path.stem
        title = meta.get("title") or doc_path.stem
        min_role = meta.get("min_role", "Analyst")
        classification = meta.get("classification") or _classification_for_role(min_role)
        department = meta.get("department", "")
        source = meta.get("source") or doc_path.parent.name
        is_injection_fixture = str(meta.get("is_injection_fixture", "false")).lower() == "true"

        if min_role not in settings.roles_hierarchy:
            min_role = _minimum_role_for_classification(classification)

        raw_chunks = chunk_text(body)
        chunks = raw_chunks[:max_chunks_per_doc]
        if chunks:
            ingested_documents.add(doc_id)
            document_counts[(department or "Unassigned", classification)] += 1
            documents_by_classification[classification] += 1

        for idx, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}__chunk_{idx}"
            chunk_meta: Dict[str, Any] = {
                "doc_id": doc_id,
                "title": title,
                "source": source,
                "min_role": min_role,
                "department": department,
                "classification": classification,
                "source_type": meta.get("source_type", ""),
                "exec_readable": str(meta.get("exec_readable", "false")).lower() == "true",
                "chunk_index": idx,
                "is_injection_fixture": is_injection_fixture,
            }

            all_ids.append(chunk_id)
            all_texts.append(chunk)
            all_metas.append(chunk_meta)
            chunk_counts[(department or "Unassigned", classification)] += 1
            chunks_by_classification[classification] += 1

    with _CURATED_DOCS_FILE.open(encoding="utf-8") as curated_file:
        for line_number, line in enumerate(curated_file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            source_meta = row.get("metadata", {})
            doc_id = row.get("document_id") or source_meta.get("document_id")
            body = row.get("text", "").strip()
            if not doc_id or not body:
                logger.warning("Skipping curated record at line %d without id or text", line_number)
                continue

            classification_data = classify_document(row)
            classification = classification_data["classification"]
            min_role = _minimum_role_for_classification(classification)
            department = classification_data["owner_department"]
            chunks = chunk_text(body)[:max_chunks_per_doc]
            if chunks:
                ingested_documents.add(doc_id)
                document_counts[(department or "Unassigned", classification)] += 1
                documents_by_classification[classification] += 1
                curated_documents_by_classification[classification] += 1

            for idx, chunk in enumerate(chunks):
                all_ids.append(f"{doc_id}__chunk_{idx}")
                all_texts.append(chunk)
                all_metas.append(
                    {
                        "doc_id": doc_id,
                        "title": source_meta.get("title") or doc_id,
                        "source": source_meta.get("source") or source_meta.get("source_organization", ""),
                        "min_role": min_role,
                        "department": department,
                        "classification": classification,
                        "source_type": source_meta.get("source_type", "curated"),
                        "exec_readable": classification_data["exec_readable"],
                        "chunk_index": idx,
                        "is_injection_fixture": False,
                    }
                )
                chunk_counts[(department or "Unassigned", classification)] += 1
                chunks_by_classification[classification] += 1
                curated_chunks_by_classification[classification] += 1

    logger.info(
        "Embedding %d chunks from %d documents (local fixtures: %d; curated JSONL: %s)",
        len(all_texts),
        len(ingested_documents),
        len(doc_files),
        _CURATED_DOCS_FILE,
    )

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

    logger.info(
        "Ingest complete: %d chunks indexed from %d documents into ChromaDB",
        len(all_ids),
        len(ingested_documents),
    )
    canaries_found = {
        token: any(token in chunk for chunk in all_texts)
        for token in canary_registry
    }
    canaries_by_department_classification: Counter = Counter()
    for token, found in canaries_found.items():
        if not found:
            continue
        matching_metadata = next(
            meta for chunk, meta in zip(all_texts, all_metas) if token in chunk
        )
        canaries_by_department_classification[
            (matching_metadata.get("department") or "Unassigned", matching_metadata.get("classification", "Unknown"))
        ] += 1
    missing_canaries = [token for token, found in canaries_found.items() if not found]
    if missing_canaries:
        raise RuntimeError(
            "Canary tokens are missing from ingested chunks: "
            + ", ".join(missing_canaries)
        )
    summary = {
        "curated_file": str(_CURATED_DOCS_FILE),
        "documents": len(ingested_documents),
        "chunks": len(all_ids),
        "documents_by_classification": dict(sorted(documents_by_classification.items())),
        "chunks_by_classification": dict(sorted(chunks_by_classification.items())),
        "curated_documents_by_classification": dict(sorted(curated_documents_by_classification.items())),
        "curated_chunks_by_classification": dict(sorted(curated_chunks_by_classification.items())),
        "documents_by_department_classification": {
            f"{department} / {classification}": count
            for (department, classification), count in sorted(document_counts.items())
        },
        "chunks_by_department_classification": {
            f"{department} / {classification}": count
            for (department, classification), count in sorted(chunk_counts.items())
        },
        "canaries_by_department_classification": {
            f"{department} / {classification}": count
            for (department, classification), count in sorted(canaries_by_department_classification.items())
        },
        "canaries_registered": len(canary_registry),
        "canaries_found_in_ingested_chunks": sum(canaries_found.values()),
        "missing_canaries": missing_canaries,
        "indexed_metadata_records": store.count,
    }
    logger.info("Ingestion summary: %s", json.dumps(summary, sort_keys=True))
    return len(all_ids)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    force = "--force" in sys.argv
    n = ingest_documents(force=force)
    print(f"\nIngested {n} chunks into ChromaDB from {_CURATED_DOCS_FILE}")
