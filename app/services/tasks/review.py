"""最终结果审核和回答者评分更新业务。"""

from difflib import SequenceMatcher

from app.crud import tasks as task_repository
from app.db.session import SessionLocal
from app.models.domain import AnswerSource, Task, TaskStatus
from app.services.tasks.task_utils import clamp_score, require_task_row


def review_final_result(
    task_id: str,
    final_result: str,
    reward_scale: float,
) -> Task:
    """根据最终答案与原答案的文本相似度更新工人和 AI 分数。"""
    with SessionLocal() as db:
        task = require_task_row(db, task_id)
        task.final_result = final_result

        for answer in task_repository.list_answers(db, task.id):
            # 相似度范围为 0~1，以 0.5 为不奖不罚的中心点。
            similarity = SequenceMatcher(
                None,
                final_result,
                answer.content,
            ).ratio()
            delta = round((similarity - 0.5) * reward_scale, 4)
            if answer.source == AnswerSource.WORKER.value:
                worker = task_repository.get_account_row(db, answer.responder_id)
                if worker:
                    worker.score = clamp_score(worker.score + delta)
            else:
                ai = task_repository.get_ai_row(db, answer.responder_id)
                if ai:
                    ai.score = clamp_score(ai.score + delta)
                    ai.metadata_json = {
                        "last_reward_delta": delta,
                        "last_similarity": round(similarity, 4),
                        "training_signal": "reward" if delta >= 0 else "penalty",
                    }

        task.status = TaskStatus.COMPLETED.value
        db.commit()
        db.refresh(task)
        return task_repository.task_with_answers(db, task)
