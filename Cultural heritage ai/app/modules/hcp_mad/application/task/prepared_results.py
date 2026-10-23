"""复用 Agent 结果的私有准备记录与提交恢复机制。

有效公开结果先单独持久化，再由具体用例原子发布或正式完成任务。
区分模型失败和数据库提交失败；准备结果存在时不重新执行模型。
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.agent.models import AiAgent
from app.modules.hcp_mad.application.task.agent_messages import _Claim, _MessageStore
from app.modules.hcp_mad.application.task.agent_results import AgentResultIssue, AgentResultValidationError
from app.modules.hcp_mad.application.task.execution_context import AgentExecutionScope
from app.modules.hcp_mad.domain.models import ContextItemType, ContextVisibility, Task, TaskContextItemTable, TaskTable
from app.modules.hcp_mad.infrastructure import execution_repository as executions
from app.modules.hcp_mad.application.task.agent_messages import _save_registry

_Validated = TypeVar("_Validated")
_Receipt = TypeVar("_Receipt")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class _PreparedResultStore(_MessageStore, Generic[_Validated, _Receipt]):
    prepared_kind = "validated_agent_result"
    commit_error: Callable[[str], RuntimeError] = RuntimeError
    issues: tuple[AgentResultIssue, ...] = ()
    validated = False
    binding_rejected = False

    def _encode(self, result: _Validated) -> dict[str, Any]:
        raise NotImplementedError

    def _decode(self, payload: dict[str, Any]) -> _Validated:
        raise NotImplementedError

    def _parse_public_content(self, content: str) -> _Validated:
        raise NotImplementedError

    def _require_owned(self, db: Session, row: TaskTable, ai: AiAgent, scope: AgentExecutionScope,
                       entry: dict[str, Any]) -> None:
        raise NotImplementedError

    def _commit(self, ai: AiAgent, scope: AgentExecutionScope) -> _Receipt:
        raise NotImplementedError

    def _prepared_claim(self, task: Task, scope: AgentExecutionScope) -> _Claim:
        return _Claim(task, scope, prepared=True)

    def _prepare(self, ai: AiAgent, scope: AgentExecutionScope, result: _Validated) -> None:
        payload = self._encode(result)
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise HTTPException(404, "Task not found")
            registry, entry = self._owned_entry(row, scope)
            self._require_owned(db, row, ai, scope, entry)
            if entry["status"] != "claimed":
                raise HTTPException(409, "Result was already prepared")
            item = executions.append_context_item(
                db, context_id=scope.context_id, execution_id=scope.execution_id, ai_id=scope.ai_id,
                item_type=ContextItemType.REVIEW, content=json.dumps(payload, ensure_ascii=False),
                visibility=ContextVisibility.PRIVATE,
                metadata={"kind": self.prepared_kind, "include_in_prompt": False, "key": self.request.key},
            )
            entry.update(status="prepared", prepared_item_id=item.id, prepared_digest=_digest(payload))
            _save_registry(row, registry)
            db.commit()

    def _prepared_result(self, db: Session, scope: AgentExecutionScope, entry: dict[str, Any]) -> _Validated:
        item = db.get(TaskContextItemTable, entry["prepared_item_id"])
        if (item is None or item.context_id != scope.context_id or item.execution_id != scope.execution_id
                or item.ai_id != scope.ai_id or item.item_type != ContextItemType.REVIEW.value
                or item.visibility != ContextVisibility.PRIVATE.value or item.metadata_json.get("key") != self.request.key
                or item.metadata_json.get("kind") != self.prepared_kind):
            raise RuntimeError("Prepared result is not owned by this execution")
        payload = json.loads(item.content)
        if _digest(payload) != entry["prepared_digest"]:
            raise RuntimeError("Prepared result integrity check failed")
        return self._decode(payload)

    def submit(self, ai: AiAgent, scope: AgentExecutionScope, content: str) -> _Receipt:
        try:
            result = self._parse_public_content(content)
        except AgentResultValidationError as exc:
            self.issues = exc.issues
            raise
        self.validated = True
        try:
            self._prepare(ai, scope, result)
            return self.commit_prepared(ai, scope)
        except HTTPException:
            self.binding_rejected = True
            raise
        except Exception:
            raise self.commit_error(scope.execution_id) from None

    def commit_prepared(self, ai: AiAgent, scope: AgentExecutionScope) -> _Receipt:
        self.validated = True
        try:
            return self._commit(ai, scope)
        except HTTPException:
            self.binding_rejected = True
            raise
        except Exception:
            raise self.commit_error(scope.execution_id) from None

    def fail(self, scope: AgentExecutionScope, error: str) -> None:
        with self.factory() as db:
            row = self._lock_task(db, scope.task_id)
            if row is None:
                raise RuntimeError("Task no longer exists")
            registry, entry = self._owned_entry(row, scope)
            if entry["status"] == "completed":
                return
            if not self.binding_rejected and (self.validated or entry["status"] == "prepared"):
                # 有效结果/提交失败不转成模型失败；无持久化准备记录时宁可阻塞，不自动再调用模型。
                entry["commit_error"] = "validated_result_persistence_incomplete"
                _save_registry(row, registry)
                db.commit()
                return
        super().fail(scope, error)

