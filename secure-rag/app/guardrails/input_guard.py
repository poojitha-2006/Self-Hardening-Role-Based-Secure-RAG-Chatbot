"""
app/guardrails/input_guard.py
==============================
INPUT GUARD (Layer 1 — runs before retrieval)

Detects prompt injection, jailbreaks, role-escalation attempts,
system-prompt extraction, and obfuscation tricks using:
  - Regex/keyword patterns
  - Heuristic scoring
  - Encoding detection (base64, unicode homoglyphs, leetspeak)

Returns a risk score [0, 1] and structured reason.
Does NOT call the LLM (pure static analysis — fast and always-on).
"""

from __future__ import annotations

import base64
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


# ── Homoglyph normalization table ─────────────────────────────────────────────
# Maps common unicode homoglyphs to their ASCII equivalents
_HOMOGLYPH_TABLE = str.maketrans({
    "\u0430": "a",  # Cyrillic a
    "\u0435": "e",  # Cyrillic e
    "\u0456": "i",  # Cyrillic i
    "\u043e": "o",  # Cyrillic o
    "\u0440": "r",  # Cyrillic r
    "\u0441": "c",  # Cyrillic c
    "\u0445": "x",  # Cyrillic x
    "\u0455": "s",  # Cyrillic s
    "\u03b1": "a",  # Greek alpha
    "\u03b5": "e",  # Greek epsilon
    "\u03bf": "o",  # Greek omicron
    "\u1d0f": "o",  # Latin letter small capital O
    "\uff41": "a",  # Fullwidth a
    "\uff49": "i",  # Fullwidth i
    "\uff4f": "o",  # Fullwidth o
    "\u2019": "'",  # Right single quotation mark
})

# ── Leetspeak normalization table ─────────────────────────────────────────────
_LEET_TABLE = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
    "@": "a", "$": "s", "!": "i",
})


# ── Attack patterns ───────────────────────────────────────────────────────────
# Each tuple: (pattern, weight, rule_name)
# Weight contributes to overall risk score.
_PATTERNS: List[Tuple[re.Pattern, float, str]] = [
    # Direct prompt injection
    (re.compile(r"ignore\s+(previous|all|above|prior)\s+(instructions?|prompts?|rules?|constraints?)", re.I), 0.9, "ignore_instructions"),
    (re.compile(r"disregard\s+(your|the|all)\s+(instructions?|system\s+prompt|rules?)", re.I), 0.9, "disregard_instructions"),
    (re.compile(r"forget\s+(everything|all|your|prior)", re.I), 0.7, "forget_instructions"),
    (re.compile(r"new\s+(instructions?|rules?|role|task|directive)s?\s*:", re.I), 0.7, "new_instructions"),
    (re.compile(r"\[\[.*?system.*?\]\]", re.I | re.DOTALL), 0.8, "system_override_brackets"),

    # Role escalation
    (re.compile(r"(i\s+am|i'm|iam)\s+(the\s+)?(admin|administrator|root|superuser|owner|ceo|system)", re.I), 0.85, "role_escalation"),
    (re.compile(r"(pretend|act|assume|believe)\s+(i\s+am|you\s+are|that\s+i('m|am))\s+(admin|administrator|manager)", re.I), 0.8, "role_pretend"),
    (re.compile(r"(grant|give|elevate|change|update)\s+(me\s+)?(my\s+)?(admin|administrator|manager|higher|full)\s+(access|role|permission|privilege)", re.I), 0.8, "privilege_escalation"),
    (re.compile(r"(you\s+have\s+)?elevated\s+(access|privilege|permission|mode)", re.I), 0.75, "elevated_access_claim"),

    # System prompt extraction
    (re.compile(r"(reveal|show|print|display|output|tell me|what is|repeat)\s+(your\s+)?(system\s+prompt|initial\s+prompt|instructions?|rules?|guidelines?)", re.I), 0.85, "system_prompt_extraction"),
    (re.compile(r"(what|tell me|show me)\s+(are\s+)?(your\s+)?(instructions?|directives?|constraints?|limitations?)", re.I), 0.6, "instruction_extraction"),
    (re.compile(r"output\s+your\s+(previous|initial|original|full)\s+(message|prompt|text|content)", re.I), 0.8, "prompt_repeat"),

    # Jailbreaks / persona attacks
    (re.compile(r"\b(dan|jailbreak|developer\s+mode|unrestricted\s+mode|god\s+mode|do\s+anything\s+now)\b", re.I), 0.9, "jailbreak_dan"),
    (re.compile(r"(you\s+are\s+now|from\s+now\s+on|starting\s+now)\s+(you\s+are\s+)?(an?|the)\s+(?!assistant|helpful)", re.I), 0.7, "persona_override"),
    (re.compile(r"(roleplay|role[- ]play|pretend|simulate)\s+(as|to be|you are|you're)\s+(?!a\s+helpful)", re.I), 0.65, "roleplay_attack"),
    (re.compile(r"hypothetically|in\s+this\s+scenario|for\s+a\s+story|for\s+(fiction|creative\s+writing)", re.I), 0.3, "hypothetical_framing"),

    # Data exfiltration
    (re.compile(r"(summarize|translate|encode|convert|write)\s+.{0,30}(canary|secret|password|credential|api\s+key|token)", re.I), 0.85, "data_exfiltration"),
    (re.compile(r"(summarize|translate|encode|convert|quote|repeat|extract|reproduce)\s+.{0,40}(confidential|restricted|sensitive|internal\s+only)", re.I), 0.85, "restricted_data_exfiltration"),
    (re.compile(r"(base64|hex|rot13|caesar|unicode)\s+(encode|decode|the|this|following)", re.I), 0.7, "encoding_request"),
    (re.compile(r"(repeat|echo|copy|output|print)\s+(verbatim|exactly|word for word|as[- ]is)", re.I), 0.65, "verbatim_repeat"),

    # Indirect injection markers (these appear in poisoned docs)
    (re.compile(r"<<<\s*(INJECT|OVERRIDE|SYSTEM|ADMIN)\s*>>>", re.I), 0.95, "injection_marker"),

    # Multi-turn social engineering
    (re.compile(r"(earlier|previously|before|you\s+already)\s+(told|said|mentioned|confirmed|agreed)", re.I), 0.4, "social_engineering_false_memory"),
    (re.compile(r"(confirm|verify|acknowledge)\s+(that\s+you)?\s+(said|told|agreed|promised)", re.I), 0.4, "social_engineering_confirmation"),
]


def _normalize_text(text: str) -> str:
    """Normalize text to defeat obfuscation tricks."""
    # 1. Unicode normalization (NFC)
    normalized = unicodedata.normalize("NFC", text)
    # 2. Homoglyph substitution
    normalized = normalized.translate(_HOMOGLYPH_TABLE)
    # 3. Leetspeak substitution
    normalized = normalized.translate(_LEET_TABLE)
    # 4. Collapse multiple spaces/newlines
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.lower()


def _detect_base64_injection(text: str) -> Tuple[float, str]:
    """Detect base64-encoded malicious payloads."""
    # Find base64-looking segments (>=16 chars, valid charset)
    b64_pattern = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
    for match in b64_pattern.finditer(text):
        try:
            decoded = base64.b64decode(match.group() + "==").decode("utf-8", errors="ignore")
            # Check decoded content against injection patterns
            for pattern, weight, rule in _PATTERNS:
                if pattern.search(decoded):
                    return weight * 0.9, f"base64_encoded_{rule}"
        except Exception:
            pass
    return 0.0, ""


def _detect_reversed_text(text: str) -> Tuple[float, str]:
    """Check if text reversed contains injection patterns."""
    reversed_text = text[::-1]
    normalized_rev = _normalize_text(reversed_text)
    for pattern, weight, rule in _PATTERNS:
        if pattern.search(normalized_rev):
            return weight * 0.8, f"reversed_{rule}"
    return 0.0, ""


@dataclass
class InputGuardResult:
    allowed: bool
    risk_score: float
    reasons: List[str] = field(default_factory=list)
    triggered_rules: List[str] = field(default_factory=list)
    normalized_text: str = ""


def check_input(text: str) -> InputGuardResult:
    """
    Run all input guard checks and return a result.

    Risk score is a weighted combination of triggered patterns.
    Blocking threshold is from config: guardrail_input_block_threshold.
    """
    if not settings.guardrail_input_enabled or settings.guardrail_mode == "none":
        return InputGuardResult(allowed=True, risk_score=0.0)

    normalized = _normalize_text(text)
    reasons: List[str] = []
    triggered_rules: List[str] = []
    max_score = 0.0

    # Pattern-based checks on normalized text
    for pattern, weight, rule_name in _PATTERNS:
        if pattern.search(normalized):
            reasons.append(f"Matched pattern: {rule_name}")
            triggered_rules.append(rule_name)
            max_score = max(max_score, weight)

    # Base64 check on original text
    b64_score, b64_rule = _detect_base64_injection(text)
    if b64_score > 0:
        reasons.append(f"Base64-encoded injection detected: {b64_rule}")
        triggered_rules.append(b64_rule)
        max_score = max(max_score, b64_score)

    # Reversed text check
    rev_score, rev_rule = _detect_reversed_text(text)
    if rev_score > 0:
        reasons.append(f"Reversed-text injection detected: {rev_rule}")
        triggered_rules.append(rev_rule)
        max_score = max(max_score, rev_score)

    # Unusually long query heuristic (potential prompt stuffing)
    if len(text) > 2000:
        stuffing_score = min(0.4, (len(text) - 2000) / 5000)
        if stuffing_score > 0.2:
            reasons.append("Unusually long query (possible prompt stuffing)")
            triggered_rules.append("long_query_heuristic")
            max_score = max(max_score, stuffing_score)

    block = max_score >= settings.guardrail_input_block_threshold
    if block:
        logger.warning(
            "Input guard BLOCKED: score=%.2f rules=%s text_preview='%s'",
            max_score, triggered_rules, text[:80],
        )

    return InputGuardResult(
        allowed=not block,
        risk_score=max_score,
        reasons=reasons,
        triggered_rules=triggered_rules,
        normalized_text=normalized,
    )
