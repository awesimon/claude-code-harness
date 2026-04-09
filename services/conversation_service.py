"""
Conversation Service - Manage conversations and messages
"""
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import desc

from models import Conversation, Message
from schemas import ConversationCreate, ConversationUpdate, MessageCreate


class ConversationService:
    def __init__(self, db: Session):
        self.db = db

    def create_conversation(self, data: ConversationCreate) -> Conversation:
        """Create a new conversation"""
        conversation = Conversation(title=data.title)
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Get a conversation by ID"""
        return self.db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()

    def list_conversations(self, limit: int = 50) -> List[Conversation]:
        """List recent conversations"""
        return self.db.query(Conversation).order_by(
            desc(Conversation.updated_at)
        ).limit(limit).all()

    def update_conversation(
        self,
        conversation_id: str,
        updates: ConversationUpdate
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
        return True

    def add_message(self, conversation_id: str, data: MessageCreate) -> Message:
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
            tool_results=data.tool_results
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def get_messages(self, conversation_id: str, limit: int = 100) -> List[Message]:
        """Get messages for a conversation"""
        return self.db.query(Message).filter(
            Message.conversation_id == conversation_id
        ).order_by(Message.timestamp).limit(limit).all()

    def get_message(self, message_id: str) -> Optional[Message]:
        """Get a single message by ID"""
        return self.db.query(Message).filter(Message.id == message_id).first()

    def update_message(
        self,
        message_id: str,
        content: Optional[str] = None,
        thinking: Optional[str] = None,
        tool_calls: Optional[list] = None,
        tool_results: Optional[list] = None
    ) -> Optional[Message]:
        """Update a message"""
        message = self.get_message(message_id)
        if not message:
            return None

        if content is not None:
            message.content = content
        if thinking is not None:
            message.thinking = thinking
        if tool_calls is not None:
            message.tool_calls = tool_calls
        if tool_results is not None:
            message.tool_results = tool_results

        self.db.commit()
        self.db.refresh(message)
        return message
