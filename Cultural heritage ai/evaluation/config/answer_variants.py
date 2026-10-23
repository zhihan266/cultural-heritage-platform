"""Experiment variants for answer evaluation."""

from evaluation.models.answer_evaluation import (
    AnswerVariantConfig,
)


LLM_ONLY = AnswerVariantConfig(
    name="llm_only",
    use_rag=False,
    use_tool=False,
)


LLM_RAG = AnswerVariantConfig(
    name="llm_rag",
    use_rag=True,
    use_tool=False,
)


LLM_TOOL = AnswerVariantConfig(
    name="llm_tool",
    use_rag=False,
    use_tool=True,
)


LLM_RAG_TOOL = AnswerVariantConfig(
    name="llm_rag_tool",
    use_rag=True,
    use_tool=True,
)


ANSWER_VARIANTS = {
    variant.name: variant
    for variant in (
        LLM_ONLY,
        LLM_RAG,
        LLM_TOOL,
        LLM_RAG_TOOL,
    )
}