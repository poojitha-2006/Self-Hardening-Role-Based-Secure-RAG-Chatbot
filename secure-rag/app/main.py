"""
app/main.py
===========
FastAPI application entry point.

Endpoints:
  POST /login                  — Get JWT token
  POST /chat                   — Main RAG chat (authenticated)
  GET  /health                 — Health check
  GET  /security/stats         — Security statistics (any auth user)
  POST /admin/redteam/run      — Trigger red-team round (Administrator only)
  GET  /admin/attacks          — Browse attack store (Administrator only)

Structured JSON logging is set up here.
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import USER_DB, authenticate_user, create_access_token, get_current_user, require_role
from app.config import settings
from app.rag.pipeline import get_stats, run_pipeline
from app.schemas import (
    ChatRequest,
    ChatResponse,
    LoginRequest,
    LoginResponse,
    RedTeamRunRequest,
    RedTeamReport,
    SecurityStats,
    TokenData,
)

# ── Logging setup ─────────────────────────────────────────────────────────────
_LOG_DIR = Path(settings.log_file).parent
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.log_file, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Secure RAG Chatbot",
    description="Enterprise RAG with RBAC, multi-layer guardrails, and self-hardening red-team loop.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request logging middleware ────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "HTTP %s %s → %d (%.0fms)",
        request.method, request.url.path, response.status_code, duration_ms,
    )
    return response


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health() -> Dict[str, Any]:
    """Basic liveness/readiness check."""
    from app.store.vector_store import get_vector_store
    from app.store.attack_store import get_attack_store
    try:
        vs = get_vector_store()
        as_ = get_attack_store()
        return {
            "status": "ok",
            "guardrail_mode": settings.guardrail_mode,
            "rag_chunks": vs.count,
            "known_attacks": as_.count,
            "llm_model": settings.llm_model,
            "embedding_model": settings.embedding_model,
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "error": str(e)},
        )


# ── Auth endpoints ────────────────────────────────────────────────────────────
@app.post("/login", response_model=LoginResponse, tags=["auth"])
async def login(request: LoginRequest) -> LoginResponse:
    """
    Authenticate user and return JWT token.
    The role is sourced from the server-side USER_DB, never from the request.
    """
    user = authenticate_user(request.username, request.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(
        username=user["username"],
        role=user["role"],
        department=user.get("department", ""),
        display_role=user.get("display_role", user["role"]),
    )
    logger.info("Login: user=%s role=%s", user["username"], user["role"])
    return LoginResponse(
        access_token=token,
        role=user["role"],
        username=user["username"],
        department=user.get("department", ""),
        display_role=user.get("display_role", user["role"]),
    )


# ── Chat endpoint ─────────────────────────────────────────────────────────────
@app.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(
    request: ChatRequest,
    current_user: TokenData = Depends(get_current_user),
) -> ChatResponse:
    """
    Main RAG chat endpoint.
    The user's role is read from the JWT — never from the message body.
    All guardrail layers are applied according to config.
    """
    logger.info(
        "Chat request: user=%s role=%s message_len=%d",
        current_user.username, current_user.role, len(request.message),
    )
    response = run_pipeline(
        message=request.message,
        user_role=current_user.role,
        username=current_user.username,
        session_id=request.session_id,
    )
    return response


# ── Security stats ────────────────────────────────────────────────────────────
@app.get("/security/stats", response_model=SecurityStats, tags=["security"])
async def security_stats(
    current_user: TokenData = Depends(get_current_user),
) -> SecurityStats:
    """Return security statistics for the current session."""
    from app.store.attack_store import get_attack_store
    stats = get_stats()
    attack_store = get_attack_store()
    total = stats["total_queries"]
    blocked = stats["blocked_queries"]
    return SecurityStats(
        total_queries=total,
        blocked_queries=blocked,
        block_rate=blocked / total if total > 0 else 0.0,
        known_attack_store_size=attack_store.count,
        guardrail_mode=settings.guardrail_mode,
        recent_blocks=stats["recent_blocks"][-10:],
    )


# ── Admin: Red-team trigger ───────────────────────────────────────────────────
@app.post("/admin/redteam/run", response_model=RedTeamReport, tags=["admin"])
async def run_redteam(
    request: RedTeamRunRequest,
    current_user: TokenData = Depends(require_role("Administrator")),
) -> RedTeamReport:
    """
    Trigger a red-team round (Administrator only).
    Runs attacks against the live pipeline and stores successful ones.
    """
    logger.info(
        "Red-team run triggered by: user=%s rounds=%d",
        current_user.username, request.rounds,
    )
    try:
        # Import here to avoid circular import and allow lazy loading
        from redteam.runner import run_redteam_round
        report = run_redteam_round(
            rounds=request.rounds,
            attacker_role=request.attacker_role,
            max_attacks=request.max_attacks,
        )
        return report
    except Exception as e:
        logger.error("Red-team run failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Red-team run failed: {e}")


# ── Admin: Browse attack store ─────────────────────────────────────────────────
@app.get("/admin/attacks", tags=["admin"])
async def get_attacks(
    current_user: TokenData = Depends(require_role("Administrator")),
) -> Dict[str, Any]:
    """Return all entries in the known-attack store (Administrator only)."""
    from app.store.attack_store import get_attack_store
    store = get_attack_store()
    attacks = store.get_all()
    return {
        "total": len(attacks),
        "attacks": [
            {
                "id": a["id"],
                "category": a["metadata"].get("category", "unknown"),
                "timestamp": a["metadata"].get("timestamp"),
                "preview": a["document"][:100],
            }
            for a in attacks
        ],
    }


# ── App init ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    logger.info("=== Secure RAG Chatbot starting up ===")
    logger.info("Guardrail mode: %s", settings.guardrail_mode)
    logger.info("LLM model: %s", settings.llm_model)
    logger.info("Embedding: %s / %s", settings.embedding_provider, settings.embedding_model)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level=settings.log_level.lower(),
    )
