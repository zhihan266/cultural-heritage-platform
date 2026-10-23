"""任务与回答 ORM 记录到领域模型的转换。"""

from app.modules.hcp_mad.domain.models import Answer, AnswerSource, AnswerTable, Task, TaskStatus, TaskTable, TaskMode


def answer_to_domain(row: AnswerTable) -> Answer:
    """把回答表记录转换为回答领域对象。"""
    return Answer(
        id=row.id,
        task_id=row.task_id,
        responder_id=row.responder_id,
        source=AnswerSource(row.source),
        content=row.content,
        score_snapshot=row.score_snapshot,
        weight=row.weight,
        execution_id=row.execution_id,
    )


def task_to_domain(row: TaskTable, answer_rows: list[AnswerTable] | None = None) -> Task:
    """把任务记录和回答记录组合为完整任务领域对象。"""
    return Task(
        id=row.id,
        user_id=row.user_id,
        question=row.question,
        answer_format=row.answer_format,
        ai_ids=row.ai_ids or [],
        required_worker_count=row.required_worker_count,
        accepted_worker_ids=row.accepted_worker_ids or [],
        status=TaskStatus(row.status),
        answers=[answer_to_domain(answer) for answer in answer_rows or []],
        final_result=row.final_result,
        metadata=row.metadata_json or {},
        mode=TaskMode(row.mode),
    )
