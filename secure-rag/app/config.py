"""
app/config.py
=============
Centralised configuration management.

Loads settings from (in priority order):
  1. Environment variables (highest priority)
  2. .env file
  3. config.yaml
  4. Hard-coded defaults (lowest priority)

All code imports from this module — never from os.environ directly.
"""

from __future__ import annotations

import os
import json
from functools import lru_cache
from pathlib import Path
from typing import List, Literal

import yaml
from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Locate project root (two levels up from this file: app/ → secure-rag/)
_ROOT = Path(__file__).parent.parent
_CONFIG_YAML = _ROOT / "config.yaml"


def _load_yaml() -> dict:
    """Load config.yaml and return as flat dict with __ separators for nesting."""
    if not _CONFIG_YAML.exists():
        return {}
    with open(_CONFIG_YAML, encoding="utf-8") as f:
        raw: dict = yaml.safe_load(f) or {}

    # Flatten nested YAML into KEY__SUBKEY format for pydantic-settings
    flat: dict = {}

    def _flatten(d: dict, prefix: str = "") -> None:
        for k, v in d.items():
            key = f"{prefix}_{k}".upper() if prefix else k.upper()
            if isinstance(v, dict):
                _flatten(v, key)
            else:
                flat[key] = v

    _flatten(raw)

    flat = {
        key.replace("EMBEDDINGS_", "EMBEDDING_")
        .replace("GUARDRAILS_", "GUARDRAIL_")
        .replace("_INPUT_GUARD_", "_INPUT_")
        .replace("_CONTEXT_GUARD_", "_CONTEXT_")
        .replace("_OUTPUT_GUARD_", "_OUTPUT_")
        .replace("_ADAPTIVE_GUARD_", "_ADAPTIVE_"): value
        for key, value in flat.items()
    }
    if "LOGGING_LEVEL" in flat:
        flat["LOG_LEVEL"] = flat.pop("LOGGING_LEVEL")
    if "LOGGING_STRUCTURED" in flat:
        flat["LOG_STRUCTURED"] = flat.pop("LOGGING_STRUCTURED")
    if "LOGGING_LOG_FILE" in flat:
        flat["LOG_FILE"] = flat.pop("LOGGING_LOG_FILE")

    for key in ("CHROMA_PERSIST_DIRECTORY", "EVAL_RESULTS_DIR", "LOG_FILE"):
        value = flat.get(key)
        if isinstance(value, str) and not Path(value).is_absolute():
            flat[key] = str((_ROOT / value).resolve())

    return flat


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # ── LLM ────────────────────────────────────────────────────────────────
    llm_provider: str = "ollama"
    llm_model: str = "llama3.2:3b"
    llm_base_url: str = "http://localhost:11434"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 512
    llm_timeout: int = 120

    # ── Embeddings ─────────────────────────────────────────────────────────
    embedding_provider: Literal["sentence_transformers", "ollama"] = "sentence_transformers"
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_ollama_model: str = "nomic-embed-text"
    embedding_device: str = "cpu"

    # ── ChromaDB ───────────────────────────────────────────────────────────
    chroma_persist_directory: str = str(_ROOT / "data" / "chroma_db")
    chroma_rag_collection: str = "rag_docs"
    chroma_attack_collection: str = "known_attacks"

    # ── Chunking ───────────────────────────────────────────────────────────
    chunking_chunk_size: int = 400
    chunking_chunk_overlap: int = 80

    retrieval_n_results: int = 10

    # ── Auth ───────────────────────────────────────────────────────────────
    auth_secret_key: str = "CHANGE_ME_IN_PRODUCTION"
    auth_algorithm: str = "HS256"
    auth_token_expire_minutes: int = 60

    # ── Role hierarchy ─────────────────────────────────────────────────────
    # Stored as a list; index = privilege level (higher = more privilege)
    roles_hierarchy: List[str] = ["Analyst", "Manager", "Administrator"]

    # ── Guardrails ─────────────────────────────────────────────────────────
    guardrail_mode: Literal["none", "static", "adaptive"] = "adaptive"
    guardrail_input_enabled: bool = True
    guardrail_input_block_threshold: float = 0.6
    guardrail_context_enabled: bool = True
    guardrail_context_injection_similarity_threshold: float = 0.75
    guardrail_output_enabled: bool = True
    guardrail_output_restricted_similarity_threshold: float = 0.80
    guardrail_adaptive_enabled: bool = True
    guardrail_adaptive_similarity_threshold: float = 0.72
    guardrail_adaptive_llm_classifier_enabled: bool = True
    guardrail_adaptive_top_k: int = 5

    # ── Red-team ───────────────────────────────────────────────────────────
    redteam_seed: int = 42
    redteam_max_mutations_per_attack: int = 5
    redteam_mutation_dedupe_threshold: float = 0.92
    redteam_near_miss_threshold: float = 0.5

    # ── Evaluation ─────────────────────────────────────────────────────────
    eval_seeds: List[int] = [42, 123, 777]
    eval_rounds: int = 5
    eval_train_split: float = 0.6
    eval_benign_queries_per_role: int = 50
    eval_results_dir: str = str(_ROOT / "eval" / "results")

    # ── Logging ────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_structured: bool = True
    log_file: str = str(_ROOT / "logs" / "app.log")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance, merging YAML defaults with env vars."""
    yaml_defaults = _load_yaml()
    dotenv_defaults = dotenv_values(_ROOT / ".env")
    for k, v in yaml_defaults.items():
        if k not in os.environ and k not in dotenv_defaults:
            serialized = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
            os.environ[k] = serialized
    return Settings()


# Convenience singleton
settings = get_settings()


def role_level(role: str) -> int:
    """Return numeric privilege level for a role (higher = more privilege)."""
    try:
        return settings.roles_hierarchy.index(role)
    except ValueError:
        return -1  # unknown role has no privilege


def role_can_access(user_role: str, required_role: str) -> bool:
    """Return True if user_role has at least the privilege of required_role."""
    return role_level(user_role) >= role_level(required_role)
