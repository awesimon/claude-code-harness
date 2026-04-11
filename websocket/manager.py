"""
WebSocket Manager - Handle real-time communication
"""
from typing import Dict, List, Set
from fastapi import WebSocket
import json
from datetime import datetime


class ConnectionManager:
    """Manage WebSocket connections"""

    def __init__(self):
        # conversation_id -> set of WebSocket connections
        self.connections: Dict[str, Set[WebSocket]] = {}
        # global connections (for system-wide updates)
        self.global_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket, conversation_id: str = None):
        """Accept and register a new connection"""
        await websocket.accept()

        if conversation_id:
            if conversation_id not in self.connections:
                self.connections[conversation_id] = set()
            self.connections[conversation_id].add(websocket)
        else:
            self.global_connections.add(websocket)

    def disconnect(self, websocket: WebSocket, conversation_id: str = None):
        """Remove a connection"""
        if conversation_id and conversation_id in self.connections:
            self.connections[conversation_id].discard(websocket)
            if not self.connections[conversation_id]:
                del self.connections[conversation_id]
        else:
            self.global_connections.discard(websocket)

    async def broadcast_to_conversation(
        self,
        conversation_id: str,
        message: dict
    ):
        """Send message to all connections in a conversation"""
        if conversation_id not in self.connections:
            return

        message['timestamp'] = datetime.utcnow().isoformat()
        disconnected = []

        for connection in self.connections[conversation_id]:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.connections[conversation_id].discard(conn)

    async def broadcast_global(self, message: dict):
        """Send message to all global connections"""
        message['timestamp'] = datetime.utcnow().isoformat()
        disconnected = []

        for connection in self.global_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.global_connections.discard(conn)

    async def send_personal_message(self, websocket: WebSocket, message: dict):
        """Send message to a specific connection"""
        message['timestamp'] = datetime.utcnow().isoformat()
        try:
            await websocket.send_json(message)
        except Exception:
            pass


# Global connection manager instance
manager = ConnectionManager()


# Event types for WebSocket messages
class WSEventType:
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_DELETED = "task_deleted"
    TASK_CLAIMED = "task_claimed"
    MESSAGE_CREATED = "message_created"
    MESSAGE_UPDATED = "message_updated"
    MESSAGE_DELETED = "message_deleted"
    MESSAGES_CLEARED = "messages_cleared"
    CONVERSATION_UPDATED = "conversation_updated"
    AGENT_STATUS_CHANGED = "agent_status_changed"
    PLAN_UPDATED = "plan_updated"
