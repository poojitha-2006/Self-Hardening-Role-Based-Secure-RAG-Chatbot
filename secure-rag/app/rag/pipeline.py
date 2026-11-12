"""
app/rag/pipeline.py
====================
The complete RAG pipeline: input guard → retrieval → context guard → LLM → output guard → adaptive guard.

This module orchestrates all layers and returns a structured response.
The pipeline is designed to be testable with each layer independently toggleable
via the guardrail_mode config setting.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.guardrails.adaptive import AdaptiveGuardResult, check_adaptive
from app.guardrails.context_guard import scan_context
from app.guardrails.input_guard import check_input
from app.guardrails.output_guard import check_output, _load_canary_registry
from app.rag.embedder import get_embedder
from app.rag.llm import get_llm
from app.rag.retriever import format_sources, retrieve
from app.schemas import ChatResponse, GuardrailDecision, SourceChunk

logger = logging.getLogger(__name__)

# In-memory stats (reset on restart — use a DB in production)
_stats: Dict[str, Any] = {
    "total_queries": 0,
    "blocked_queries": 0,
    "recent_blocks": [],
}


def get_stats() -> Dict[str, Any]:
    return dict(_stats)


def _make_guard_decision(
    layer: str,
    allowed: bool,
    score: float,
    reason: str,
    triggered_rules: List[str] = None,
) -> GuardrailDecision:
    return GuardrailDecision(
        layer=layer,
        decision="allow" if allowed else "block",
        score=round(score, 3),
        reason=reason,
        triggered_rules=triggered_rules or [],
    )


def run_pipeline(
    message: str,
    user_role: str,
    username: str,
    session_id: Optional[str] = None,
    user_department: Optional[str] = None,
) -> ChatResponse:
    """
    Execute the full RAG pipeline for a user query.

    Pipeline stages:
      1. Adaptive guard (pre-retrieval — fast rejection of known attacks)
      2. Input guard (static rules)
      3. RBAC-enforced retrieval
      4. Context guard (injection scan on retrieved chunks)
      5. LLM generation
      6. Output guard (canary/secret leak scan)

    Returns a ChatResponse with answer, sources, guardrail decisions, and latency.
    """
    start_time = time.perf_counter()
    guardrail_decisions: List[GuardrailDecision] = []
    _stats["total_queries"] += 1

    # Pre-compute query embedding (reused across stages to avoid re-embedding)
    query_embedding: Optional[List[float]] = None
    try:
        embedder = get_embedder()
        query_embedding = embedder.embed_one(message)
    except Exception as e:
        logger.error("Failed to embed query: %s", e)

    # ── Stage 0: Adaptive Guard (pre-retrieval) ──────────────────────────────
    if settings.guardrail_mode == "adaptive":
        adaptive_result = check_adaptive(query=message, query_embedding=query_embedding)
        guardrail_decisions.append(
            _make_guard_decision(
                layer="adaptive",
                allowed=adaptive_result.allowed,
                score=adaptive_result.risk_score,
                reason="; ".join(adaptive_result.reasons) if adaptive_result.reasons else "No known attack match",
                triggered_rules=adaptive_result.triggered_rules,
            )
        )
        if not adaptive_result.allowed:
            return _blocked_response(
                reason="Adaptive guard: " + "; ".join(adaptive_result.reasons),
                guardrail_decisions=guardrail_decisions,
                start_time=start_time,
                username=username,
                message=message,
                user_role=user_role,
            )

    # ── Stage 1: Input Guard (static rules) ──────────────────────────────────
    if settings.guardrail_mode in ("static", "adaptive"):
        input_result = check_input(message)
        guardrail_decisions.append(
            _make_guard_decision(
                layer="input",
                allowed=input_result.allowed,
                score=input_result.risk_score,
                reason="; ".join(input_result.reasons) if input_result.reasons else "Input clean",
                triggered_rules=input_result.triggered_rules,
            )
        )
        if not input_result.allowed:
            return _blocked_response(
                reason="Input guard: " + "; ".join(input_result.reasons),
                guardrail_decisions=guardrail_decisions,
                start_time=start_time,
                username=username,
                message=message,
                user_role=user_role,
            )

    # ── Stage 2: RBAC-Enforced Retrieval ────────────────────────────────────
    try:
        chunks = retrieve(
            query=message,
            user_role=user_role,
            n_results=settings.retrieval_n_results,
            user_department=user_department,
            enforce_access=settings.guardrail_mode != "none",
        )
    except Exception as e:
        logger.error("Retrieval failed: %s", e)
        chunks = []

    # ── Stage 3: Context Guard ───────────────────────────────────────────────
    if settings.guardrail_mode in ("static", "adaptive"):
        context_result = scan_context(chunks)
        safe_chunks = context_result.safe_chunks
        # Add flagged chunks back with warning (logged but passed to LLM with note)
        if context_result.injection_detected:
            guardrail_decisions.append(
                _make_guard_decision(
                    layer="context",
                    allowed=True,  # We quarantine, not block the whole query
                    score=1.0 if context_result.quarantined_chunks else 0.6,
                    reason="; ".join(context_result.reasons) if context_result.reasons else "Context clean",
                    triggered_rules=["context_injection_quarantine"] if context_result.quarantined_chunks else [],
                )
            )
        else:
            guardrail_decisions.append(
                _make_guard_decision(
                    layer="context",
                    allowed=True,
                    score=0.0,
                    reason="Context clean",
                    triggered_rules=[],
                )
            )
    else:
        safe_chunks = chunks

    sources = format_sources(safe_chunks)

    # ── Stage 4: LLM Generation ──────────────────────────────────────────────
    try:
        llm = get_llm()
        raw_answer = llm.generate(
            user_question=message,
            context_chunks=safe_chunks,
        )
    except Exception as e:
        logger.error("LLM generation failed: %s", e)
        raw_answer = "I'm sorry, I was unable to process your request at this time. Please try again."

    # ── Stage 5: Output Guard ─────────────────────────────────────────────────
    if settings.guardrail_mode in ("static", "adaptive"):
        canary_registry = _load_canary_registry()
        output_result = check_output(
            response_text=raw_answer,
            user_role=user_role,
            canary_registry=canary_registry,
        )
        guardrail_decisions.append(
            _make_guard_decision(
                layer="output",
                allowed=output_result.allowed,
                score=output_result.restricted_similarity,
                reason="; ".join(output_result.reasons) if output_result.reasons else "Output clean",
                triggered_rules=output_result.triggered_rules,
            )
        )
        final_answer = output_result.safe_response
        output_blocked = not output_result.allowed
    else:
        final_answer = raw_answer
        output_blocked = False

    latency_ms = (time.perf_counter() - start_time) * 1000

    decision_str = "block" if output_blocked else "allow"
    all_triggers = []
    for gd in guardrail_decisions:
        all_triggers.extend(gd.triggered_rules)

    if output_blocked:
        _stats["blocked_queries"] += 1
        _stats["recent_blocks"].append({
            "username": username,
            "message_preview": message[:80],
            "reason": "Output guard blocked",
            "timestamp": time.time(),
        })
        _stats["recent_blocks"] = _stats["recent_blocks"][-50:]  # Keep last 50

    _log_audit_to_sqlite(
        username=username,
        user_role=user_role,
        message=message,
        allowed_doc_count=len(safe_chunks),
        retrieved_chunk_count=len(chunks),
        decision=decision_str,
        triggers=all_triggers,
        blocked_reason="; ".join(output_result.reasons) if output_blocked else None,
    )

    logger.info(
        "Pipeline complete: user=%s role=%s latency=%.0fms blocked=%s",
        username, user_role, latency_ms, output_blocked,
    )

    return ChatResponse(
        answer=final_answer,
        sources=sources,
        guardrail_decisions=guardrail_decisions,
        blocked=output_blocked,
        block_reason="; ".join(output_result.reasons) if output_blocked else None,
        latency_ms=round(latency_ms, 1),
    )


def _log_audit_to_sqlite(
    username: str,
    user_role: str,
    message: str,
    allowed_doc_count: int,
    retrieved_chunk_count: int,
    decision: str,
    triggers: List[str],
    blocked_reason: Optional[str] = None,
) -> None:
    """Persist query audit record into SQLite rag_access.db audit_log table."""
    try:
        import sqlite3
        db_path = Path(__file__).resolve().parents[2] / "db" / "rag_access.db"
        if not db_path.exists():
            return
        conn = sqlite3.connect(str(db_path), timeout=3)
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        with conn:
            conn.execute(
                """INSERT INTO audit_log
                   (timestamp, username, role_id, query_text, allowed_doc_count, retrieved_chunk_count, decision, guardrail_triggers, blocked_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now_str,
                    username,
                    user_role,
                    message[:500],
                    allowed_doc_count,
                    retrieved_chunk_count,
                    decision,
                    ", ".join(triggers) if triggers else "none",
                    blocked_reason or "",
                ),
            )
    except Exception as e:
        logger.debug("Failed writing to SQLite audit_log: %s", e)


def _blocked_response(
    reason: str,
    guardrail_decisions: List[GuardrailDecision],
    start_time: float,
    username: str,
    message: str,
    user_role: str = "Analyst",
) -> ChatResponse:
    """Return a standardised block response."""
    _stats["blocked_queries"] += 1
    _stats["recent_blocks"].append({
        "username": username,
        "message_preview": message[:80],
        "reason": reason,
        "timestamp": time.time(),
    })
    _stats["recent_blocks"] = _stats["recent_blocks"][-50:]

    all_triggers = []
    for gd in guardrail_decisions:
        all_triggers.extend(gd.triggered_rules)

    _log_audit_to_sqlite(
        username=username,
        user_role=user_role,
        message=message,
        allowed_doc_count=0,
        retrieved_chunk_count=0,
        decision="block",
        triggers=all_triggers,
        blocked_reason=reason,
    )

    latency_ms = (time.perf_counter() - start_time) * 1000
    return ChatResponse(
        answer="I'm sorry, but I cannot process this request. It has been flagged by the security system.",
        sources=[],
        guardrail_decisions=guardrail_decisions,
        blocked=True,
        block_reason=reason,
        latency_ms=round(latency_ms, 1),
    )
