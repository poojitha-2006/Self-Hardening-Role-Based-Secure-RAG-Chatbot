"""Repair only attack IDs in the existing train/validation/test JSONL files."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redteam.build_attack_dataset import assign_attack_ids

ATTACKS_DIR = ROOT / "data" / "attacks"
SPLITS = ("train", "validation", "test")


def read_split(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"Expected JSON object at {path}:{line_number}")
        rows.append(row)
    return rows


def counts_by_split_and_source(
    rows_by_split: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, str], int]:
    return dict(Counter(
        (split, str(row.get("source", "")))
        for split, rows in rows_by_split.items()
        for row in rows
    ))


def main() -> int:
    paths = {split: ATTACKS_DIR / f"{split}.jsonl" for split in SPLITS}
    rows_by_split = {split: read_split(path) for split, path in paths.items()}
    before_counts = counts_by_split_and_source(rows_by_split)
    combined_rows = [
        row
        for split in SPLITS
        for row in rows_by_split[split]
    ]
    assign_attack_ids(combined_rows)

    ids = [row.get("id") for row in combined_rows]
    if len(ids) != len(set(ids)):
        raise RuntimeError("ID repair did not produce globally unique attack IDs")
    rewritten: dict[str, str] = {}
    offset = 0
    for split in SPLITS:
        rows = rows_by_split[split]
        rewritten[split] = "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in combined_rows[offset:offset + len(rows)]
        )
        offset += len(rows)

    after_counts = counts_by_split_and_source(rows_by_split)
    if before_counts != after_counts:
        raise RuntimeError("ID repair changed row counts by split or source")
    for split, path in paths.items():
        path.write_text(rewritten[split], encoding="utf-8", newline="\n")

    print("Rows by split/source before and after (must match):")
    for key in sorted(before_counts):
        split, source = key
        count = before_counts[key]
        print(f"{split}\t{source}\tbefore={count}\tafter={after_counts[key]}")
    print(f"Total rows: {len(combined_rows)}; unique IDs: {len(set(ids))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
