"""检索阶段错误及脱敏诊断。

保留底层异常链和堆栈，将阶段/异常类型与零召回区分，
日志不包含密钥或请求鉴权信息，不更改既有检索策略和失败降级规则。
"""

import logging
import re
import traceback
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class RagRetrievalError(RuntimeError):
    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(f"RAG {stage} failed ({type(cause).__name__})")
        self.stage = stage


def retrieval_failure_details(exc: Exception, *, task_id: str) -> dict[str, str]:
    """只将稳定类型与阶段返回业务层；完整脱敏堆栈保留在服务日志。"""
    root: BaseException = exc
    seen: set[int] = set()
    while root.__cause__ is not None and id(root) not in seen:
        seen.add(id(root))
        root = root.__cause__
    report = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    cfg = get_settings()
    for name in ("embedding_api_key", "rag_milvus_token", "deepseek_api_key", "tavily_api_key"):
        secret: Any = getattr(cfg, name, None)
        value = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
        if isinstance(value, str) and value:
            report = report.replace(value, "[REDACTED]")
    report = re.sub(r"(?i)Bearer\s+[^\s\"',;]+", "Bearer [REDACTED]", report)
    report = re.sub(r"(?i)(api[_-]?key|token|password)=([^\s&\"']+)", r"\1=[REDACTED]", report)
    report = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", report)
    stage = exc.stage if isinstance(exc, RagRetrievalError) else "retrieval"
    logger.error("RAG retrieval failed: task=%s stage=%s root_type=%s\n%s",
                 task_id, stage, type(root).__name__, report)
    return {"rag_error_stage": stage, "rag_error_type": type(root).__name__}
