"""
LLM服务模块
支持OpenAI和Anthropic API调用
"""

import logging
import os
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, AsyncIterator, Union

import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    """LLM提供商"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass
class Message:
    """消息"""
    role: str  # system, user, assistant, tool
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None

    def to_openai(self) -> Dict[str, Any]:
        """转换为OpenAI格式"""
        msg = {"role": self.role, "content": self.content}
        if self.name:
            msg["name"] = self.name
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg

    def to_anthropic(self) -> Dict[str, Any]:
        """转换为Anthropic格式"""
        return {
            "role": self.role if self.role in ["user", "assistant"] else "user",
            "content": self.content,
        }


@dataclass
class ChatCompletionRequest:
    """聊天完成请求"""
    messages: List[Message]
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    stream: bool = False
    tools: Optional[List[Dict]] = None
    tool_choice: Optional[Union[str, Dict]] = None
    provider: LLMProvider = LLMProvider.OPENAI


@dataclass
class ChatCompletionResponse:
    """聊天完成响应"""
    id: str
    model: str
    content: str
    role: str = "assistant"
    tool_calls: Optional[List[Dict]] = None
    usage: Optional[Dict[str, int]] = None
    finish_reason: Optional[str] = None
    raw_response: Optional[Dict] = None
    reasoning_content: Optional[str] = None  # For models like kimi-k2.5 that return thinking content


class LLMService:
    """LLM服务"""

    def __init__(self):
        self._openai_client: Optional[AsyncOpenAI] = None
        self._anthropic_client: Optional[httpx.AsyncClient] = None
        self._default_model = os.getenv("DEFAULT_MODEL", "gpt-4o")
        self._default_max_tokens = int(os.getenv("DEFAULT_MAX_TOKENS", "4096"))
        self._default_temperature = float(os.getenv("DEFAULT_TEMPERATURE", "0.7"))

    def _get_openai_client(self) -> AsyncOpenAI:
        """获取OpenAI客户端"""
        if self._openai_client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")

            base_url = os.getenv("OPENAI_BASE_URL")
            self._openai_client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url if base_url else None,
            )
        return self._openai_client

    def _get_anthropic_client(self) -> httpx.AsyncClient:
        """获取Anthropic客户端"""
        if self._anthropic_client is None:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")

            self._anthropic_client = httpx.AsyncClient(
                base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                timeout=60.0,
            )
        return self._anthropic_client

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """
        聊天完成

        Args:
            request: 聊天完成请求

        Returns:
            聊天完成响应
        """
        if request.provider == LLMProvider.OPENAI:
            return await self._openai_chat_completion(request)
        elif request.provider == LLMProvider.ANTHROPIC:
            return await self._anthropic_chat_completion(request)
        else:
            raise ValueError(f"Unsupported provider: {request.provider}")

    async def chat_completion_stream(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[ChatCompletionResponse]:
        """
        流式聊天完成

        Args:
            request: 聊天完成请求

        Yields:
            聊天完成响应片段
        """
        if request.provider == LLMProvider.OPENAI:
            async for chunk in self._openai_chat_completion_stream(request):
                yield chunk
        elif request.provider == LLMProvider.ANTHROPIC:
            async for chunk in self._anthropic_chat_completion_stream(request):
                yield chunk
        else:
            raise ValueError(f"Unsupported provider: {request.provider}")

    def _get_temperature(self, request_temp: Optional[float]) -> float:
        """获取 temperature，优先使用请求中的值，否则使用默认值"""
        return request_temp if request_temp is not None else 1.0

    async def _openai_chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """OpenAI聊天完成"""
        client = self._get_openai_client()

        model = request.model or self._default_model
        messages = [m.to_openai() for m in request.messages]

        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": self._get_temperature(request.temperature),
        }

        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        else:
            kwargs["max_tokens"] = self._default_max_tokens

        # Only add tools if explicitly provided and not empty
        # Some providers (like infini-ai) don't support tools
        if request.tools and len(request.tools) > 0:
            kwargs["tools"] = request.tools
            if request.tool_choice:
                kwargs["tool_choice"] = request.tool_choice

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")
            logger.error(f"Request kwargs: {kwargs}")
            raise

        choice = response.choices[0]
        message = choice.message

        return ChatCompletionResponse(
            id=response.id,
            model=response.model,
            content=message.content or "",
            role=message.role,
            tool_calls=[tc.model_dump() for tc in message.tool_calls] if message.tool_calls else None,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            } if response.usage else None,
            finish_reason=choice.finish_reason,
            raw_response=response.model_dump(),
        )

    async def _openai_chat_completion_stream(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[ChatCompletionResponse]:
        """OpenAI流式聊天完成 - 直接使用httpx处理SSE"""
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")

        model = request.model or self._default_model
        messages = [m.to_openai() for m in request.messages]

        # 构建请求体
        payload = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature if request.temperature is not None else 0.7,
            "stream": True,
            "max_tokens": request.max_tokens or self._default_max_tokens,
        }

        if request.tools:
            payload["tools"] = request.tools
        if request.tool_choice:
            payload["tool_choice"] = request.tool_choice

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=httpx.Timeout(60.0, connect=10.0),
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                            choices = data.get("choices", [])
                            if not choices:
                                continue

                            choice = choices[0]
                            delta = choice.get("delta", {})

                            # 提取工具调用
                            tool_calls = None
                            if delta.get("tool_calls"):
                                tool_calls = delta["tool_calls"]

                            # 提取推理内容 (kimi-k2.5等模型)
                            reasoning_content = delta.get("reasoning_content")

                            yield ChatCompletionResponse(
                                id=data.get("id", ""),
                                model=data.get("model", model),
                                content=delta.get("content", ""),
                                role=delta.get("role", "assistant"),
                                tool_calls=tool_calls,
                                finish_reason=choice.get("finish_reason"),
                                raw_response=data,
                                reasoning_content=reasoning_content,
                            )
                        except json.JSONDecodeError:
                            # 忽略无法解析的行
                            continue

    async def _anthropic_chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """Anthropic聊天完成"""
        client = self._get_anthropic_client()

        model = request.model or "claude-3-sonnet-20240229"
        messages = [m.to_anthropic() for m in request.messages]

        # 分离system消息
        system_message = None
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                chat_messages.append(msg)

        data = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": request.max_tokens or self._default_max_tokens,
            "temperature": self._get_temperature(request.temperature),
        }
        if system_message:
            data["system"] = system_message

        # 添加工具支持（Anthropic 使用 tools 参数）
        if request.tools:
            # Anthropic 工具格式与 OpenAI 类似
            data["tools"] = request.tools

        response = await client.post("/v1/messages", json=data)
        response.raise_for_status()
        result = response.json()

        content = ""
        tool_calls = None

        if result.get("content"):
            for block in result["content"]:
                if block["type"] == "text":
                    content += block["text"]
                elif block["type"] == "tool_use":
                    # Anthropic 工具调用格式
                    if tool_calls is None:
                        tool_calls = []
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}))
                        }
                    })

        return ChatCompletionResponse(
            id=result["id"],
            model=result["model"],
            content=content,
            role="assistant",
            tool_calls=tool_calls,
            usage={
                "input_tokens": result["usage"]["input_tokens"],
                "output_tokens": result["usage"]["output_tokens"],
            } if "usage" in result else None,
            finish_reason=result.get("stop_reason"),
            raw_response=result,
        )

    async def _anthropic_chat_completion_stream(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[ChatCompletionResponse]:
        """Anthropic流式聊天完成"""
        client = self._get_anthropic_client()

        model = request.model or "claude-3-sonnet-20240229"
        messages = [m.to_anthropic() for m in request.messages]

        # 分离system消息
        system_message = None
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                chat_messages.append(msg)

        data = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": request.max_tokens or self._default_max_tokens,
            "temperature": self._get_temperature(request.temperature),
            "stream": True,
        }
        if system_message:
            data["system"] = system_message

        async with client.stream("POST", "/v1/messages", json=data) as response:
            response.raise_for_status()

            current_content = ""
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    json_str = line[6:]
                    if json_str == "[DONE]":
                        break

                    try:
                        event = json.loads(json_str)
                        event_type = event.get("type")

                        if event_type == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                current_content += text

                                yield ChatCompletionResponse(
                                    id=event.get("message", {}).get("id", ""),
                                    model=model,
                                    content=text,
                                    role="assistant",
                                    raw_response=event,
                                )
                    except json.JSONDecodeError:
                        continue

    async def close(self):
        """关闭客户端"""
        if self._anthropic_client:
            await self._anthropic_client.aclose()


# 全局LLM服务实例
llm_service = LLMService()
