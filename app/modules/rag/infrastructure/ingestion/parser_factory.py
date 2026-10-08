from pathlib import Path

from app.modules.rag.infrastructure.ingestion.docx_parser import DocxParser
from app.modules.rag.domain.parser import DocumentParser
from app.modules.rag.infrastructure.ingestion.text_parser import TextParser
from app.modules.rag.infrastructure.ingestion.pdf_parser import PdfParser


class ParserFactory:
    """根据文件类型创建对应的文档解析器。"""

    _parser_classes: dict[str, type[DocumentParser]] = {
        ".txt": TextParser,
        ".docx": DocxParser,
        ".pdf": PdfParser,
    }

    @classmethod
    def get_parser(cls, file_path: Path) -> DocumentParser:
        suffix = file_path.suffix.lower()

        parser_class = cls._parser_classes.get(suffix)

        if parser_class is None:
            supported_types = ", ".join(cls._parser_classes.keys())

            raise ValueError(
                f"暂不支持文件类型：{suffix}，"
                f"当前支持：{supported_types}"
            )

        return parser_class()
