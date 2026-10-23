"""
RAG 检索结果的提示词上下文格式化。

负责将知识片段及其来源转换为可供回答生成使用的参考资料文本，
并在联合检索场景中分别标注公共知识库与当前任务上传资料。
本模块只组织文本，不执行检索或模型调用。
"""

from __future__ import annotations

from app.modules.rag.domain.vector_models import (
    VectorSearchHit,
)
from app.modules.rag.domain.retrieval_models import (
    CombinedRetrievalResult,
)


def build_rag_context(
    hits: list[VectorSearchHit],
) -> str:
    """
    把 Retriever 返回的 TopK Chunk
    转换成可以直接放入 Prompt 的上下文。

    示例：

    [资料1]
    来源：forbidden_city.txt
    内容：紫禁城始建于……

    [资料2]
    来源：great_wall.txt
    内容：长城……
    """

    if not hits:
        return ""

    parts: list[str] = []

    for index, hit in enumerate(
        hits,
        start=1,
    ):
        parts.append(
            f"[资料{index}]\n"
            f"来源：{hit.source}\n"
            f"内容：{hit.content}"
        )

    return "\n\n".join(parts)


def build_combined_rag_context(
    result: CombinedRetrievalResult,
) -> str:
    """
    把公共知识库和用户临时资料组合成一份 Prompt Context。

    两种来源明确分开，避免模型无法判断资料来源。
    """

    sections: list[str] = []

    # ========================================================
    # 1. 公共知识库资料
    # ========================================================

    if result.public_hits:
        public_parts: list[str] = []

        for index, hit in enumerate(
            result.public_hits,
            start=1,
        ):
            public_parts.append(
                f"[公共知识库资料{index}]\n"
                f"来源：{hit.source}\n"
                f"内容：{hit.content}"
            )

        sections.append(
            "===== 公共知识库 =====\n\n"
            + "\n\n".join(public_parts)
        )

    # ========================================================
    # 2. 用户当前 Task 上传的资料
    # ========================================================

    if result.temporary_hits:
        temporary_parts: list[str] = []

        for index, hit in enumerate(
            result.temporary_hits,
            start=1,
        ):
            temporary_parts.append(
                f"[用户上传资料{index}]\n"
                f"来源：{hit.source}\n"
                f"内容：{hit.content}"
            )

        sections.append(
            "===== 用户本次上传资料 =====\n\n"
            + "\n\n".join(temporary_parts)
        )

    return "\n\n".join(sections)