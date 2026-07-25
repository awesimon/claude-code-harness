"""
Fork Subagent 机制
实现 Agent Fork 功能，对齐 Claude Code 的 forkSubagent.ts
"""
import asyncio
import uuid
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


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
    """Compatibility adapter over the durable AgentScheduler."""

    def __init__(self, scheduler=None):
        self.scheduler = scheduler

    def _require_scheduler(self):
        if self.scheduler is None:
            raise RuntimeError(
                "ForkSubagentManager requires an explicit AgentScheduler"
            )
        return self.scheduler

    def _status_or_none(self, fork_id: str):
        if self.scheduler is None:
            return None
        from harness.agents import AgentNotFound

        try:
            return self.scheduler.status(fork_id)
        except AgentNotFound:
            return None

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
        forked_messages = build_forked_messages(
            directive=directive,
            assistant_message=assistant_message,
            parent_messages=parent_messages,
        )
        from agents.types import AgentIsolationMode, AgentRequest

        record = await self._require_scheduler().spawn(
            AgentRequest(
                prompt=directive,
                description=directive,
                agent_type="general-purpose",
                background=True,
                initial_messages=[*parent_messages, *forked_messages],
                isolation=(
                    AgentIsolationMode.WORKTREE if isolate_worktree else None
                ),
                definition_metadata={
                    "fork": {
                        "parent_session_id": parent_session_id,
                    }
                },
            )
        )
        return record.agent_id

    def get_fork(self, fork_id: str) -> Optional[Dict[str, Any]]:
        record = self._status_or_none(fork_id)
        if record is None:
            return None
        metadata = record.definition_snapshot.get("metadata", {})
        fork = metadata.get("fork", {}) if isinstance(metadata, dict) else {}
        isolation = record.definition_snapshot.get("isolation")
        return {
            "fork_id": record.agent_id,
            "parent_session_id": fork.get("parent_session_id"),
            "messages": record.definition_snapshot.get(
                "initial_messages", fork.get("messages", [])
            ),
            "isolate_worktree": (
                isolation == "worktree"
                if isolation is not None
                else bool(fork.get("isolate_worktree"))
            ),
            "status": record.status.value,
        }

    def update_fork_status(self, fork_id: str, status: str):
        record = self._status_or_none(fork_id)
        if record is None or record.status.value == status:
            return None
        if status in {"cancelled", "stopped", "killed"}:
            return asyncio.create_task(self._require_scheduler().stop(fork_id))
        raise RuntimeError("Fork status is owned by AgentScheduler lifecycle transitions")

    def cleanup_fork(self, fork_id: str):
        """Retain durable history and cancel a live fork when requested."""
        record = self._status_or_none(fork_id)
        if record is None or record.status.value in {
            "completed",
            "failed",
            "cancelled",
            "timed_out",
            "interrupted",
            "orphaned",
        }:
            return None
        return asyncio.create_task(self._require_scheduler().stop(fork_id))


_fork_manager: ForkSubagentManager | None = None


def get_fork_manager(scheduler=None) -> ForkSubagentManager:
    """Return a durable adapter, retaining the legacy no-argument singleton."""
    if scheduler is not None:
        return ForkSubagentManager(scheduler)
    global _fork_manager
    if _fork_manager is None:
        _fork_manager = ForkSubagentManager()
    return _fork_manager
