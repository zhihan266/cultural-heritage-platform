"""独立回答结果与执行关联；旧 JudgeResult 仅预留，本阶段不驱动后续分支。"""

from __future__ import annotations

from pydantic import BaseModel, Field

class WorkerResult(BaseModel):
    """
    单个 Worker Agent 的回答结果。

    status/error 属于工程执行信息，
    不属于 Judge 的业务判断。
    """
    agent_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)

    success: bool= True

    answer: str | None = None

    error: str | None = None
    execution_id: str | None = None
    answer_id: str | None = None

class IndependentResult(BaseModel):
    """
    两个independent大模型返回的值的集合，暴露给judge模型

    """
    question: str
    worker_results: list[WorkerResult] = Field(
        min_length=2,
        max_length=2,
    )
    # 实际获得的 RAG 检索结果数量。
    rag_hit_count: int = 0
    # 检索失败时记录错误摘要，不泄露内部凭证。
    rag_error: str | None = None


class JudgeResult(BaseModel):
    """
    consistent=True:
        两个答案表达的核心事实基本一致，
        Judge 直接聚合答案。

    consistent=False:
        两个答案存在实质差异，
        输出 debate_context 给 Debate 阶段。
    """
    consistent: bool

    aggregated_answer: str | None = None

    debate_context: str | None = None
