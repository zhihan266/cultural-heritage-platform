from pathlib import Path

from evaluation.config.answer_variants import (
    ANSWER_VARIANTS,
)
from evaluation.datasets.answer_dataset import (
    load_answer_evaluation_cases,
)


def test_four_answer_variants_exist():

    assert set(
        ANSWER_VARIANTS
    ) == {
        "llm_only",
        "llm_rag",
        "llm_tool",
        "llm_rag_tool",
    }


def test_answer_variant_matrix():

    assert (
        ANSWER_VARIANTS[
            "llm_only"
        ].use_rag
        is False
    )

    assert (
        ANSWER_VARIANTS[
            "llm_only"
        ].use_tool
        is False
    )

    assert (
        ANSWER_VARIANTS[
            "llm_rag"
        ].use_rag
        is True
    )

    assert (
        ANSWER_VARIANTS[
            "llm_rag"
        ].use_tool
        is False
    )

    assert (
        ANSWER_VARIANTS[
            "llm_tool"
        ].use_rag
        is False
    )

    assert (
        ANSWER_VARIANTS[
            "llm_tool"
        ].use_tool
        is True
    )

    assert (
        ANSWER_VARIANTS[
            "llm_rag_tool"
        ].use_rag
        is True
    )

    assert (
        ANSWER_VARIANTS[
            "llm_rag_tool"
        ].use_tool
        is True
    )


def test_load_answer_dataset():

    path = Path(
        "evaluation/datasets/"
        "answer_eval_v1.json"
    )

    cases = (
        load_answer_evaluation_cases(
            path
        )
    )

    assert cases

    assert cases[0].case_id
    assert cases[0].question