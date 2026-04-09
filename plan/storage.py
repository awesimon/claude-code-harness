"""
计划文件存储管理
处理计划文件的创建、读取、更新和删除
"""
import os
import re
import random
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
import aiofiles

from .types import PlanContext


# 用于生成计划文件slug的形容词和名词
ADJECTIVES = [
    "bright", "calm", "clever", "bold", "brave", "cool", "eager", "fair",
    "fine", "glad", "good", "happy", "kind", "nice", "proud", "wise",
    "quick", "quiet", "sharp", "smart", "swift", "warm", "zealous",
    "azure", "crimson", "golden", "silver", "vivid", "gentle", "fierce"
]

NOUNS = [
    "eagle", "falcon", "hawk", "lion", "tiger", "wolf", "bear", "deer",
    "fox", "owl", "raven", "swan", "dolphin", "whale", "shark", "wolf",
    "river", "mountain", "forest", "ocean", "meadow", "canyon", "valley",
    "star", "moon", "sun", "comet", "galaxy", "nebula", "cosmos",
    "crystal", "diamond", "emerald", "ruby", "sapphire", "pearl"
]


class PlanStorage:
    """计划文件存储管理器"""

    def __init__(self, plans_directory: Optional[str] = None):
        self.plans_directory = self._resolve_plans_directory(plans_directory)
        self._slug_cache: Dict[str, str] = {}  # session_id -> slug

    def _resolve_plans_directory(self, custom_dir: Optional[str]) -> Path:
        """解析计划文件存储目录"""
        if custom_dir:
            path = Path(custom_dir).resolve()
        else:
            # 默认存储在 ~/.claude_code/plans
            home = Path.home()
            path = home / ".claude_code" / "plans"

        # 确保目录存在
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _generate_slug(self) -> str:
        """生成随机的单词slug"""
        adj = random.choice(ADJECTIVES)
        noun = random.choice(NOUNS)
        return f"{adj}-{noun}"

    def _get_unique_slug(self, session_id: str) -> str:
        """获取唯一的slug，缓存每个session的slug"""
        if session_id in self._slug_cache:
            return self._slug_cache[session_id]

        # 尝试生成不冲突的slug
        for _ in range(10):
            slug = self._generate_slug()
            plan_path = self._get_plan_path(slug)
            if not plan_path.exists():
                self._slug_cache[session_id] = slug
                return slug

        # 如果都冲突，添加随机数字后缀
        slug = f"{self._generate_slug()}-{random.randint(1000, 9999)}"
        self._slug_cache[session_id] = slug
        return slug

    def _get_plan_path(self, slug: str, agent_id: Optional[str] = None) -> Path:
        """获取计划文件路径"""
        if agent_id:
            filename = f"{slug}-agent-{agent_id}.md"
        else:
            filename = f"{slug}.md"
        return self.plans_directory / filename

    def get_plan_file_path(
        self,
        session_id: str,
        agent_id: Optional[str] = None
    ) -> str:
        """获取计划文件的完整路径"""
        slug = self._get_unique_slug(session_id)
        return str(self._get_plan_path(slug, agent_id))

    async def save_plan(
        self,
        session_id: str,
        content: str,
        agent_id: Optional[str] = None
    ) -> str:
        """
        保存计划内容到文件

        Returns:
            保存的文件路径
        """
        file_path = self.get_plan_file_path(session_id, agent_id)

        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(content)

        return file_path

    async def load_plan(
        self,
        session_id: str,
        agent_id: Optional[str] = None
    ) -> Optional[str]:
        """加载计划内容"""
        slug = self._slug_cache.get(session_id)
        if not slug:
            return None

        file_path = self._get_plan_path(slug, agent_id)

        if not file_path.exists():
            return None

        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            return await f.read()

    async def update_plan(
        self,
        session_id: str,
        content: str,
        agent_id: Optional[str] = None
    ) -> str:
        """更新计划内容"""
        return await self.save_plan(session_id, content, agent_id)

    def plan_exists(self, session_id: str, agent_id: Optional[str] = None) -> bool:
        """检查计划文件是否存在"""
        slug = self._slug_cache.get(session_id)
        if not slug:
            return False

        file_path = self._get_plan_path(slug, agent_id)
        return file_path.exists()

    def get_plan_context(
        self,
        session_id: str,
        agent_id: Optional[str] = None
    ) -> Optional[PlanContext]:
        """获取计划上下文信息"""
        slug = self._slug_cache.get(session_id)
        if not slug:
            return None

        file_path = self._get_plan_path(slug, agent_id)
        if not file_path.exists():
            return None

        stat = file_path.stat()
        return PlanContext(
            plan_file_path=str(file_path),
            created_at=datetime.fromtimestamp(stat.st_ctime),
            updated_at=datetime.fromtimestamp(stat.st_mtime),
        )

    def clear_session(self, session_id: str):
        """清除会话的slug缓存"""
        if session_id in self._slug_cache:
            del self._slug_cache[session_id]

    def list_all_plans(self) -> list:
        """列出所有计划文件"""
        plans = []
        for file_path in self.plans_directory.glob("*.md"):
            stat = file_path.stat()
            plans.append({
                "filename": file_path.name,
                "path": str(file_path),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return plans


# 全局存储实例
_plan_storage: Optional[PlanStorage] = None


def get_plan_storage(plans_directory: Optional[str] = None) -> PlanStorage:
    """获取全局计划存储实例"""
    global _plan_storage
    if _plan_storage is None:
        _plan_storage = PlanStorage(plans_directory)
    return _plan_storage


def reset_plan_storage():
    """重置全局计划存储实例（用于测试）"""
    global _plan_storage
    _plan_storage = None
