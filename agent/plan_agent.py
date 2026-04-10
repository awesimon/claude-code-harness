"""
Plan Agent - 规划专家 Agent

A specialized agent that only explores the codebase and creates implementation plans.
- Read-only: NO file modifications allowed
- Uses tools: GlobTool, GrepTool, ReadFileTool, BashTool (read-only commands only)
- System prompt emphasizes planning and architecture design
- Output: Implementation plan with step-by-step approach

对齐 Claude Code 的 planAgent.ts
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from .agent_runner import AgentConfig, AgentResult, run_agent


# Plan Agent 系统提示词
PLAN_AGENT_SYSTEM_PROMPT = """You are a software architect and planning specialist for Claude Code, Anthropic's official CLI for Claude. Your role is to explore the codebase and design comprehensive implementation plans.

=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===
This is a READ-ONLY planning task. You are STRICTLY PROHIBITED from:
- Creating new files (no Write, touch, or file creation of any kind)
- Modifying existing files (no Edit operations)
- Deleting files (no rm or deletion)
- Moving or copying files (no mv or cp)
- Creating temporary files anywhere, including /tmp
- Using redirect operators (>, >>, |) or heredocs to write to files
- Running ANY commands that change system state

Your role is EXCLUSIVELY to explore the codebase and design implementation plans. You do NOT have access to file editing tools - attempting to edit files will fail.

## Your Process

1. **Understand Requirements**: Focus on the requirements provided and apply your assigned perspective throughout the design process.

2. **Explore Thoroughly**:
   - Read any files provided to you in the initial prompt
   - Find existing patterns and conventions using Glob, Grep, and Read
   - Understand the current architecture
   - Identify similar features as reference
   - Trace through relevant code paths
   - Use Bash ONLY for read-only operations (ls, git status, git log, git diff, find, grep, cat, head, tail)
   - NEVER use Bash for: mkdir, touch, rm, cp, mv, git add, git commit, npm install, pip install, or any file creation/modification

3. **Design Solution**:
   - Create implementation approach based on your assigned perspective
   - Consider trade-offs and architectural decisions
   - Follow existing patterns where appropriate

4. **Detail the Plan**:
   - Provide step-by-step implementation strategy
   - Identify dependencies and sequencing
   - Anticipate potential challenges

## Required Output Format

Your response should include:

### Overview
Brief summary of the approach and key decisions.

### Implementation Steps
1. Step 1: [Description]
2. Step 2: [Description]
3. Step 3: [Description]
...

### Key Considerations
- Important trade-offs or design decisions
- Potential challenges and mitigation strategies
- Testing approach

### Critical Files for Implementation
List 3-5 files most critical for implementing this plan:
- path/to/file1.py
- path/to/file2.py
- path/to/file3.py

REMEMBER: You can ONLY explore and plan. You CANNOT and MUST NOT write, edit, or modify any files. You do NOT have access to file editing tools."""


@dataclass
class PlanAgentOptions:
    """Plan Agent 选项"""
    model: Optional[str] = None
    temperature: float = 0.7
    max_turns: int = 50
    thoroughness: str = "medium"  # quick, medium, very thorough
    perspective: Optional[str] = None  # 可选的视角/角度


def _build_thoroughness_instruction(thoroughness: str) -> str:
    """构建详细程度指令"""
    instructions = {
        "quick": """
Quick exploration mode:
- Focus on finding the most relevant files quickly
- Use targeted searches
- Limit to 2-3 key files for understanding patterns
- Prioritize speed over completeness
""",
        "medium": """
Medium exploration mode:
- Balance speed and thoroughness
- Explore main directories and key files
- Understand core patterns and conventions
- Look at 4-6 relevant files
""",
        "very thorough": """
Very thorough exploration mode:
- Comprehensive codebase analysis
- Search multiple locations and naming conventions
- Deep dive into related features and implementations
- Examine edge cases and error handling
- Look at 8+ files for complete understanding
""",
    }
    return instructions.get(thoroughness, instructions["medium"])


def create_plan_agent_config(options: Optional[PlanAgentOptions] = None) -> AgentConfig:
    """
    创建 Plan Agent 配置

    Args:
        options: Plan Agent 选项

    Returns:
        AgentConfig: Agent 配置
    """
    if options is None:
        options = PlanAgentOptions()

    # 构建系统提示词
    system_prompt = PLAN_AGENT_SYSTEM_PROMPT

    if options.perspective:
        system_prompt += f"\n\n## Your Perspective\n{options.perspective}\n"

    system_prompt += f"\n\n## Thoroughness Level\n{_build_thoroughness_instruction(options.thoroughness)}\n"

    return AgentConfig(
        name="plan-agent",
        agent_type="plan",
        system_prompt=system_prompt,
        tools=["glob", "grep", "read_file", "bash"],
        disallowed_tools=["write_file", "edit_file", "agent", "exit_plan_mode"],
        max_turns=options.max_turns,
        model=options.model,
        temperature=options.temperature,
        is_read_only=True,
    )


async def run_plan_agent(
    prompt: str,
    options: Optional[PlanAgentOptions] = None,
    parent_session_id: Optional[str] = None,
) -> AgentResult:
    """
    运行 Plan Agent

    Args:
        prompt: 任务描述和要求
        options: Plan Agent 选项
        parent_session_id: 父会话 ID

    Returns:
        AgentResult: 执行结果，包含实施计划

    Example:
        ```python
        result = await run_plan_agent(
            prompt="Implement a user authentication system with JWT tokens",
            options=PlanAgentOptions(
                thoroughness="very thorough",
                perspective="Focus on security best practices"
            )
        )
        print(result.content)
        ```
    """
    config = create_plan_agent_config(options)
    return await run_agent(config, prompt, parent_session_id)


def format_plan_result(result: AgentResult) -> str:
    """
    格式化 Plan Agent 结果

    Args:
        result: Agent 执行结果

    Returns:
        格式化的计划文本
    """
    if result.status.value == "failed":
        return f"Plan generation failed: {result.error}"

    output = f"""# Implementation Plan

**Agent ID**: {result.agent_id}
**Duration**: {result.duration_ms}ms
**Tools Used**: {result.tool_use_count}

---

{result.content}

---

*Generated by Plan Agent*"""

    return output


class PlanAgent:
    """
    Plan Agent 类

    提供规划功能的高层接口
    """

    def __init__(self, options: Optional[PlanAgentOptions] = None):
        self.options = options or PlanAgentOptions()
        self.config = create_plan_agent_config(self.options)

    async def plan(
        self,
        task: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        """
        创建实施计划

        Args:
            task: 任务描述
            context: 可选的上下文信息

        Returns:
            AgentResult: 包含实施计划的结果
        """
        # 构建提示词
        prompt = task
        if context:
            context_str = "\n".join([f"- {k}: {v}" for k, v in context.items()])
            prompt = f"Context:\n{context_str}\n\nTask: {task}"

        return await run_plan_agent(prompt, self.options)

    async def plan_with_perspective(
        self,
        task: str,
        perspective: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        """
        使用特定视角创建实施计划

        Args:
            task: 任务描述
            perspective: 规划视角（如"Focus on performance", "Consider security implications"）
            context: 可选的上下文信息

        Returns:
            AgentResult: 包含实施计划的结果
        """
        self.options.perspective = perspective
        self.config = create_plan_agent_config(self.options)
        return await self.plan(task, context)
