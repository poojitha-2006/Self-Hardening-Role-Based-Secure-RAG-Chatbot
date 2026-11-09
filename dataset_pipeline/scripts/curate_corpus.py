"""Create a deterministic, bounded-size public corpus from processed JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INPUT_DEFAULT = ROOT / "data/processed/documents.jsonl"
OUTPUT_DEFAULT = ROOT / "data/curated/documents.jsonl"
REPORT_DEFAULT = ROOT / "data/curated/curation_report.json"
MIN_CHARS = 500
MAX_CHARS = 60_000
LEGAL_CUAD_LIMIT = 50
LEGAL_MAUD_LIMIT = 30
CHUNK_SIZE = 900
CHUNK_OVERLAP = 120
TARGETS = {
    "Executive": 30,
    "HR/People": 30,
    "Engineering": 30,
    "Product": 30,
    "AI/Data": 30,
    "Cybersecurity/IT": 30,
    "Finance": 30,
    "Legal": 30,
    "Sales": 30,
    "Marketing": 30,
    "Customer Support": 30,
    "Procurement/Operations": 30,
}

TOPIC_MAP = {
    "Executive and company strategy": "Executive",
    "Executive": "Executive",
    "Company-wide": "Executive",
    "People and HR": "HR/People",
    "People / HR": "HR/People",
    "Engineering and software development": "Engineering",
    "Engineering": "Engineering",
    "Product management": "Product",
    "Product": "Product",
    "AI, data and analytics": "AI/Data",
    "AI / Data": "AI/Data",
    "Cybersecurity and IT": "Cybersecurity/IT",
    "Cybersecurity": "Cybersecurity/IT",
    "Finance and accounting": "Finance",
    "Finance": "Finance",
    "Legal, contracts and compliance": "Legal",
    "Legal": "Legal",
    "Sales and customer success": "Sales",
    "Sales": "Sales",
    "Marketing and communications": "Marketing",
    "Marketing": "Marketing",
    "Customer support": "Customer Support",
    "Procurement and operations": "Procurement/Operations",
    "Operations / IT": "Procurement/Operations",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected an object at {path}:{line_number}")
            rows.append(row)
    return rows


def topic_for(metadata: dict[str, Any]) -> str:
    value = metadata.get("department") or metadata.get("topic")
    return TOPIC_MAP.get(str(value), str(value) if value else "Unknown")


def source_group(row: dict[str, Any]) -> str:
    metadata = row.get("metadata", {})
    document_id = str(row.get("document_id", ""))
    source = str(metadata.get("source_organization", metadata.get("source", "Unknown")))
    if document_id.startswith("CUAD-"):
        return "CUAD"
    if document_id.startswith("MAUD-"):
        return "MAUD"
    if source in {"GitLab", "GitLab Handbook", "GitLab Handbook sitemap"}:
        return "GitLab Handbook"
    return source


def validate_source_row(row: dict[str, Any], root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    document_id = str(row.get("document_id", "<missing-id>"))
    metadata = row.get("metadata")
    text = row.get("text")
    if not isinstance(metadata, dict):
        return [f"{document_id}: metadata must be an object"]
    if not isinstance(text, str) or not text.strip():
        errors.append(f"{document_id}: text is empty")
        return errors
    content_hash = metadata.get("content_hash")
    actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if content_hash != actual_hash:
        errors.append(f"{document_id}: text content_hash mismatch")
    for field in ("raw_path", "processed_text_path"):
        value = metadata.get(field)
        if not isinstance(value, str) or not value:
            errors.append(f"{document_id}: missing {field}")
            continue
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            errors.append(f"{document_id}: {field} does not exist: {value}")
    if not metadata.get("source_url"):
        errors.append(f"{document_id}: missing source_url")
    if not metadata.get("license_or_usage"):
        errors.append(f"{document_id}: missing license_or_usage")
    if not metadata.get("source_organization"):
        errors.append(f"{document_id}: missing source_organization")
    return errors


def estimated_chunks(text: str) -> int:
    length = len(text)
    if length <= CHUNK_SIZE:
        return 1 if length else 0
    return 1 + math.ceil((length - CHUNK_SIZE) / (CHUNK_SIZE - CHUNK_OVERLAP))


def deduplicate_and_filter(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counters = {"input": len(rows), "short_dropped": 0, "duplicate_dropped": 0, "invalid_dropped": 0}
    seen: set[str] = set()
    eligible: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        text = row.get("text")
        if not isinstance(text, str) or len(text.strip()) < MIN_CHARS:
            counters["short_dropped"] += 1
            continue
        errors = validate_source_row(row)
        if errors:
            counters["invalid_dropped"] += 1
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in seen:
            counters["duplicate_dropped"] += 1
            continue
        seen.add(digest)
        canonical_source = source_group(row)
        canonical_org = "GitLab Handbook" if metadata.get("source_organization") in {"GitLab", "GitLab Handbook sitemap"} else metadata.get("source_organization", canonical_source)
        normalized = dict(row)
        normalized["metadata"] = {**metadata, "department": topic_for(metadata), "source": canonical_source, "source_organization": canonical_org}
        eligible.append(normalized)
    return eligible, counters


def sample_legal(eligible: list[dict[str, Any]], seed: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    legal_handbook = [row for row in eligible if topic_for(row["metadata"]) == "Legal" and source_group(row) != "CUAD" and source_group(row) != "MAUD"]
    cuad = [row for row in eligible if topic_for(row["metadata"]) == "Legal" and source_group(row) == "CUAD"]
    maud = [row for row in eligible if topic_for(row["metadata"]) == "Legal" and source_group(row) == "MAUD"]
    rng = random.Random(seed)
    selected_cuad, cuad_strata = stratified_legal_sample(cuad, LEGAL_CUAD_LIMIT, rng, "CUAD")
    selected_maud, maud_strata = stratified_legal_sample(maud, LEGAL_MAUD_LIMIT, rng, "MAUD")
    return legal_handbook + selected_cuad + selected_maud, {
        "legal_handbook_kept": len(legal_handbook),
        "cuad_available": len(cuad),
        "cuad_kept": len(selected_cuad),
        "cuad_type_strata": cuad_strata,
        "maud_available": len(maud),
        "maud_kept": len(selected_maud),
        "maud_type_strata": maud_strata,
    }


def derived_contract_type(row: dict[str, Any], source: str) -> str:
    if source == "MAUD":
        return "merger_agreement_dataset"
    title = str(row.get("metadata", {}).get("title", "")).lower()
    groups = (
        ("merger_acquisition", ("merger", "acquisition", "stock purchase")),
        ("license", ("license", "licensing")),
        ("distribution_reseller", ("distribution", "reseller", "dealer")),
        ("employment", ("employment", "employee", "executive")),
        ("services_consulting", ("services", "service agreement", "consulting")),
        ("confidentiality_nda", ("non-disclosure", "nondisclosure", "confidentiality", "nda")),
        ("data_privacy", ("data processing", "privacy", "data protection")),
        ("lease_property", ("lease", "real estate", "property")),
        ("supply_purchase", ("purchase", "supply", "supplier")),
        ("partnership_alliance", ("partnership", "alliance", "joint venture")),
    )
    for label, terms in groups:
        if any(term in title for term in terms):
            return label
    return "other_or_unspecified"


def stratified_legal_sample(rows: list[dict[str, Any]], limit: int, rng: random.Random,
                            source: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        contract_type = derived_contract_type(row, source)
        strata[contract_type].append(row)
    for group in strata.values():
        rng.shuffle(group)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        added = False
        for contract_type in sorted(strata):
            if strata[contract_type] and len(selected) < limit:
                row = strata[contract_type].pop()
                normalized = {**row, "metadata": {**row["metadata"], "contract_type_derived_from_title": contract_type}}
                selected.append(normalized)
                added = True
        if not added:
            break
    counts = Counter(row["metadata"]["contract_type_derived_from_title"] for row in selected)
    return sorted(selected, key=lambda row: row["document_id"]), dict(sorted(counts.items()))


def truncate_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {**row, "metadata": dict(row["metadata"])}
    text = row["text"]
    if len(text) > MAX_CHARS:
        result["text"] = text[:MAX_CHARS]
        result["truncated"] = True
        result["metadata"]["truncated"] = True
        result["metadata"]["original_content_hash"] = result["metadata"].get("content_hash")
        result["metadata"]["content_hash"] = hashlib.sha256(result["text"].encode("utf-8")).hexdigest()
    else:
        result["truncated"] = False
        result["metadata"]["truncated"] = False
    return result


def build_report(rows: list[dict[str, Any]], filter_counts: dict[str, int], legal_counts: dict[str, int], seed: int) -> dict[str, Any]:
    topic_counts = Counter(topic_for(row["metadata"]) for row in rows)
    source_counts = Counter(source_group(row) for row in rows)
    chars_by_topic: Counter[str] = Counter()
    chunks_by_topic: Counter[str] = Counter()
    for row in rows:
        topic = topic_for(row["metadata"])
        chars_by_topic[topic] += len(row["text"])
        chunks_by_topic[topic] += estimated_chunks(row["text"])
    nonlegal_short = {topic: count for topic, count in TARGETS.items()
                      if topic != "Legal" and topic_counts[topic] < count}
    legal_count = topic_counts["Legal"]
    legal_share = legal_count / len(rows) if rows else 0.0
    sec_agent = os.environ.get("SEC_USER_AGENT")
    sec_filings_status = "SEC filings included" if sec_agent else "Finance is Handbook-based; SEC filings not included"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_path": "data/processed/documents.jsonl",
        "output_path": "data/curated/documents.jsonl",
        "seed": seed,
        "input_rows": filter_counts["input"],
        "curated_documents": len(rows),
        "filter_counts": filter_counts,
        "legal_selection": legal_counts,
        "documents_by_topic": {topic: topic_counts.get(topic, 0) for topic in TARGETS},
        "documents_by_source": dict(sorted(source_counts.items())),
        "characters_by_topic": {topic: chars_by_topic.get(topic, 0) for topic in TARGETS},
        "estimated_chunks_by_topic": {topic: chunks_by_topic.get(topic, 0) for topic in TARGETS},
        "total_characters": sum(len(row["text"]) for row in rows),
        "estimated_chunks": sum(estimated_chunks(row["text"]) for row in rows),
        "truncated_documents": sum(bool(row.get("truncated")) for row in rows),
        "legal_share": legal_share,
        "legal_share_limit": 0.20,
        "legal_share_target_met": legal_share <= 0.20,
        "minimum_documents_target": 350,
        "minimum_documents_met": len(rows) >= 350,
        "topic_minimum": 30,
        "nonlegal_topics_below_minimum": nonlegal_short,
        "topics_requiring_more_sources": nonlegal_short,
        "required_expansion_command_not_run": (
            "python scripts/fetch_data.py --max-handbook-pages 250 --enable-pdf"
            if nonlegal_short else None
        ),
        "sec_filings_status": sec_filings_status,
        "network_downloads_performed_by_curator": False,
        "raw_inputs_modified": False,
    }


def curate(input_path: Path = INPUT_DEFAULT, output_path: Path = OUTPUT_DEFAULT,
           report_path: Path = REPORT_DEFAULT, seed: int = 20261018) -> dict[str, Any]:
    rows = read_jsonl(input_path)
    eligible, filter_counts = deduplicate_and_filter(rows)
    legal, legal_counts = sample_legal(eligible, seed)
    nonlegal = [row for row in eligible if topic_for(row["metadata"]) != "Legal"]
    curated = [truncate_row(row) for row in sorted(nonlegal + legal, key=lambda row: row["document_id"])]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in curated:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = build_report(curated, filter_counts, legal_counts, seed)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--report", type=Path, default=REPORT_DEFAULT)
    parser.add_argument("--seed", type=int, default=20261018)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = curate(args.input, args.output, args.report, args.seed)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Curation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: report[key] for key in (
        "curated_documents", "documents_by_topic", "legal_share", "legal_share_target_met",
        "minimum_documents_met", "nonlegal_topics_below_minimum", "required_expansion_command_not_run",
        "sec_filings_status",
    )}, ensure_ascii=False, indent=2))
    if report.get("nonlegal_topics_below_minimum"):
        short = report["nonlegal_topics_below_minimum"]
        print(f"\nNon-Legal topics below target of 30 ({len(short)} topics):")
        for topic, target in short.items():
            print(f"  - {topic}: {report['documents_by_topic'].get(topic, 0)}/{target}")
        print("\nExpansion command (do NOT run without user confirmation):")
        print(f"  {report['required_expansion_command_not_run']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())