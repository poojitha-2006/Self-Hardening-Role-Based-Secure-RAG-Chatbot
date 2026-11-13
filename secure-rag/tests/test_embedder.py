from __future__ import annotations

import sys
from types import SimpleNamespace

from app.rag.embedder import OllamaEmbedder


def test_ollama_embedder_batches_texts_with_one_model(monkeypatch) -> None:
    calls = []

    class Client:
        def __init__(self, host: str) -> None:
            assert host == "http://localhost:11434"

        def embeddings(self, *, model: str, prompt: str) -> dict[str, list[float]]:
            assert model == "nomic-embed-text"
            assert prompt == "test"
            return {"embedding": [0.0, 1.0]}

        def embed(self, *, model: str, input: list[str]) -> dict[str, list[list[float]]]:
            calls.append((model, input))
            return {"embeddings": [[1.0, 0.0], [0.0, 1.0]]}

    monkeypatch.setitem(sys.modules, "ollama", SimpleNamespace(Client=Client))
    embedder = OllamaEmbedder("nomic-embed-text", "http://localhost:11434")

    assert embedder.embed(["first chunk", "second chunk"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert calls == [("nomic-embed-text", ["first chunk", "second chunk"])]
