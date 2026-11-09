"""
eval/plots.py
=============
Generate evaluation plots from experiment results.

Plots:
  1. ASR vs Round (line chart)
  2. FPR vs Round (line chart)
  3. Per-category ASR heatmap (final round)
  4. Latency over rounds (bar chart)
  5. Attack store growth (line chart)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def generate_all_plots(
    metrics: List[Dict[str, Any]],
    output_dir: Path,
) -> None:
    """Generate all evaluation plots and save to output_dir."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available; skipping plots")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    rounds = [m["round"] for m in metrics]
    asr_values = [m["asr"] * 100 for m in metrics]
    fpr_values = [m["fpr"] * 100 for m in metrics]
    accuracy_values = [m.get("answer_accuracy", 0) * 100 for m in metrics]
    store_sizes = [m.get("attack_store_size", 0) for m in metrics]
    latency_means = [m.get("attack_latency_mean_ms", 0) for m in metrics]

    plt.style.use("seaborn-v0_8-darkgrid")
    colors = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12"]

    # ── Plot 1: ASR & FPR vs Round ────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(rounds, asr_values, "o-", color=colors[0], linewidth=2, markersize=8, label="ASR %")
    ax.plot(rounds, fpr_values, "s--", color=colors[1], linewidth=2, markersize=8, label="FPR %")
    ax.set_xlabel("Round", fontsize=12)
    ax.set_ylabel("Rate (%)", fontsize=12)
    ax.set_title("Attack Success Rate & False Positive Rate by Round", fontsize=14, fontweight="bold")
    ax.legend(fontsize=11)
    ax.set_xticks(rounds)
    ax.set_ylim(0, 100)
    ax.axvline(x=1.5, color="gray", linestyle=":", alpha=0.7, label="Adaptive starts")
    plt.tight_layout()
    plt.savefig(output_dir / "asr_fpr_by_round.png", dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("Saved asr_fpr_by_round.png")

    # ── Plot 2: Answer Accuracy vs Round ─────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(rounds, accuracy_values, color=colors[2], alpha=0.8)
    ax.set_xlabel("Round", fontsize=12)
    ax.set_ylabel("Accuracy (%)", fontsize=12)
    ax.set_title("Authorized Answer Accuracy by Round", fontsize=14, fontweight="bold")
    ax.set_xticks(rounds)
    ax.set_ylim(0, 100)
    for r, v in zip(rounds, accuracy_values):
        ax.text(r, v + 1, f"{v:.0f}%", ha="center", fontsize=10)
    plt.tight_layout()
    plt.savefig(output_dir / "accuracy_by_round.png", dpi=150, bbox_inches="tight")
    plt.close()

    # ── Plot 3: Per-category ASR heatmap (final round) ───────────────────────
    final_round = metrics[-1] if metrics else {}
    cat_asr = final_round.get("asr_by_category", {})
    if cat_asr:
        categories = list(cat_asr.keys())
        asr_cat_values = [v * 100 for v in cat_asr.values()]
        fig, ax = plt.subplots(figsize=(10, 4))
        bars = ax.barh(categories, asr_cat_values, color=[
            colors[0] if v > 50 else (colors[1] if v > 20 else colors[2])
            for v in asr_cat_values
        ], alpha=0.85)
        ax.set_xlabel("Attack Success Rate (%)", fontsize=12)
        ax.set_title(f"Per-Category ASR (Round {final_round.get('round', '?')})", fontsize=14, fontweight="bold")
        ax.set_xlim(0, 100)
        for bar, v in zip(bars, asr_cat_values):
            ax.text(v + 1, bar.get_y() + bar.get_height() / 2, f"{v:.0f}%", va="center", fontsize=10)
        plt.tight_layout()
        plt.savefig(output_dir / "category_asr_final_round.png", dpi=150, bbox_inches="tight")
        plt.close()

    # ── Plot 4: Attack Store Growth ───────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.fill_between(rounds, store_sizes, alpha=0.3, color=colors[3])
    ax.plot(rounds, store_sizes, "o-", color=colors[3], linewidth=2, markersize=8)
    ax.set_xlabel("Round", fontsize=12)
    ax.set_ylabel("Stored Attacks", fontsize=12)
    ax.set_title("Attack Store Growth Over Rounds", fontsize=14, fontweight="bold")
    ax.set_xticks(rounds)
    plt.tight_layout()
    plt.savefig(output_dir / "attack_store_growth.png", dpi=150, bbox_inches="tight")
    plt.close()

    # ── Plot 5: Latency over rounds ───────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(rounds, latency_means, color=colors[1], alpha=0.8)
    ax.set_xlabel("Round", fontsize=12)
    ax.set_ylabel("Mean Latency (ms)", fontsize=12)
    ax.set_title("Mean Attack Query Latency by Round", fontsize=14, fontweight="bold")
    ax.set_xticks(rounds)
    plt.tight_layout()
    plt.savefig(output_dir / "latency_by_round.png", dpi=150, bbox_inches="tight")
    plt.close()

    logger.info("All plots saved to %s", output_dir)
