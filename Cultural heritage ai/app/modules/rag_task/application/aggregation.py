"""任务答案聚合、评分和最终完成流程。

普通 AI 与工人的原始回答继续保存在 rag_task_answers 表中。本模块使用数据库中
首个已启用的 judge 类型 AI 生成最终答案，并将其给出的 0~1 评分写入
Answer.weight。
"""

import json
import math
from typing import Any
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.modules.rag_task.infrastructure import repository as task_repository
from app.modules.rag_task.infrastructure import execution_repository
from app.modules.rag_task.application.context import prompts as context_prompts
from app.modules.rag_task.application.context.builder import ContextBuilder
from app.modules.agent.mapper import ai_to_domain
from app.modules.rag_task.infrastructure.mapper import answer_to_domain
from app.db.session import SessionLocal
from app.modules.agent.models import AiAgentTable
from app.modules.rag_task.domain.models import AnswerTable, TaskTable
from app.modules.agent.models import AiType
from app.modules.rag_task.domain.models import Answer, Task, TaskStatus
from app.modules.agent.application.facade import agent_facade
import logging

logger = logging.getLogger(__name__)

# 兼容原有模型替身注入；底层 HTTP 实现由 Agent 模块持有。
request_local_ai_raw = agent_facade.generate_text


def build_rag_reference_section(rag_context: str | None) -> str:
    """构造聚合和评分阶段共用的 RAG 参考资料部分。"""
    return context_prompts.build_rag_reference_section(rag_context)


def _resolve_rag_context(db: Session, task_id: str, rag_context: str | None) -> str | None:
    """保留显式参数兼容性，手动聚合时复用已保存资料而不重新检索。"""
    if rag_context is not None:
        return rag_context
    context = execution_repository.get_task_context(db, task_id)
    if context is None:
        return None
    return ContextBuilder.get_rag_context(
        context, execution_repository.list_context_items(db, context.id),
    )


def build_aggregation_prompt(
    task: Task, answers: list[Answer], rag_context: str | None = None
) -> str:
    """构造最终答案聚合提示词，保留原导入入口。"""
    return context_prompts.build_aggregation_prompt(task, answers, rag_context)


async def aggregate_task_answers(db: Session, task_id: str, rag_context: str | None = None) -> Task:
    """调用聚合 AI 生成最终答案并写入 Task.final_result。"""
    task_row = _require_task(db, task_id)
    rag_context = _resolve_rag_context(db, task_id, rag_context)
    _require_answers(db, task_id)
    aggregator = _require_aggregator(db)
    task_row.status = TaskStatus.AGGREGATING.value
    task = task_repository.task_with_answers(db, task_row)
    prompt = build_aggregation_prompt(task, task.answers, rag_context=rag_context)
    aggregator_domain = ai_to_domain(aggregator)
    db.commit()
    try:
        final_result = await request_local_ai_raw(
            aggregator_domain,
            user_prompt=prompt,
            system_prompt=context_prompts.AGGREGATION_SYSTEM_PROMPT,
        )
    except Exception:
        db.rollback()
        _set_failed_status(db, task_id)
        raise
    task_row = _require_task(db, task_id)
    task_row.final_result = final_result
    db.commit()
    db.refresh(task_row)
    return task_repository.task_with_answers(db, task_row)


def build_scoring_prompt(
    task: Task, answers: list[Answer], final_result: str, rag_context: str | None = None
) -> str:
    """构造回答评分提示词，保留原导入入口。"""
    return context_prompts.build_scoring_prompt(task, answers, final_result, rag_context)


def parse_score_result(raw_text: str) -> list[dict[str, Any]]:
    """解析聚合 AI 的评分 JSON，并兼容少量模型格式偏差。"""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        object_start = cleaned.find("{")
        if object_start < 0:
            logger.error("Aggregator scoring response contains no JSON object: %r", raw_text)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "Aggregator scoring response is not valid JSON"
            )
        decoder = json.JSONDecoder()
        try:
            payload, end_index = decoder.raw_decode(cleaned[object_start:])
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse aggregator scoring response: %r", raw_text)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "Aggregator scoring response is not valid JSON"
            ) from exc
        remaining = cleaned[object_start + end_index :].strip()
        if remaining:
            logger.warning("Aggregator returned extra content after scoring JSON: %r", remaining)
    scores = payload.get("scores") if isinstance(payload, dict) else None
    if not isinstance(scores, list):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Aggregator scoring response does not contain a scores list",
        )
    valid_items = [item for item in scores if isinstance(item, dict)]
    if not valid_items:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Aggregator scoring response contains no valid score items"
        )
    return valid_items


async def score_task_answers(
    db: Session, task_id: str, rag_context: str | None = None
) -> list[Answer]:
    """调用聚合 AI 评分，并将分数写入对应 Answer.weight。"""
    task_row = _require_task(db, task_id)
    rag_context = _resolve_rag_context(db, task_id, rag_context)
    _require_answers(db, task_id)
    if not task_row.final_result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Task final_result is empty")
    aggregator = _require_aggregator(db)
    task = task_repository.task_with_answers(db, task_row)
    prompt = build_scoring_prompt(
        task, task.answers, task_row.final_result, rag_context=rag_context
    )
    aggregator_domain = ai_to_domain(aggregator)
    db.commit()
    try:
        raw_result = await request_local_ai_raw(
            aggregator_domain,
            user_prompt=prompt,
            system_prompt=context_prompts.SCORING_SYSTEM_PROMPT,
        )
        logger.info("Raw scoring response for task %s: %r", task_id, raw_result)
        score_items = parse_score_result(raw_result)
        score_map = _build_score_map(score_items)
    except Exception:
        db.rollback()
        _set_failed_status(db, task_id)
        raise
    answer_rows = _require_answers(db, task_id)
    expected_ids = {answer_row.id for answer_row in answer_rows}
    returned_ids = set(score_map.keys())
    missing_ids = expected_ids - returned_ids
    unknown_ids = returned_ids - expected_ids
    if missing_ids:
        logger.error(
            "Aggregator scoring response for task %s is incomplete. Missing answer IDs: %s",
            task_id,
            sorted(missing_ids),
        )
        _set_failed_status(db, task_id)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Aggregator scoring response is incomplete. Missing answer IDs: {sorted(missing_ids)}",
        )
    if unknown_ids:
        logger.error(
            "Aggregator scoring response for task %s contains unknown answer IDs: %s",
            task_id,
            sorted(unknown_ids),
        )
        _set_failed_status(db, task_id)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Aggregator returned unknown answer IDs: {sorted(unknown_ids)}",
        )
    for answer_row in answer_rows:
        answer_row.weight = score_map[answer_row.id]
    db.commit()
    for answer_row in answer_rows:
        db.refresh(answer_row)
    return [answer_to_domain(answer_row) for answer_row in answer_rows]


async def finish_task_process(
    db: Session, task_id: str, rag_context: str | None = None
) -> tuple[Task, list[Answer]]:
    """依次完成聚合、评分和任务完成状态更新。"""
    await aggregate_task_answers(db, task_id, rag_context=rag_context)
    answers = await score_task_answers(db, task_id, rag_context=rag_context)
    task_row = _require_task(db, task_id)
    task_row.status = TaskStatus.COMPLETED.value
    db.commit()
    db.refresh(task_row)
    return (task_repository.task_with_answers(db, task_row), answers)


async def finish_task(task_id: str, rag_context: str | None = None) -> tuple[Task, list[Answer]]:
    """使用独立会话执行完整聚合流程，供后台编排和 API 共同复用。"""
    with SessionLocal() as db:
        try:
            return await finish_task_process(db, task_id, rag_context=rag_context)
        except Exception:
            db.rollback()
            raise


def _require_task(db: Session, task_id: str) -> TaskTable:
    """查询任务，不存在时返回 404。"""
    task = task_repository.get_task_row(db, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task does not exist")
    if task.mode == "crowd":
        raise HTTPException(409, "Crowd knowledge uses the processing queue")
    return task


def _require_answers(db: Session, task_id: str) -> list[AnswerTable]:
    """查询任务原始回答，没有回答时返回 400。"""
    answers = task_repository.list_answers(db, task_id)
    if not answers:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Task has no answers")
    return answers


def _require_aggregator(db: Session) -> AiAgentTable:
    """查询首个已启用的 judge AI 用于聚合与评分，没有配置时返回 400。"""
    aggregator = db.scalar(
        select(AiAgentTable)
        .where(AiAgentTable.enabled.is_(True), AiAgentTable.type == AiType.JUDGE.value)
        .order_by(AiAgentTable.id)
        .limit(1)
    )
    if not aggregator:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No enabled judge AI is configured")
    return aggregator


def _set_failed_status(db: Session, task_id: str) -> None:
    """提交失败状态，避免模型调用异常后任务停留在处理中。"""
    task = _require_task(db, task_id)
    if task.status != TaskStatus.COMPLETED.value:
        task.status = TaskStatus.FAILED.value
        db.commit()


def _build_score_map(score_items: list[dict[str, Any]]) -> dict[str, float]:
    """提取并限制评分范围，忽略缺少有效 answer_id 或 score 的条目。"""
    score_map: dict[str, float] = {}
    for item in score_items:
        answer_id = item.get("answer_id")
        raw_score = item.get("score")
        if not isinstance(answer_id, str) or not answer_id:
            continue
        if raw_score is None:
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score):
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "Aggregator returned a non-finite score"
            )
        if answer_id in score_map:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Aggregator returned duplicate score for answer ID: {answer_id}",
            )
        score_map[answer_id] = round(min(max(score, 0.0), 1.0), 4)
    return score_map
