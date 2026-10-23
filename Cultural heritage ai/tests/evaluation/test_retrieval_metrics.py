from evaluation.metrics.retrieval_metrics import (
    mean,
    recall_at_k,
    reciprocal_rank,
)


def test_recall_at_k() -> None:
    relevant = [
        "forbidden_city"
    ]

    retrieved = [
        "temple_of_heaven",
        "forbidden_city",
        "great_wall",
    ]

    # Top1 没有正确文档。
    assert recall_at_k(
        relevant,
        retrieved,
        k=1,
    ) == 0.0

    # Top3 已经包含正确文档。
    assert recall_at_k(
        relevant,
        retrieved,
        k=3,
    ) == 1.0


def test_reciprocal_rank() -> None:
    relevant = [
        "forbidden_city"
    ]

    retrieved = [
        "temple_of_heaven",
        "forbidden_city",
        "great_wall",
    ]

    # 正确文档排名第二。
    #
    # RR = 1 / 2 = 0.5
    assert reciprocal_rank(
        relevant,
        retrieved,
    ) == 0.5


def test_mean() -> None:
    values = [
        1.0,
        0.5,
        0.0,
    ]

    assert mean(
        values
    ) == 0.5