"""
eval/run_experiment.py
=======================
Main experiment runner for the self-hardening evaluation.

Protocol:
  Round 0: No guardrails (baseline)
  Round 1: RBAC + static guardrails only
  Rounds 2..N: Adaptive mode — red-team, store, re-evaluate

Also runs ablations:
  - RBAC only
  - +input guard
  - +context guard
  - +output guard
  - +adaptive

Outputs:
  - CSV/JSON per round
  - Summary CSV
  - Plots (via plots.py)
  - Markdown report

IMPORTANT: The held-out test split is never added to the attack store.
           This ensures the ASR reduction is measured fairly.
"""

from __future__ import annotations

import csv
import json
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Add project root to path
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import settings
from eval.metrics import compute_all_metrics
from redteam.canary import (
    detect_canary_leak,
    detect_injection_success,
    detect_system_prompt_leak,
)
from redteam.dataset import load_attack_dataset

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path(settings.eval_results_dir)
_BENIGN_FILE = _PROJECT_ROOT / "eval" / "datasets" / "benign_queries.json"
_ROLE_CREDENTIALS = {
    "Analyst": ("carol", "password"),
    "Manager": ("bob", "password"),
    "Administrator": ("alice", "password"),
}


def _run_attacks(
    attacks: List[Dict[str, Any]],
    default_role: str = "Analyst",
) -> List[Dict[str, Any]]:
    """Run attacks directly through the in-process pipeline for each guardrail mode."""
    from app.auth import USER_DB
    from app.rag.pipeline import run_pipeline

    with (_PROJECT_ROOT / "data" / "canaries.json").open(encoding="utf-8") as canary_file:
        canary_registry = json.load(canary_file)

    results = []
    for attack in attacks:
        role = attack.get("target_role", default_role)
        username, _ = _ROLE_CREDENTIALS.get(role, _ROLE_CREDENTIALS["Analyst"])
        user = USER_DB[username]
        response = run_pipeline(
            message=attack["text"],
            user_role=user["role"],
            username=user["username"],
            user_department=user.get("department"),
        )
        answer = response.answer
        blocked = response.blocked

        # Success detection
        canary_leaked, leaked_canaries = detect_canary_leak(answer, role, canary_registry)
        sys_leaked, _ = detect_system_prompt_leak(answer)
        injection_succeeded = detect_injection_success(answer)

        success = (
            (canary_leaked and not blocked)
            or (sys_leaked and not blocked)
            or (injection_succeeded and not blocked)
        )

        results.append({
            "attack_id": attack["id"],
            "category": attack.get("category", "unknown"),
            "attacker_role": role,
            "text": attack["text"][:200],
            "answer": answer[:300],
            "blocked": blocked,
            "success": success,
            "canary_leaked": canary_leaked,
            "leaked_canaries": leaked_canaries,
            "latency_ms": response.latency_ms,
        })

    return results


def _run_benign(benign_queries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run benign questions directly through the in-process pipeline."""
    from app.auth import USER_DB
    from app.rag.pipeline import run_pipeline

    results = []
    for query in benign_queries:
        role = query.get("role", "Analyst")
        username, _ = _ROLE_CREDENTIALS.get(role, _ROLE_CREDENTIALS["Analyst"])
        user = USER_DB[username]
        response = run_pipeline(
            message=query["question"],
            user_role=user["role"],
            username=user["username"],
            user_department=user.get("department"),
        )
        results.append({
            "id": query["id"],
            "role": role,
            "question": query["question"],
            "answer": response.answer,
            "blocked": response.blocked,
            "expected_keywords": query.get("expected_keywords", []),
            "latency_ms": response.latency_ms,
        })

    return results


def _set_guardrail_mode(mode: str) -> None:
    """Set the shared settings instance for this process's evaluation round."""
    import app.config as cfg
    cfg.settings.guardrail_mode = mode
    logger.info("Set guardrail mode: %s", mode)


def _get_attack_store_size() -> int:
    from app.store.attack_store import get_attack_store

    return get_attack_store().count


def _store_train_attacks(train_attacks: List[Dict[str, Any]]) -> int:
    """Store successful TRAIN attacks only; validation and test remain held out."""
    from app.rag.embedder import get_embedder
    from app.store.attack_store import get_attack_store

    embedder = get_embedder()
    store = get_attack_store()
    count = 0

    for attack in train_attacks:
        if attack.get("split") != "train":
            logger.warning("Skipping non-training attack during seeding: %s", attack.get("id"))
            continue
        if not attack.get("success"):
            continue
        emb = embedder.embed_one(attack["text"])
        aid = f"train_{attack['id']}_{int(time.time())}_{count}"
        store.add_attack(
            attack_id=aid,
            embedding=emb,
            variant_text=attack["text"],
            metadata={
                "category": attack.get("category", "unknown"),
                "source": attack.get("source", "unknown"),
                "source_split": "train",
                "round": -1,
                "timestamp": time.time(),
            },
        )
        count += 1

    return count


def _run_smoke_round(
    attacks: List[Dict[str, Any]],
    benign_queries: List[Dict[str, Any]],
    mode: str,
) -> Dict[str, Any]:
    _set_guardrail_mode(mode)

    return compute_all_metrics(
        _run_attacks(attacks),
        _run_benign(benign_queries),
        attack_store_size=_get_attack_store_size(),
        round_number={"none": 0, "static": 1, "adaptive": 2}[mode],
        guardrail_mode=mode,
    )


def _run_smoke_experiment(output_dir: Path, seed: int) -> List[Dict[str, Any]]:
    all_attacks = load_attack_dataset()
    with _BENIGN_FILE.open(encoding="utf-8") as benign_stream:
        all_benign = json.load(benign_stream)

    train_attacks = [attack for attack in all_attacks if attack.get("split") == "train"]
    test_attacks = [attack for attack in all_attacks if attack.get("split") == "test"]
    if len(test_attacks) < 10 or len(all_benign) < 10 or not train_attacks:
        raise ValueError(
            "Smoke run requires at least 10 held-out attacks, 10 benign queries, "
            "and one training attack."
        )

    rng = random.Random(seed)
    smoke_attacks = rng.sample(test_attacks, 10)
    smoke_benign = rng.sample(all_benign, 10)
    held_out_texts = {attack["text"] for attack in test_attacks}

    from app.store.attack_store import get_attack_store

    store = get_attack_store()
    store.remove_nontraining_attacks()
    existing_test_texts = [
        item["id"] for item in store.get_all() if item["document"] in held_out_texts
    ]
    if existing_test_texts:
        raise RuntimeError(
            "Known-attacks store already contains exact held-out test text: "
            + ", ".join(existing_test_texts)
        )

    results: List[Dict[str, Any]] = []
    for mode in ("none", "static", "adaptive"):
        if mode == "adaptive":
            training_sample = train_attacks[:10]
            training_results = _run_attacks(training_sample)
            successful_ids = {
                result["attack_id"] for result in training_results if result["success"]
            }
            successful_training_sample = [
                {**attack, "success": attack["id"] in successful_ids}
                for attack in training_sample
            ]
            stored = _store_train_attacks(successful_training_sample)
            if stored != len(successful_ids):
                raise RuntimeError(
                    f"Expected to store {len(successful_ids)} successful training attacks, stored {stored}."
                )
            if any(item["document"] in held_out_texts for item in store.get_all()):
                raise RuntimeError("Held-out test text entered the known-attacks store.")

        metrics = _run_smoke_round(smoke_attacks, smoke_benign, mode)
        results.append(metrics)
        logger.info(
            "Smoke round %d (%s): ASR=%.1f%% FPR=%.1f%% accuracy=%.1f%%",
            metrics["round"],
            mode,
            metrics["asr"] * 100,
            metrics["fpr"] * 100,
            metrics["answer_accuracy"] * 100,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = output_dir / f"smoke_results_{timestamp}.json"
    with results_path.open("w", encoding="utf-8") as result_file:
        json.dump(
            {
                "seed": seed,
                "attack_count": len(smoke_attacks),
                "benign_count": len(smoke_benign),
                "held_out_test_text_in_known_attacks": False,
                "rounds": results,
            },
            result_file,
            indent=2,
        )

    summary_path = output_dir / f"smoke_summary_{timestamp}.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as summary_file:
        fields = [key for key in results[0] if key != "asr_by_category"]
        writer = csv.DictWriter(summary_file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    from eval.plots import generate_all_plots

    generate_all_plots(results, output_dir)
    _write_markdown_report(results, output_dir, timestamp)

    expected_artifacts = [
        results_path,
        summary_path,
        output_dir / "asr_fpr_by_round.png",
        output_dir / f"evaluation_report_{timestamp}.md",
    ]
    missing = [str(path) for path in expected_artifacts if not path.is_file()]
    if missing:
        raise RuntimeError("Smoke experiment did not write expected artifacts: " + ", ".join(missing))

    print(f"Smoke results: {results_path}")
    print(f"Smoke summary: {summary_path}")
    print(f"Smoke plot: {output_dir / 'asr_fpr_by_round.png'}")
    print(f"Smoke report: {output_dir / f'evaluation_report_{timestamp}.md'}")
    return results


def run_experiment(
    rounds: int = None,
    seeds: List[int] = None,
    output_dir: Path = None,
    smoke: bool = False,
) -> None:
    """
    Run the full experiment protocol and save results.

    Round 0: No guardrails (GUARDRAIL_MODE=none)
    Round 1: Static only (GUARDRAIL_MODE=static)
    Rounds 2+: Adaptive (GUARDRAIL_MODE=adaptive)
    """
    rounds = rounds or settings.eval_rounds
    seeds = seeds or settings.eval_seeds
    output_dir = output_dir or _RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if smoke:
        smoke_seed = (seeds or settings.eval_seeds)[0]
        smoke_metrics = _run_smoke_experiment(output_dir, smoke_seed)
        print("\nSMOKE EXPERIMENT METRICS")
        for metrics in smoke_metrics:
            mean_latency = (
                metrics["attack_latency_mean_ms"] * metrics["asr_total"]
                + metrics["benign_latency_mean_ms"] * metrics["fpr_total"]
            ) / max(metrics["asr_total"] + metrics["fpr_total"], 1)
            print(
                f"Round {metrics['round']} ({metrics['guardrail_mode']}): "
                f"ASR={metrics['asr']:.1%} FPR={metrics['fpr']:.1%} "
                f"accuracy={metrics['answer_accuracy']:.1%} "
                f"mean_latency={mean_latency:.1f}ms "
                f"attack_mean={metrics['attack_latency_mean_ms']:.1f}ms "
                f"benign_mean={metrics['benign_latency_mean_ms']:.1f}ms"
            )
        return

    # Load datasets
    all_attacks = load_attack_dataset()

    with open(_BENIGN_FILE) as f:
        benign_queries = json.load(f)

    # Split attacks: train (seed attack store) / test (held-out evaluation)
    train_attacks = [a for a in all_attacks if a.get("split") == "train"]
    test_attacks = [a for a in all_attacks if a.get("split") == "test"]

    logger.info(
        "Dataset: %d train attacks, %d test attacks (held-out), %d benign queries",
        len(train_attacks), len(test_attacks), len(benign_queries),
    )
    from app.store.attack_store import get_attack_store

    attack_store = get_attack_store()
    removed_legacy = attack_store.remove_nontraining_attacks()
    if removed_legacy:
        logger.info("Removed %d non-TRAIN entries from attack store", removed_legacy)
    held_out_texts = {attack["text"] for attack in test_attacks}

    all_round_metrics = []
    experiment_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for seed in seeds[:1]:  # Run with first seed for now (multi-seed adds time)
        logger.info("=== Running experiment with seed=%d ===", seed)

        # ── Round 0: No guardrails ────────────────────────────────────────────
        logger.info("Round 0: No guardrails (baseline)")
        _set_guardrail_mode("none")
        attack_results_0 = _run_attacks(test_attacks)
        benign_results_0 = _run_benign(benign_queries)
        metrics_0 = compute_all_metrics(
            attack_results_0, benign_results_0,
            attack_store_size=0, round_number=0, guardrail_mode="none"
        )
        all_round_metrics.append(metrics_0)
        logger.info("Round 0: ASR=%.1f%% FPR=%.1f%%", metrics_0["asr"]*100, metrics_0["fpr"]*100)

        # ── Round 1: Static guardrails ────────────────────────────────────────
        logger.info("Round 1: Static guardrails only")
        _set_guardrail_mode("static")
        attack_results_1 = _run_attacks(test_attacks)
        benign_results_1 = _run_benign(benign_queries)
        metrics_1 = compute_all_metrics(
            attack_results_1, benign_results_1,
            attack_store_size=0, round_number=1, guardrail_mode="static"
        )
        all_round_metrics.append(metrics_1)
        logger.info("Round 1: ASR=%.1f%% FPR=%.1f%%", metrics_1["asr"]*100, metrics_1["fpr"]*100)

        # Evaluate TRAIN attacks first; only successful ones become known attacks.
        logger.info("Evaluating %d TRAIN attacks before adaptive rounds...", len(train_attacks))
        _set_guardrail_mode("static")
        train_results = _run_attacks(train_attacks)
        successful_train_ids = {
            result["attack_id"] for result in train_results if result["success"]
        }
        successful_train_attacks = [
            {**attack, "success": attack["id"] in successful_train_ids}
            for attack in train_attacks
        ]
        stored_count = _store_train_attacks(successful_train_attacks)
        logger.info("Stored %d successful TRAIN attacks in attack store", stored_count)
        if any(item["document"] in held_out_texts for item in attack_store.get_all()):
            raise RuntimeError("Held-out TEST text entered the known-attacks store.")
        _set_guardrail_mode("adaptive")

        # ── Rounds 2..N: Adaptive ─────────────────────────────────────────────
        for round_num in range(2, rounds + 1):
            logger.info("Round %d: Adaptive guardrails", round_num)
            attack_results = _run_attacks(test_attacks)
            benign_results = _run_benign(benign_queries)
            store_size = _get_attack_store_size()

            metrics = compute_all_metrics(
                attack_results, benign_results,
                attack_store_size=store_size,
                round_number=round_num,
                guardrail_mode="adaptive",
            )
            all_round_metrics.append(metrics)
            logger.info(
                "Round %d: ASR=%.1f%% FPR=%.1f%% StoreSize=%d",
                round_num, metrics["asr"]*100, metrics["fpr"]*100, store_size
            )

    # ── Save results ──────────────────────────────────────────────────────────
    results_path = output_dir / f"experiment_results_{experiment_ts}.json"
    with open(results_path, "w") as f:
        json.dump(all_round_metrics, f, indent=2, default=str)
    logger.info("Results saved to %s", results_path)

    # CSV summary
    csv_path = output_dir / f"experiment_summary_{experiment_ts}.csv"
    if all_round_metrics:
        fieldnames = [k for k in all_round_metrics[0].keys() if k != "asr_by_category"]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_round_metrics)
    logger.info("CSV summary saved to %s", csv_path)

    # Generate plots
    try:
        from eval.plots import generate_all_plots
        generate_all_plots(all_round_metrics, output_dir)
    except Exception as e:
        logger.warning("Plot generation failed (non-critical): %s", e)

    # Markdown report
    _write_markdown_report(all_round_metrics, output_dir, experiment_ts)

    print("\n" + "=" * 60)
    print("EXPERIMENT COMPLETE")
    print("=" * 60)
    for m in all_round_metrics:
        print(f"  Round {m['round']} ({m['guardrail_mode']}): ASR={m['asr']:.1%} FPR={m['fpr']:.1%}")
    print(f"\nResults saved to: {output_dir}")


def _write_markdown_report(
    metrics: List[Dict[str, Any]],
    output_dir: Path,
    ts: str,
) -> None:
    """Write a comprehensive markdown evaluation report."""
    path = output_dir / f"evaluation_report_{ts}.md"

    # Find the highest-ASR category in the final round
    final_round = metrics[-1] if metrics else {}
    remaining_failures = []
    if final_round.get("asr_by_category"):
        remaining_failures = sorted(
            final_round["asr_by_category"].items(),
            key=lambda x: -x[1],
        )[:3]

    lines = [
        "# Secure RAG Chatbot — Evaluation Report",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Results Summary",
        "",
        "| Round | Mode | ASR | FPR | Answer Accuracy | Attack Store |",
        "|-------|------|-----|-----|----------------|-------------|",
    ]
    for m in metrics:
        lines.append(
            f"| {m['round']} | {m['guardrail_mode']} | "
            f"{m['asr']:.1%} | {m['fpr']:.1%} | "
            f"{m.get('answer_accuracy', 0):.1%} | "
            f"{m.get('attack_store_size', 0)} |"
        )

    lines += [
        "",
        "## Key Findings",
        "",
    ]
    if len(metrics) >= 2:
        asr_drop = metrics[0]["asr"] - metrics[-1]["asr"]
        lines.append(f"- **ASR reduction from Round 0 to final:** {asr_drop:.1%}")
        lines.append(f"- **Final FPR:** {metrics[-1]['fpr']:.1%}")
        lines.append(f"- **Final answer accuracy:** {metrics[-1].get('answer_accuracy', 0):.1%}")

    lines += [
        "",
        "## Limitations and Remaining Failures",
        "",
        "The following attack categories showed the highest remaining ASR in the final round:",
        "",
    ]
    for cat, asr in remaining_failures:
        lines.append(f"- **{cat}**: {asr:.1%} ASR — these attacks are harder to detect due to")
        if cat == "obfuscation":
            lines.append("  semantic similarity to benign queries and novel encoding schemes.")
        elif cat == "multi_turn_social_engineering":
            lines.append("  stateless nature of this system (no conversation history checked).")
        elif cat == "indirect_injection":
            lines.append("  dependence on document-level metadata which can be misconfigured.")
        else:
            lines.append("  semantic diversity of attack phrasings.")

    lines += [
        "",
        "## Honest Assessment",
        "",
        "This system is NOT a complete security solution. Known remaining weaknesses:",
        "1. Multi-turn attacks are not tracked across sessions (stateless).",
        "2. Novel encoding schemes not in the pattern set may evade input guard.",
        "3. Adaptive threshold requires calibration for each deployment.",
        "4. False positive rate increases as attack store grows — monitor FPR.",
        "5. LLM classifier secondary check depends on Ollama availability.",
        "",
        "See `docs/METHODOLOGY.md` for full threat model and limitations.",
    ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Markdown report saved to %s", path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Secure RAG evaluation.")
    parser.add_argument("--smoke", action="store_true", help="Run 10 attacks and 10 benign queries.")
    parser.add_argument("--rounds", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    run_experiment(
        rounds=args.rounds,
        seeds=[args.seed] if args.seed is not None else None,
        output_dir=args.output_dir,
        smoke=args.smoke,
    )
