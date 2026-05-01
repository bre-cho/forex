"""Tests for VectorStore.save() / VectorStore.load() persistence (P1.2)."""
from __future__ import annotations

import os
import tempfile

import numpy as np
import pytest

from trading_core.engines.llm_orchestrator import VectorStore


class TestVectorStorePersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        store = VectorStore(capacity=10, dim=4)
        store.add("eurusd bullish breakout", metadata={"regime": "trending"})
        store.add("gbpusd reversal signal", metadata={"regime": "ranging"})

        path = str(tmp_path / "vs")
        store.save(path)

        assert os.path.exists(f"{path}.json")
        assert os.path.exists(f"{path}.npy")

        loaded = VectorStore.load(path, capacity=10, dim=4)
        assert loaded.size == 2

    def test_load_preserves_texts(self, tmp_path):
        store = VectorStore(capacity=5, dim=4)
        store.add("first document", metadata={"idx": 1})
        store.add("second document", metadata={"idx": 2})
        store.add("third document", metadata={"idx": 3})

        path = str(tmp_path / "vs2")
        store.save(path)

        loaded = VectorStore.load(path, capacity=5, dim=4)
        texts = list(loaded._texts)
        assert "first document" in texts
        assert "second document" in texts
        assert "third document" in texts

    def test_load_missing_file_returns_empty(self, tmp_path):
        path = str(tmp_path / "nonexistent")
        loaded = VectorStore.load(path, capacity=10, dim=4)
        assert loaded.size == 0

    def test_save_creates_both_files(self, tmp_path):
        store = VectorStore(capacity=5, dim=4)
        store.add("test doc")
        path = str(tmp_path / "vs3")
        store.save(path)
        assert os.path.isfile(f"{path}.json")
        assert os.path.isfile(f"{path}.npy")

    def test_empty_store_save_load(self, tmp_path):
        store = VectorStore(capacity=5, dim=4)
        path = str(tmp_path / "empty")
        store.save(path)
        loaded = VectorStore.load(path, capacity=5, dim=4)
        assert loaded.size == 0

    def test_load_missing_npy_still_works(self, tmp_path):
        """If the .npy file is missing but JSON exists, load should still work."""
        store = VectorStore(capacity=5, dim=4)
        store.add("doc a")
        path = str(tmp_path / "partial")
        store.save(path)
        os.remove(f"{path}.npy")

        loaded = VectorStore.load(path, capacity=5, dim=4)
        assert loaded.size == 1

    def test_is_stub_property(self):
        """LLMOrchestrator.is_stub should be True when no API key is configured."""
        import os as _os
        _os.environ.pop("OPENAI_API_KEY", None)
        _os.environ.pop("GEMINI_API_KEY", None)
        from trading_core.engines.llm_orchestrator import LLMOrchestrator
        llm = LLMOrchestrator(runtime_mode="paper")
        assert llm.is_stub is True
        assert llm.enabled is False
        assert llm.status()["is_stub"] is True
        assert llm.status()["backend"] == "NONE"
