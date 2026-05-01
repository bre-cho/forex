"""Advanced engine guard — feature flag for L6/L7/L8 research engines.

L6, L7, and L8 engines (causal, meta-learning, self-play, game-theory,
utility-optimisation, sovereign oversight, autonomous enterprise) are
research-grade and MUST NOT be used in production without explicit opt-in.

Usage — call this at the top of every advanced engine module::

    from trading_core.engines._advanced_guard import require_advanced_engines

    require_advanced_engines("CausalStrategyEngine")

Environment variable control
-----------------------------
``ENABLE_ADVANCED_ENGINES``
    ``"true"``  — allow instantiation (required for staging / experiments).
    ``"false"`` (default) — raise ``RuntimeError`` in production/staging;
    emit a ``RuntimeWarning`` in development/test.

``APP_ENV``
    ``"production"`` / ``"prod"`` / ``"staging"`` — hard block unless flag set.
    Anything else (``"development"``, ``"test"``, …) — emit warning only.
"""
from __future__ import annotations

import os
import warnings

_APP_ENV = str(os.getenv("APP_ENV", "development") or "development").strip().lower()
_ENABLE_ADVANCED = (
    str(os.getenv("ENABLE_ADVANCED_ENGINES", "false") or "false").strip().lower()
    == "true"
)
_PRODUCTION_ENVS = {"production", "prod", "staging"}


def require_advanced_engines(engine_name: str) -> None:
    """Fail-closed guard for L6/L7/L8 research engines.

    Call at the start of ``__init__`` for every advanced engine class.

    Raises
    ------
    RuntimeError
        When called in a production/staging environment without
        ``ENABLE_ADVANCED_ENGINES=true``.

    Warns
    -----
    RuntimeWarning
        When called in a non-production environment without
        ``ENABLE_ADVANCED_ENGINES=true``.
    """
    if _ENABLE_ADVANCED:
        return

    msg = (
        f"{engine_name} is a research-grade (L6/L7/L8) engine that is "
        "NOT validated for live trading with real money. "
        "Set ENABLE_ADVANCED_ENGINES=true to explicitly opt in after a "
        "full audit and stakeholder sign-off."
    )

    if _APP_ENV in _PRODUCTION_ENVS:
        raise RuntimeError(
            f"{msg} Current APP_ENV={_APP_ENV!r} forbids instantiation."
        )

    # Development / test — warn, don't block.
    warnings.warn(msg, RuntimeWarning, stacklevel=3)
