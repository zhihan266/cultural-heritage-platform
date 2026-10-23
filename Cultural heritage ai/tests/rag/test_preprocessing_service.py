from pathlib import Path

from app.modules.rag.infrastructure.ingestion.parser_factory import ParserFactory

from app.modules.rag.domain.models import RagChunk
from app.modules.rag.application.preprocessing_service import PreprocessingService
from app.modules.rag.application.preprocessing.splitter import DocumentSplitter
from app.modules.rag.application.preprocessing.splitter_config import SplitterConfig



def test_preprocessing_txt_success(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "heritage.txt"

    test_file.write_text(
        (
            "故宫位于北京市中心，是明清两代的皇家宫殿。"
            "故宫始建于明永乐四年，建成于永乐十八年。"
            "故宫整体采用中轴对称布局，建筑等级严密。"
            "太和殿是故宫外朝三大殿之一。"
        ),
        encoding="utf-8",
    )

    splitter = DocumentSplitter(
        SplitterConfig(
            chunk_size=50,
            chunk_overlap=10,
            min_chunk_chars=20,
        )
    )

    service = PreprocessingService(
        parser_resolver=ParserFactory.get_parser,
        splitter=splitter
    )

    chunks = service.process(test_file)

    assert chunks
    assert all(
        isinstance(chunk, RagChunk)
        for chunk in chunks
    )

    for index, chunk in enumerate(chunks):
        assert chunk.chunk_index == index
        assert chunk.source == "heritage.txt"
        assert chunk.file_type == ".txt"
        assert chunk.content
        assert len(chunk.content) <= 50
        assert chunk.metadata["cleaning_version"]
        assert chunk.metadata["splitter_version"]


def test_unsupported_file_type(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test.xlsx"
    test_file.write_text("测试内容", encoding="utf-8")

    service = PreprocessingService(parser_resolver=ParserFactory.get_parser)

    try:
        service.process(test_file)
    except ValueError as exc:
        assert "暂不支持文件类型" in str(exc)
    else:
        raise AssertionError(
            "不支持的文件类型应当抛出 ValueError"
        )
