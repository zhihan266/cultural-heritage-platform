"""Execute one answer-evaluation case under a controlled variant."""

from __future__ import annotations

import time
from typing import Any

from app.modules.agent.application.facade import agent_facade
from app.modules.agent.models import AiAgent
from app.modules.rag.application.context_builder import (
    build_rag_context,
)
from app.modules.rag.domain.retriever import Retrieval
from app.modules.tool.application.facade import ToolCatalog, ToolRunner
from app.modules.tool.domain.models import (
    ToolExecutionResult,
)

from evaluation.models.answer_evaluation import (
    AnswerEvaluationCase,
    AnswerRunResult,
    AnswerVariantConfig,
    ToolCallRecord,
)
from app.modules.agent.llm_usage import (
    LlmRequestUsage,
)

request_local_ai_raw_with_usage = agent_facade.generate_text_with_usage
run_tool_calling_agent_with_usage = agent_facade.run_agent_with_usage


SYSTEM_PROMPT = (
    "你是一个文化遗产知识问答助手。\n"
    "请准确回答用户的问题。\n"
    "不要编造无法确认的事实。\n"
    "如果用户消息中提供了参考资料，优先依据参考资料回答。\n"
    "如果工具可用且确实有必要，可以调用工具获取信息。\n"
    "如果现有信息不足，请明确说明。"
)


class RecordingToolExecutor:
    """
    Evaluation-only ToolExecutor wrapper.

    不改变正式 ToolExecutor 的执行逻辑，
    只额外记录本次 Agent 实际调用了哪些 Tool。
    """

    def __init__(
        self,
        delegate: ToolRunner,
    ) -> None:
        self._delegate = delegate

        self.records: list[
            ToolCallRecord
        ] = []

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:

        try:
            result = await self._delegate.execute(
                tool_name,
                arguments,
            )

        except Exception as exc:

            self.records.append(
                ToolCallRecord(
                    tool_name=tool_name,
                    arguments=dict(arguments),
                    success=False,
                    result=None,
                    error=str(exc),
                )
            )

            raise

        self.records.append(
            ToolCallRecord(
                tool_name=tool_name,
                arguments=dict(arguments),
                success=True,
                result=result.output,
                error=None,
            )
        )

        return result


class AnswerVariantExecutor:
    """
    统一执行四组 Answer Evaluation。

    四组必须使用同一个 ai：

        llm_only
        llm_rag
        llm_tool
        llm_rag_tool

    只允许改变：

        use_rag
        use_tool
    """

    def __init__(
        self,
        *,
        ai: AiAgent,
        retriever: Retrieval,
        tool_registry: ToolCatalog,
        tool_executor: ToolRunner,
        rag_top_k: int = 5,
        tool_max_steps: int = 5,
    ) -> None:

        if rag_top_k <= 0:
            raise ValueError(
                "rag_top_k must be greater than 0"
            )

        if tool_max_steps <= 0:
            raise ValueError(
                "tool_max_steps must be greater than 0"
            )

        self.ai = ai

        self.retriever = retriever

        self.tool_registry = tool_registry

        self.tool_executor = tool_executor

        self.rag_top_k = rag_top_k

        self.tool_max_steps = tool_max_steps

    async def run(
        self,
        *,
        case: AnswerEvaluationCase,
        variant: AnswerVariantConfig,
    ) -> AnswerRunResult:
        """
        对一条 case 执行一种 variant。
        """

        started_at = time.perf_counter()

        hits = []

        # =========================
        # RAG
        # =========================

        if variant.use_rag:

            hits = await self.retriever.retrieve(
                query=case.question,
                top_k=self.rag_top_k,
                document_id=None,
            )

        user_prompt = self._build_user_prompt(
            question=case.question,
            hits=hits,
        )

        # =========================
        # Tool / Normal LLM
        # =========================

        tool_calls: list[
            ToolCallRecord
        ] = []

        usage: LlmRequestUsage | None = None
        llm_request_count = 0

        llm_usage_trace: list[
            LlmRequestUsage
        ] = []

        if variant.use_tool:

            recording_executor = (
                RecordingToolExecutor(
                    self.tool_executor
                )
            )

            messages = [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ]

            agent_result = (
                await run_tool_calling_agent_with_usage(
                    self.ai,
                    messages=messages,
                    tool_registry=self.tool_registry,
                    tool_executor=recording_executor,
                    max_steps=self.tool_max_steps,
                )
            )

            answer = agent_result.answer

            usage = agent_result.usage

            llm_request_count = (
                agent_result.request_count
            )
            llm_usage_trace = (
                agent_result.usage_trace
            )

            tool_calls = (
                recording_executor.records
            )

        else:

            llm_result = (
                await request_local_ai_raw_with_usage(
                    self.ai,
                    user_prompt=user_prompt,
                    system_prompt=SYSTEM_PROMPT,
                )
            )

            answer = llm_result.content

            usage = llm_result.usage

            llm_request_count = 1

            if usage is not None:
                llm_usage_trace = [
                    LlmRequestUsage.from_usage(
                        request_index=1,
                        usage=usage,
                    )
                ]

        latency_ms = (
            time.perf_counter()
            - started_at
        ) * 1000

        # =========================
        # Evaluation Result
        # =========================

        retrieved_chunk_ids = [
            hit.chunk_id
            for hit in hits
        ]

        retrieved_contexts = [
            hit.content
            for hit in hits
        ]

        model_name = (
            self.ai.model_name
            if (
                self.ai.model_name
                and self.ai.model_name.strip()
            )
            else self.ai.name
        )

        return AnswerRunResult(
            case_id=case.case_id,
            variant=variant.name,
            question=case.question,
            answer=answer,
            model_name=model_name,

            rag_enabled=variant.use_rag,

            retrieved_chunk_ids=(
                retrieved_chunk_ids
            ),

            retrieved_contexts=(
                retrieved_contexts
            ),

            tool_enabled=variant.use_tool,

            tool_calls=tool_calls,

            latency_ms=latency_ms,

            input_tokens=(
                usage.prompt_tokens
                if usage is not None
                else None
            ),

            output_tokens=(
                usage.completion_tokens
                if usage is not None
                else None
            ),

            total_tokens=(
                usage.total_tokens
                if usage is not None
                else None
            ),

            llm_request_count=(
                llm_request_count
            ),

            llm_usage_trace=llm_usage_trace,

            prompt_cache_hit_tokens=(
                usage.prompt_cache_hit_tokens
                if usage is not None
                else None
            ),

            prompt_cache_miss_tokens=(
                usage.prompt_cache_miss_tokens
                if usage is not None
                else None
            ),
            # 下一步再根据模型价格计算。
            estimated_cost=None,
        )

    def _build_user_prompt(
        self,
        *,
        question: str,
        hits: list,
    ) -> str:
        """
        所有 Variant 保持统一问题格式。

        唯一区别：
        RAG 开启时附加检索 Context。
        """

        if not hits:

            return (
                "请回答下面的问题：\n\n"
                f"{question}"
            )

        context = build_rag_context(
            hits
        )

        return (
            "请回答下面的问题。\n\n"
            f"用户问题：\n{question}\n\n"
            "以下是系统检索到的参考资料。\n"
            "请优先依据参考资料回答；"
            "如果资料不足，请明确说明。\n\n"
            f"参考资料：\n{context}"
        )
