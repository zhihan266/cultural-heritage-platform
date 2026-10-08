from pathlib import Path
from dataclasses import replace
import hashlib
from collections.abc import Callable
from app.modules.rag.domain.models import RagChunk
from app.modules.rag.domain.parser import DocumentParser
from app.modules.rag.application.preprocessing.cleaner import DocumentCleaner
from app.modules.rag.application.preprocessing.splitter import DocumentSplitter
from app.modules.rag.application.preprocessing.splitter_config import SplitterConfig


class PreprocessingService:
    """编排文档预处理流程："""

    def __init__(
        self,
        *,
        parser_resolver: Callable[[Path], DocumentParser],
        cleaner: DocumentCleaner | None = None,
        splitter: DocumentSplitter | None = None,
    ) -> None:
        self.parser_resolver = parser_resolver
        self.cleaner = cleaner or DocumentCleaner()
        self.splitter = splitter or DocumentSplitter(
            SplitterConfig(chunk_size=800, chunk_overlap=120, min_chunk_chars=100)
        )

    def process(self, file_path: Path) -> list[RagChunk]:
        """完成一份文件的全部预处理流程。"""
        parser = self.parser_resolver(file_path)
        raw_documents = parser.parse(file_path)
        if not raw_documents:
            raise ValueError(f"文件没有解析出有效文档：{file_path}")
        document_id = self._build_document_id(file_path)
        normalized_documents = []
        for document in raw_documents:
            normalized_document = replace(document, document_id=document_id)
            normalized_documents.append(normalized_document)
        cleaned_documents = self.cleaner.clean(normalized_documents)
        chunks = self.splitter.split(cleaned_documents)
        if not chunks:
            raise ValueError(f"文件没有生成有效切片：{file_path}")
        return chunks

    @staticmethod
    def _build_document_id(file_path: Path) -> str:
        """根据文件内容生成稳定的 document_id。"""
        sha256 = hashlib.sha256()
        with file_path.open("rb") as file:
            while True:
                block = file.read(1024 * 1024)
                if not block:
                    break
                sha256.update(block)
        return sha256.hexdigest()
