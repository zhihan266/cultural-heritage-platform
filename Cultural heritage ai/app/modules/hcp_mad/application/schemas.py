"""HCP-MAD 提交参数，复用现有任务问题与响应数据约定。"""

from typing import Literal

from pydantic import ConfigDict, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from app.modules.hcp_mad.application.task.facade import TaskCreateWithDocumentPayload


class HcpTaskCreateRequest(TaskCreateWithDocumentPayload):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "question": "天坛主要用于什么？", "answer_format": "普通文本",
        "use_rag": True, "metadata": {},
    }]})

    answer_format: str = Field(default="普通文本", min_length=1)
    # 保留旧空值的运行时兼容与拒绝客户端指定的校验；不作为公开请求字段展示。
    ai_ids: SkipJsonSchema[list[str]] = Field(default_factory=list, max_length=0)
    worker_count: SkipJsonSchema[Literal[0]] = 0
    use_rag: bool = True

    @field_validator("question", "answer_format")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Must not be blank")
        return value.strip()
