"""从当前 Task 最新共享 RAG 文本提取不可拆分的证据块。

识别现有 RagFacade 的普通、公共与上传资料格式，保留原始标签和来源。
相同正文合并时保留全部来源别名；未知格式整体保留，不猜测来源或切断事实。
不执行检索，也不读取其他任务或旧版本的检索记录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from app.modules.hcp_mad.application.context.debate_state import EvidenceReference
from app.modules.hcp_mad.application.task.facade import ContextItemView


@dataclass(frozen=True)
class DebateEvidence:
    refs: tuple[EvidenceReference, ...]
    sources: tuple[str, ...]
    content: str
    retrieval_rank: int


_HEADER = re.compile(
    r"(?m)^\[(资料\d+|公共知识库资料\d+|用户上传资料\d+)\]\r?\n来源：([^\r\n]+)\r?\n内容："
)
_SECTION = re.compile(r"(?m)^===== (公共知识库|用户本次上传资料) =====\s*$")
_TRAILING_SECTION = re.compile(r"\n\n===== (公共知识库|用户本次上传资料) =====\s*$")


def extract_evidence(item: ContextItemView) -> tuple[list[DebateEvidence], int]:
    text = item.content
    matches = list(_HEADER.finditer(text))
    parts: list[DebateEvidence] = []
    if matches and not _SECTION.sub("", text[:matches[0].start()]).strip():
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = _TRAILING_SECTION.sub("", text[match.end():end]).strip()
            parts.append(DebateEvidence(
                refs=(EvidenceReference(item_id=item.id, label=match.group(1)),),
                sources=(match.group(2),), content=content, retrieval_rank=index,
            ))
    else:
        parts = [DebateEvidence(
            refs=(EvidenceReference(item_id=item.id, label="raw"),),
            sources=(f"ContextItem:{item.id}",), content=text, retrieval_rank=0,
        )]
    unique: dict[str, DebateEvidence] = {}
    duplicates = 0
    for part in parts:
        previous = unique.get(part.content)
        if previous is None:
            unique[part.content] = part
        else:
            duplicates += 1
            unique[part.content] = replace(
                previous, refs=(*previous.refs, *part.refs),
                sources=tuple(dict.fromkeys((*previous.sources, *part.sources))),
            )
    return list(unique.values()), duplicates


def select_task_evidence(
    progress: dict[str, Any], shared: list[ContextItemView],
) -> tuple[list[DebateEvidence], int]:
    """沿用最新任务级 RAG 选择；明确失效时不复用旧版本。"""
    if progress.get("use_rag", True) is not True:
        return [], 0
    items = [item for item in shared if item.item_type == "RAG_CONTEXT"
             and item.execution_id is None and item.ai_id is None]
    if not items or items[-1].metadata.get("retrieval_failed") is True or not items[-1].content.strip():
        return [], 0
    return extract_evidence(items[-1])
