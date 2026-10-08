from app.modules.rag.domain.models import RagDocument
from app.modules.rag.application.preprocessing.splitter import DocumentSplitter
from app.modules.rag.application.preprocessing.splitter import SplitterConfig


def create_document(
    content: str,
) -> RagDocument:
    return RagDocument(
        document_id="doc-001",
        content=content,
        source="test.txt",
        file_type=".txt",
        metadata={
            "file_path": "data/test.txt",
            "category": "文化遗产",
        },
    )


def test_short_document_produces_one_chunk() -> None:
    document = create_document(
        "故宫位于北京市中心。"
    )

    splitter = DocumentSplitter(
        SplitterConfig(
            chunk_size=100,
            chunk_overlap=20,
            min_chunk_chars=20,
        )
    )

    chunks = splitter.split([document])

    assert len(chunks) == 1

    chunk = chunks[0]

    assert chunk.content == document.content
    assert chunk.document_id == document.document_id
    assert chunk.chunk_index == 0
    assert chunk.start_char == 0
    assert chunk.end_char == len(document.content)
    assert chunk.source == "test.txt"

    assert (
        chunk.metadata["category"]
        == "文化遗产"
    )


def test_long_document_produces_multiple_chunks() -> None:
    content = (
        "故宫位于北京市中心，是明清两代的皇家宫殿。"
        "故宫始建于明永乐四年，建成于永乐十八年。"
        "故宫整体采用中轴对称布局，建筑等级严密。"
        "太和殿是故宫外朝三大殿之一。"
        "故宫收藏了大量珍贵文物和历史档案。"
    )

    document = create_document(content)

    splitter = DocumentSplitter(
        SplitterConfig(
            chunk_size=50,
            chunk_overlap=10,
            min_chunk_chars=20,
            boundary_search_ratio=0.5,
        )
    )

    chunks = splitter.split([document])

    assert len(chunks) > 1

    for expected_index, chunk in enumerate(chunks):
        assert chunk.chunk_index == expected_index
        assert len(chunk.content) <= 50

        # 验证记录的位置确实对应原始正文。
        assert (
            document.content[
                chunk.start_char:chunk.end_char
            ]
            == chunk.content
        )

    # 相邻切片之间应当存在重叠区间。
    assert (
        chunks[1].start_char
        < chunks[0].end_char
    )


def test_chunk_ids_are_stable() -> None:
    document = create_document(
        "故宫位于北京市中心。"
        "故宫始建于明永乐四年。"
        "故宫是世界文化遗产。"
    )

    splitter = DocumentSplitter(
        SplitterConfig(
            chunk_size=25,
            chunk_overlap=5,
            min_chunk_chars=10,
        )
    )

    first_result = splitter.split([document])
    second_result = splitter.split([document])

    assert [
        chunk.chunk_id
        for chunk in first_result
    ] == [
        chunk.chunk_id
        for chunk in second_result
    ]


def test_empty_document_is_rejected() -> None:
    document = create_document(" \n\t ")

    splitter = DocumentSplitter()

    try:
        splitter.split([document])
    except ValueError as exc:
        assert "文档内容为空" in str(exc)
    else:
        raise AssertionError(
            "空文档应该抛出 ValueError"
        )
