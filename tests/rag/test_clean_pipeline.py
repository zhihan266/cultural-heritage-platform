import pytest

from app.modules.rag.domain.models import RagDocument
from app.modules.rag.application.preprocessing.cleaner_config import CleanerConfig
from app.modules.rag.application.preprocessing.cleaner import DocumentCleaner



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
        },
    )


def test_clean_document_success() -> None:
    raw_document = create_document(
        "\ufeff故宫   位于北京市中心。\r\n"
        "\r\n"
        "\r\n"
        "故宫\t始建于明永乐四年。\u200b"
    )

    cleaner = DocumentCleaner()

    cleaned_documents = cleaner.clean(
        [raw_document]
    )

    assert len(cleaned_documents) == 1

    cleaned_document = cleaned_documents[0]

    assert cleaned_document.content == (
        "故宫 位于北京市中心。\n\n"
        "故宫 始建于明永乐四年。"
    )

    assert (
        cleaned_document.document_id
        == raw_document.document_id
    )

    assert (
        cleaned_document.source
        == raw_document.source
    )

    assert (
        cleaned_document.file_type
        == raw_document.file_type
    )

    assert (
        cleaned_document.metadata[
            "cleaning_version"
        ]
        == "basic-v1"
    )

    # 原始对象没有被修改。
    assert "\ufeff" in raw_document.content


def test_empty_document_is_rejected() -> None:
    raw_document = create_document(
        " \n\t\u200b\ufeff "
    )

    cleaner = DocumentCleaner()

    with pytest.raises(
        ValueError,
        match="文档清理后没有剩余可用内容",
    ):
        cleaner.clean([raw_document])


def test_empty_list_returns_empty_list() -> None:
    cleaner = DocumentCleaner()

    assert cleaner.clean([]) == []


def test_custom_min_content_chars() -> None:
    document = create_document("故宫")

    cleaner = DocumentCleaner(
        CleanerConfig(
            min_content_chars=3,
        )
    )

    with pytest.raises(ValueError):
        cleaner.clean([document])
