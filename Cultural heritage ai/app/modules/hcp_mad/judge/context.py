"""限制 Judge 只引用本次两个 Worker 的正式共享答案。

复用 Task 的上下文选择策略，不复制消息组装或工具配对规则，
也不把 Worker 私有协议或隐藏推理拼接进 Judge 指令。
"""

from app.modules.hcp_mad.application.task.facade import ContextSelectionPolicy, ContextView
from app.modules.hcp_mad.independent.schemas import IndependentResult


def build_judge_policy(result: IndependentResult, context: ContextView | None) -> ContextSelectionPolicy:
    workers = result.worker_results
    if len({worker.agent_id for worker in workers}) != 2 or not all(
        worker.success and worker.answer and worker.answer.strip()
        and worker.execution_id and worker.answer_id for worker in workers
    ):
        raise ValueError("Judge requires two persisted successful worker answers")
    if context is None:
        raise ValueError("Shared task context is missing")
    final_items = {item.execution_id: item for item in context.items
                   if item.item_type == "FINAL_ANSWER" and item.visibility == "shared"}
    for worker in workers:
        item = final_items.get(worker.execution_id)
        if item is None or item.ai_id != worker.agent_id or item.metadata.get("answer_id") != worker.answer_id or item.content != worker.answer:
            raise ValueError("Worker result does not match its persisted shared answer")
    return ContextSelectionPolicy(
        shared_reference_types=frozenset({"FINAL_ANSWER"}),
        shared_reference_execution_ids=frozenset(
            worker.execution_id for worker in workers if worker.execution_id is not None
        ),
    )
