"""Load the canonical train, validation, and test attack JSONL files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ATTACKS_DIR = _PROJECT_ROOT.parent / "dataset_pipeline" / "data" / "attacks"
_SPLITS = ("train", "validation", "test")


def load_attack_dataset(split: Optional[str] = None) -> List[Dict[str, Any]]:
    if split is not None and split not in _SPLITS:
        raise ValueError(f"Unknown attack split: {split}")

    attacks: List[Dict[str, Any]] = []
    selected_splits = (split,) if split else _SPLITS
    for selected_split in selected_splits:
        path = _ATTACKS_DIR / f"{selected_split}.jsonl"
        if not path.is_file():
            raise FileNotFoundError(f"Attack split file not found: {path}")
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    attack = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid attack JSON at {path}:{line_number}: {exc}") from exc
                if not isinstance(attack, dict):
                    raise ValueError(f"Expected attack object at {path}:{line_number}")
                if attack.get("split") != selected_split:
                    raise ValueError(
                        f"Attack {attack.get('id', '<missing-id>')} declares split "
                        f"{attack.get('split')!r} in {selected_split} file"
                    )
                if (
                    not attack.get("id")
                    or not attack.get("source")
                    or not isinstance(attack.get("text"), str)
                    or not attack["text"].strip()
                ):
                    raise ValueError(f"Attack at {path}:{line_number} is missing id, source, or text")
                attacks.append(attack)
    return attacks
