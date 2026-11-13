"""Measure authorized benign-question retrieval recall without LLM calls."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
BENIGN_FILE = REPOSITORY_ROOT / "dataset_pipeline" / "data" / "benign" / "benign_queries.jsonl"
CURATED_FILE = REPOSITORY_ROOT / "dataset_pipeline" / "data" / "curated" / "documents.jsonl"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _role_for_classification(classification: str) -> str:
    roles = {
        "Public": "Analyst",
        "Internal": "Analyst",
        "Confidential": "Manager",
        "Restricted": "Administrator",
    }
    try:
        return roles[classification]
    except KeyError as exc:
        raise ValueError(f"Unknown source classification: {classification}") from exc


def aggregate_hit_counts(
    results: Iterable[dict[str, Any]],
    group_field: str,
) -> dict[str, dict[int, tuple[int, int]]]:
    totals: dict[str, int] = defaultdict(int)
    hits: dict[str, dict[int, int]] = defaultdict(lambda: {1: 0, 5: 0, 10: 0})
    for result in results:
        group = str(result[group_field] or "Unassigned")
        ranks = result["ranks"]
        totals[group] += 1
        for cutoff in (1, 5, 10):
            if any(rank <= cutoff for rank in ranks):
                hits[group][cutoff] += 1
    return {
        group: {
            cutoff: (hits[group][cutoff], total)
            for cutoff in (1, 5, 10)
        }
        for group, total in sorted(totals.items())
    }


def _load_source_labels() -> dict[str, str]:
    labels: dict[str, str] = {}
    for row in _read_jsonl(CURATED_FILE):
        metadata = row.get("metadata", {})
        document_id = row.get("document_id") or metadata.get("document_id")
        source = (
            metadata.get("source")
            or metadata.get("source_organization")
            or metadata.get("source_type")
            or "Unknown"
        )
        if document_id:
            labels[str(document_id)] = str(source)
    return labels


def _retrieve_questions(embedder: Any) -> list[dict[str, Any]]:
    from app.rag.retriever import _build_rbac_filter, _can_access_chunk
    from app.store.vector_store import get_vector_store

    store = get_vector_store()
    source_labels = _load_source_labels()
    rows = [
        row for row in _read_jsonl(BENIGN_FILE)
        if row.get("case_type") == "benign_qa" and not row.get("should_be_refused", False)
    ]
    results = []
    for row in rows:
        classification = str(row.get("classification", "Public"))
        department = str(row.get("department") or "")
        role = _role_for_classification(classification)
        raw_chunks = store.query(
            query_embedding=embedder.embed_one(str(row["query"])),
            n_results=10,
            where=_build_rbac_filter(role, department),
        )
        chunks = [
            chunk for chunk in raw_chunks
            if _can_access_chunk(chunk["metadata"], role, department)
            and not chunk["metadata"].get("is_injection_fixture")
        ]
        target_doc_id = str(row.get("doc_id") or "")
        ranks = [
            rank for rank, chunk in enumerate(chunks, 1)
            if chunk["metadata"].get("doc_id") == target_doc_id
        ]
        results.append({
            "source": source_labels.get(target_doc_id, row.get("source_type", "Unknown")),
            "department": department or "Unassigned",
            "ranks": ranks,
        })
    return results


def _print_table(title: str, table: dict[str, dict[int, tuple[int, int]]]) -> None:
    print(title)
    print(f"{'Group':<36} {'hit@1':>12} {'hit@5':>12} {'hit@10':>12}")
    for group, counts in table.items():
        cells = []
        for cutoff in (1, 5, 10):
            hit_count, total = counts[cutoff]
            rate = hit_count / total if total else 0
            cells.append(f"{hit_count}/{total} ({rate:.1%})")
        print(f"{group[:36]:<36} {cells[0]:>12} {cells[1]:>12} {cells[2]:>12}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chroma-minilm",
        action="store_true",
        help="Use Chroma's ONNX MiniLM embedder to check an index built before migration.",
    )
    args = parser.parse_args(argv)

    if args.chroma_minilm:
        from app.rag.embedder import ChromaMiniLMEmbedder

        embedder = ChromaMiniLMEmbedder()
        embedding_label = "Chroma ONNX all-MiniLM-L6-v2"
    else:
        from app.rag.embedder import get_embedder

        embedder = get_embedder()
        embedding_label = type(embedder).__name__

    results = _retrieve_questions(embedder)
    print(f"Authorized benign questions: {len(results)}; embedder: {embedding_label}; LLM calls: 0")
    _print_table("By source", aggregate_hit_counts(results, "source"))
    _print_table("By department", aggregate_hit_counts(results, "department"))
    overall = [{"source": "All", "ranks": row["ranks"]} for row in results]
    _print_table("Overall", aggregate_hit_counts(overall, "source"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
