"""WebSocket router — real-time bot status + workspace notifications."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.cache import get_redis
from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.core.security import decode_token
from app.core.token_revocation import is_user_access_token_revoked_after, normalize_iat_ms
from app.models import BotInstance, User, WorkspaceMember

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])
settings = get_settings()

_bot_connections: dict[str, Set[WebSocket]] = {}
_workspace_connections: dict[str, Set[WebSocket]] = {}
_user_connections: dict[str, Set[WebSocket]] = {}
_workspace_user_connections: dict[str, dict[str, Set[WebSocket]]] = {}
_WS_IDLE_TIMEOUT_SECONDS = settings.ws_idle_timeout_seconds
_WS_MAX_CONNECTIONS_PER_USER = settings.ws_max_connections_per_user
_WS_MAX_CONNECTIONS_PER_USER_PER_WORKSPACE = settings.ws_max_connections_per_user_per_workspace


@router.websocket("/ws/bots/{bot_instance_id}")
async def ws_bot(websocket: WebSocket, bot_instance_id: str):
    access = await _can_access_bot(websocket, bot_instance_id)
    if access is None:
        await websocket.close(code=1008)
        return
    user, workspace_id = access
    if not _can_open_connection(user.id, workspace_id):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _bot_connections.setdefault(bot_instance_id, set()).add(websocket)
    _register_connection(user.id, workspace_id, websocket)
    logger.info("WS connected: bot=%s", bot_instance_id)
    listen_task: asyncio.Task | None = None
    pubsub = None
    channel = f"signals:{bot_instance_id}"
    try:
        redis = await get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        listen_task = asyncio.create_task(_listen_redis(pubsub, websocket))
        while True:
            data = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=_WS_IDLE_TIMEOUT_SECONDS,
            )
            # ping/pong keepalive
            if data == "ping":
                await _send_event(websocket, event="pong", payload={"ok": True})
    except asyncio.TimeoutError:
        await _send_event(websocket, event="timeout", payload={"reason": "idle_timeout"})
        await websocket.close(code=1001)
    except WebSocketDisconnect:
        logger.info("WS disconnected: bot=%s", bot_instance_id)
    except Exception as exc:
        logger.error("WS error: %s", exc)
    finally:
        _bot_connections.get(bot_instance_id, set()).discard(websocket)
        _unregister_connection(user.id, workspace_id, websocket)
        if listen_task is not None:
            listen_task.cancel()
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                pass
            try:
                await pubsub.close()
            except Exception:
                pass


@router.websocket("/ws/workspaces/{workspace_id}/notifications")
async def ws_workspace(websocket: WebSocket, workspace_id: str):
    user = await _can_access_workspace(websocket, workspace_id)
    if user is None:
        await websocket.close(code=1008)
        return
    if not _can_open_connection(user.id, workspace_id):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _workspace_connections.setdefault(workspace_id, set()).add(websocket)
    _register_connection(user.id, workspace_id, websocket)
    logger.info("WS connected: workspace=%s", workspace_id)
    listen_task: asyncio.Task | None = None
    pubsub = None
    channel = f"workspace:{workspace_id}:notifications"
    try:
        redis = await get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        listen_task = asyncio.create_task(_listen_redis(pubsub, websocket))
        while True:
            data = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=_WS_IDLE_TIMEOUT_SECONDS,
            )
            if data == "ping":
                await _send_event(websocket, event="pong", payload={"ok": True})
    except asyncio.TimeoutError:
        await _send_event(websocket, event="timeout", payload={"reason": "idle_timeout"})
        await websocket.close(code=1001)
    except WebSocketDisconnect:
        logger.info("WS disconnected: workspace=%s", workspace_id)
    except Exception as exc:
        logger.error("WS error: %s", exc)
    finally:
        _workspace_connections.get(workspace_id, set()).discard(websocket)
        _unregister_connection(user.id, workspace_id, websocket)
        if listen_task is not None:
            listen_task.cancel()
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                pass
            try:
                await pubsub.close()
            except Exception:
                pass


async def _listen_redis(pubsub, websocket: WebSocket):
    """Forward Redis pub/sub messages to the WebSocket."""
    async for message in pubsub.listen():
        if message["type"] == "message":
            try:
                payload = _decode_message_data(message.get("data"))
                await _send_event(websocket, event="message", payload=payload)
            except Exception:
                break


def _extract_bearer_token(websocket: WebSocket) -> str | None:
    token = websocket.query_params.get("token")
    if token:
        return token
    authorization = websocket.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


async def _resolve_authenticated_user(websocket: WebSocket) -> User | None:
    token = _extract_bearer_token(websocket)
    if not token:
        return None
    try:
        payload = decode_token(token)
    except Exception:
        return None
    if payload.get("type") != "access":
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    if await is_user_access_token_revoked_after(
        user_id,
        normalize_iat_ms(payload.get("iat_ms", payload.get("iat"))),
    ):
        return None
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
        return result.scalar_one_or_none()


async def _can_access_workspace(websocket: WebSocket, workspace_id: str) -> User | None:
    user = await _resolve_authenticated_user(websocket)
    if user is None:
        return None
    if user.is_superuser:
        return user
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.id,
            )
        )
        if result.scalar_one_or_none() is None:
            return None
    return user


async def _can_access_bot(websocket: WebSocket, bot_instance_id: str) -> tuple[User, str] | None:
    user = await _resolve_authenticated_user(websocket)
    if user is None:
        return None
    async with AsyncSessionLocal() as db:
        if user.is_superuser:
            bot = await db.execute(
                select(BotInstance.workspace_id).where(BotInstance.id == bot_instance_id)
            )
            workspace_id = bot.scalar_one_or_none()
            return (user, workspace_id) if workspace_id else None
        bot_result = await db.execute(
            select(BotInstance.workspace_id).where(BotInstance.id == bot_instance_id)
        )
        workspace_id = bot_result.scalar_one_or_none()
        if workspace_id is None:
            return None
        membership = await db.execute(
            select(WorkspaceMember.id).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.id,
            )
        )
        if membership.scalar_one_or_none() is None:
            return None
        return (user, workspace_id)


def _can_open_connection(user_id: str, workspace_id: str) -> bool:
    user_count = len(_user_connections.get(user_id, set()))
    workspace_count = len(
        _workspace_user_connections.get(workspace_id, {}).get(user_id, set())
    )
    if user_count >= _WS_MAX_CONNECTIONS_PER_USER:
        return False
    if workspace_count >= _WS_MAX_CONNECTIONS_PER_USER_PER_WORKSPACE:
        return False
    return True


def _register_connection(user_id: str, workspace_id: str, websocket: WebSocket) -> None:
    _user_connections.setdefault(user_id, set()).add(websocket)
    _workspace_user_connections.setdefault(workspace_id, {}).setdefault(user_id, set()).add(
        websocket
    )


def _unregister_connection(user_id: str, workspace_id: str, websocket: WebSocket) -> None:
    _user_connections.get(user_id, set()).discard(websocket)
    workspace_bucket = _workspace_user_connections.get(workspace_id, {})
    workspace_bucket.get(user_id, set()).discard(websocket)
    if user_id in workspace_bucket and not workspace_bucket[user_id]:
        workspace_bucket.pop(user_id, None)
    if workspace_id in _workspace_user_connections and not _workspace_user_connections[workspace_id]:
        _workspace_user_connections.pop(workspace_id, None)
    if user_id in _user_connections and not _user_connections[user_id]:
        _user_connections.pop(user_id, None)


def _decode_message_data(data: object) -> dict:
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except json.JSONDecodeError:
            return {"value": data}
    return {"value": data}


async def _send_event(websocket: WebSocket, event: str, payload: dict) -> None:
    await websocket.send_text(
        json.dumps(
            {"event": event, "payload": payload, "timestamp": int(time.time())},
            separators=(",", ":"),
            sort_keys=True,
        )
    )
