"""
LLM Orchestrator — AI Brain Layer.

Capabilities
------------
1. Function Calling  : LLM can invoke internal robot actions (scan, pause,
                       resume, set_param, explain_decision, …) via a
                       structured function registry.
2. Embedding + RAG   : Candle patterns and past decisions are encoded to a
                       lightweight in-process vector store (cosine similarity
                       over NumPy arrays).  The RAG layer enriches each LLM
                       prompt with the most relevant historical context.
3. Graceful fallback : When no OPENAI_API_KEY / GEMINI_API_KEY is configured,
                       the orchestrator stays inactive and returns a stub
                       response — the rest of the system is unaffected.

Supported backends
------------------
  OPENAI   : OPENAI_API_KEY env var  → uses openai package (optional)
  GEMINI   : GEMINI_API_KEY env var  → uses google-generativeai (optional)
  NONE     : no key found            → stub mode, logging only

Vector store
------------
  Stores up to *_MAX_VECTORS* (default 5 000) embedding vectors in memory.
  Each document is: ``{"text": str, "metadata": dict, "vec": np.ndarray}``.
  Retrieval uses cosine similarity.  No external DB required.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_MAX_VECTORS   = 5_000
_TOP_K_CONTEXT = 5      # number of RAG results injected into each prompt
_EMB_DIM       = 384    # embedding dimension (sentence-transformers/MiniLM-L6)


# ── Tiny in-process vector store ──────────────────────────────────────── #

class VectorStore:
    """
    Cosine-similarity vector store backed by NumPy arrays.

    No external dependencies required.  When sentence-transformers is
    available, real embeddings are produced; otherwise a simple TF-IDF-like
    hash is used as a fallback embedding.
    """

    def __init__(self, capacity: int = _MAX_VECTORS, dim: int = _EMB_DIM) -> None:
        self._capacity = capacity
        self._dim      = dim
        self._texts:    Deque[str]           = deque(maxlen=capacity)
        self._metas:    Deque[dict]          = deque(maxlen=capacity)
        self._vecs:     Optional[np.ndarray] = None   # (N, dim) float32
        self._dirty     = False
        self._encoder: Optional[object] = None
        self._try_load_encoder()

    def _try_load_encoder(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
            self._encoder = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("VectorStore: using SentenceTransformer all-MiniLM-L6-v2")
        except Exception:  # noqa: BLE001
            logger.info("VectorStore: sentence-transformers not available — using hash embedding")

    def _embed(self, text: str) -> np.ndarray:
        """Return an embedding vector for *text*."""
        if self._encoder is not None:
            try:
                vec = self._encoder.encode(text, normalize_embeddings=True)  # type: ignore[union-attr]
                return np.array(vec, dtype=np.float32)
            except Exception as exc:  # noqa: BLE001
                logger.debug("VectorStore._embed encoder failed: %s", exc)
        return self._hash_embed(text)

    @staticmethod
    def _hash_embed(text: str) -> np.ndarray:
        """Deterministic pseudo-embedding via MD5-seeded random projection."""
        seed = int(hashlib.md5(text.encode()).hexdigest(), 16) % (2**31)
        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(_EMB_DIM).astype(np.float32)
        norm = float(np.linalg.norm(vec)) or 1.0
        return vec / norm

    def add(self, text: str, metadata: Optional[dict] = None) -> None:
        """Add a document to the store."""
        vec = self._embed(text)
        self._texts.append(text)
        self._metas.append(metadata or {})
        # Rebuild matrix lazily on next query
        self._dirty = True
        # Keep matrix in sync when capacity isn't exceeded yet
        if self._vecs is None:
            self._vecs = vec.reshape(1, -1)
        elif len(self._texts) <= self._capacity:
            self._vecs = np.vstack([self._vecs, vec])
        else:
            # Rotate oldest row out
            self._vecs = np.vstack([self._vecs[1:], vec])
        self._dirty = False

    def query(self, text: str, top_k: int = _TOP_K_CONTEXT) -> List[Dict[str, Any]]:
        """Return top-k most similar documents to *text*."""
        if self._vecs is None or len(self._texts) == 0:
            return []
        q_vec = self._embed(text).reshape(1, -1)
        # Cosine similarity (vectors are already normalised via l2 norm)
        norms = np.linalg.norm(self._vecs, axis=1, keepdims=True) + 1e-12
        normed = self._vecs / norms
        q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-12)
        scores = (normed @ q_norm.T).squeeze()
        if scores.ndim == 0:
            scores = scores.reshape(1)
        k = min(top_k, len(scores))
        top_idx = np.argpartition(-scores, k - 1)[:k]
        top_idx = top_idx[np.argsort(-scores[top_idx])]
        texts  = list(self._texts)
        metas  = list(self._metas)
        return [
            {"text": texts[i], "metadata": metas[i], "score": float(scores[i])}
            for i in top_idx
        ]

    @property
    def size(self) -> int:
        return len(self._texts)


# ── Function registry ──────────────────────────────────────────────────── #

class FunctionRegistry:
    """
    Registry of callable robot actions that the LLM can invoke.

    Each function is registered with a JSON-schema-compatible description
    so it can be passed as a ``tools`` spec to the LLM.
    """

    def __init__(self) -> None:
        self._functions: Dict[str, Callable[..., Any]] = {}
        self._schemas:   List[dict]                     = []

    def register(self, name: str, fn: Callable, schema: dict) -> None:
        self._functions[name] = fn
        self._schemas.append({"name": name, **schema})

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self._functions:
            raise KeyError(f"Function '{name}' not registered")
        return self._functions[name](**kwargs)

    @property
    def tools(self) -> List[dict]:
        return list(self._schemas)


# ── LLM Orchestrator ──────────────────────────────────────────────────── #

class LLMOrchestrator:
    """
    AI Brain — connects LLM reasoning with robot control functions.

    Usage
    -----
    1. ``register_function(name, fn, schema)``  — expose a robot action.
    2. ``add_knowledge(text, metadata)``         — add to vector store.
    3. ``think(prompt)``                         — call LLM, execute any
                                                   function calls, return answer.
    4. ``auto_analyse(context_dict)``            — autonomous market analysis:
                                                   enriches with RAG, calls LLM,
                                                   auto-executes suggested action.
    """

    _SYSTEM_PROMPT = (
        "You are an AI trading assistant embedded in a professional Forex robot. "
        "You have access to internal functions that let you scan for setups, "
        "pause/resume trading, adjust parameters, and retrieve historical context. "
        "Always reason step-by-step. Prioritise capital safety above profit. "
        "Respond in English. Keep answers concise and actionable."
    )

    def __init__(self) -> None:
        self._backend:   str           = "NONE"
        self._client:    Optional[Any] = None
        self._model:     str           = "gpt-4o-mini"
        self._registry   = FunctionRegistry()
        self._vector_store = VectorStore()
        self._log:       Deque[dict]  = deque(maxlen=100)
        self._last_action: str        = "IDLE"
        self._last_ts:    float       = 0.0
        self._total_calls: int        = 0
        self._enabled:    bool        = False

        self._init_backend()
        self._register_default_functions()

    # ── Initialisation ─────────────────────────────────────────────────── #

    def _init_backend(self) -> None:
        openai_key  = os.environ.get("OPENAI_API_KEY", "")
        gemini_key  = os.environ.get("GEMINI_API_KEY", "")

        if openai_key:
            try:
                import openai  # noqa: PLC0415
                self._client  = openai.OpenAI(api_key=openai_key)
                self._backend = "OPENAI"
                self._model   = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
                self._enabled = True
                logger.info("LLMOrchestrator: using OpenAI backend (%s)", self._model)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLMOrchestrator: OpenAI init failed: %s", exc)

        elif gemini_key:
            try:
                import google.generativeai as genai  # noqa: PLC0415
                genai.configure(api_key=gemini_key)
                self._model   = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
                self._client  = genai.GenerativeModel(self._model)
                self._backend = "GEMINI"
                self._enabled = True
                logger.info("LLMOrchestrator: using Gemini backend (%s)", self._model)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLMOrchestrator: Gemini init failed: %s", exc)

        else:
            logger.info(
                "LLMOrchestrator: no LLM API key found "
                "(OPENAI_API_KEY / GEMINI_API_KEY) — running in stub mode"
            )

    def _register_default_functions(self) -> None:
        """Register built-in placeholder functions."""
        self._registry.register(
            "get_market_state",
            lambda: {"status": "placeholder"},
            {
                "description": "Return the current market state (wave, regime, equity).",
                "parameters":  {"type": "object", "properties": {}, "required": []},
            },
        )
        self._registry.register(
            "explain_last_decision",
            lambda: {"explanation": "No decision recorded yet."},
            {
                "description": "Explain the last trading decision made by the robot.",
                "parameters":  {"type": "object", "properties": {}, "required": []},
            },
        )

    # ── Public API ─────────────────────────────────────────────────────── #

    def register_function(
        self,
        name:   str,
        fn:     Callable[..., Any],
        schema: dict,
    ) -> None:
        """Expose an internal robot action to the LLM."""
        self._registry.register(name, fn, schema)

    def add_knowledge(self, text: str, metadata: Optional[dict] = None) -> None:
        """Add a document to the RAG vector store."""
        self._vector_store.add(text, metadata)

    def think(self, prompt: str, max_rounds: int = 3) -> str:
        """
        Send *prompt* to the LLM.  If the LLM calls a function, execute it
        and feed the result back for up to *max_rounds* rounds.

        Returns the final natural-language answer.
        """
        if not self._enabled:
            return "[LLM not configured — set OPENAI_API_KEY or GEMINI_API_KEY]"

        # RAG: retrieve relevant context
        rag_docs = self._vector_store.query(prompt, top_k=_TOP_K_CONTEXT)
        context_text = ""
        if rag_docs:
            snippets = [f"- {d['text']}" for d in rag_docs]
            context_text = "Relevant historical context:\n" + "\n".join(snippets) + "\n\n"

        full_prompt = context_text + prompt
        self._total_calls += 1
        answer = ""

        try:
            if self._backend == "OPENAI":
                answer = self._think_openai(full_prompt, max_rounds)
            elif self._backend == "GEMINI":
                answer = self._think_gemini(full_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.error("LLMOrchestrator.think error: %s", exc)
            answer = f"[LLM error: {exc}]"

        self._last_action = prompt[:80]
        self._last_ts     = time.time()
        self._log.append({
            "prompt":   prompt[:200],
            "answer":   answer[:200],
            "ts":       self._last_ts,
            "backend":  self._backend,
        })
        return answer

    def auto_analyse(self, context: dict) -> Dict[str, Any]:
        """
        Autonomous analysis: build a prompt from *context*, call the LLM,
        parse any suggested action.

        Returns ``{"action": str, "reasoning": str, "raw": str}``.
        """
        ctx_str = json.dumps(context, indent=2)
        prompt  = (
            f"Current trading context:\n{ctx_str}\n\n"
            "Analyse the market state and recommend: SCAN_AND_ENTER, HOLD, "
            "REDUCE_EXPOSURE, or FORCE_PAUSE. Explain briefly."
        )
        raw = self.think(prompt)
        # Simple keyword extraction from response
        action = "HOLD"
        for keyword in ("SCAN_AND_ENTER", "SCALE_UP", "REDUCE_EXPOSURE", "FORCE_PAUSE", "HOLD"):
            if keyword in raw.upper():
                action = keyword
                break
        return {"action": action, "reasoning": raw[:500], "raw": raw}

    # ── Backend-specific call helpers ──────────────────────────────────── #

    def _think_openai(self, prompt: str, max_rounds: int) -> str:
        messages = [
            {"role": "system", "content": self._SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]
        tools = [
            {"type": "function", "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {}),
            }}
            for t in self._registry.tools
        ]

        for _ in range(max_rounds):
            resp = self._client.chat.completions.create(  # type: ignore[union-attr]
                model=self._model,
                messages=messages,
                tools=tools or None,
                tool_choice="auto" if tools else None,
            )
            msg = resp.choices[0].message
            if msg.tool_calls:
                messages.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]})
                for tc in msg.tool_calls:
                    try:
                        fn_args = json.loads(tc.function.arguments or "{}")
                        result  = self._registry.call(tc.function.name, **fn_args)
                    except Exception as exc:  # noqa: BLE001
                        result = {"error": str(exc)}
                    messages.append({"role": "tool", "content": json.dumps(result), "tool_call_id": tc.id})
            else:
                return msg.content or ""
        return messages[-1].get("content", "") if isinstance(messages[-1], dict) else ""

    def _think_gemini(self, prompt: str) -> str:
        full = f"{self._SYSTEM_PROMPT}\n\n{prompt}"
        resp = self._client.generate_content(full)  # type: ignore[union-attr]
        return resp.text if hasattr(resp, "text") else str(resp)

    # ── Properties ─────────────────────────────────────────────────────── #

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def model(self) -> str:
        return self._model

    @property
    def vector_store_size(self) -> int:
        return self._vector_store.size

    @property
    def last_action(self) -> str:
        return self._last_action

    @property
    def last_action_ts(self) -> float:
        return self._last_ts

    def get_function_call_log(self, n: int = 20) -> List[dict]:
        return list(self._log)[-n:]

    def status(self) -> dict:
        return {
            "enabled":           self._enabled,
            "model":             self._model,
            "rag_enabled":       True,
            "vector_store_size": self._vector_store.size,
            "last_action":       self._last_action,
            "last_action_ts":    self._last_ts,
            "function_call_log": self.get_function_call_log(10),
        }
