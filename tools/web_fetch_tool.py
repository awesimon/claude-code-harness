"""
网页获取工具模块
提供获取网页内容并转换为 Markdown 的功能
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
import time
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


@dataclass
class WebFetchInput:
    """网页获取工具的输入参数"""
    url: str
    prompt: str  # 用于处理获取内容的提示


@dataclass
class WebFetchOutput:
    """网页获取工具的输出结果"""
    bytes: int
    code: int
    code_text: str
    result: str
    duration_ms: int
    url: str


class RedirectDetectedError(Exception):
    """检测到重定向错误"""
    def __init__(self, original_url: str, redirect_url: str, status_code: int):
        self.original_url = original_url
        self.redirect_url = redirect_url
        self.status_code = status_code
        super().__init__(f"Redirect detected: {original_url} -> {redirect_url}")


@register_tool
class WebFetchTool(Tool[WebFetchInput, WebFetchOutput]):
    """
    网页获取工具

    获取指定 URL 的网页内容，并将其转换为 Markdown 格式。
    支持处理重定向、超时、错误重试等场景。

    重要提示：此工具无法获取需要认证的私有 URL（如 Google Docs、Confluence、Jira、GitHub 等）。
    对于这些服务，请使用专门的 MCP 工具。

    技术特点：
    - 使用 httpx 进行异步 HTTP 请求
    - 使用 BeautifulSoup 解析 HTML
    - 支持 HTML 到 Markdown 的转换
    - 自动处理重定向
    - 支持超时和重试机制
    """

    name = "web_fetch"
    description = "获取网页内容并转换为 Markdown。支持超时、重试和错误处理。"
    version = "1.0"

    # 配置参数
    DEFAULT_TIMEOUT = 30.0  # 默认超时 30 秒
    MAX_RETRIES = 2  # 最大重试次数
    MAX_CONTENT_LENGTH = 100_000  # 最大内容长度（字符）
    MAX_REDIRECTS = 5  # 最大重定向次数

    # 预批准的域名列表（可直接获取内容）
    PREAPPROVED_HOSTS = [
        "docs.python.org",
        "developer.mozilla.org",
        "github.com",
        "stackoverflow.com",
        "wikipedia.org",
        "pypi.org",
        "readthedocs.io",
        "npmjs.com",
        "docs.rs",
    ]

    async def validate(self, input_data: WebFetchInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.url or not input_data.url.strip():
            return ToolValidationError("url 不能为空")

        # 验证 URL 格式
        try:
            parsed = urlparse(input_data.url)
            if not parsed.scheme or not parsed.netloc:
                return ToolValidationError(f"无效的 URL 格式: {input_data.url}")
            if parsed.scheme not in ("http", "https"):
                return ToolValidationError(f"不支持的协议: {parsed.scheme}")
        except Exception as e:
            return ToolValidationError(f"URL 解析错误: {str(e)}")

        if not input_data.prompt:
            return ToolValidationError("prompt 不能为空")

        return None

    async def execute(self, input_data: WebFetchInput) -> ToolResult:
        """执行网页获取"""
        start_time = time.time()
        url = input_data.url.strip()
        prompt = input_data.prompt.strip()

        # 检查 URL 是否需要认证警告
        warning_message = self._check_auth_warning(url)

        try:
            # 执行 HTTP 请求获取内容
            content, bytes_size, status_code, status_text, content_type = \
                await self._fetch_url(url)

            # 将 HTML 转换为 Markdown
            markdown_content = self._html_to_markdown(content, url)

            # 如果内容过长，进行截断
            if len(markdown_content) > self.MAX_CONTENT_LENGTH:
                markdown_content = markdown_content[:self.MAX_CONTENT_LENGTH] + \
                    "\n\n[内容已截断...]"

            # 如果提供了 prompt，可以基于 prompt 处理内容（简化版）
            processed_result = self._apply_prompt_to_content(
                prompt, markdown_content, url
            )

            duration_ms = int((time.time() - start_time) * 1000)

            output = WebFetchOutput(
                bytes=bytes_size,
                code=status_code,
                code_text=status_text,
                result=warning_message + processed_result if warning_message else processed_result,
                duration_ms=duration_ms,
                url=url
            )

            return ToolResult.ok(
                data=output,
                message=f"成功获取网页: {url} ({status_code} {status_text})",
                metadata={
                    "url": url,
                    "content_type": content_type,
                    "bytes": bytes_size,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                }
            )

        except RedirectDetectedError as e:
            # 处理重定向
            duration_ms = int((time.time() - start_time) * 1000)
            status_text = self._get_redirect_status_text(e.status_code)

            message = f"""检测到重定向到不同主机

原始 URL: {e.original_url}
重定向 URL: {e.redirect_url}
状态码: {e.status_code} {status_text}

要完成请求，请使用新的 URL 重新调用 WebFetch：
- url: "{e.redirect_url}"
- prompt: "{prompt}"""

            output = WebFetchOutput(
                bytes=len(message.encode('utf-8')),
                code=e.status_code,
                code_text=status_text,
                result=message,
                duration_ms=duration_ms,
                url=url
            )

            return ToolResult.ok(
                data=output,
                message=f"检测到重定向: {e.original_url} -> {e.redirect_url}",
                metadata={
                    "redirect": True,
                    "original_url": e.original_url,
                    "redirect_url": e.redirect_url,
                    "status_code": e.status_code,
                }
            )

        except httpx.TimeoutException:
            return ToolResult.error(
                ToolExecutionError(
                    f"请求超时（{self.DEFAULT_TIMEOUT}秒）",
                    details={"timeout": self.DEFAULT_TIMEOUT, "url": url}
                )
            )
        except httpx.HTTPStatusError as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"HTTP 错误: {e.response.status_code}",
                    details={
                        "status_code": e.response.status_code,
                        "url": url,
                        "response": e.response.text[:500]
                    }
                )
            )
        except httpx.RequestError as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"请求错误: {str(e)}",
                    details={"url": url, "error_type": "request_error"}
                )
            )
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"获取网页失败: {str(e)}",
                    details={
                        "url": url,
                        "exception_type": type(e).__name__,
                        "error": str(e)
                    }
                )
            )

    async def _fetch_url(self, url: str) -> tuple[str, int, int, str, str]:
        """
        获取 URL 内容

        Returns:
            tuple: (content, bytes_size, status_code, status_text, content_type)
        """
        original_host = urlparse(url).netloc.lower()

        async with httpx.AsyncClient(
            timeout=self.DEFAULT_TIMEOUT,
            follow_redirects=True,
            max_redirects=self.MAX_REDIRECTS
        ) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ClaudeCode/1.0; +https://claude.ai)"
                }
            )
            response.raise_for_status()

            # 检查是否重定向到不同主机
            if response.history:
                final_url = str(response.url)
                final_host = urlparse(final_url).netloc.lower()

                # 如果主机不同，抛出重定向错误
                if final_host != original_host and response.history:
                    redirect_response = response.history[0]
                    raise RedirectDetectedError(
                        url,
                        final_url,
                        redirect_response.status_code
                    )

            content = response.text
            bytes_size = len(response.content)
            status_code = response.status_code
            status_text = response.reason_phrase
            content_type = response.headers.get("content-type", "text/html")

            return content, bytes_size, status_code, status_text, content_type

    def _html_to_markdown(self, html: str, base_url: str) -> str:
        """
        将 HTML 转换为 Markdown

        使用 BeautifulSoup 解析 HTML 并转换为 Markdown 格式。
        """
        soup = BeautifulSoup(html, 'html.parser')

        # 移除脚本和样式标签
        for script in soup(["script", "style", "nav", "footer", "header", "aside"]):
            script.decompose()

        # 尝试提取主要内容
        main_content = None

        # 查找主要内容区域
        for selector in ['main', 'article', '[role="main"]', '.content', '#content',
                         '.article', '.post', '.documentation']:
            main_content = soup.select_one(selector)
            if main_content:
                break

        # 如果没有找到主要内容区域，使用 body
        if not main_content:
            main_content = soup.find('body') or soup

        # 提取标题
        title = ""
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)

        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)

        # 构建 Markdown
        lines = []

        if title:
            lines.append(f"# {title}")
            lines.append("")

        # 处理各个元素
        for element in main_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                                               'p', 'pre', 'code', 'ul', 'ol',
                                               'li', 'a', 'strong', 'em', 'blockquote']):
            if element.name == 'h1':
                if element.get_text(strip=True) != title:  # 避免重复标题
                    lines.append(f"# {element.get_text(strip=True)}")
                    lines.append("")
            elif element.name == 'h2':
                lines.append(f"## {element.get_text(strip=True)}")
                lines.append("")
            elif element.name == 'h3':
                lines.append(f"### {element.get_text(strip=True)}")
                lines.append("")
            elif element.name == 'h4':
                lines.append(f"#### {element.get_text(strip=True)}")
                lines.append("")
            elif element.name == 'h5':
                lines.append(f"##### {element.get_text(strip=True)}")
                lines.append("")
            elif element.name == 'h6':
                lines.append(f"###### {element.get_text(strip=True)}")
                lines.append("")
            elif element.name == 'p':
                text = self._extract_text_with_links(element)
                if text.strip():
                    lines.append(text)
                    lines.append("")
            elif element.name == 'pre':
                # 代码块
                code = element.get_text()
                lang = ""
                if element.find('code'):
                    classes = element.find('code').get('class', [])
                    for cls in classes:
                        if cls.startswith('language-') or cls.startswith('lang-'):
                            lang = cls.split('-')[1]
                            break
                lines.append(f"```{lang}")
                lines.append(code.strip())
                lines.append("```")
                lines.append("")
            elif element.name == 'code' and element.parent.name != 'pre':
                # 行内代码
                continue  # 已经在 _extract_text_with_links 中处理
            elif element.name == 'ul':
                for li in element.find_all('li', recursive=False):
                    text = self._extract_text_with_links(li)
                    lines.append(f"- {text}")
                lines.append("")
            elif element.name == 'ol':
                for i, li in enumerate(element.find_all('li', recursive=False), 1):
                    text = self._extract_text_with_links(li)
                    lines.append(f"{i}. {text}")
                lines.append("")
            elif element.name == 'blockquote':
                text = self._extract_text_with_links(element)
                for line in text.split('\n'):
                    lines.append(f"> {line}")
                lines.append("")

        return '\n'.join(lines)

    def _extract_text_with_links(self, element) -> str:
        """提取包含链接的文本"""
        result = []
        for content in element.contents:
            if isinstance(content, str):
                result.append(content)
            elif content.name == 'a':
                href = content.get('href', '')
                text = content.get_text(strip=True)
                if href and text:
                    result.append(f"[{text}]({href})")
                else:
                    result.append(text)
            elif content.name == 'code':
                result.append(f"`{content.get_text(strip=True)}`")
            elif content.name == 'strong' or content.name == 'b':
                result.append(f"**{content.get_text(strip=True)}**")
            elif content.name == 'em' or content.name == 'i':
                result.append(f"*{content.get_text(strip=True)}*")
            else:
                result.append(content.get_text())

        return ''.join(result).strip()

    def _apply_prompt_to_content(self, prompt: str, content: str, url: str) -> str:
        """
        根据提示处理内容

        这是一个简化版实现，实际应用中可以使用 LLM 来处理。
        目前只进行简单的格式化。
        """
        # 如果提示要求提取特定信息，可以进行简单的关键词提取
        result_parts = []

        # 添加原始 URL
        result_parts.append(f"**来源**: {url}")
        result_parts.append("")

        # 添加用户提示
        result_parts.append(f"**处理提示**: {prompt}")
        result_parts.append("")

        # 添加分隔线
        result_parts.append("---")
        result_parts.append("")

        # 添加内容
        result_parts.append(content)

        return '\n'.join(result_parts)

    def _check_auth_warning(self, url: str) -> str:
        """
        检查 URL 是否需要认证警告

        对于需要认证的私有 URL 返回警告信息。
        """
        parsed = urlparse(url)
        hostname = parsed.netloc.lower()

        # 检查常见的需要认证的服务
        auth_services = [
            ('docs.google.com', 'Google Docs'),
            ('drive.google.com', 'Google Drive'),
            ('confluence.', 'Confluence'),
            ('jira.', 'Jira'),
            ('github.com', 'GitHub（某些私有仓库）'),
            ('notion.so', 'Notion'),
            ('sharepoint', 'SharePoint'),
            ('dropbox.com', 'Dropbox'),
        ]

        for domain, service_name in auth_services:
            if domain in hostname:
                return f"""⚠️ **警告**: {url} 可能指向需要认证的 {service_name} 服务。
此工具无法访问需要认证的私有内容。如果需要访问，请使用专门的 MCP 工具。

"""

        return ""

    def _get_redirect_status_text(self, status_code: int) -> str:
        """获取重定向状态文本"""
        status_map = {
            301: "Moved Permanently",
            302: "Found",
            303: "See Other",
            307: "Temporary Redirect",
            308: "Permanent Redirect",
        }
        return status_map.get(status_code, "Redirect")

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "要获取内容的 URL"
                },
                "prompt": {
                    "type": "string",
                    "description": "用于处理获取内容的提示"
                }
            },
            "required": ["url", "prompt"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "bytes": {
                    "type": "integer",
                    "description": "获取内容的大小（字节）"
                },
                "code": {
                    "type": "integer",
                    "description": "HTTP 响应状态码"
                },
                "code_text": {
                    "type": "string",
                    "description": "HTTP 响应状态文本"
                },
                "result": {
                    "type": "string",
                    "description": "处理后的 Markdown 结果"
                },
                "duration_ms": {
                    "type": "integer",
                    "description": "请求耗时（毫秒）"
                },
                "url": {
                    "type": "string",
                    "description": "获取的 URL"
                }
            }
        }
        return schema
