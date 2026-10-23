"""任务业务服务的统一公开入口。

集中导出任务管理、回答和查询等业务函数。各实现文件按任务管理、答案处理、
结果审核、查询、排行榜和公共工具划分，避免 Router 依赖具体实现模块。
"""

from app.modules.rag_task.application.answers import add_ai_answer, add_worker_answer, maybe_complete_task
from app.modules.rag_task.application.management import accept_task, create_task
from app.modules.rag_task.infrastructure.queries import (
    available_hall_tasks,
    completed_tasks_for_user,
    get_task,
    tasks_for_user,
)
from app.modules.rag_task.application.rankings import ai_scoreboard, worker_scoreboard
from app.modules.rag_task.application.review import review_final_result
from app.modules.rag_task.task_utils import clamp_score, summarize_task

__all__ = [
    "accept_task",
    "add_ai_answer",
    "add_worker_answer",
    "ai_scoreboard",
    "available_hall_tasks",
    "clamp_score",
    "completed_tasks_for_user",
    "create_task",
    "get_task",
    "maybe_complete_task",
    "review_final_result",
    "summarize_task",
    "tasks_for_user",
    "worker_scoreboard",
]
