"""
LLM服务模块
支持OpenAI和Anthropic API调用
"""

import logging
import os
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, AsyncIterator, Union

from anthropic import (
    APIStatusError as AnthropicAPIStatusError,
    AsyncAnthropic,
    NOT_GIVEN,
)
from openai import APIStatusError, AsyncOpenAI

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
        """转换为 OpenAI Chat Completions 格式（兼容多数国产网关的校验规则）"""
        msg: Dict[str, Any] = {"role": self.role}

        # 带 tool_calls 且无正文时须用 null，部分网关对 content:"" 会报 400（表现为偶发）
        if self.role == "assistant" and self.tool_calls:
            text = (self.content or "").strip()
            msg["content"] = text if text else None
        elif self.role == "tool":
            msg["content"] = self.content if self.content is not None else ""
        else:
            msg["content"] = self.content if self.content is not None else ""

        # Chat Completions 的 tool 消息标准字段不含 name，严格网关可能对多余字段报错
        if self.name and self.role != "tool":
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
        self._anthropic_client: Optional[AsyncAnthropic] = None
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

    def _get_anthropic_client(self) -> AsyncAnthropic:
        """获取 Anthropic 官方 Async SDK 客户端"""
        if self._anthropic_client is None:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")

            base = os.getenv("ANTHROPIC_BASE_URL") or None
            self._anthropic_client = AsyncAnthropic(
                api_key=api_key,
                base_url=base,
            )
        return self._anthropic_client

    @staticmethod
    def _openai_tools_to_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将 OpenAI 风格的 tools 转为 Anthropic Messages API 的 tool 定义。"""
        out: List[Dict[str, Any]] = []
        for t in tools:
            if t.get("type") == "function" and isinstance(t.get("function"), dict):
                fn = t["function"]
                params = fn.get("parameters")
                if not isinstance(params, dict):
                    params = {"type": "object", "properties": {}}
                out.append(
                    {
                        "name": fn.get("name", ""),
                        "description": fn.get("description") or "",
                        "input_schema": params,
                    }
                )
            elif "name" in t and "input_schema" in t:
                out.append(t)
        return out

    @staticmethod
    def _anthropic_tool_choice_param(
        tool_choice: Optional[Union[str, Dict[str, Any]]],
    ) -> Any:
        if tool_choice is None:
            return NOT_GIVEN
        if isinstance(tool_choice, dict):
            return tool_choice
        if tool_choice == "auto":
            return {"type": "auto"}
        if tool_choice == "none":
            return {"type": "none"}
        if tool_choice == "required":
            return {"type": "any"}
        return NOT_GIVEN

    @staticmethod
    def _anthropic_stop_to_finish_reason(stop_reason: Optional[str]) -> Optional[str]:
        if not stop_reason:
            return None
        mapping = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
        }
        return mapping.get(stop_reason, stop_reason)

    def _build_anthropic_create_kwargs(self, request: ChatCompletionRequest) -> Dict[str, Any]:
        model = request.model or os.getenv(
            "ANTHROPIC_DEFAULT_MODEL", "claude-3-5-sonnet-20241022"
        )
        raw_messages = [m.to_anthropic() for m in request.messages]
        system_message: Optional[str] = None
        chat_messages: List[Dict[str, Any]] = []
        for msg in raw_messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                chat_messages.append(msg)

        max_tokens = request.max_tokens or self._default_max_tokens
        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
            "temperature": self._get_temperature(request.temperature),
        }
        if system_message:
            kwargs["system"] = system_message

        if (
            request.tools
            and len(request.tools) > 0
            and not os.getenv("LLM_DISABLE_TOOLS", "").lower() in ("1", "true", "yes")
        ):
            kwargs["tools"] = self._openai_tools_to_anthropic(request.tools)
            tc = self._anthropic_tool_choice_param(request.tool_choice)
            if tc is not NOT_GIVEN:
                kwargs["tool_choice"] = tc

        return kwargs

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

    def _build_openai_chat_kwargs(self, request: ChatCompletionRequest) -> Dict[str, Any]:
        """构建 OpenAI Chat Completions 请求参数（流式与非流式共用）"""
        model = request.model or self._default_model
        messages = [m.to_openai() for m in request.messages]
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self._get_temperature(request.temperature),
        }
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        else:
            kwargs["max_tokens"] = self._default_max_tokens
        if (
            request.tools
            and len(request.tools) > 0
            and not os.getenv("LLM_DISABLE_TOOLS", "").lower() in ("1", "true", "yes")
        ):
            kwargs["tools"] = request.tools
            if request.tool_choice:
                kwargs["tool_choice"] = request.tool_choice
        return kwargs

    async def _openai_chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """OpenAI聊天完成"""
        client = self._get_openai_client()
        kwargs = self._build_openai_chat_kwargs(request)

        response = await client.chat.completions.create(**kwargs, timeout=60.0)

        choice = response.choices[0]
        message = choice.message
        content = message.content
        reasoning_content = choice.model_extra.get("reasoning_content")
        return ChatCompletionResponse(
            id=response.id,
            model=response.model,
            content=content,
            role=message.role,
            tool_calls=[tc.model_dump() for tc in message.tool_calls] if message.tool_calls else None,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            } if response.usage else None,
            finish_reason=choice.finish_reason,
            raw_response=response.model_dump(),
            reasoning_content=reasoning_content,
        )

    async def _openai_chat_completion_stream(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[ChatCompletionResponse]:
        """OpenAI 流式聊天完成（AsyncOpenAI SDK）"""
        client = self._get_openai_client()
        kwargs = self._build_openai_chat_kwargs(request)
        model = request.model or self._default_model
        n_messages = len(kwargs.get("messages") or [])
        n_tools = len(kwargs.get("tools") or [])

        try:
            stream = await client.chat.completions.create(
                **kwargs,
                stream=True,
                timeout=60.0,
            )
        except APIStatusError as e:
            body = ""
            if e.response is not None:
                try:
                    body = e.response.text
                except Exception:
                    body = str(e.body) if getattr(e, "body", None) else ""
            logger.error(
                "OpenAI stream create failed status=%s model=%s messages=%s tools=%s body=%s",
                getattr(e, "status_code", None),
                model,
                n_messages,
                n_tools,
                (body or str(e))[:12000],
            )
            raise
        except Exception as e:
            logger.error("OpenAI stream create failed: %s kwargs_keys=%s", e, list(kwargs.keys()))
            raise

        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            delta_dict = delta.model_dump(exclude_none=False)

            tool_calls = None
            if delta.tool_calls:
                tool_calls = [tc.model_dump(exclude_none=True) for tc in delta.tool_calls]

            reasoning_content = delta_dict.get("reasoning_content")

            raw: Optional[Dict[str, Any]] = None
            try:
                raw = chunk.model_dump()
            except Exception:
                raw = None

            yield ChatCompletionResponse(
                id=chunk.id or "",
                model=chunk.model or model,
                content=delta.content or "",
                role=delta.role or "assistant",
                tool_calls=tool_calls,
                finish_reason=choice.finish_reason,
                raw_response=raw,
                reasoning_content=reasoning_content,
            )

    async def _anthropic_chat_completion(
        self,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResponse:
        """Anthropic 聊天完成（官方 SDK）"""
        client = self._get_anthropic_client()
        kwargs = self._build_anthropic_create_kwargs(request)
        model = kwargs["model"]

        try:
            msg = await client.messages.create(**kwargs, timeout=60.0)
        except AnthropicAPIStatusError as e:
            body = ""
            if getattr(e, "response", None) is not None:
                try:
                    body = e.response.text
                except Exception:
                    body = str(getattr(e, "body", None) or "")
            logger.error(
                "Anthropic messages.create failed status=%s body=%s",
                getattr(e, "status_code", None),
                (body or str(e))[:12000],
            )
            raise
        except Exception as e:
            logger.error("Anthropic API call failed: %s", e)
            raise

        content = ""
        tool_calls: Optional[List[Dict[str, Any]]] = None

        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                content += getattr(block, "text", "") or ""
            elif btype == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                inp = getattr(block, "input", None)
                if inp is None and hasattr(block, "model_dump"):
                    inp = block.model_dump().get("input", {})
                if not isinstance(inp, dict):
                    inp = {}
                tool_calls.append(
                    {
                        "id": getattr(block, "id", "") or "",
                        "type": "function",
                        "function": {
                            "name": getattr(block, "name", "") or "",
                            "arguments": json.dumps(inp, ensure_ascii=False, default=str),
                        },
                    }
                )

        usage = None
        if msg.usage is not None:
            usage = {
                "input_tokens": getattr(msg.usage, "input_tokens", 0),
                "output_tokens": getattr(msg.usage, "output_tokens", 0),
            }

        raw: Optional[Dict[str, Any]] = None
        try:
            raw = msg.model_dump()
        except Exception:
            raw = None

        return ChatCompletionResponse(
            id=msg.id,
            model=msg.model,
            content=content,
            role="assistant",
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=getattr(msg, "stop_reason", None),
            raw_response=raw,
        )

    async def _anthropic_chat_completion_stream(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[ChatCompletionResponse]:
        """Anthropic 流式（messages.create(stream=True)，事件映射为与 OpenAI 流一致的 ChatCompletionResponse）"""
        client = self._get_anthropic_client()
        kwargs = self._build_anthropic_create_kwargs(request)
        model = kwargs["model"]

        try:
            stream = await client.messages.create(**kwargs, stream=True, timeout=60.0)
        except AnthropicAPIStatusError as e:
            body = ""
            if getattr(e, "response", None) is not None:
                try:
                    body = e.response.text
                except Exception:
                    body = str(getattr(e, "body", None) or "")
            logger.error(
                "Anthropic stream create failed status=%s model=%s body=%s",
                getattr(e, "status_code", None),
                model,
                (body or str(e))[:12000],
            )
            raise
        except Exception as e:
            logger.error("Anthropic stream create failed: %s", e)
            raise

        msg_id = ""
        # 当前 tool_use 块（按 content block index）
        tool_meta: Dict[int, Dict[str, str]] = {}

        async for event in stream:
            et = getattr(event, "type", None)
            raw_evt: Optional[Dict[str, Any]] = None
            try:
                raw_evt = event.model_dump()
            except Exception:
                raw_evt = None

            if et == "message_start":
                m = getattr(event, "message", None)
                msg_id = getattr(m, "id", "") if m is not None else ""

            elif et == "content_block_start":
                idx = getattr(event, "index", 0)
                cb = getattr(event, "content_block", None)
                cb_type = getattr(cb, "type", None) if cb is not None else None
                if cb_type == "tool_use":
                    tool_meta[idx] = {
                        "id": getattr(cb, "id", "") or "",
                        "name": getattr(cb, "name", "") or "",
                    }
                    yield ChatCompletionResponse(
                        id=msg_id,
                        model=model,
                        content="",
                        role="assistant",
                        tool_calls=[
                            {
                                "index": idx,
                                "id": tool_meta[idx]["id"],
                                "function": {
                                    "name": tool_meta[idx]["name"],
                                    "arguments": "",
                                },
                            }
                        ],
                        raw_response=raw_evt,
                    )

            elif et == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta is None:
                    continue
                dt = getattr(delta, "type", None)
                idx = getattr(event, "index", 0)

                if dt == "text_delta":
                    text = getattr(delta, "text", "") or ""
                    if text:
                        yield ChatCompletionResponse(
                            id=msg_id,
                            model=model,
                            content=text,
                            role="assistant",
                            raw_response=raw_evt,
                        )
                elif dt == "input_json_delta":
                    partial = getattr(delta, "partial_json", "") or ""
                    meta = tool_meta.get(idx, {"id": "", "name": ""})
                    yield ChatCompletionResponse(
                        id=msg_id,
                        model=model,
                        content="",
                        role="assistant",
                        tool_calls=[
                            {
                                "index": idx,
                                "id": meta.get("id", ""),
                                "function": {
                                    "name": meta.get("name", ""),
                                    "arguments": partial,
                                },
                            }
                        ],
                        raw_response=raw_evt,
                    )
                elif dt == "thinking_delta":
                    think = getattr(delta, "thinking", "") or ""
                    if think:
                        yield ChatCompletionResponse(
                            id=msg_id,
                            model=model,
                            content="",
                            role="assistant",
                            reasoning_content=think,
                            raw_response=raw_evt,
                        )

            elif et == "message_delta":
                md = getattr(event, "delta", None)
                sr = getattr(md, "stop_reason", None) if md is not None else None
                fr = self._anthropic_stop_to_finish_reason(sr)
                if fr:
                    yield ChatCompletionResponse(
                        id=msg_id,
                        model=model,
                        content="",
                        role="assistant",
                        finish_reason=fr,
                        raw_response=raw_evt,
                    )

    async def close(self):
        """关闭客户端"""
        if self._anthropic_client is not None:
            await self._anthropic_client.close()
            self._anthropic_client = None


# 全局LLM服务实例
llm_service = LLMService()
