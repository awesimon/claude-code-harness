"""
进程内共享的 QueryEngine / LLMService 引用。
由 main 在创建实例后 bind，供路由与 chat_stream 等模块读取，避免循环 import main。
"""

from __future__ import annotations

from typing import Optional

from query_engine import QueryEngine
from services.llm_service import LLMService

query_engine: Optional[QueryEngine] = None
llm_service: Optional[LLMService] = None


def bind(qe: QueryEngine, llm: LLMService) -> None:
    global query_engine, llm_service
    query_engine = qe
    llm_service = llm
