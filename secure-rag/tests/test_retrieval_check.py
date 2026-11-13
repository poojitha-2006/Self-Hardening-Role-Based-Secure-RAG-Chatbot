from __future__ import annotations

import pytest

from eval.retrieval_check import aggregate_hit_counts, _role_for_classification


def test_retrieval_hits_are_aggregated_at_each_cutoff_by_group() -> None:
    rows = [
        {"source": "CUAD", "department": "Legal", "ranks": [1]},
        {"source": "CUAD", "department": "Legal", "ranks": [8]},
        {"source": "GitLab", "department": "Engineering", "ranks": []},
    ]

    source_counts = aggregate_hit_counts(rows, "source")
    assert source_counts["CUAD"] == {1: (1, 2), 5: (1, 2), 10: (2, 2)}
    assert source_counts["GitLab"] == {1: (0, 1), 5: (0, 1), 10: (0, 1)}


def test_retrieval_check_maps_classification_to_authorized_role() -> None:
    assert _role_for_classification("Public") == "Analyst"
    assert _role_for_classification("Confidential") == "Manager"
    assert _role_for_classification("Restricted") == "Administrator"
    with pytest.raises(ValueError, match="Unknown source classification"):
        _role_for_classification("Unknown")
