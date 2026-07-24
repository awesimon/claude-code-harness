"""
Conversation Service - Manage conversations and messages
"""

from typing import List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session, sessionmaker

from models import Conversation, Message
from schemas import ConversationCreate, ConversationUpdate, MessageCreate
from state_core import EventType, SessionRuntime, SQLAlchemyStateStore


class ConversationService:
    def __init__(self, db: Session):
        self.db = db
        self._factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
        self._store = SQLAlchemyStateStore(self._factory)

    def _runtime(self, conversation_id: str) -> SessionRuntime:
        return SessionRuntime(conversation_id, self._store)

    def create_conversation(self, data: ConversationCreate) -> Conversation:
        """Create a new conversation"""
        conversation = Conversation(title=data.title)
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        self._runtime(conversation.id)
        return conversation

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Get a conversation by ID"""
        return self.db.query(Conversation).filter(Conversation.id == conversation_id).first()

    def list_conversations(self, limit: int = 50) -> List[Conversation]:
        """List recent conversations"""
        return (
            self.db.query(Conversation).order_by(desc(Conversation.updated_at)).limit(limit).all()
        )

    def update_conversation(
        self, conversation_id: str, updates: ConversationUpdate
    ) -> Optional[Conversation]:
        """Update a conversation"""
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None

        if updates.title is not None:
            conversation.title = updates.title

        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation and all its messages/tasks"""
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return False

        self.db.delete(conversation)
        self.db.commit()
        self._store.tasks.delete_list(conversation_id)
        self._store.states.delete_session(conversation_id)
        return True

    def add_message(
        self,
        conversation_id: str,
        data: MessageCreate,
        *,
        project_only: bool = False,
    ) -> Message:
        """Add a message to a conversation"""
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation {conversation_id} not found")

        message = Message(
            conversation_id=conversation_id,
            role=data.role,
            content=data.content,
            thinking=data.thinking,
            tool_calls=data.tool_calls,
            tool_results=data.tool_results,
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        if not project_only:
            runtime = self._runtime(conversation_id)
            event_type = (
                EventType.USER_MESSAGE if data.role == "user" else EventType.ASSISTANT_MESSAGE
            )
            runtime.append_event(
                event_type,
                {
                    "content": data.content,
                    "thinking": data.thinking,
                    "legacyMessageId": message.id,
                },
            )
            for call in data.tool_calls or []:
                runtime.append_event(
                    EventType.TOOL_CALL,
                    {
                        "toolCallId": call.get("id") or call.get("tool_call_id"),
                        "name": call.get("name"),
                        "input": call.get("arguments") or call.get("input"),
                    },
                )
            for result in data.tool_results or []:
                runtime.append_event(
                    EventType.TOOL_RESULT,
                    {
                        "toolCallId": result.get("tool_call_id") or result.get("toolCallId"),
                        "content": result.get("content") or result.get("result"),
                    },
                )
        return message

    def get_messages(self, conversation_id: str, limit: int = 100) -> List[Message]:
        """Get messages for a conversation"""
        return (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.timestamp)
            .limit(limit)
            .all()
        )

    def get_message(self, message_id: str) -> Optional[Message]:
        """Get a single message by ID"""
        return self.db.query(Message).filter(Message.id == message_id).first()

    def delete_message(self, message_id: str) -> bool:
        """Delete a message by ID"""
        message = self.get_message(message_id)
        if not message:
            return False

        self.db.delete(message)
        self.db.commit()
        return True

    def clear_messages(self, conversation_id: str) -> bool:
        """Delete all messages in a conversation"""
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return False

        self.db.query(Message).filter(Message.conversation_id == conversation_id).delete()
        self.db.commit()
        return True
