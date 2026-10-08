from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.modules.rag.domain.models import RagChunk, RagDocument
from app.modules.rag.application.preprocessing.splitter_config import SplitterConfig


@dataclass(frozen=True)
class _TextSlice:
    """
    切片器内部使用的临时对象。

    下划线表示它只在 splitting 模块内部使用，
    不属于整个项目对外公开的领域模型。
    """

    content: str
    start_char: int
    end_char: int


class DocumentSplitter:
    """
    按自然文本边界切分 RagDocument。

    优先寻找：
    段落 → 换行 → 句号 → 问号 → 分号 → 逗号 → 空格

    找不到合适边界时，才按照最大长度强制切割。
    """

    SPLITTER_VERSION = "boundary-aware-v1"

    def __init__(
        self,
        config: SplitterConfig | None = None,
    ) -> None:
        self.config = config or SplitterConfig()

    def split(
        self,
        documents: list[RagDocument],
    ) -> list[RagChunk]:
        """将多份清理后的文档转换为 RagChunk 列表。"""

        if not documents:
            return []

        chunks: list[RagChunk] = []

        for document in documents:
            document_chunks = self._split_document(
                document
            )
            chunks.extend(document_chunks)

        return chunks

    def _split_document(
        self,
        document: RagDocument,
    ) -> list[RagChunk]:
        """切分一份 RagDocument。"""

        if not isinstance(document.content, str):
            raise TypeError(
                "RagDocument.content 必须是字符串"
            )

        if not document.content.strip():
            raise ValueError(
                f"文档内容为空，无法切片："
                f"{document.document_id}"
            )

        text_slices = self._split_text(
            document.content
        )

        chunks: list[RagChunk] = []

        for chunk_index, text_slice in enumerate(
            text_slices
        ):
            chunk_id = self._build_chunk_id(
                document_id=document.document_id,
                chunk_index=chunk_index,
                content=text_slice.content,
                start_char=text_slice.start_char,
                end_char=text_slice.end_char,
            )

            chunk_metadata = dict(document.metadata)

            chunk_metadata.update(
                {
                    "splitter_version": (
                        self.SPLITTER_VERSION
                    ),
                    "chunk_size_limit": (
                        self.config.chunk_size
                    ),
                    "chunk_overlap": (
                        self.config.chunk_overlap
                    ),
                    "chunk_char_count": len(
                        text_slice.content
                    ),
                }
            )

            chunk = RagChunk(
                chunk_id=chunk_id,
                document_id=document.document_id,
                content=text_slice.content,
                chunk_index=chunk_index,
                start_char=text_slice.start_char,
                end_char=text_slice.end_char,
                source=document.source,
                file_type=document.file_type,
                metadata=chunk_metadata,
            )

            chunks.append(chunk)

        return chunks

    def _split_text(
        self,
        text: str,
    ) -> list[_TextSlice]:
        """使用滑动窗口和自然边界切割文本。"""

        text_length = len(text)

        if text_length <= self.config.chunk_size:
            start_char, end_char, content = (
                self._trim_slice(
                    text=text,
                    start_char=0,
                    end_char=text_length,
                )
            )

            return [
                _TextSlice(
                    content=content,
                    start_char=start_char,
                    end_char=end_char,
                )
            ]

        slices: list[_TextSlice] = []

        start_char = 0

        while start_char < text_length:
            maximum_end = min(
                start_char + self.config.chunk_size,
                text_length,
            )

            if maximum_end == text_length:
                chosen_end = text_length
            else:
                chosen_end = self._find_best_boundary(
                    text=text,
                    start_char=start_char,
                    maximum_end=maximum_end,
                )

            if chosen_end <= start_char:
                # 防御性处理，避免错误边界造成死循环。
                chosen_end = maximum_end

            (
                actual_start,
                actual_end,
                content,
            ) = self._trim_slice(
                text=text,
                start_char=start_char,
                end_char=chosen_end,
            )

            if content:
                slices.append(
                    _TextSlice(
                        content=content,
                        start_char=actual_start,
                        end_char=actual_end,
                    )
                )

            if chosen_end >= text_length:
                break

            next_start = (
                chosen_end
                - self.config.chunk_overlap
            )

            # 确保每次循环都向后移动，防止死循环。
            start_char = max(
                next_start,
                start_char + 1,
            )

        return slices

    def _find_best_boundary(
        self,
        *,
        text: str,
        start_char: int,
        maximum_end: int,
    ) -> int:
        """
        在最大切片长度之前寻找自然边界。

        只搜索窗口后半部分，避免因为前面很早出现一个句号，
        就生成特别短的切片。
        """

        search_width = int(
            self.config.chunk_size
            * self.config.boundary_search_ratio
        )

        search_start = max(
            start_char + self.config.min_chunk_chars,
            maximum_end - search_width,
        )

        for separator in self.config.separators:
            position = text.rfind(
                separator,
                search_start,
                maximum_end,
            )

            if position != -1:
                # 将分隔符保留在前一个切片中。
                return position + len(separator)

        # 找不到任何合适边界时，执行硬切。
        return maximum_end

    @staticmethod
    def _trim_slice(
        *,
        text: str,
        start_char: int,
        end_char: int,
    ) -> tuple[int, int, str]:
        """
        删除切片首尾空白，同时修正字符位置。

        这样可以保证：
        text[start_char:end_char] == content
        """

        raw_text = text[start_char:end_char]

        left_trim_count = (
            len(raw_text)
            - len(raw_text.lstrip())
        )

        right_trim_count = (
            len(raw_text)
            - len(raw_text.rstrip())
        )

        actual_start = start_char + left_trim_count
        actual_end = end_char - right_trim_count

        if actual_end < actual_start:
            actual_end = actual_start

        content = text[
            actual_start:actual_end
        ]

        return actual_start, actual_end, content

    @staticmethod
    def _build_chunk_id(
        *,
        document_id: str,
        chunk_index: int,
        content: str,
        start_char: int,
        end_char: int,
    ) -> str:
        """
        根据文档、位置和内容生成稳定的切片 ID。

        相同文档和相同内容重新切片时，
        会得到相同的 chunk_id。
        """

        identity_text = (
            f"{document_id}|"
            f"{chunk_index}|"
            f"{start_char}|"
            f"{end_char}|"
            f"{content}"
        )

        return hashlib.sha256(
            identity_text.encode("utf-8")
        ).hexdigest()
