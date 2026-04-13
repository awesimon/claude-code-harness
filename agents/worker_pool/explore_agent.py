"""
Explore Agent - 代码库探索专家 Agent

A specialized agent for codebase exploration.
- Read-only: NO file modifications allowed
- Uses tools: GlobTool, GrepTool, ReadFileTool, BashTool (ls, find, grep only)
- System prompt emphasizes understanding existing patterns
- Fast and efficient at finding code and understanding structure

对齐 Claude Code 的 exploreAgent.ts
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set

from .agent_runner import AgentConfig, AgentResult, run_agent


# Explore Agent 系统提示词
EXPLORE_AGENT_SYSTEM_PROMPT = """You are a file search specialist for Claude Code, Anthropic's official CLI for Claude. You excel at thoroughly navigating and exploring codebases.

=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===
This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:
- Creating new files (no Write, touch, or file creation of any kind)
- Modifying existing files (no Edit operations)
- Deleting files (no rm or deletion)
- Moving or copying files (no mv or cp)
- Creating temporary files anywhere, including /tmp
- Using redirect operators (>, >>, |) or heredocs to write to files
- Running ANY commands that change system state

Your role is EXCLUSIVELY to search and analyze existing code. You do NOT have access to file editing tools - attempting to edit files will fail.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents
- Understanding code structure and patterns

Guidelines:
- Use Glob for broad file pattern matching (e.g., "**/*.py", "src/**/*.tsx")
- Use Grep for searching file contents with regex
- Use Read when you know the specific file path you need to read
- Use Bash ONLY for read-only operations:
  * Allowed: ls, git status, git log, git diff, find, grep, cat, head, tail, pwd, wc
  * NOT allowed: mkdir, touch, rm, cp, mv, git add, git commit, npm install, pip install
- NEVER use Bash for: any file creation/modification, installation, or system changes
- Adapt your search approach based on the thoroughness level specified by the caller
- Communicate your final report directly as a regular message - do NOT attempt to create files
- Be thorough: Check multiple locations, consider different naming conventions, look for related files

NOTE: You are meant to be a fast agent that returns output as quickly as possible. In order to achieve this you must:
- Make efficient use of the tools that you have at your disposal: be smart about how you search for files and implementations
- Wherever possible you should try to spawn multiple parallel tool calls for grepping and reading files
- Use glob patterns to find related files quickly
- Combine grep searches to locate specific patterns across the codebase

Complete the user's search request efficiently and report your findings clearly."""


@dataclass
class ExploreAgentOptions:
    """Explore Agent 选项"""
    model: Optional[str] = None
    temperature: float = 0.7
    max_turns: int = 50
    thoroughness: str = "medium"  # quick, medium, very thorough
    max_files_to_read: int = 20


def _build_thoroughness_instruction(thoroughness: str) -> str:
    """构建详细程度指令"""
    instructions = {
        "quick": """
Quick exploration mode:
- Focus on finding the most relevant files quickly
- Use 1-2 targeted searches
- Limit to 2-3 key files for understanding
- Prioritize speed over completeness
- Complete in 1-2 turns if possible
""",
        "medium": """
Medium exploration mode:
- Balance speed and thoroughness
- Use 2-4 targeted searches
- Explore main directories and key files
- Look at 4-6 relevant files
- Consider multiple naming conventions
""",
        "very thorough": """
Very thorough exploration mode:
- Comprehensive codebase analysis
- Use multiple search strategies in parallel
- Search multiple locations and naming conventions
- Look at 8+ files for complete understanding
- Trace through related implementations
- Examine test files and documentation
""",
    }
    return instructions.get(thoroughness, instructions["medium"])


def create_explore_agent_config(options: Optional[ExploreAgentOptions] = None) -> AgentConfig:
    """
    创建 Explore Agent 配置

    Args:
        options: Explore Agent 选项

    Returns:
        AgentConfig: Agent 配置
    """
    if options is None:
        options = ExploreAgentOptions()

    # 构建系统提示词
    system_prompt = EXPLORE_AGENT_SYSTEM_PROMPT
    system_prompt += f"\n\n## Thoroughness Level\n{_build_thoroughness_instruction(options.thoroughness)}\n"
    system_prompt += f"\n## Limits\n- Maximum files to read: {options.max_files_to_read}\n"

    return AgentConfig(
        name="explore-agent",
        agent_type="explore",
        system_prompt=system_prompt,
        tools=["glob", "grep", "read_file", "bash"],
        disallowed_tools=["write_file", "edit_file", "agent", "exit_plan_mode"],
        max_turns=options.max_turns,
        model=options.model,
        temperature=options.temperature,
        is_read_only=True,
    )


async def run_explore_agent(
    prompt: str,
    options: Optional[ExploreAgentOptions] = None,
    parent_session_id: Optional[str] = None,
) -> AgentResult:
    """
    运行 Explore Agent

    Args:
        prompt: 探索任务描述（如"Find all API endpoints", "How does authentication work?"）
        options: Explore Agent 选项
        parent_session_id: 父会话 ID

    Returns:
        AgentResult: 执行结果，包含探索发现

    Example:
        ```python
        result = await run_explore_agent(
            prompt="Find how user authentication is implemented in this codebase",
            options=ExploreAgentOptions(thoroughness="very thorough")
        )
        print(result.content)
        ```
    """
    config = create_explore_agent_config(options)
    return await run_agent(config, prompt, parent_session_id)


def format_explore_result(result: AgentResult) -> str:
    """
    格式化 Explore Agent 结果

    Args:
        result: Agent 执行结果

    Returns:
        格式化的探索报告
    """
    if result.status.value == "failed":
        return f"Exploration failed: {result.error}"

    output = f"""# Exploration Report

**Agent ID**: {result.agent_id}
**Duration**: {result.duration_ms}ms
**Tools Used**: {result.tool_use_count}

---

{result.content}

---

*Explored by Explore Agent*"""

    return output


class ExploreAgent:
    """
    Explore Agent 类

    提供代码库探索功能的高层接口
    """

    def __init__(self, options: Optional[ExploreAgentOptions] = None):
        self.options = options or ExploreAgentOptions()
        self.config = create_explore_agent_config(self.options)

    async def explore(self, query: str) -> AgentResult:
        """
        探索代码库

        Args:
            query: 探索查询

        Returns:
            AgentResult: 包含探索结果
        """
        return await run_explore_agent(query, self.options)

    async def find_files(self, pattern: str) -> AgentResult:
        """
        查找匹配模式的文件

        Args:
            pattern: 文件模式（如 "**/*.py", "src/components/**/*.tsx"）

        Returns:
            AgentResult: 包含找到的文件
        """
        prompt = f"""Find all files matching the pattern: {pattern}

For each file found, provide:
- File path
- Brief description of its purpose (if apparent from name/location)

Use GlobTool to find the files."""
        return await run_explore_agent(prompt, self.options)

    async def search_code(
        self,
        keyword: str,
        file_pattern: Optional[str] = None,
        context_lines: int = 3,
    ) -> AgentResult:
        """
        搜索代码

        Args:
            keyword: 搜索关键词或正则表达式
            file_pattern: 可选的文件过滤模式
            context_lines: 上下文行数

        Returns:
            AgentResult: 包含搜索结果
        """
        pattern_clause = f" in files matching '{file_pattern}'" if file_pattern else ""
        prompt = f"""Search for "{keyword}"{pattern_clause} in the codebase.

Find all occurrences and provide:
- File path and line number
- The matching code with {context_lines} lines of context
- Brief explanation of what each occurrence is doing

Use GrepTool for the search."""
        return await run_explore_agent(prompt, self.options)

    async def understand_feature(self, feature_name: str) -> AgentResult:
        """
        理解特定功能的实现

        Args:
            feature_name: 功能名称

        Returns:
            AgentResult: 包含功能实现分析
        """
        prompt = f"""Understand how the "{feature_name}" feature is implemented in this codebase.

1. Find all relevant files
2. Read the main implementation files
3. Trace the code flow
4. Identify key components and their interactions
5. Note any configuration or dependencies

Provide a comprehensive summary of how this feature works."""

        options = ExploreAgentOptions(
            thoroughness="very thorough",
            max_files_to_read=self.options.max_files_to_read,
        )
        return await run_explore_agent(prompt, options)

    async def analyze_architecture(self, component: Optional[str] = None) -> AgentResult:
        """
        分析架构

        Args:
            component: 可选的特定组件名称

        Returns:
            AgentResult: 包含架构分析
        """
        if component:
            prompt = f"""Analyze the architecture of the "{component}" component in this codebase.

1. Find the main files and modules
2. Understand the component structure
3. Identify interfaces and dependencies
4. Map the data flow
5. Note design patterns used

Provide an architectural overview with key files and their relationships."""
        else:
            prompt = """Analyze the overall architecture of this codebase.

1. Identify the main directories and their purposes
2. Understand the project structure and organization
3. Find key modules and their relationships
4. Identify design patterns and conventions
5. Map the high-level data flow

Provide an architectural overview of the project."""

        options = ExploreAgentOptions(
            thoroughness="very thorough",
            max_files_to_read=self.options.max_files_to_read,
        )
        return await run_explore_agent(prompt, options)
