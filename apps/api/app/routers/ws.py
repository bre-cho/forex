"""WebSocket router — real-time bot status + workspace notifications."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.cache import get_redis

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

_bot_connections: dict[str, Set[WebSocket]] = {}
_workspace_connections: dict[str, Set[WebSocket]] = {}


@router.websocket("/ws/bots/{bot_instance_id}")
async def ws_bot(websocket: WebSocket, bot_instance_id: str):
    await websocket.accept()
    _bot_connections.setdefault(bot_instance_id, set()).add(websocket)
    logger.info("WS connected: bot=%s", bot_instance_id)
    try:
        redis = await get_redis()
        channel = f"signals:{bot_instance_id}"
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
        try:
            listen_task.cancel()
        except Exception:
            pass


@router.websocket("/ws/workspaces/{workspace_id}/notifications")
async def ws_workspace(websocket: WebSocket, workspace_id: str):
    await websocket.accept()
    _workspace_connections.setdefault(workspace_id, set()).add(websocket)
    logger.info("WS connected: workspace=%s", workspace_id)
    try:
        redis = await get_redis()
        channel = f"workspace:{workspace_id}:notifications"
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
        try:
            listen_task.cancel()
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
