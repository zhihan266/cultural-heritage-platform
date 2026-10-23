"""最终结果审核和回答者评分更新业务。"""

from app.modules.agent import repository as agent_repository
from app.modules.account import repository as account_repository
from difflib import SequenceMatcher
from fastapi import HTTPException
from sqlalchemy import text

from app.modules.hcp_mad.infrastructure import repository as task_repository
from app.db.session import SessionLocal
from app.modules.hcp_mad.domain.models import AnswerSource, Task, TaskMode, TaskStatus
from app.modules.hcp_mad.task_utils import clamp_score


def review_final_result(
    task_id: str,
    final_result: str,
    reward_scale: float,
) -> Task:
    """根据最终答案与原答案的文本相似度更新工人和 AI 分数。"""
    # 发布者权限在 user_router.final_review 校验；这里负责替换结果和更新信誉分。
    # 修改的是 Account/AiAgent.score，区别于聚合模型对单条答案给出的 Answer.weight。
    with SessionLocal() as db:
        # 与终局发布使用同一父 Task 锁；SQLite 也须先 BEGIN IMMEDIATE，不能读后升级锁。
        # 普通审核沿用原行为，只增加串行锁；运行中的上限终局则拒绝覆盖和奖励。
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        task = task_repository.get_task_row(db, task_id, for_update=True)
        if task is None:
            raise HTTPException(404, "Task does not exist")
        if task.mode == TaskMode.HCP_MAD.value:
            debate = (task.metadata_json or {}).get("hcp_mad", {}).get("debate", {})
            if debate.get("phase") == "finalizing":
                raise HTTPException(409, "Cannot manually review while debate finalization is pending")
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
                worker = account_repository.get_account_row(db, answer.responder_id)
                if worker:
                    worker.score = clamp_score(worker.score + delta)
            else:
                ai = agent_repository.get_ai_row(db, answer.responder_id)
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
