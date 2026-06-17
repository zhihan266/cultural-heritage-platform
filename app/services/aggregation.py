"""任务答案聚合、评分和最终完成流程。

普通 AI 与工人的原始回答继续保存在 answers 表中。本模块使用数据库中
首个已启用的聚合 AI 生成最终答案，并将聚合 AI 给出的 0~1 评分写入
Answer.weight。
"""

import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import tasks as task_repository
from app.db.mapper import ai_to_domain, answer_to_domain
from app.db.tables import AiAgentTable, AnswerTable, TaskTable
from app.models.domain import AiType, Answer, Task, TaskStatus
from app.services.local_ai import request_local_ai_raw


def build_aggregation_prompt(task: Task, answers: list[Answer]) -> str:
    """构造最终答案聚合提示词，不将提示词持久化到数据库。"""
    answer_blocks = "\n\n".join(
        (
            f"回答 ID：{answer.id}\n"
            f"回答来源：{answer.source.value}\n"
            f"回答内容：{answer.content}"
        )
        for answer in answers
    )
    return (
        f"用户问题：{task.question}\n\n"
        f"最终答案格式要求：{task.answer_format}\n\n"
        f"以下是工人和普通 AI 提交的原始回答：\n\n{answer_blocks}\n\n"
        "请综合这些回答，去除冲突、重复和不可靠内容，生成准确、完整、"
        "可直接交付给用户的最终答案。严格遵守格式要求，只输出最终答案。"
    )


async def aggregate_task_answers(db: Session, task_id: str) -> Task:
    """调用聚合 AI 生成最终答案并写入 Task.final_result。"""
    task_row = _require_task(db, task_id)
    answer_rows = _require_answers(db, task_id)
    aggregator = _require_aggregator(db)

    task_row.status = TaskStatus.AGGREGATING.value
    task = task_repository.task_with_answers(db, task_row)
    prompt = build_aggregation_prompt(task, task.answers)
    final_result = await request_local_ai_raw(
        ai_to_domain(aggregator),
        user_prompt=prompt,
        system_prompt=(
            "你是文化遗产问答系统的聚合专家。你的职责是综合多份候选回答，"
            "生成唯一、准确、完整且符合用户格式要求的最终答案。"
        ),
    )

    task_row.final_result = final_result
    db.commit()
    db.refresh(task_row)
    return task_repository.task_with_answers(db, task_row)


def build_scoring_prompt(
    task: Task,
    answers: list[Answer],
    final_result: str,
) -> str:
    """构造回答评分提示词，要求聚合 AI 只返回 JSON。"""
    answer_blocks = "\n\n".join(
        (
            f"回答 ID：{answer.id}\n"
            f"回答来源：{answer.source.value}\n"
            f"回答内容：{answer.content}"
        )
        for answer in answers
    )
    return (
        f"原始问题：{task.question}\n\n"
        f"最终答案：{final_result}\n\n"
        f"待评分回答：\n\n{answer_blocks}\n\n"
        "请根据正确性、完整性、相关性以及与最终答案的一致程度，为每条回答"
        "给出 0 到 1 之间的分数。只输出以下 JSON 结构，不要输出解释、"
        "Markdown 或其他文字：\n"
        '{"scores":[{"answer_id":"回答ID","score":0.85,'
        '"reason":"简短原因"}]}'
    )


def parse_score_result(raw_text: str) -> list[dict[str, Any]]:
    """解析聚合 AI 的评分 JSON，并兼容 ```json ... ``` 包裹。"""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    # 模型偶尔会在 JSON 前后附加少量文本，截取最外层 JSON 对象兼容处理。
    object_start = cleaned.find("{")
    object_end = cleaned.rfind("}")
    if object_start >= 0 and object_end > object_start:
        cleaned = cleaned[object_start : object_end + 1]

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Aggregator scoring response is not valid JSON",
        ) from exc

    scores = payload.get("scores") if isinstance(payload, dict) else None
    if not isinstance(scores, list):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Aggregator scoring response does not contain a scores list",
        )
    return [item for item in scores if isinstance(item, dict)]


async def score_task_answers(db: Session, task_id: str) -> list[Answer]:
    """调用聚合 AI 评分，并将分数写入对应 Answer.weight。"""
    task_row = _require_task(db, task_id)
    answer_rows = _require_answers(db, task_id)
    if not task_row.final_result:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Task final_result is empty",
        )
    aggregator = _require_aggregator(db)

    task = task_repository.task_with_answers(db, task_row)
    prompt = build_scoring_prompt(task, task.answers, task_row.final_result)
    raw_result = await request_local_ai_raw(
        ai_to_domain(aggregator),
        user_prompt=prompt,
        system_prompt=(
            "你是严格的回答质量评审器。你必须为每条候选回答评分，并且只输出"
            "符合要求的 JSON，不得输出 Markdown 或额外说明。"
        ),
    )
    score_items = parse_score_result(raw_result)
    score_map = _build_score_map(score_items)

    # 未被模型返回的回答记为 0，保证任务下每条 answer.weight 都有确定值。
    for answer_row in answer_rows:
        answer_row.weight = score_map.get(answer_row.id, 0.0)

    db.commit()
    for answer_row in answer_rows:
        db.refresh(answer_row)
    return [answer_to_domain(answer_row) for answer_row in answer_rows]


async def finish_task_process(
    db: Session,
    task_id: str,
) -> tuple[Task, list[Answer]]:
    """依次完成聚合、评分和任务完成状态更新。"""
    await aggregate_task_answers(db, task_id)
    answers = await score_task_answers(db, task_id)

    task_row = _require_task(db, task_id)
    task_row.status = TaskStatus.COMPLETED.value
    db.commit()
    db.refresh(task_row)
    return task_repository.task_with_answers(db, task_row), answers


def _require_task(db: Session, task_id: str) -> TaskTable:
    """查询任务，不存在时返回 404。"""
    task = task_repository.get_task_row(db, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task does not exist")
    return task


def _require_answers(db: Session, task_id: str) -> list[AnswerTable]:
    """查询任务原始回答，没有回答时返回 400。"""
    answers = task_repository.list_answers(db, task_id)
    if not answers:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Task has no answers",
        )
    return answers


def _require_aggregator(db: Session) -> AiAgentTable:
    """查询首个已启用的聚合 AI，没有配置时返回 400。"""
    aggregator = db.scalar(
        select(AiAgentTable)
        .where(
            AiAgentTable.enabled.is_(True),
            AiAgentTable.type == AiType.AGGREGATOR.value,
        )
        .order_by(AiAgentTable.id)
        .limit(1)
    )
    if not aggregator:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No enabled aggregator AI is configured",
        )
    return aggregator


def _build_score_map(score_items: list[dict[str, Any]]) -> dict[str, float]:
    """提取并限制评分范围，忽略缺少有效 answer_id 或 score 的条目。"""
    score_map: dict[str, float] = {}
    for item in score_items:
        answer_id = item.get("answer_id")
        raw_score = item.get("score")
        if not isinstance(answer_id, str) or not answer_id:
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        score_map[answer_id] = round(min(max(score, 0.0), 1.0), 4)
    return score_map
