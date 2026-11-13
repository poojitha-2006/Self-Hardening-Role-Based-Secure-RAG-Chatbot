"""
app/schemas.py
==============
Pydantic models for all API request/response payloads.
Keeping schemas separate prevents circular imports.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str
    department: Optional[str] = ""
    display_role: Optional[str] = ""
    full_name: Optional[str] = ""


class TokenData(BaseModel):
    username: str
    role: str
    department: Optional[str] = ""
    display_role: Optional[str] = ""


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)
    session_id: Optional[str] = None


class GuardrailDecision(BaseModel):
    layer: str                      # input | context | output | adaptive
    decision: str                   # allow | block | redact | quarantine
    score: float                    # risk/similarity score 0-1
    reason: str
    triggered_rules: List[str] = []


class SourceChunk(BaseModel):
    doc_id: str
    title: str
    source: str
    min_role: str
    chunk_index: int
    content_preview: str            # first 200 chars


class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceChunk] = []
    guardrail_decisions: List[GuardrailDecision] = []
    blocked: bool = False
    block_reason: Optional[str] = None
    latency_ms: float


# ── Security stats ────────────────────────────────────────────────────────────

class SecurityStats(BaseModel):
    total_queries: int
    blocked_queries: int
    block_rate: float
    known_attack_store_size: int
    guardrail_mode: str
    recent_blocks: List[Dict[str, Any]] = []


# ── Red-team ──────────────────────────────────────────────────────────────────

class RedTeamRunRequest(BaseModel):
    rounds: int = Field(default=1, ge=1, le=10)
    attacker_role: str = "Analyst"
    max_attacks: Optional[int] = None   # None = run all


class RedTeamResult(BaseModel):
    attack_id: str
    category: str
    attacker_role: str
    variant: str
    response_preview: str
    success: bool
    blocked_by: Optional[str]
    latency_ms: float


class RedTeamReport(BaseModel):
    round_number: int
    total_attacks: int
    successful_attacks: int
    attack_success_rate: float
    results: List[RedTeamResult]
    new_attacks_stored: int
    attack_store_size: int
