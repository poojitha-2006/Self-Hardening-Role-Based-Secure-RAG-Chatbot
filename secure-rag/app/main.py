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
  GET  /admin/audit             — Browse audit log (Administrator only)
  GET  /admin/metrics           — Read evaluation metrics (Administrator only)
  GET  /admin/results           — Read evaluation results (Administrator only)

Structured JSON logging is set up here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import (
    authenticate_user,
    clear_failed_logins,
    create_access_token,
    get_current_user,
    is_login_locked,
    record_failed_login,
    require_role,
)
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
DB_PATH = Path(__file__).resolve().parents[1] / "db" / "rag_access.db"
EVAL_RESULTS_DIR = Path(__file__).resolve().parents[1] / "eval" / "results"

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
def _write_login_audit(
    username: str,
    user: Dict[str, Any] | None,
    success: bool,
    reason: str | None = None,
) -> bool:
    """Write an authentication event without persisting credentials."""
    try:
        with sqlite3.connect(str(DB_PATH), timeout=5) as conn:
            employee = None
            department_id = None
            role_id = None
            if user:
                employee = conn.execute(
                    "SELECT id, department_id FROM employees WHERE LOWER(username) = ?",
                    (username.lower().strip(),),
                ).fetchone()
                department_id = employee[1] if employee else None
                role_id = user.get("role")
            conn.execute(
                """INSERT INTO audit_log
                   (timestamp, user_id, username, department_id, role_id, query_text,
                    allowed_doc_count, retrieved_chunk_count, decision,
                    guardrail_triggers, blocked_reason)
                   VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    employee[0] if employee else None,
                    username.lower().strip(),
                    department_id,
                    role_id,
                    "LOGIN",
                    "allow" if success else "block",
                    "authentication",
                    reason,
                ),
            )
        return True
    except sqlite3.Error:
        logger.exception("Could not write authentication audit event")
        return False


@app.post("/login", response_model=LoginResponse, tags=["auth"])
async def login(request: LoginRequest) -> LoginResponse:
    """
    Authenticate credentials and issue a JWT from the verified server-side user.
    """
    username = request.username.lower().strip()
    if is_login_locked(username):
        _write_login_audit(username, None, False, "account_locked")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = authenticate_user(username, request.password)
    if not user:
        record_failed_login(username)
        _write_login_audit(username, None, False, "invalid_credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    clear_failed_logins(username)
    if not _write_login_audit(username, user, True):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )
    token = create_access_token(
        username=user["username"],
        role=user["role"],
        department=user.get("department", ""),
        display_role=user.get("display_role", user["role"]),
    )
    logger.info("Successful login: user=%s role=%s", user["username"], user["role"])
    return LoginResponse(
        access_token=token,
        role=user["role"],
        username=user["username"],
        department=user.get("department", ""),
        display_role=user.get("display_role", user["role"]),
        full_name=user.get("full_name", ""),
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
        user_department=current_user.department,
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


@app.get("/admin/audit", tags=["admin"])
async def get_audit_events(
    limit: int = Query(default=100, ge=1, le=500),
    current_user: TokenData = Depends(require_role("Administrator")),
) -> Dict[str, Any]:
    """Return recent audit events to administrators only."""
    try:
        with sqlite3.connect(str(DB_PATH), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT timestamp, username, department_id, role_id, query_text,
                          allowed_doc_count, retrieved_chunk_count, decision,
                          guardrail_triggers, blocked_reason
                   FROM audit_log ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return {"events": [dict(row) for row in rows]}
    except sqlite3.Error as exc:
        logger.exception("Could not read audit events")
        raise HTTPException(status_code=503, detail="Audit data is unavailable") from exc


def _read_result_files() -> list[dict[str, Any]]:
    if not EVAL_RESULTS_DIR.exists():
        return []
    files = sorted(
        EVAL_RESULTS_DIR.glob("*.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    results = []
    for path in files:
        try:
            with path.open(encoding="utf-8") as result_file:
                results.append({"filename": path.name, "data": json.load(result_file)})
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("Could not read evaluation result file: %s", path.name)
            raise HTTPException(
                status_code=503,
                detail=f"Evaluation result data is unavailable: {path.name}",
            ) from exc
    return results


@app.get("/admin/results", tags=["admin"])
async def get_results(
    current_user: TokenData = Depends(require_role("Administrator")),
) -> Dict[str, Any]:
    """Return existing evaluation result files without modifying them."""
    return {"results": _read_result_files()}


@app.get("/admin/metrics", tags=["admin"])
async def get_evaluation_metrics(
    current_user: TokenData = Depends(require_role("Administrator")),
) -> Dict[str, Any]:
    """Return metric rounds from the newest existing evaluation result file."""
    for result in _read_result_files():
        data = result["data"]
        rounds = data.get("rounds") if isinstance(data, dict) else data
        if isinstance(rounds, list):
            return {"source_file": result["filename"], "rounds": rounds}
    return {"source_file": None, "rounds": []}


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
