"""WebSocket router — real-time bot status + workspace notifications."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.cache import get_redis
from app.core.db import AsyncSessionLocal
from app.core.security import decode_token
from app.models import BotInstance, User, WorkspaceMember

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

_bot_connections: dict[str, Set[WebSocket]] = {}
_workspace_connections: dict[str, Set[WebSocket]] = {}


@router.websocket("/ws/bots/{bot_instance_id}")
async def ws_bot(websocket: WebSocket, bot_instance_id: str):
    if not await _can_access_bot(websocket, bot_instance_id):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _bot_connections.setdefault(bot_instance_id, set()).add(websocket)
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
            data = await websocket.receive_text()
            # ping/pong keepalive
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info("WS disconnected: bot=%s", bot_instance_id)
    except Exception as exc:
        logger.error("WS error: %s", exc)
    finally:
        _bot_connections.get(bot_instance_id, set()).discard(websocket)
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
    if not await _can_access_workspace(websocket, workspace_id):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _workspace_connections.setdefault(workspace_id, set()).add(websocket)
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
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info("WS disconnected: workspace=%s", workspace_id)
    except Exception as exc:
        logger.error("WS error: %s", exc)
    finally:
        _workspace_connections.get(workspace_id, set()).discard(websocket)
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
                await websocket.send_text(message["data"])
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
    user_id = payload.get("sub")
    if not user_id:
        return None
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
        return result.scalar_one_or_none()


async def _can_access_workspace(websocket: WebSocket, workspace_id: str) -> bool:
    user = await _resolve_authenticated_user(websocket)
    if user is None:
        return False
    if user.is_superuser:
        return True
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.id,
            )
        )
        return result.scalar_one_or_none() is not None


async def _can_access_bot(websocket: WebSocket, bot_instance_id: str) -> bool:
    user = await _resolve_authenticated_user(websocket)
    if user is None:
        return False
    async with AsyncSessionLocal() as db:
        if user.is_superuser:
            bot = await db.execute(select(BotInstance.id).where(BotInstance.id == bot_instance_id))
            return bot.scalar_one_or_none() is not None
        bot_result = await db.execute(
            select(BotInstance.workspace_id).where(BotInstance.id == bot_instance_id)
        )
        workspace_id = bot_result.scalar_one_or_none()
        if workspace_id is None:
            return False
        membership = await db.execute(
            select(WorkspaceMember.id).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.id,
            )
        )
        return membership.scalar_one_or_none() is not None
