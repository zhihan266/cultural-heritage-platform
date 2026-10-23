"""UTF-8 文本文件的文档解析器。

读取非空 TXT 正文并转换为统一的 RagDocument，保留文件来源与路径，
供预处理服务继续清洗、生成稳定文档标识和切片。
"""

from pathlib import Path
from uuid import uuid4

from app.modules.rag.domain.models import RagDocument
from app.modules.rag.domain.parser import DocumentParser


class TextParser(DocumentParser):

    def parse(self, file_path: Path) -> list[RagDocument]:
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在：{file_path}")

        if not file_path.is_file():
            raise ValueError(f"传入路径不是文件：{file_path}")

        content = file_path.read_text(encoding="utf-8").strip()

        if not content:
            raise ValueError("TXT 文档内容为空")

        document = RagDocument(
            document_id=str(uuid4()),
            content=content,
            source=file_path.name,
            file_type=file_path.suffix.lower(),
            metadata={
                "file_path": str(file_path),
            },
        )
        return [document]
