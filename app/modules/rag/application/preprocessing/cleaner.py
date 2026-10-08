from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from app.modules.rag.domain.models import RagDocument
from app.modules.rag.application.preprocessing.cleaner_config import CleanerConfig


class DocumentCleaner:
    """
    对解析后的 RagDocument 执行基础文本清理。

    当前只处理确定性较强的格式噪声：
    1. 统一 Unicode；
    2. 统一换行符；
    3. 删除零宽字符和 BOM；
    4. 统一空格和 Tab；
    5. 压缩连续空行；
    6. 过滤清理后为空的文档。
    """

    _INVISIBLE_CHARACTERS = (
        "\u200b",  # 零宽空格
        "\u200c",  # 零宽非连接符
        "\u200d",  # 零宽连接符
        "\ufeff",  # BOM
        "\u2060",  # Word Joiner
        "\u00ad",  # 软连字符
    )

    def __init__(
        self,
        config: CleanerConfig | None = None,
    ) -> None:
        self.config = config or CleanerConfig()

    def clean(
        self,
        documents: list[RagDocument],
    ) -> list[RagDocument]:
        """
        清理一组 RagDocument。

        不修改 Parser 返回的原始对象，而是返回新的对象。
        """

        if not documents:
            return []

        cleaned_documents: list[RagDocument] = []

        for document in documents:
            cleaned_content = self._clean_text(
                document.content
            )

            visible_char_count = self._count_visible_chars(
                cleaned_content
            )

            # 清理后没有足够正文的文档，不进入后续切片流程。
            if (
                visible_char_count
                < self.config.min_content_chars
            ):
                continue

            # metadata 是可变字典，所以先复制。
            cleaned_metadata = dict(document.metadata)

            cleaned_metadata.update(
                {
                    "cleaning_version": "basic-v1",
                    "original_char_count": len(
                        document.content
                    ),
                    "cleaned_char_count": len(
                        cleaned_content
                    ),
                    "visible_char_count": (
                        visible_char_count
                    ),
                }
            )

            # 创建新的 RagDocument，不修改原始对象。
            cleaned_document = replace(
                document,
                content=cleaned_content,
                metadata=cleaned_metadata,
            )

            cleaned_documents.append(
                cleaned_document
            )

        if not cleaned_documents:
            raise ValueError(
                "文档清理后没有剩余可用内容"
            )

        return cleaned_documents

    def _clean_text(self, text: str) -> str:
        """清理单个文本字符串。"""

        if not isinstance(text, str):
            raise TypeError(
                "RagDocument.content 必须是字符串"
            )

        # 统一 Unicode 表示。
        text = unicodedata.normalize("NFC", text)

        # 统一 Windows、Linux 和旧 Mac 换行符。
        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")

        # 不换行空格转换为普通空格。
        text = text.replace("\u00a0", " ")

        # 删除不可见字符。
        for character in self._INVISIBLE_CHARACTERS:
            text = text.replace(character, "")

        cleaned_lines: list[str] = []

        for raw_line in text.split("\n"):
            # 连续空格和 Tab 压缩成一个普通空格。
            cleaned_line = re.sub(
                r"[ \t]+",
                " ",
                raw_line,
            ).strip()

            cleaned_lines.append(cleaned_line)

        content = "\n".join(cleaned_lines)

        content = self._collapse_blank_lines(
            content
        )

        return content.strip()

    def _collapse_blank_lines(
        self,
        text: str,
    ) -> str:
        """限制连续空白行的数量。"""

        # 一行空白对应两个连续换行符。
        max_newlines = (
            self.config.max_blank_lines + 1
        )

        excessive_newlines_pattern = (
            rf"\n{{{max_newlines + 1},}}"
        )

        replacement = "\n" * max_newlines

        return re.sub(
            excessive_newlines_pattern,
            replacement,
            text,
        )

    @staticmethod
    def _count_visible_chars(text: str) -> int:
        """统计除空白符之外的字符数量。"""

        return len(
            re.sub(r"\s+", "", text)
        )
