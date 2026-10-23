"""旧无任务 Worker 调用的只读输入；正式业务使用已有 Task 共享 Context。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndependentContext:
    """
    Independent 阶段两个 Worker 共享的任务上下文。

    两个 Worker 收到相同的：
    - 用户问题
    - RAG 检索结果
    - Tool 定义
    - System Prompt

    但两个 Worker 的模型配置和内部推理过程相互独立。
    """

    question: str

    rag_context: list[str]

    system_prompt: str
