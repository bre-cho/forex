from __future__ import annotations

from .mt5 import MT5Provider


class MT5DemoProvider(MT5Provider):
    """Explicit demo-only MT5 provider."""

    def __init__(self, *args, **kwargs):
        kwargs["live"] = False
        super().__init__(*args, **kwargs)
