"""
兼容层：原顶层包 `agent` 已迁入 `agents.worker_pool`。

新代码请使用：`from agents.worker_pool import WorkerPoolManager, Agent, ...`
"""

from agents.worker_pool import *  # noqa: F403
