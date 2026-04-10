"""
Database models for SQLite backend
"""
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, List
from sqlalchemy import create_engine, Column, String, Text, DateTime, ForeignKey, Integer, Enum, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
import uuid
import os

Base = declarative_base()

# Database file path
DB_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
DB_PATH = os.path.join(DB_DIR, 'claude_code.db')

# Ensure data directory exists
os.makedirs(DB_DIR, exist_ok=True)

# Create engine
engine = create_engine(f'sqlite:///{DB_PATH}', echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class TaskStatus(str, PyEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ConversationState(str, PyEnum):
    """对话状态枚举"""
    NORMAL = "normal"
    PLANNING = "planning"


class TeamMemberStatus(str, PyEnum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=True)
    state = Column(String, default=ConversationState.NORMAL)  # 'normal', 'planning'
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"))
    role = Column(String, nullable=False)  # 'user', 'assistant', 'system'
    content = Column(Text, nullable=False)
    thinking = Column(Text, nullable=True)
    tool_calls = Column(JSON, nullable=True)  # Store tool calls as JSON
    tool_results = Column(JSON, nullable=True)  # Store tool results as JSON
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True)
    team_id = Column(String, ForeignKey("teams.id", ondelete="CASCADE"), nullable=True)
    subject = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    active_form = Column(String, nullable=True)  # Present continuous form for spinner
    owner = Column(String, nullable=True)  # Agent ID or name
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    blocks = Column(JSON, default=list)  # List of task IDs this task blocks
    blocked_by = Column(JSON, default=list)  # List of task IDs that block this task
    meta = Column(JSON, default=dict)  # Arbitrary metadata - use 'meta' not 'metadata'
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    conversation = relationship("Conversation", back_populates="tasks")
    team = relationship("Team")


class Plan(Base):
    __tablename__ = "plans"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), unique=True)
    content = Column(Text, nullable=False)
    version = Column(Integer, default=1)  # Plan version for recovery
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    conversation = relationship("Conversation")


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    agent_type = Column(String, default="worker")
    capabilities = Column(JSON, default=list)
    status = Column(String, default="idle")  # 'idle', 'busy', 'completed', 'error'
    max_concurrent_tasks = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Team(Base):
    __tablename__ = "teams"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)
    lead_agent_id = Column(String, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id = Column(String, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(String, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    agent_type = Column(String, default="worker")
    status = Column(Enum(TeamMemberStatus), default=TeamMemberStatus.IDLE)
    joined_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    team = relationship("Team", back_populates="members")

    # Ensure unique agent per team
    __table_args__ = (
        {'sqlite_autoincrement': True},
    )


def init_db():
    """Initialize the database, creating all tables"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
