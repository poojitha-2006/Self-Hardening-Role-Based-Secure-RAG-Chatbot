"""
redteam/runner.py
==================
Red-team runner: executes attacks against the live pipeline and records results.

The runner:
  1. Loads attacks from train/validation/test JSONL files (filtered by split)
  2. Optionally generates mutations
  3. Sends each attack as a low-privilege user via the HTTP API
  4. Detects success via canary.py
  5. Stores successful attacks in the attack_store (for adaptive guardrail)
  6. Returns a structured report

Can be called from:
  - CLI: python -m redteam.runner
  - FastAPI: POST /admin/redteam/run
  - Eval: run_experiment.py
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.schemas import RedTeamReport, RedTeamResult
from redteam.canary import AttackResult
from redteam.dataset import load_attack_dataset

logger = logging.getLogger(__name__)

_API_BASE = "http://localhost:8000"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Seeded user credentials for each role
_ROLE_CREDENTIALS = {
    "Analyst": ("carol", "password"),
    "Manager": ("bob", "password"),
    "Administrator": ("alice", "password"),
}


def _load_attacks(split: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load the selected canonical JSONL split or all split files."""
    return load_attack_dataset(split)


def _validate_local_target(base_url: str) -> None:
    host = urlsplit(base_url).hostname
    if host not in _LOCAL_HOSTS:
        raise ValueError("The red-team runner is restricted to localhost targets.")


def _login(role: str, base_url: str = _API_BASE) -> Optional[str]:
    """Obtain a JWT token for the given role."""
    username, password = _ROLE_CREDENTIALS.get(role, ("carol", "password"))
    try:
        resp = httpx.post(
            f"{base_url}/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
    except Exception as e:
        logger.error("Login failed for role=%s: %s", role, e)
        return None


def _send_attack(
    attack_text: str,
    token: str,
    base_url: str = _API_BASE,
) -> Dict[str, Any]:
    """Send an attack query to the chat endpoint and return the response."""
    try:
        resp = httpx.post(
            f"{base_url}/chat",
            json={"message": attack_text, "session_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.llm_timeout + 30,
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            return {
                "answer": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "blocked": True,
                "block_reason": f"HTTP error {resp.status_code}",
                "guardrail_decisions": [],
                "latency_ms": 0,
            }
    except Exception as e:
        logger.error("Attack request failed: %s", e)
        return {
            "answer": f"Request failed: {e}",
            "blocked": True,
            "block_reason": "Connection error",
            "guardrail_decisions": [],
            "latency_ms": 0,
        }


def _store_successful_attack(
    attack: Dict[str, Any],
    variant_text: str,
    result: AttackResult,
    round_num: int,
) -> None:
    """Store a successful attack in the adaptive guardrail's attack store."""
    try:
        from app.rag.embedder import get_embedder
        from app.store.attack_store import get_attack_store

        embedder = get_embedder()
        attack_store = get_attack_store()

        embedding = embedder.embed_one(variant_text)
        attack_id = f"rt_{round_num}_{attack['id']}_{int(time.time())}"

        metadata = {
            "attack_id": attack["id"],
            "category": attack.get("category", "unknown"),
            "target_role": attack.get("target_role", "unknown"),
            "target_canary": attack.get("target_canary", ""),
            "attacker_role": result.attacker_role,
            "source": attack.get("source", "unknown"),
            "source_split": attack.get("split"),
            "round": round_num,
            "timestamp": time.time(),
        }
        attack_store.add_attack(
            attack_id=attack_id,
            embedding=embedding,
            variant_text=variant_text,
            metadata=metadata,
        )
        logger.info("Stored successful attack: id=%s category=%s", attack_id, metadata["category"])
    except Exception as e:
        logger.error("Failed to store attack in attack_store: %s", e)


def _is_held_out_attack(attack: Dict[str, Any], requested_split: Optional[str]) -> bool:
    return requested_split in {"validation", "test"} or attack.get("split") in {
        "validation",
        "test",
    }


def _is_training_attack(attack: Dict[str, Any], requested_split: Optional[str]) -> bool:
    return requested_split in {None, "train"} and attack.get("split") == "train"


def run_redteam_round(
    rounds: int = 1,
    attacker_role: str = "Analyst",
    max_attacks: Optional[int] = None,
    include_mutations: bool = False,
    split: Optional[str] = None,
    base_url: str = _API_BASE,
    round_number: int = 1,
) -> RedTeamReport:
    """
    Execute a full red-team round.

    Args:
        rounds: Number of times to repeat the attack set.
        attacker_role: The role to attack as.
        max_attacks: Limit total attacks (None = all).
        include_mutations: Whether to generate mutation variants.
        split: Filter attacks by split ('train', 'validation', 'test', or None for all).
        base_url: API base URL.
        round_number: Current round number (for metadata).

    Returns:
        RedTeamReport with results.
    """
    _validate_local_target(base_url)
    from app.store.attack_store import get_attack_store

    get_attack_store().remove_nontraining_attacks()
    attacks = _load_attacks(split=split)
    if max_attacks:
        attacks = attacks[:max_attacks]

    if include_mutations:
        from redteam.mutator import generate_all_variants
        mutations = generate_all_variants(attacks, seed=settings.redteam_seed)
        attacks = attacks + mutations[:max_attacks * 2 if max_attacks else len(mutations)]

    # Filter by target role (only run attacks aimed at the attacker's role or below)
    # Note: We also keep attacks with no role restriction
    attacks = [
        a for a in attacks
        if a.get("target_role", attacker_role) == attacker_role
        or a.get("target_role") is None
    ]

    logger.info(
        "Red-team round %d: %d attacks as %s",
        round_number, len(attacks), attacker_role,
    )

    # Login as attacker
    token = _login(attacker_role, base_url)
    if not token:
        logger.error("Could not obtain token for role=%s", attacker_role)
        return RedTeamReport(
            round_number=round_number,
            total_attacks=0,
            successful_attacks=0,
            attack_success_rate=0.0,
            results=[],
            new_attacks_stored=0,
            attack_store_size=0,
        )

    results: List[RedTeamResult] = []
    successful_attacks = 0
    new_attacks_stored = 0

    for attack in attacks:
        variant_text = attack["text"]

        raw_response = _send_attack(variant_text, token, base_url)
        answer = raw_response.get("answer", "")
        blocked = raw_response.get("blocked", False)
        guardrail_decisions = raw_response.get("guardrail_decisions", [])
        latency_ms = raw_response.get("latency_ms", 0.0)

        # Determine which layer blocked (if any)
        blocked_by = None
        if blocked:
            for gd in guardrail_decisions:
                if gd.get("decision") == "block":
                    blocked_by = gd.get("layer")
                    break

        # Canary/success detection
        ar = AttackResult(
            attack_id=attack["id"],
            attack_text=variant_text,
            category=attack.get("category", "unknown"),
            attacker_role=attacker_role,
            target_canary=attack.get("target_canary"),
            response=answer,
            blocked=blocked,
            blocked_by=blocked_by,
            latency_ms=latency_ms,
            guardrail_decisions=guardrail_decisions,
        )

        if ar.success:
            successful_attacks += 1
            if _is_training_attack(attack, split):
                _store_successful_attack(attack, variant_text, ar, round_number)
                new_attacks_stored += 1
                logger.warning(
                    "ATTACK SUCCESS: id=%s category=%s canaries=%s",
                    attack["id"], attack.get("category"), ar.leaked_canaries,
                )
        results.append(
            RedTeamResult(
                attack_id=attack["id"],
                category=attack.get("category", "unknown"),
                attacker_role=attacker_role,
                variant=variant_text[:200],
                response_preview=answer[:300],
                success=ar.success,
                blocked_by=blocked_by,
                latency_ms=latency_ms,
            )
        )

    total = len(results)
    asr = successful_attacks / total if total > 0 else 0.0

    # Get current attack store size
    try:
        store_size = get_attack_store().count
    except Exception:
        store_size = 0

    logger.info(
        "Round %d complete: total=%d success=%d ASR=%.2f%%",
        round_number, total, successful_attacks, asr * 100,
    )

    return RedTeamReport(
        round_number=round_number,
        total_attacks=total,
        successful_attacks=successful_attacks,
        attack_success_rate=round(asr, 4),
        results=results,
        new_attacks_stored=new_attacks_stored,
        attack_store_size=store_size,
    )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    role = sys.argv[1] if len(sys.argv) > 1 else "Analyst"
    split = sys.argv[2] if len(sys.argv) > 2 else None
    report = run_redteam_round(attacker_role=role, split=split)
    print(f"\nRound complete: ASR={report.attack_success_rate:.1%}")
    print(f"Successful attacks: {report.successful_attacks}/{report.total_attacks}")
    print(f"Attack store size: {report.attack_store_size}")
