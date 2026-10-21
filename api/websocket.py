"""
Real-Time WebSocket Management.
Handles multi-client connections, guild subscriptions, heartbeat ping/pongs,
and instant broadcasting of voice events and statistics changes.
"""

from __future__ import annotations
import json
import time
import logging
from typing import Dict, Optional, Set
from fastapi import WebSocket, WebSocketDisconnect
from services.config import get_settings
from services.security import verify_secret

logger = logging.getLogger("websocket")


class ConnectionManager:
    """
    Manages active WebSocket connections, guild-specific subscriptions,
    and thread-safe async broadcasting.
    """

    def __init__(self):
        # All active sockets
        self.active_connections: Set[WebSocket] = set()
        # guild_id -> set of WebSockets subscribed to that guild
        self.guild_subscriptions: Dict[str, Set[WebSocket]] = {}
        # socket -> guild_id currently subscribed to
        self.socket_to_guild: Dict[WebSocket, str] = {}
        # socket -> authenticated status (admin access)
        self.authenticated_sockets: Dict[WebSocket, bool] = {}

    async def connect(
        self,
        websocket: WebSocket,
        token: Optional[str] = None,
        guild_id: Optional[str] = None,
    ) -> bool:
        """Accepts a WebSocket connection and registers initial state."""
        await websocket.accept()
        self.active_connections.add(websocket)

        # Check authentication if provided
        settings = get_settings()
        is_admin = bool(token and verify_secret(token, settings.dashboard_secret))
        self.authenticated_sockets[websocket] = is_admin

        if guild_id:
            self.subscribe(websocket, guild_id)

        # Send connection established handshake
        await self.send_personal_message(
            {
                "type": "connection_established",
                "authenticated": is_admin,
                "timestamp": time.time(),
            },
            websocket,
        )
        return True

    def disconnect(self, websocket: WebSocket) -> None:
        """Removes a disconnected socket from all registries."""
        self.active_connections.discard(websocket)
        self.authenticated_sockets.pop(websocket, None)

        guild_id = self.socket_to_guild.pop(websocket, None)
        if guild_id and guild_id in self.guild_subscriptions:
            self.guild_subscriptions[guild_id].discard(websocket)
            if not self.guild_subscriptions[guild_id]:
                del self.guild_subscriptions[guild_id]

    def subscribe(self, websocket: WebSocket, guild_id: str) -> None:
        """Subscribes a socket to real-time updates for a specific guild."""
        old_guild = self.socket_to_guild.get(websocket)
        if old_guild and old_guild in self.guild_subscriptions:
            self.guild_subscriptions[old_guild].discard(websocket)

        self.socket_to_guild[websocket] = guild_id
        if guild_id not in self.guild_subscriptions:
            self.guild_subscriptions[guild_id] = set()
        self.guild_subscriptions[guild_id].add(websocket)

    async def send_personal_message(
        self, message: dict, websocket: WebSocket
    ) -> None:
        """Sends a JSON payload to a specific socket."""
        try:
            await websocket.send_text(json.dumps(message))
        except Exception as e:
            logger.debug("Failed sending message to WebSocket: %s", e)
            self.disconnect(websocket)

    async def broadcast_to_guild(self, guild_id: str, message: dict) -> None:
        """
        Broadcasts a message to all sockets subscribed to a specific guild,
        plus any sockets subscribed to wildcard/all.
        """
        subscribers = set(self.guild_subscriptions.get(str(guild_id), set()))
        subscribers.update(self.guild_subscriptions.get("all", set()))

        if not subscribers:
            return

        payload = json.dumps(message)
        dead_sockets = []

        for socket in subscribers:
            try:
                await socket.send_text(payload)
            except Exception:
                dead_sockets.append(socket)

        for dead in dead_sockets:
            self.disconnect(dead)

    async def broadcast_all(self, message: dict) -> None:
        """Broadcasts a payload to every connected client."""
        if not self.active_connections:
            return

        payload = json.dumps(message)
        dead_sockets = []

        for socket in self.active_connections:
            try:
                await socket.send_text(payload)
            except Exception:
                dead_sockets.append(socket)

        for dead in dead_sockets:
            self.disconnect(dead)


# Global connection manager instance
ws_manager = ConnectionManager()
