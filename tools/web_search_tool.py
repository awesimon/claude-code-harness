"""
网络搜索工具模块
提供 Web 搜索功能，支持搜索查询和结果过滤
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import asyncio
import time
import os

import httpx

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


@dataclass
class WebSearchInput:
    """网络搜索工具的输入参数"""
    query: str
    allowed_domains: Optional[List[str]] = None
    blocked_domains: Optional[List[str]] = None


@dataclass
class SearchHit:
    """搜索结果条目"""
    title: str
    url: str


@dataclass
class SearchResult:
    """搜索结果"""
    tool_use_id: str
    content: List[SearchHit] = field(default_factory=list)


@dataclass
class WebSearchOutput:
    """网络搜索输出结果"""
    query: str
    results: List[Any]
    duration_seconds: float


@register_tool
class WebSearchTool(Tool[WebSearchInput, WebSearchOutput]):
    """
    网络搜索工具

    使用搜索引擎 API（如 SerpAPI、Google Custom Search 等）执行网络搜索。
    支持按域名过滤搜索结果。

    需要在环境变量中配置搜索 API 密钥：
    - SERPAPI_KEY: SerpAPI 密钥
    或
    - GOOGLE_API_KEY 和 GOOGLE_CSE_ID: Google Custom Search API
    """

    name = "web_search"
    description = "执行网络搜索，获取当前信息。支持按域名过滤结果。"
    version = "1.0"

    # 默认使用 SerpAPI
    DEFAULT_SEARCH_PROVIDER = "serpapi"

    async def validate(self, input_data: WebSearchInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.query or not input_data.query.strip():
            return ToolValidationError("query 不能为空")

        if len(input_data.query.strip()) < 2:
            return ToolValidationError("query 长度至少为 2 个字符")

        # 检查是否同时指定了 allowed_domains 和 blocked_domains
        if input_data.allowed_domains and input_data.blocked_domains:
            return ToolValidationError(
                "不能同时指定 allowed_domains 和 blocked_domains"
            )

        # 检查 API 密钥是否配置
        if not self._get_api_key():
            return ToolValidationError(
                "未配置搜索 API 密钥。请设置 SERPAPI_KEY 环境变量"
            )

        return None

    def _get_api_key(self) -> Optional[str]:
        """获取搜索 API 密钥"""
        return os.environ.get("SERPAPI_KEY") or os.environ.get("GOOGLE_API_KEY")

    async def execute(self, input_data: WebSearchInput) -> ToolResult:
        """执行网络搜索"""
        start_time = time.time()
        query = input_data.query.strip()

        try:
            # 根据配置的 provider 选择搜索方式
            provider = self.DEFAULT_SEARCH_PROVIDER

            if provider == "serpapi":
                search_results = await self._search_serpapi(
                    query,
                    input_data.allowed_domains,
                    input_data.blocked_domains
                )
            else:
                search_results = await self._search_google_cse(
                    query,
                    input_data.allowed_domains,
                    input_data.blocked_domains
                )

            duration = time.time() - start_time

            output = WebSearchOutput(
                query=query,
                results=search_results,
                duration_seconds=round(duration, 2)
            )

            return ToolResult.ok(
                data=output,
                message=f"搜索完成: 找到 {len(search_results)} 个结果，耗时 {duration:.2f} 秒",
                metadata={
                    "query": query,
                    "result_count": len(search_results),
                    "duration_seconds": duration,
                }
            )

        except httpx.HTTPStatusError as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"搜索 API 请求失败: {e.response.status_code}",
                    details={
                        "status_code": e.response.status_code,
                        "response": e.response.text[:500]
                    }
                )
            )
        except httpx.RequestError as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"搜索请求错误: {str(e)}",
                    details={"error_type": "request_error"}
                )
            )
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(
                    f"搜索执行失败: {str(e)}",
                    details={"exception_type": type(e).__name__}
                )
            )

    async def _search_serpapi(
        self,
        query: str,
        allowed_domains: Optional[List[str]],
        blocked_domains: Optional[List[str]]
    ) -> List[Dict[str, Any]]:
        """使用 SerpAPI 执行搜索"""
        api_key = os.environ.get("SERPAPI_KEY")
        if not api_key:
            raise ToolValidationError("未配置 SERPAPI_KEY 环境变量")

        # 构建搜索参数
        params = {
            "q": query,
            "api_key": api_key,
            "engine": "google",
            "num": 10,
        }

        # 添加域名过滤
        if allowed_domains:
            # site: 操作符限制特定域名
            site_filter = " OR ".join([f"site:{domain}" for domain in allowed_domains])
            params["q"] = f"({params['q']}) ({site_filter})"
        elif blocked_domains:
            # 排除特定域名
            for domain in blocked_domains:
                params["q"] += f" -site:{domain}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://serpapi.com/search",
                params=params
            )
            response.raise_for_status()
            data = response.json()

        # 解析搜索结果
        results = []

        # 处理 organic_results
        organic_results = data.get("organic_results", [])
        for result in organic_results[:10]:
            results.append({
                "title": result.get("title", ""),
                "url": result.get("link", ""),
                "snippet": result.get("snippet", ""),
            })

        # 处理知识图谱等
        knowledge_graph = data.get("knowledge_graph", {})
        if knowledge_graph:
            results.insert(0, {
                "title": knowledge_graph.get("title", ""),
                "url": knowledge_graph.get("source", {}).get("link", ""),
                "snippet": knowledge_graph.get("description", ""),
                "type": "knowledge_graph"
            })

        return results

    async def _search_google_cse(
        self,
        query: str,
        allowed_domains: Optional[List[str]],
        blocked_domains: Optional[List[str]]
    ) -> List[Dict[str, Any]]:
        """使用 Google Custom Search API 执行搜索"""
        api_key = os.environ.get("GOOGLE_API_KEY")
        cse_id = os.environ.get("GOOGLE_CSE_ID")

        if not api_key or not cse_id:
            raise ToolValidationError(
                "未配置 GOOGLE_API_KEY 或 GOOGLE_CSE_ID 环境变量"
            )

        # 构建搜索参数
        params = {
            "key": api_key,
            "cx": cse_id,
            "q": query,
            "num": 10,
        }

        # 添加域名过滤
        if allowed_domains:
            site_filter = " OR ".join([f"site:{domain}" for domain in allowed_domains])
            params["q"] = f"({params['q']}) ({site_filter})"
        elif blocked_domains:
            for domain in blocked_domains:
                params["q"] += f" -site:{domain}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params=params
            )
            response.raise_for_status()
            data = response.json()

        # 解析搜索结果
        results = []
        items = data.get("items", [])

        for item in items:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })

        return results

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询字符串",
                    "minLength": 2
                },
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "仅包含来自这些域名的搜索结果（可选）"
                },
                "blocked_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "排除来自这些域名的搜索结果（可选）"
                }
            },
            "required": ["query"]
        }
        schema["returns"] = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "执行的搜索查询"},
                "results": {
                    "type": "array",
                    "description": "搜索结果列表"
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "搜索耗时（秒）"
                }
            }
        }
        return schema
