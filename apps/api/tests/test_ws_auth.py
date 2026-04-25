from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.routers import ws


class _DummyWebSocket:
    def __init__(self, query: dict[str, str] | None = None, headers: dict[str, str] | None = None):
        self.query_params = query or {}
        self.headers = headers or {}


def test_extract_bearer_token_from_query_and_header():
    query_ws = _DummyWebSocket(query={"token": "query-token"})
    header_ws = _DummyWebSocket(headers={"authorization": "Bearer header-token"})

    assert ws._extract_bearer_token(query_ws) == "query-token"
    assert ws._extract_bearer_token(header_ws) == "header-token"


def test_websocket_rejects_unauthenticated_connection():
    app = FastAPI()
    app.include_router(ws.router)

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/workspaces/ws-1/notifications"):
                pass


def test_connection_limit_enforced_per_user_and_workspace():
    ws._user_connections.clear()
    ws._workspace_user_connections.clear()

    fake_socket = object()
    user_id = "user-1"
    workspace_id = "workspace-1"

    ws._register_connection(user_id, workspace_id, fake_socket)  # type: ignore[arg-type]
    for idx in range(ws._WS_MAX_CONNECTIONS_PER_USER_PER_WORKSPACE - 1):
        ws._register_connection(user_id, workspace_id, object())  # type: ignore[arg-type]

    assert ws._can_open_connection(user_id, workspace_id) is False
