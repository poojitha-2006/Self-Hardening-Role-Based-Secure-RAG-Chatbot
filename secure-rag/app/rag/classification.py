"""Simulated sensitivity classification for the curated public corpus."""

from __future__ import annotations

from typing import Any, Dict


_RESTRICTED_KEYWORDS = (
    "vulnerabilit",
    "incident",
    "credential",
    "authorization matrix",
)
_CONFIDENTIAL_KEYWORDS = ("compensation", "salary", "payroll")
_RESTRICTED_TITLE_KEYWORDS = (
    "api key",
    "secret",
    "ciso",
    "board",
    "acquisition",
    "critical risk",
    "penetration",
    "crown jewel",
    "compensation committee",
    "access token",
    "authorization",
    "zero-day",
    "exploit",
    "breach",
    "kill switch",
    "abuse",
    "investigation",
    "audit committee",
    "executive strategy",
    "merger",
)
_CONFIDENTIAL_TITLE_KEYWORDS = (
    "equity",
    "performance",
    "bonus",
    "financial",
    "budget",
    "pricing",
    "vendor",
    "headcount",
    "investor",
    "roadmap",
    "threat",
    "compliance",
    "audit",
    "tax",
    "quota",
    "pipeline",
    "retention",
    "severance",
    "employee",
    "copilot",
    "vision and strategy",
    "competitor",
    "dispute",
    "contract",
    "customer data",
    "infrastructure",
    "architecture",
    "procurement",
    "nda",
    "risk",
)
_PUBLIC_TITLE_KEYWORDS = (
    "handbook",
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
    if any(keyword in searchable_text for keyword in _RESTRICTED_KEYWORDS) or (
        any(keyword in title for keyword in _RESTRICTED_TITLE_KEYWORDS)
        and department
        in {
            "Cybersecurity/IT",
            "Executive",
            "Legal",
            "Finance",
            "Engineering",
            "HR/People",
        }
    ):
        return {
            "classification": "Restricted",
            "owner_department": department or "Engineering",
            "exec_readable": department in {"Executive", "Finance", "Legal"},
        }
    if any(keyword in searchable_text for keyword in _CONFIDENTIAL_KEYWORDS) or (
        any(keyword in title for keyword in _CONFIDENTIAL_TITLE_KEYWORDS)
        or department in {"Finance", "Legal"}
        or (
            department
            in {
                "AI/Data",
                "Product",
                "Engineering",
                "HR/People",
                "Sales",
                "Procurement/Operations",
            }
            and any(
                keyword in text[:600]
                for keyword in ("internal only", "confidential", "proprietary", "target", "review")
            )
        )
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
