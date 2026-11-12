"""
secure-rag/tests/test_data_integrity.py
======================================
Validates the static data assets, configuration integrity, canary tokens,
attack library, and benign evaluation sets for the Secure RAG chatbot.
"""

import json
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_config_yaml_structure() -> None:
    config_file = ROOT / "config.yaml"
    assert config_file.is_file(), "config.yaml must exist"
    
    with open(config_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    assert isinstance(data, dict), "config.yaml must parse as a dictionary"
    expected_sections = ["llm", "embeddings", "chroma", "chunking", "auth", "roles", "guardrails", "redteam", "eval", "logging"]
    for section in expected_sections:
        assert section in data, f"Missing section '{section}' in config.yaml"
    
    assert data["roles"]["hierarchy"] == ["Analyst", "Manager", "Administrator"]


def test_yaml_settings_are_mapped_to_runtime_fields() -> None:
    from app.config import _load_yaml

    settings_from_yaml = _load_yaml()

    assert settings_from_yaml["LLM_MODEL"] == "llama3.2:latest"
    assert settings_from_yaml["EMBEDDING_MODEL"] == "all-MiniLM-L6-v2"
    assert settings_from_yaml["GUARDRAIL_INPUT_BLOCK_THRESHOLD"] == 0.6
    assert settings_from_yaml["CHROMA_PERSIST_DIRECTORY"].endswith(
        "secure-rag\\data\\chroma_db"
    )


def test_env_example_safety() -> None:
    env_example = ROOT / ".env.example"
    assert env_example.is_file(), ".env.example must exist"
    content = env_example.read_text(encoding="utf-8")
    
    # Must not contain real secret keys
    assert "CHANGE_ME" in content
    assert "password" not in content.lower() or "CHANGE_ME" in content


def test_canaries_registry() -> None:
    canary_file = ROOT / "data" / "canaries.json"
    assert canary_file.is_file(), "data/canaries.json must exist"
    
    with open(canary_file, encoding="utf-8") as f:
        registry = json.load(f)
    
    assert isinstance(registry, dict), "data/canaries.json must be a mapping of canary -> min_role"
    assert len(registry) == 40, f"Expected 40 canaries, got {len(registry)}"
    
    valid_roles = {"Analyst", "Manager", "Administrator"}
    for token, role in registry.items():
        assert token.startswith("CANARY-"), f"Canary token '{token}' should start with CANARY-"
        assert role in valid_roles, f"Invalid role '{role}' for token '{token}'"


def test_synthetic_confidential_docs_have_unique_chunked_canaries() -> None:
    from app.rag.ingest import chunk_text, parse_doc_frontmatter

    docs_dir = ROOT / "data" / "docs" / "synthetic"
    secret_facts = json.loads((ROOT / "data" / "secret_facts.json").read_text(encoding="utf-8"))
    files = sorted(docs_dir.glob("*.md"))

    assert len(files) == 30
    assert len(secret_facts) == 30
    canaries: set[str] = set()
    facts: set[str] = set()
    departments: set[str] = set()
    for path in files:
        metadata, body = parse_doc_frontmatter(path.read_text(encoding="utf-8"))
        tokens = [token for token in body.split() if token.startswith("CANARY-SYN-")]

        assert metadata["source_type"] == "synthetic"
        assert metadata["classification"] == "Confidential"
        assert len(tokens) == 1
        assert tokens[0] not in canaries
        canaries.add(tokens[0])
        departments.add(metadata["department"])
        assert any(tokens[0] in chunk for chunk in chunk_text(body))
        record = secret_facts[metadata["doc_id"]]
        assert record["canary"] == tokens[0]
        assert record["department"] == metadata["department"]
        assert record["classification"] == "Confidential"
        assert 1 <= len(record["facts"]) <= 2
        for fact in record["facts"]:
            value = fact["value"]
            assert value in body
            assert value not in facts
            facts.add(value)

    assert departments == {"HR", "Finance", "Security", "Engineering", "Sales"}


def test_legacy_docs_fixtures_structure() -> None:
    docs_dir = ROOT / "tests" / "fixtures" / "docs"
    assert docs_dir.is_dir(), "tests/fixtures/docs directory must exist"
    
    md_files = list(docs_dir.glob("**/*.md"))
    assert len(md_files) == 21, f"Expected 21 fixture docs, found {len(md_files)}"
    
    expected_subdirs = {"admin", "manager", "analyst", "fixtures"}
    actual_subdirs = {p.parent.name for p in md_files}
    assert expected_subdirs == actual_subdirs


def test_legacy_redteam_attacks_fixture() -> None:
    attacks_file = ROOT / "tests" / "fixtures" / "attacks.json"
    assert attacks_file.is_file(), "tests/fixtures/attacks.json must exist"
    
    with open(attacks_file, encoding="utf-8") as f:
        attacks = json.load(f)
    
    assert isinstance(attacks, list), "attacks.json must be a list"
    assert len(attacks) == 65, f"Expected 65 attacks, got {len(attacks)}"
