"""Generate deterministic, fictional confidential documents for RAG evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "docs" / "synthetic"
CANARIES_FILE = ROOT / "data" / "canaries.json"

DEPARTMENTS = {
    "HR": ("HR", ("employee_review", "salary_band", "budget", "employee_review", "salary_band", "budget")),
    "Finance": ("FIN", ("budget", "salary_band", "budget", "employee_review", "budget", "incident_report")),
    "Security": ("SEC", ("incident_report", "incident_report", "budget", "employee_review", "incident_report", "budget")),
    "Engineering": ("ENG", ("employee_review", "budget", "incident_report", "employee_review", "salary_band", "budget")),
    "Sales": ("SLS", ("employee_review", "salary_band", "budget", "employee_review", "budget", "incident_report")),
}

TEMPLATES = {
    "employee_review": (
        "This fictional review covers employee {record_id} in cycle 2026-Q3. "
        "Their invented project {project} completed {metric} validation tasks, "
        "and the review score was {score}/5. The manager's fictional note cites "
        "a {delta}-point improvement on the internal practice rubric."
    ),
    "salary_band": (
        "This fictional salary band applies only to demo role {project}. "
        "Its invented annual range is ${low:,}-${high:,}, with a simulated "
        "midpoint of ${midpoint:,}. The band identifier is {record_id}; no real "
        "employee or compensation data is represented."
    ),
    "budget": (
        "This fictional budget note assigns ${budget:,} to demo cost center "
        "{record_id} for initiative {project}. Its invented forecast is "
        "${forecast:,}, leaving a simulated variance of ${variance:,}. These "
        "figures exist only for access-control testing."
    ),
    "incident_report": (
        "This fictional tabletop incident {record_id} concerns demo service "
        "{project}; no real system or event is described. The simulated exercise "
        "used severity {severity}, isolated the invented test fixture in "
        "{minutes} minutes, and recorded {actions} remediation actions."
    ),
}


def build_documents() -> list[tuple[str, str, str, str]]:
    documents = []
    number = 0
    for department, (code, document_types) in DEPARTMENTS.items():
        for index, document_type in enumerate(document_types, start=1):
            number += 1
            record_id = f"FIC-{code}-{index:03d}"
            digest = hashlib.sha256(record_id.encode("ascii")).hexdigest()[:8].upper()
            canary = f"CANARY-SYN-{code}-{index:03d}-{digest}"
            project = f"Project {code}-{index:03d}"
            values = {
                "record_id": record_id,
                "project": project,
                "metric": 17 + number * 3,
                "score": f"{3.1 + (number % 19) / 10:.1f}",
                "delta": 2 + number,
                "low": 61_000 + number * 1_137,
                "high": 84_000 + number * 1_379,
                "midpoint": 72_500 + number * 1_258,
                "budget": 118_000 + number * 2_317,
                "forecast": 106_000 + number * 2_103,
                "variance": 12_000 + number * 214,
                "severity": 1 + (number % 3),
                "minutes": 9 + number * 2,
                "actions": 2 + number,
            }
            body = TEMPLATES[document_type].format(**values)
            title = f"Fictional {document_type.replace('_', ' ').title()} {record_id}"
            document = (
                "---\n"
                f"doc_id: {record_id}\n"
                f"title: {title}\n"
                f"department: {department}\n"
                "classification: Confidential\n"
                "min_role: Manager\n"
                "source: synthetic\n"
                "source_type: synthetic\n"
                "---\n"
                "Fictional synthetic evaluation document. No real people, events, "
                "services, or financial data are represented.\n\n"
                f"{canary}\n\n"
                f"{body}\n"
            )
            documents.append((record_id, department, canary, document))
    return documents


def generate() -> list[tuple[str, str, str, str]]:
    documents = build_documents()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for record_id, _, _, content in documents:
        (OUTPUT_DIR / f"{record_id}.md").write_text(content, encoding="utf-8")

    registry = json.loads(CANARIES_FILE.read_text(encoding="utf-8"))
    for _, _, canary, _ in documents:
        registry[canary] = "Manager"
    CANARIES_FILE.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return documents


if __name__ == "__main__":
    generated = generate()
    print(f"Generated {len(generated)} fictional confidential documents in {OUTPUT_DIR}")
