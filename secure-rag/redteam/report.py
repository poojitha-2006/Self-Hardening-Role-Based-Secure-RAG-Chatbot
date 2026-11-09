"""
redteam/report.py
==================
Red-team report generation: saves results to CSV/JSON and generates a
structured summary with per-category breakdowns.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path(__file__).parent.parent / "eval" / "results"


def save_round_results(
    report_dict: Dict[str, Any],
    round_number: int,
    output_dir: Path = None,
) -> Path:
    """Save a round's results as JSON."""
    output_dir = output_dir or _RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"redteam_round_{round_number}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, default=str)
    logger.info("Saved red-team results to %s", path)
    return path


def format_report_summary(report_dict: Dict[str, Any]) -> str:
    """Return a markdown-formatted summary of the red-team round."""
    results = report_dict.get("results", [])

    # Per-category breakdown
    by_category: Dict[str, Dict[str, int]] = {}
    for r in results:
        cat = r.get("category", "unknown")
        if cat not in by_category:
            by_category[cat] = {"total": 0, "success": 0}
        by_category[cat]["total"] += 1
        if r.get("success"):
            by_category[cat]["success"] += 1

    lines = [
        f"# Red-Team Round {report_dict.get('round_number', '?')} Results",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Total Attacks:** {report_dict.get('total_attacks', 0)}",
        f"**Successful:** {report_dict.get('successful_attacks', 0)}",
        f"**ASR:** {report_dict.get('attack_success_rate', 0):.1%}",
        f"**Attack Store Size:** {report_dict.get('attack_store_size', 0)}",
        "",
        "## Per-Category Breakdown",
        "",
        "| Category | Total | Successful | ASR |",
        "|----------|-------|-----------|-----|",
    ]
    for cat, counts in sorted(by_category.items()):
        asr = counts["success"] / counts["total"] if counts["total"] > 0 else 0
        lines.append(f"| {cat} | {counts['total']} | {counts['success']} | {asr:.1%} |")

    return "\n".join(lines)


"""
redteam/__init__.py
"""
