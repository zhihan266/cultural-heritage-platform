from evaluation.config.answer_variants import (
    ANSWER_VARIANTS,
)


def test_variant_capabilities():

    llm_only = (
        ANSWER_VARIANTS["llm_only"]
    )

    assert llm_only.use_rag is False
    assert llm_only.use_tool is False


    llm_rag = (
        ANSWER_VARIANTS["llm_rag"]
    )

    assert llm_rag.use_rag is True
    assert llm_rag.use_tool is False


    llm_tool = (
        ANSWER_VARIANTS["llm_tool"]
    )

    assert llm_tool.use_rag is False
    assert llm_tool.use_tool is True


    llm_rag_tool = (
        ANSWER_VARIANTS[
            "llm_rag_tool"
        ]
    )

    assert llm_rag_tool.use_rag is True
    assert llm_rag_tool.use_tool is True