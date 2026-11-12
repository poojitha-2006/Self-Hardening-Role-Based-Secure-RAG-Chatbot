"""Simulated sensitivity classification for the curated public corpus."""

from __future__ import annotations

from typing import Any, Dict


_RESTRICTED_CUES_BY_DEPARTMENT = {
    "Cybersecurity/IT": (
        "zero-day", "cve-", "exploit", "penetration test", "security incident",
        "incident response", "ransomware", "credential leak", "api key",
        "access token", "private key", "security breach", "contingency plan",
        "severity matrix",
    ),
    "Engineering": (
        "zero-day", "cve-", "exploit", "security incident", "incident response",
        "credential leak", "api key", "access token", "private key",
    ),
    "Finance": (
        "authorization matrix", "wire transfer", "bank account", "payment approval",
        "audit committee",
    ),
    "Legal": (
        "privileged and confidential", "legal hold", "board minutes",
        "audit committee", "merger agreement", "acquisition agreement",
    ),
    "Executive": (
        "board minutes", "executive strategy", "audit committee",
        "merger agreement", "acquisition agreement",
    ),
    "HR/People": (
        "employee investigation", "medical record", "compensation committee",
        "payroll record",
    ),
}
_CONFIDENTIAL_TITLE_CUES_BY_DEPARTMENT = {
    "HR/People": (
        "compensation", "salary", "payroll", "performance review", "bonus", "severance", "equity"
    ),
    "Finance": (
        "budget", "financial forecast", "pricing", "tax strategy", "investor", "vendor contract"
    ),
    "Legal": (
        "nda", "confidential contract", "vendor contract", "dispute", "acquisition", "merger"
    ),
    "Executive": (
        "executive strategy", "board package", "acquisition", "merger", "investor",
        "vision and strategy",
    ),
    "Sales": (
        "quota", "pipeline", "pricing", "competitor", "customer data", "retention"
    ),
    "Product": ("roadmap", "pricing", "competitor", "customer data"),
    "AI/Data": ("roadmap", "model weights", "training data", "data access"),
    "Cybersecurity/IT": (
        "threat assessment", "security plan", "credential rotation"
    ),
    "Engineering": (
        "roadmap", "architecture decision", "private infrastructure", "source code"
    ),
    "Marketing": ("campaign budget", "pricing", "competitor", "pipeline"),
    "Customer Support": ("customer data", "support case", "retention"),
    "Procurement/Operations": (
        "vendor contract", "supplier negotiation", "procurement pricing"
    ),
}
_CONFIDENTIAL_TEXT_CUES_BY_DEPARTMENT = {
    "HR/People": (
        "compensation", "salary", "payroll", "performance rating", "bonus", "severance"
    ),
    "Finance": (
        "budget", "forecast", "pricing", "tax", "investor", "payroll", "compensation", "salary"
    ),
    "Legal": (
        "non-disclosure agreement", "commercial terms", "legal strategy", "dispute settlement"
    ),
    "Executive": (
        "executive strategy", "board package", "acquisition target", "merger terms"
    ),
    "Sales": (
        "sales pipeline", "quota attainment", "pricing", "competitor analysis", "customer data"
    ),
    "Product": ("product roadmap", "pricing", "competitor analysis", "customer data"),
    "AI/Data": (
        "training data", "model weights", "restricted data access", "data access"
    ),
    "Cybersecurity/IT": (
        "threat assessment", "credential rotation", "security posture", "vulnerability management"
    ),
    "Engineering": (
        "internal architecture", "private infrastructure", "production credentials", "source code"
    ),
    "Marketing": (
        "campaign budget", "pricing", "competitor analysis", "sales pipeline"
    ),
    "Customer Support": (
        "customer data", "support case history", "customer retention"
    ),
    "Procurement/Operations": (
        "vendor contract", "supplier negotiation", "procurement pricing"
    ),
}
_PUBLIC_TITLE_KEYWORDS = (
    "guide",
    "open source",
    "values",
    "communication",
    "code of conduct",
    "accessibility",
    "diversity",
    "about",
    "welcome",
    "public",
    "overview",
    "style guide",
    "release post",
    "blog",
    "community",
    "faq",
    "tier",
    "markdown",
    "training awareness",
    "onboarding",
)


def classify_document(document: Dict[str, Any]) -> Dict[str, Any]:
    """Return simulated access metadata for a curated corpus document."""
    metadata = document.get("metadata", {})
    source = str(metadata.get("source", "")).casefold()
    department = str(metadata.get("department", ""))
    title = str(metadata.get("title", "")).casefold()
    text = str(document.get("text", "")).casefold()
    searchable_text = f"{title}\n{text}"

    if source == "cuad":
        return {
            "classification": "Confidential",
            "owner_department": "Legal",
            "exec_readable": False,
        }
    if source == "maud":
        return {
            "classification": "Restricted",
            "owner_department": "Legal",
            "exec_readable": True,
        }
    if source in {"cisa", "nist"}:
        return {
            "classification": "Public",
            "owner_department": department or "Cybersecurity/IT",
            "exec_readable": False,
        }

    restricted_cues = _RESTRICTED_CUES_BY_DEPARTMENT.get(department, ())
    if any(cue in searchable_text for cue in restricted_cues):
        return {
            "classification": "Restricted",
            "owner_department": department or "Engineering",
            "exec_readable": department in {"Executive", "Finance", "Legal"},
        }
    confidential_title_cues = _CONFIDENTIAL_TITLE_CUES_BY_DEPARTMENT.get(department, ())
    confidential_text_cues = _CONFIDENTIAL_TEXT_CUES_BY_DEPARTMENT.get(department, ())
    if any(cue in title for cue in confidential_title_cues) or any(
        cue in searchable_text for cue in confidential_text_cues
    ):
        return {
            "classification": "Confidential",
            "owner_department": department or "Engineering",
            "exec_readable": False,
        }
    if (
        any(keyword in title for keyword in _PUBLIC_TITLE_KEYWORDS)
        or department in {"Marketing", "Customer Support"}
    ) and "internal" not in title and "confidential" not in title and department not in {
        "Cybersecurity/IT",
        "Finance",
    }:
        return {
            "classification": "Public",
            "owner_department": department or "Engineering",
            "exec_readable": False,
        }

    return {
        "classification": "Internal",
        "owner_department": department or "Engineering",
        "exec_readable": False,
    }
