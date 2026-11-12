"""Validate curated documents, attack splits, and benign evaluation rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOPICS = {
    "Executive", "HR/People", "Engineering", "Product", "AI/Data", "Cybersecurity/IT",
    "Finance", "Legal", "Sales", "Marketing", "Customer Support", "Procurement/Operations",
}
ATTACK_SPLITS = ("train", "validation", "test")
ATTACK_CATEGORIES = {
    "direct_prompt_injection", "jailbreak_roleplay", "role_escalation",
    "system_prompt_secret_extraction", "indirect_injection", "exfiltration_obfuscation",
    "direct_request",
}
ALLOWED_CROSS_SPLIT_SOURCES = {"handwritten", "legacy_handwritten"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no}: expected object")
            rows.append(row)
    return rows


def resolve_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def validate_curated(report: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    path = ROOT / "data/curated/documents.jsonl"
    errors: list[str] = []
    if not path.is_file():
        return ["Missing data/curated/documents.jsonl"], {}
    try:
        rows = load_jsonl(path)
    except (OSError, ValueError) as exc:
        return [f"Could not parse curated JSONL: {exc}"], {}
    ids: set[str] = set()
    topics: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    hashes: Counter[str] = Counter()
    for row in rows:
        doc_id = row.get("document_id")
        text = row.get("text")
        metadata = row.get("metadata")
        if not isinstance(doc_id, str) or not doc_id:
            errors.append("Curated row has missing document_id")
            continue
        if doc_id in ids:
            errors.append(f"Duplicate document_id: {doc_id}")
        ids.add(doc_id)
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{doc_id}: empty text")
            continue
        if len(text) < 500:
            errors.append(f"{doc_id}: text under 500 characters")
        if len(text) > 60_000:
            errors.append(f"{doc_id}: text exceeds 60000 characters")
        if not isinstance(metadata, dict):
            errors.append(f"{doc_id}: metadata missing")
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if metadata.get("content_hash") != digest:
            errors.append(f"{doc_id}: content_hash mismatch")
        hashes[digest] += 1
        if metadata.get("source") in {"GitLab", "GitLab Handbook sitemap"}:
            errors.append(f"{doc_id}: GitLab source label was not canonicalized")
        for field in ("raw_path", "processed_text_path"):
            resolved = resolve_path(metadata.get(field))
            if resolved is None or not resolved.is_file():
                errors.append(f"{doc_id}: missing {field}")
        for field in ("source_url", "license_or_usage", "source_organization", "department"):
            if not metadata.get(field):
                errors.append(f"{doc_id}: missing metadata {field}")
        topic = str(metadata.get("department", "Unknown"))
        if topic not in TOPICS:
            errors.append(f"{doc_id}: unknown topic {topic}")
        topics[topic] += 1
        source_counts[str(metadata.get("source", "Unknown"))] += 1
        if row.get("truncated") and len(text) != 60_000:
            errors.append(f"{doc_id}: truncated flag without 60000-character cap")
    if any(count > 1 for count in hashes.values()):
        errors.append("Duplicate content hashes remain in curated corpus")
    if len(rows) < 350:
        errors.append(f"Curated corpus has {len(rows)} documents; minimum is 350")
    legal_share = topics["Legal"] / len(rows) if rows else 0.0
    if legal_share > 0.20 and not report.get("required_expansion_command_not_run"):
        errors.append(f"Legal share is {legal_share:.1%}; maximum is 20% and no expansion plan documented")
    topic_shortfalls = {topic: 30 - topics[topic] for topic in TOPICS if topic != "Legal" and topics[topic] < 30}
    if topic_shortfalls and not report.get("nonlegal_topics_below_minimum"):
        errors.append(f"Non-Legal topic minimums unmet and not recorded in curation report: {topic_shortfalls}")
    return errors, {"documents": len(rows), "topics": dict(topics), "sources": dict(source_counts),
                   "legal_share": legal_share, "topic_shortfalls": topic_shortfalls}


def validate_attacks() -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    rows_by_split: dict[str, list[dict[str, Any]]] = {}
    for split in ATTACK_SPLITS:
        path = ROOT / f"data/attacks/{split}.jsonl"
        if not path.is_file():
            errors.append(f"Missing attack split: {path.relative_to(ROOT)}")
            rows_by_split[split] = []
            continue
        try:
            rows_by_split[split] = load_jsonl(path)
        except (OSError, ValueError) as exc:
            errors.append(f"Invalid attack split {split}: {exc}")
            rows_by_split[split] = []
    manifest_path = ROOT / "data/attacks/manifest.json"
    if not manifest_path.is_file():
        errors.append("Missing data/attacks/manifest.json")
        manifest: dict[str, Any] = {}
    else:
        manifest = load_json(manifest_path)
    seen_ids: set[str] = set()
    source_splits: dict[str, set[str]] = {}
    text_splits: dict[str, str] = {}
    category_counts: dict[str, dict[str, int]] = {}
    for split, rows in rows_by_split.items():
        categories: Counter[str] = Counter()
        for row in rows:
            for field in ("id", "source", "split", "category", "text", "language", "license"):
                if not row.get(field):
                    errors.append(f"{split}: attack missing {field}")
            if row.get("split") != split:
                errors.append(f"{row.get('id')}: row is in {split} but declares {row.get('split')}")
            if row.get("language") != "en":
                errors.append(f"{row.get('id')}: non-English row retained")
            if row.get("category") not in ATTACK_CATEGORIES:
                errors.append(f"{row.get('id')}: category outside threat model")
            if row.get("id") in seen_ids:
                errors.append(f"Duplicate attack ID: {row.get('id')}")
            seen_ids.add(row.get("id"))
            normalized_text = str(row.get("text", "")).strip().casefold()
            previous_split = text_splits.get(normalized_text)
            if previous_split and previous_split != split:
                errors.append(f"Duplicate attack text crosses {previous_split}/{split}: {row.get('id')}")
            text_splits[normalized_text] = split
            source_splits.setdefault(str(row.get("source")), set()).add(split)
            categories[str(row.get("category"))] += 1
        category_counts[split] = dict(categories)
    overlaps = {source: sorted(splits) for source, splits in source_splits.items() if len(splits) > 1}
    unexpected_overlaps = {
        source: splits for source, splits in overlaps.items()
        if source not in ALLOWED_CROSS_SPLIT_SOURCES
    }
    if unexpected_overlaps:
        errors.append(f"Attack sources overlap splits: {unexpected_overlaps}")
    manifest_overlaps = set(manifest.get("source_split_overlap", []))
    if manifest_overlaps - ALLOWED_CROSS_SPLIT_SOURCES:
        errors.append("Attack manifest reports source split overlap")
    leakage = manifest.get("leakage_check", {})
    if leakage.get("status") != "completed":
        errors.append(f"Attack near-duplicate leakage check not completed: {leakage.get('reason', leakage.get('status'))}")
    counts = {split: len(rows) for split, rows in rows_by_split.items()}
    targets = {"train": 300, "validation": 100, "test": 300}
    for split, target in targets.items():
        if counts[split] < target and not manifest.get("shortfalls", {}).get(split):
            errors.append(f"Attack {split} has {counts[split]}/{target}; see manifest source availability")
    return errors, {"counts": counts, "categories": category_counts, "source_split_overlap": overlaps,
                   "leakage_check": leakage.get("status")}


def validate_benign() -> tuple[list[str], dict[str, Any]]:
    path = ROOT / "data/benign/benign_queries.jsonl"
    if not path.is_file():
        return ["Missing data/benign/benign_queries.jsonl"], {}
    rows = load_jsonl(path)
    curated_rows = load_jsonl(ROOT / "data/curated/documents.jsonl")
    docs = {row.get("document_id"): row for row in curated_rows}
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from eval.build_benign_set import simulated_access_metadata

    errors: list[str] = []
    counts = Counter(row.get("case_type") for row in rows)
    for row in rows:
        for field in ("id", "query", "case_type", "should_be_refused", "classification", "department"):
            if field not in row:
                errors.append(f"Benign row missing {field}")
        if row.get("case_type") in {"benign_qa", "over_blocking_test", "unauthorized_access_probe"}:
            doc_id = row.get("doc_id")
            document = docs.get(doc_id)
            if document is None:
                errors.append(f"{row.get('id')}: source document not found")
                continue
            access_metadata = simulated_access_metadata(document)
            source_department = document.get("metadata", {}).get("department") or access_metadata["owner_department"]
            if row.get("classification") != access_metadata["classification"]:
                errors.append(f"{row.get('id')}: classification does not match the simulated source policy")
            if row.get("department") != source_department:
                errors.append(f"{row.get('id')}: department does not match the source document")
            start = row.get("chunk_ref", {}).get("start_char")
            end = row.get("chunk_ref", {}).get("end_char")
            span = row.get("answer_span")
            if not isinstance(start, int) or not isinstance(end, int) or not isinstance(span, str):
                errors.append(f"{row.get('id')}: missing source span reference")
                continue
            source = document["text"][start:end]
            if span not in source:
                errors.append(f"{row.get('id')}: answer span not in cited source chunk")
            if any(keyword not in source for keyword in row.get("expected_keywords", [])):
                errors.append(f"{row.get('id')}: expected keyword not in cited source chunk")
        if row.get("case_type") == "unauthorized_access_probe":
            if row.get("should_be_refused") is not True:
                errors.append(f"{row.get('id')}: unauthorized probe must expect refusal")
            if row.get("classification") == "Public":
                errors.append(f"{row.get('id')}: public document must not be a refusal probe")
            if row.get("unauthorized_department") == row.get("department"):
                errors.append(f"{row.get('id')}: refusal probe must use a different department")
        elif row.get("case_type") in {"benign_qa", "over_blocking_test"} and row.get("should_be_refused") is not False:
            errors.append(f"{row.get('id')}: authorized benign question must not expect refusal")
    if counts["benign_qa"] < 150:
        errors.append(f"Only {counts['benign_qa']} generated benign QA rows; target is 150")
    if counts["over_blocking_test"] < 20:
        errors.append(f"Only {counts['over_blocking_test']} over-blocking rows; target is 20")
    report_path = ROOT / "data/benign/validation_report.json"
    if report_path.is_file():
        report = load_json(report_path)
        if len(report.get("spot_check_questions", [])) < min(20, counts["benign_qa"]):
            errors.append("Benign validation report lacks its random manual spot-check sample")
    else:
        errors.append("Missing data/benign/validation_report.json")
    return errors, {"rows": len(rows), "counts": dict(counts)}


def main() -> int:
    errors: list[str] = []
    summary: dict[str, Any] = {}
    report = load_json(ROOT / "data/curated/curation_report.json") if (ROOT / "data/curated/curation_report.json").is_file() else {}
    curated_errors, summary["curated"] = validate_curated(report)
    attack_errors, summary["attacks"] = validate_attacks()
    benign_errors, summary["benign"] = validate_benign()
    errors.extend(curated_errors + attack_errors + benign_errors)
    summary["passed"] = not errors
    summary["errors"] = errors
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())