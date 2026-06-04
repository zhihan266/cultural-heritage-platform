from difflib import SequenceMatcher
from uuid import uuid4

from fastapi import HTTPException, status

from app.db.memory import store
from app.models.domain import Account, AiAgent, Answer, AnswerSource, Role, Task, TaskStatus
from app.schemas.domain import TaskCreateRequest


def summarize_task(task: Task) -> dict:
    return {
        "id": task.id,
        "question": task.question,
        "answer_format": task.answer_format,
        "status": task.status,
        "required_worker_count": task.required_worker_count,
        "accepted_worker_count": len(task.accepted_worker_ids),
        "answer_count": len(task.answers),
        "final_result": task.final_result,
    }


def create_task(payload: TaskCreateRequest) -> Task:
    user = store.accounts.get(payload.user_id)
    if not user or user.role != Role.USER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User does not exist")

    missing_ai_ids = [ai_id for ai_id in payload.ai_ids if ai_id not in store.ai_agents]
    if missing_ai_ids:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"AI does not exist: {', '.join(missing_ai_ids)}",
        )

    task = Task(
        id=f"task-{uuid4().hex[:8]}",
        user_id=payload.user_id,
        question=payload.question,
        answer_format=payload.answer_format,
        ai_ids=payload.ai_ids,
        required_worker_count=payload.worker_count,
        metadata=payload.metadata,
    )
    store.tasks[task.id] = task
    return task


def accept_task(task_id: str, worker_id: str) -> Task:
    task = get_task(task_id)
    worker = store.accounts.get(worker_id)
    if not worker or worker.role != Role.WORKER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker does not exist")
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Task is already completed")
    if worker_id in task.accepted_worker_ids:
        return task
    if len(task.accepted_worker_ids) >= task.required_worker_count:
        raise HTTPException(status.HTTP_409_CONFLICT, "Required worker count is full")

    task.accepted_worker_ids.append(worker_id)
    task.status = TaskStatus.ANSWERING
    return task


def add_worker_answer(task_id: str, worker_id: str, content: str) -> tuple[Task, Answer]:
    task = get_task(task_id)
    worker = store.accounts.get(worker_id)
    if not worker or worker.role != Role.WORKER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker does not exist")
    if worker_id not in task.accepted_worker_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Worker must accept the task first")
    return add_answer(task, worker_id, AnswerSource.WORKER, content, worker.score)


def add_ai_answer(task_id: str, ai_id: str, content: str) -> tuple[Task, Answer]:
    task = get_task(task_id)
    ai = store.ai_agents.get(ai_id)
    if not ai:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI does not exist")
    if ai_id not in task.ai_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This AI was not assigned to the task")
    return add_answer(task, ai_id, AnswerSource.AI, content, ai.score)


def add_answer(
    task: Task,
    responder_id: str,
    source: AnswerSource,
    content: str,
    score_snapshot: float,
) -> tuple[Task, Answer]:
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Task is already completed")
    if any(
        answer.responder_id == responder_id and answer.source == source
        for answer in task.answers
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Responder has already submitted an answer")

    answer = Answer(
        id=f"answer-{uuid4().hex[:8]}",
        task_id=task.id,
        responder_id=responder_id,
        source=source,
        content=content,
        score_snapshot=score_snapshot,
    )
    task.answers.append(answer)
    maybe_complete_task(task)
    return task, answer


def maybe_complete_task(task: Task) -> None:
    expected_answer_count = task.required_worker_count + len(task.ai_ids)
    if expected_answer_count == 0:
        return
    if len(task.answers) < expected_answer_count:
        task.status = TaskStatus.ANSWERING
        return

    task.status = TaskStatus.AGGREGATING
    total_score = sum(max(answer.score_snapshot, 0.01) for answer in task.answers)
    result_parts: list[str] = []
    for answer in task.answers:
        answer.weight = round(max(answer.score_snapshot, 0.01) / total_score, 4)
        result_parts.append(
            f"[{answer.source.value}:{answer.responder_id}, weight={answer.weight}] {answer.content}"
        )

    task.final_result = (
        f"Answer format requirement: {task.answer_format}\n"
        f"Weighted synthesis:\n"
        + "\n".join(result_parts)
    )
    task.status = TaskStatus.COMPLETED


def review_final_result(task_id: str, final_result: str, reward_scale: float) -> Task:
    task = get_task(task_id)
    task.final_result = final_result

    for answer in task.answers:
        similarity = SequenceMatcher(None, final_result, answer.content).ratio()
        delta = round((similarity - 0.5) * reward_scale, 4)
        if answer.source == AnswerSource.WORKER:
            worker = store.accounts.get(answer.responder_id)
            if worker:
                worker.score = clamp_score(worker.score + delta)
        else:
            ai = store.ai_agents.get(answer.responder_id)
            if ai:
                ai.score = clamp_score(ai.score + delta)
                ai.metadata = {
                    "last_reward_delta": delta,
                    "last_similarity": round(similarity, 4),
                    "training_signal": "reward" if delta >= 0 else "penalty",
                }

    task.status = TaskStatus.COMPLETED
    return task


def clamp_score(score: float) -> float:
    return round(min(max(score, 0.1), 10.0), 4)


def get_task(task_id: str) -> Task:
    task = store.tasks.get(task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task does not exist")
    return task


def available_hall_tasks() -> list[Task]:
    return [
        task
        for task in store.tasks.values()
        if task.status in {TaskStatus.WAITING, TaskStatus.ANSWERING}
        and len(task.accepted_worker_ids) < task.required_worker_count
    ]


def completed_tasks_for_user(user_id: str) -> list[Task]:
    return [
        task
        for task in store.tasks.values()
        if task.user_id == user_id and task.status == TaskStatus.COMPLETED
    ]


def worker_scoreboard() -> list[Account]:
    return sorted(
        [account for account in store.accounts.values() if account.role == Role.WORKER],
        key=lambda account: account.score,
        reverse=True,
    )


def ai_scoreboard() -> list[AiAgent]:
    return sorted(store.ai_agents.values(), key=lambda ai: ai.score, reverse=True)
