"""
Fork Subagent 机制
实现 Agent Fork 功能，对齐 Claude Code 的 forkSubagent.ts
"""
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from agents.types import AgentContext

logger = logging.getLogger(__name__)


# Fork 子Agent的标记
FORK_BOILERPLATE_TAG = "fork-directive"
FORK_DIRECTIVE_PREFIX = "FORK DIRECTIVE: "
FORK_PLACEHOLDER_RESULT = "Fork started — processing in background"


@dataclass
class ForkConfig:
    """Fork 配置"""
    directive: str
    inherit_context: bool = True
    isolate_worktree: bool = False


def build_forked_messages(
    directive: str,
    assistant_message: Dict[str, Any],
    parent_messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    构建 Fork 子Agent的消息列表

    对齐 Claude Code 的 buildForkedMessages 函数

    策略：
    1. 保留完整的父级助手消息（所有 tool_use 块）
    2. 为每个 tool_use 构建 placeholder tool_result
    3. 添加子Agent指令作为最后一条消息

    这样可以最大化 prompt 缓存命中率
    """
    # 克隆助手消息
    full_assistant_message = {
        **assistant_message,
        "uuid": str(uuid.uuid4()),
    }

    # 收集所有 tool_use 块
    content = assistant_message.get("content", [])
    if isinstance(content, list):
        tool_use_blocks = [
            block for block in content
            if block.get("type") == "tool_use"
        ]
    else:
        tool_use_blocks = []

    if not tool_use_blocks:
        # 没有 tool_use，直接返回指令消息
        return [build_child_message(directive)]

    # 构建 tool_result 块
    tool_result_blocks = []
    for block in tool_use_blocks:
        tool_result_blocks.append({
            "type": "tool_result",
            "tool_use_id": block.get("id", ""),
            "content": [
                {
                    "type": "text",
                    "text": FORK_PLACEHOLDER_RESULT,
                }
            ],
        })

    # 构建用户消息：所有 placeholder tool_results + 子Agent指令
    child_message = build_child_message(directive)

    return [
        full_assistant_message,
        {
            "role": "user",
            "content": [
                *tool_result_blocks,
                {
                    "type": "text",
                    "text": child_message["content"][0]["text"] if isinstance(child_message["content"], list) else child_message["content"],
                }
            ]
        }
    ]


def build_child_message(directive: str) -> Dict[str, Any]:
    """
    构建子Agent消息

    对齐 Claude Code 的 buildChildMessage 函数
    """
    boilerplate = f"""<{FORK_BOILERPLATE_TAG}>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT — that's for the parent. You ARE the fork. Do NOT spawn sub-agents; execute directly.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: Bash, Read, Write, Edit, etc.
5. If you modify files, commit your changes before reporting. Include the commit hash in your report.
6. Do NOT emit text between tool calls. Use tools silently, then report once at the end.
7. Stay strictly within your directive's scope. If you discover related systems outside your scope, mention them in one sentence at most — other workers cover those areas.
8. Keep your report under 500 words unless the directive specifies otherwise. Be factual and concise.
9. Your response MUST begin with "Scope:". No preamble, no thinking-out-loud.
10. REPORT structured facts, then stop

Output format (plain text labels, not markdown headers):
  Scope: <echo back your assigned scope in one sentence>
  Result: <the answer or key findings, limited to the scope above>
  Key files: <relevant file paths — include for research tasks>
  Files changed: <list with commit hash — include only if you modified files>
  Issues: <list — include only if there are issues to flag>
</{FORK_BOILERPLATE_TAG}>

{FORK_DIRECTIVE_PREFIX}{directive}"""

    return {
        "role": "user",
        "content": [{"type": "text", "text": boilerplate}],
    }


def build_worktree_notice(parent_cwd: str, worktree_cwd: str) -> str:
    """
    构建 worktree 隔离通知

    对齐 Claude Code 的 buildWorktreeNotice 函数
    """
    return f"""You've inherited the conversation context above from a parent agent working in {parent_cwd}. You are operating in an isolated git worktree at {worktree_cwd} — same repository, same relative file structure, separate working copy. Paths in the inherited context refer to the parent's working directory; translate them to your worktree root. Re-read files before editing if the parent may have modified them since they appear in the context. Your changes stay in this worktree and will not affect the parent's files."""


def is_in_fork_child(messages: List[Dict[str, Any]]) -> bool:
    """
    检查是否在 Fork 子Agent中

    用于防止递归 Fork
    """
    for msg in messages:
        if msg.get("role") != "user":
            continue

        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if f"<{FORK_BOILERPLATE_TAG}>" in text:
                        return True
        elif isinstance(content, str):
            if f"<{FORK_BOILERPLATE_TAG}>" in content:
                return True

    return False


class ForkSubagentManager:
    """
    Fork Subagent 管理器

    管理 Fork 子Agent的生命周期
    """

    def __init__(self):
        self._forks: Dict[str, Dict[str, Any]] = {}

    async def create_fork(
        self,
        parent_session_id: str,
        directive: str,
        assistant_message: Dict[str, Any],
        parent_messages: List[Dict[str, Any]],
        isolate_worktree: bool = False,
    ) -> str:
        """
        创建 Fork 子Agent

        Args:
            parent_session_id: 父会话ID
            directive: 子Agent指令
            assistant_message: 触发 Fork 的助手消息
            parent_messages: 父会话消息历史
            isolate_worktree: 是否使用 worktree 隔离

        Returns:
            Fork ID
        """
        import uuid

        fork_id = f"fork-{uuid.uuid4().hex[:8]}"

        # 构建 Fork 消息
        forked_messages = build_forked_messages(
            directive=directive,
            assistant_message=assistant_message,
            parent_messages=parent_messages,
        )

        # 存储 Fork 信息
        self._forks[fork_id] = {
            "fork_id": fork_id,
            "parent_session_id": parent_session_id,
            "directive": directive,
            "messages": forked_messages,
            "isolate_worktree": isolate_worktree,
            "status": "created",
        }

        logger.info(f"Created fork {fork_id} from parent {parent_session_id}")

        return fork_id

    def get_fork(self, fork_id: str) -> Optional[Dict[str, Any]]:
        """获取 Fork 信息"""
        return self._forks.get(fork_id)

    def update_fork_status(self, fork_id: str, status: str):
        """更新 Fork 状态"""
        if fork_id in self._forks:
            self._forks[fork_id]["status"] = status

    def cleanup_fork(self, fork_id: str):
        """清理 Fork"""
        if fork_id in self._forks:
            del self._forks[fork_id]


# 全局 Fork 管理器
_fork_manager: Optional[ForkSubagentManager] = None


def get_fork_manager() -> ForkSubagentManager:
    """获取全局 Fork 管理器"""
    global _fork_manager
    if _fork_manager is None:
        _fork_manager = ForkSubagentManager()
    return _fork_manager


import uuid
