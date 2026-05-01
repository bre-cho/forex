"""cTrader OAuth2 token auto-refresh service.

Runs as a background asyncio task and proactively refreshes the access token
before it expires.  When a refresh fails the service pauses the associated
BotRuntime and emits an alert so that operators can intervene.

Usage
-----
    refresher = CTraderTokenRefresher(
        client_id=...,
        client_secret=...,
        refresh_token=...,
        on_token_refreshed=async_callback,   # receives (access_token, refresh_token, expires_in)
        on_refresh_failed=async_callback,    # receives (reason: str)
        refresh_margin_seconds=300,          # refresh 5 minutes before expiry
    )
    await refresher.start(expires_in_seconds=3600)
    ...
    await refresher.stop()
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# cTrader Open API token endpoint (production)
_CTRADER_TOKEN_URL = "https://openapi.ctrader.com/apps/token"


class CTraderTokenRefresher:
    """Background service that refreshes cTrader OAuth2 tokens proactively.

    The service schedules a refresh ``refresh_margin_seconds`` before the
    current access token expires.  On success it invokes ``on_token_refreshed``
    with the new credentials.  On failure it retries with exponential backoff
    (5 s → 30 s → 120 s) then calls ``on_refresh_failed`` so the caller can
    pause the bot and raise an incident.
    """

    _BACKOFF_STEPS = (5, 30, 120)

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        on_token_refreshed: Callable[[str, str, int], Awaitable[None]],
        on_refresh_failed: Callable[[str], Awaitable[None]],
        refresh_margin_seconds: int = 300,
        token_url: str = _CTRADER_TOKEN_URL,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._on_token_refreshed = on_token_refreshed
        self._on_refresh_failed = on_refresh_failed
        self._margin = int(refresh_margin_seconds)
        self._token_url = token_url
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, expires_in_seconds: int) -> None:
        """Start the background refresh loop.

        Parameters
        ----------
        expires_in_seconds:
            Remaining lifetime of the *current* access token in seconds.
            Typically taken directly from the ``expires_in`` field returned
            by the broker's token endpoint.
        """
        if self._task is not None and not self._task.done():
            logger.debug("CTraderTokenRefresher already running")
            return
        self._stop_event.clear()
        self._expires_at = time.time() + float(expires_in_seconds)
        self._task = asyncio.create_task(self._refresh_loop(), name="ctrader_token_refresh")
        logger.info(
            "CTraderTokenRefresher started (expires_in=%ds, margin=%ds)",
            expires_in_seconds,
            self._margin,
        )

    async def stop(self) -> None:
        """Stop the background refresh loop."""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("CTraderTokenRefresher stopped")

    def update_refresh_token(self, refresh_token: str, expires_in_seconds: int) -> None:
        """Update stored credentials after a successful external refresh."""
        self._refresh_token = refresh_token
        self._expires_at = time.time() + float(expires_in_seconds)
        logger.debug("CTraderTokenRefresher credentials updated (new expiry in %ds)", expires_in_seconds)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            wait_seconds = max(0.0, self._expires_at - time.time() - self._margin)
            logger.debug(
                "CTraderTokenRefresher: next refresh in %.0fs (expires_at=%.0f)",
                wait_seconds,
                self._expires_at,
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop_event.wait()),
                    timeout=wait_seconds,
                )
                # stop_event fired — exit loop cleanly
                break
            except asyncio.TimeoutError:
                # Scheduled wake-up: attempt the refresh
                pass

            if self._stop_event.is_set():
                break

            await self._do_refresh_with_backoff()

    async def _do_refresh_with_backoff(self) -> None:
        last_error = "unknown"
        for attempt, backoff in enumerate((*self._BACKOFF_STEPS, None)):
            if self._stop_event.is_set():
                return
            try:
                access_token, refresh_token, expires_in = await self._call_token_endpoint()
                # Update internal state
                self._refresh_token = refresh_token
                self._expires_at = time.time() + float(expires_in)
                await self._on_token_refreshed(access_token, refresh_token, expires_in)
                logger.info(
                    "CTraderTokenRefresher: token refreshed successfully (expires_in=%ds)",
                    expires_in,
                )
                return
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "CTraderTokenRefresher: refresh attempt %d failed: %s",
                    attempt + 1,
                    exc,
                )
                if backoff is None:
                    break
                if self._stop_event.is_set():
                    return
                await asyncio.sleep(float(backoff))

        # All retries exhausted — notify caller
        reason = f"ctrader_token_refresh_failed_after_retries:{last_error}"
        logger.error("CTraderTokenRefresher: %s", reason)
        try:
            await self._on_refresh_failed(reason)
        except Exception as cb_exc:
            logger.error("CTraderTokenRefresher: on_refresh_failed callback error: %s", cb_exc)

    async def _call_token_endpoint(self) -> tuple[str, str, int]:
        """POST to the cTrader token endpoint and return (access_token, refresh_token, expires_in).

        Uses ``asyncio`` + standard library ``urllib`` to avoid an extra HTTP
        dependency.  In environments that already have ``aiohttp`` or ``httpx``
        available those can be substituted here.
        """
        import json
        import urllib.parse
        import urllib.request

        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
            }
        ).encode("utf-8")

        loop = asyncio.get_event_loop()

        def _blocking_request() -> dict[str, Any]:
            req = urllib.request.Request(
                url=self._token_url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8"))

        payload: dict[str, Any] = await loop.run_in_executor(None, _blocking_request)

        access_token = str(payload.get("accessToken") or payload.get("access_token") or "")
        new_refresh_token = str(
            payload.get("refreshToken") or payload.get("refresh_token") or self._refresh_token
        )
        expires_in = int(payload.get("expiresIn") or payload.get("expires_in") or 3600)

        if not access_token:
            raise RuntimeError(f"ctrader_token_endpoint_missing_access_token: {payload}")

        return access_token, new_refresh_token, expires_in
