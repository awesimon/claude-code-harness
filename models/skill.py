"""
Agent Skills 标准协议数据模型
对应 agentskills.io 规范
"""
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator
from datetime import datetime
from enum import Enum


class SkillMetadata(BaseModel):
    """Skill 元数据（metadata 字段）"""
    author: Optional[str] = None
    version: Optional[str] = None
    # 允许任意额外字段
    class Config:
        extra = "allow"


class SkillDefinition(BaseModel):
    """
    Skill 定义模型
    对应 SKILL.md 的 frontmatter + 内容
    """
    # 必需字段
    name: str = Field(..., min_length=1, max_length=64, regex=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    description: str = Field(..., min_length=1, max_length=1024)

    # 可选字段
    license: Optional[str] = None
    compatibility: Optional[str] = Field(None, max_length=500)
    metadata: Optional[SkillMetadata] = None
    allowed_tools: Optional[List[str]] = None  # 从 allowed-tools 解析

    # 运行时字段（非 frontmatter）
    content: str = ""  # Markdown 内容（frontmatter 之后）
    base_dir: Optional[str] = None  # Skill 所在目录

    # 可选子目录
    has_scripts: bool = False
    has_references: bool = False
    has_assets: bool = False

    # 加载时间戳
    loaded_at: Optional[datetime] = None

    @validator('name')
    def validate_name(cls, v):
        """验证 name 字段符合规范"""
        if '--' in v:
            raise ValueError('name cannot contain consecutive hyphens')
        if v.startswith('-') or v.endswith('-'):
            raise ValueError('name cannot start or end with hyphen')
        return v

    @validator('allowed_tools', pre=True)
    def parse_allowed_tools(cls, v):
        """解析 allowed-tools（空格分隔的字符串或列表）"""
        if isinstance(v, str):
            return v.split()
        return v

    class Config:
        extra = "allow"  # 允许额外字段用于扩展


class SkillExecutionResult(BaseModel):
    """Skill 执行结果"""
    success: bool
    data: Any = None
    message: str = ""
    error: Optional[str] = None
    executed_at: datetime = Field(default_factory=datetime.now)
