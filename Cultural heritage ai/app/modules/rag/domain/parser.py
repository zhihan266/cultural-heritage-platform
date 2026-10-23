"""
RAG 文件解析器的统一抽象。

约定从文件路径解析出 RagDocument 列表，
使预处理服务通过接口调用 TXT、PDF、DOCX 等具体解析实现。
本模块不负责文件格式选择、文本清理或切片。
"""

from abc import ABC, abstractmethod
from pathlib import Path

from app.modules.rag.domain.models import RagDocument


class DocumentParser(ABC):

    @abstractmethod
    def parse(self, file_path: Path) -> list[RagDocument]:
        pass
