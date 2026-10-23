"""任务共享上下文与 Agent 执行记录的应用层持久化适配。

使用短事务保存公开消息、工具协议、执行阶段与请求尝试次数，
每轮通过纯 ContextBuilder 选择模型可见内容；不保存模型隐藏推理字段。
本模块不替代 ToolExecutor，也不持有跨模型请求的数据库事务。
"""

import asyncio
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.modules.agent.execution_observer import AgentLoopEvent
from app.modules.agent.application.facade import AgentRequestBuilder, AgentExecutionScope, AgentRequestBuilderFactory  # noqa: F401 - business compatibility exports
from app.modules.hcp_mad.application.task.context.builder import ContextBuilder
from app.modules.hcp_mad.application.task.context.policies import ContextSelectionPolicy
from app.modules.hcp_mad.domain.models import (
    AgentExecutionTable,
    Answer,
    ContextItemType,
    ContextVisibility,
    ExecutionStage,
    ExecutionStatus,
    TaskContextItemTable,
    TaskContextTable,
)
from app.modules.hcp_mad.infrastructure import execution_repository as repository
from app.modules.hcp_mad.infrastructure import repository as task_repository
from app.modules.hcp_mad.infrastructure.mapper import answer_to_domain




class TaskExecutionContext:
    """在现有 Task 下组织共享 Context 与各 Execution 的独立协议轨迹。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def prepare(self, task_id: str, question: str, rag_context: str | None = None) -> str:
        """幂等建立共享问题与可选 RAG 资料；已有资料不因兼容参数为空被清除。"""
        with self._session_factory() as db:
            context_id = self._prepare_context(db, task_id, question, rag_context)
            db.commit()
            return context_id

    @staticmethod
    def _prepare_context(db: Session, task_id: str, question: str, rag_context: str | None = None) -> str:
        """在调用方短事务内准备共享输入，供正式执行和原子领取中间发言复用。"""
        context = repository.create_task_context(db, task_id=task_id)
        items = repository.list_context_items(db, context.id)
        for item_type, content in (
            (ContextItemType.USER_MESSAGE, question), (ContextItemType.RAG_CONTEXT, rag_context),
        ):
            if not content:
                continue
            previous = next(
                (item for item in reversed(items) if item.item_type == item_type.value
                 and item.execution_id is None and item.visibility.lower() == "shared"), None,
            )
            if previous is None or previous.content != content:
                repository.append_context_item(
                    db, context_id=context.id, item_type=item_type, content=content,
                    visibility=ContextVisibility.SHARED,
                )
        return context.id

    def create_execution(self, task_id: str, ai_id: str, context_id: str) -> str:
        with self._session_factory() as db:
            execution = repository.create_agent_execution(
                db, task_id=task_id, ai_id=ai_id, context_id=context_id,
            )
            execution_id = execution.id
            db.commit()
            return execution_id

    def start(self, execution_id: str) -> None:
        with self._session_factory() as db:
            execution = repository.get_agent_execution(db, execution_id, for_update=True)
            if execution is None or execution.status != ExecutionStatus.WAITING.value:
                raise RuntimeError("Only a waiting execution can be started")
            repository.update_agent_execution_status(db, execution_id, ExecutionStatus.RUNNING)
            repository.update_agent_execution_stage(db, execution_id, ExecutionStage.THINKING)
            db.commit()

    def invalidate_rag(self, task_id: str, *, retrieval_failed: bool = True) -> None:
        """本轮检索明确失败时，用空共享条目失效旧证据，维持无 RAG 降级语义。"""
        with self._session_factory() as db:
            context = repository.create_task_context(db, task_id=task_id)
            repository.append_context_item(
                db, context_id=context.id, item_type=ContextItemType.RAG_CONTEXT,
                content="", metadata={"retrieval_failed": retrieval_failed},
            )
            db.commit()

    def fail(self, execution_id: str, error_message: str) -> None:
        with self._session_factory() as db:
            execution = repository.get_agent_execution(db, execution_id, for_update=True)
            if execution is None or execution.status == ExecutionStatus.COMPLETED.value:
                return
            repository.update_agent_execution_status(
                db, execution_id, ExecutionStatus.FAILED, error_message=error_message,
            )
            db.commit()

    def load(self, context_id: str) -> tuple[TaskContextTable, list[TaskContextItemTable]]:
        """返回已加载且脱离会话的快照，不向 Builder 泄漏数据库会话。"""
        with self._session_factory() as db:
            context = repository.get_task_context_by_id(db, context_id)
            if context is None:
                raise RuntimeError("Task context does not exist")
            items = repository.list_context_items(db, context_id)
            db.expunge_all()
            return context, items

    def load_for_task(self, task_id: str) -> tuple[TaskContextTable, list[TaskContextItemTable]] | None:
        """只读加载任务上下文，供对外 API 生成不含会话的可见性快照。"""
        with self._session_factory() as db:
            context = repository.get_task_context(db, task_id)
            if context is None:
                return None
            items = repository.list_context_items(db, context.id)
            db.expunge_all()
            return context, items

    def load_execution_result(self, execution_id: str) -> tuple[AgentExecutionTable, Answer | None] | None:
        """读取执行状态和正式答案，不向外泄漏用于答案映射的 ORM 或 Session。"""
        with self._session_factory() as db:
            execution = repository.get_agent_execution(db, execution_id)
            if execution is None:
                return None
            answer_row = task_repository.find_answer_by_execution(db, execution_id)
            answer = answer_to_domain(answer_row) if answer_row is not None else None
            db.expunge_all()
            return execution, answer

    def rag_context_for_task(self, task_id: str) -> str | None:
        with self._session_factory() as db:
            context = repository.get_task_context(db, task_id)
            if context is None:
                return None
            items = repository.list_context_items(db, context.id)
            for item in reversed(items):
                if (item.item_type == ContextItemType.RAG_CONTEXT.value
                        and item.execution_id is None and item.visibility.lower() == "shared"):
                    return item.content
        return None

    def record(self, execution_id: str, event: AgentLoopEvent) -> None:
        """将白名单事件映射为阶段和协议数据；最终答案另与 Answer 原子提交。"""
        with self._session_factory() as db:
            execution = repository.get_agent_execution(db, execution_id, for_update=True)
            if execution is None or execution.status != ExecutionStatus.RUNNING.value:
                raise RuntimeError("Agent execution is not running")
            stage = {
                "llm_request": ExecutionStage.THINKING,
                "tool_calls": ExecutionStage.TOOL_CALLING,
                "tool_started": ExecutionStage.TOOL_EXECUTING,
                "tool_result": ExecutionStage.THINKING,
                "final_answer": ExecutionStage.FINALIZING,
            }.get(event.kind)
            if stage is not None:
                repository.update_agent_execution_stage(db, execution_id, stage)
            if event.kind == "llm_request":
                repository.increment_llm_request_count(db, execution_id)
            item_type = None
            content = None
            metadata: dict[str, Any] = {"step": event.step}
            if event.kind == "llm_response" and event.data.get("tool_calls"):
                content = event.data.get("content")
                if content:
                    item_type = ContextItemType.AGENT_MESSAGE
                    metadata.update(stage="thinking", include_in_prompt=False)
            elif event.kind in {"tool_calls", "tool_result"}:
                item_type = (ContextItemType.TOOL_CALL if event.kind == "tool_calls"
                             else ContextItemType.TOOL_RESULT)
                content = json.dumps(event.data["message"], ensure_ascii=False)
                metadata["stage"] = ("tool_calling" if event.kind == "tool_calls"
                                     else "tool_executing")
            if item_type is not None and content is not None:
                repository.append_context_item(
                    db, context_id=execution.context_id, execution_id=execution.id,
                    ai_id=execution.ai_id, item_type=item_type, content=content,
                    visibility=ContextVisibility.PRIVATE, metadata=metadata,
                )
            db.commit()


class PersistentAgentObserver:
    """把现有无数据库 Agent Loop 连接到执行持久化和上下文选择。"""

    def __init__(self, store: TaskExecutionContext, *, execution_id: str, context_id: str,
                 ai_id: str, system_prompt: str, answer_format: str,
                 context_policy: ContextSelectionPolicy | None = None,
                 context_stage: str = "thinking",
                 request_builder: AgentRequestBuilder | None = None) -> None:
        self._store = store
        self._execution_id = execution_id
        self._context_id = context_id
        self._ai_id = ai_id
        self._system_prompt = system_prompt
        self._answer_format = answer_format
        self._context_policy = context_policy
        self._context_stage = context_stage
        self._builder = ContextBuilder()
        self.request_builder = request_builder

    async def build_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.request_builder is not None:
            raise RuntimeError("Custom request context requires the resolved Provider inputs")
        context, items = await asyncio.to_thread(self._store.load, self._context_id)
        options: dict[str, Any] = {"policy": self._context_policy} if self._context_policy is not None else {}
        return self._builder.build_messages(
            context, items, execution_id=self._execution_id, ai_id=self._ai_id,
            stage=self._context_stage, system_prompt=self._system_prompt,
            answer_format=self._answer_format,
            **options,
        )

    async def on_event(self, event: AgentLoopEvent) -> None:
        await asyncio.to_thread(self._store.record, self._execution_id, event)
