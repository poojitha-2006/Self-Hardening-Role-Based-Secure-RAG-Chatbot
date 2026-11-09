"""Export employee-authorized document chunks as JSONL for later vector indexing."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("chunk_documents")
INDEXABLE_STATUSES = {"downloaded", "partial", "synthetic_fixture"}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object at {path}")
    return value


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n\n", start + chunk_size // 2, end),
                           text.rfind(" ", start + chunk_size // 2, end))
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def document_rules(document_id: str, policy: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    for item in policy.get("documents", []):
        if item.get("document_id") == document_id:
            classification = item.get("classification")
            resource_type = item.get("resource_type")
            rules = policy.get("classifications", {}).get(classification, {}).get("resource_rules", {}).get(resource_type, {})
            return classification, rules
    return None, {}


def employee_can_read(record: dict[str, Any], employee: dict[str, Any], policy: dict[str, Any]) -> bool:
    classification = record.get("classification")
    if classification == "Public":
        return True
    if classification == "Internal":
        allowed_departments = policy.get("classifications", {}).get("Internal", {}).get("allowed_departments", [])
        return employee.get("department") in allowed_departments
    configured_classification, rules = document_rules(record.get("document_id", ""), policy)
    if configured_classification != classification:
        return False
    return employee.get("role_id") in rules.get("allowed_roles", []) and employee.get("department") in rules.get("allowed_departments", [])


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".json":
        payload = load_json(path)
        content = payload.get("content")
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
    return path.read_text(encoding="utf-8")


def export_chunks(
    employee_id: str,
    processed: dict[str, Any],
    employees: dict[str, Any],
    policy: dict[str, Any],
    chunk_size: int,
    overlap: int,
) -> list[dict[str, Any]]:
    employee = next((item for item in employees.get("employees", []) if item.get("employee_id") == employee_id), None)
    if employee is None:
        raise ValueError(f"Unknown employee ID: {employee_id}")
    results: list[dict[str, Any]] = []
    for record in processed.get("records", []):
        if record.get("download_status") not in INDEXABLE_STATUSES or not record.get("content_path"):
            continue
        if not employee_can_read(record, employee, policy):
            continue
        path = Path(record["content_path"])
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            LOG.warning("Skipping missing content for %s: %s", record.get("document_id"), path)
            continue
        try:
            text = read_document(path)
        except (OSError, ValueError) as exc:
            LOG.warning("Skipping unreadable content for %s: %s", record.get("document_id"), exc)
            continue
        metadata = record.get("rag_metadata") or {
            "document_id": record.get("document_id"), "title": record.get("title"),
            "department": record.get("department"), "source": record.get("source"),
            "source_url": record.get("url"), "classification": record.get("classification"),
            "record_type": record.get("record_type"), "is_synthetic": record.get("record_type") == "synthetic_test_record",
        }
        for chunk_index, chunk in enumerate(split_text(text, chunk_size, overlap)):
            results.append({
                "id": f"{record['document_id']}:{chunk_index}",
                "document_id": record["document_id"],
                "chunk_index": chunk_index,
                "text": chunk,
                "metadata": {**metadata, "employee_id": employee_id},
            })
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--employee-id", required=True, help="Employee ID from data/employees.json")
    parser.add_argument("--catalog", default="data/catalog_downloaded.json")
    parser.add_argument("--employees", default="data/employees.json")
    parser.add_argument("--policy", default="data/role_policy.yaml")
    parser.add_argument("--output", help="Output JSONL path (default: data/chunks/<employee-id>.jsonl)")
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--overlap", type=int, default=150)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.chunk_size <= 0 or args.overlap < 0 or args.overlap >= args.chunk_size:
        LOG.error("Chunk size must be positive and overlap must be in [0, chunk_size)")
        return 2
    try:
        processed = load_json(ROOT / args.catalog)
        employees = load_json(ROOT / args.employees)
        policy_value = yaml.safe_load((ROOT / args.policy).read_text(encoding="utf-8"))
        if not isinstance(policy_value, dict):
            raise ValueError("Role policy must contain a YAML object")
        chunks = export_chunks(args.employee_id, processed, employees, policy_value, args.chunk_size, args.overlap)
        output = (ROOT / args.output).resolve() if args.output else ROOT / "data/chunks" / f"{args.employee_id}.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in chunks), encoding="utf-8")
        LOG.info("Wrote %d authorized chunks for %s to %s", len(chunks), args.employee_id, output)
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        LOG.error("Chunk export failed: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())