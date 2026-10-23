"""Load answer evaluation datasets."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.models.answer_evaluation import (
    AnswerEvaluationCase,
)


def load_answer_evaluation_cases(
    path: Path,
) -> list[AnswerEvaluationCase]:

    raw_cases = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(
        raw_cases,
        list,
    ):
        raise ValueError(
            "Answer evaluation dataset "
            "must be a JSON list"
        )

    cases: list[
        AnswerEvaluationCase
    ] = []

    for item in raw_cases:

        case_id = item.get(
            "id"
        )

        question = item.get(
            "question"
        )

        reference_answer = item.get(
            "reference_answer"
        )

        if not case_id:
            raise ValueError(
                f"Missing id: {item}"
            )

        if not question:
            raise ValueError(
                f"{case_id} has no question"
            )

        if reference_answer is None:
            raise ValueError(
                f"{case_id} has no "
                "reference_answer"
            )

        cases.append(
            AnswerEvaluationCase(
                case_id=case_id,
                question=question,
                reference_answer=(
                    reference_answer
                ),
                category=item.get(
                    "category",
                    "unknown",
                ),
                difficulty=item.get(
                    "difficulty",
                    "unknown",
                ),
                requires_rag=bool(
                    item.get(
                        "requires_rag",
                        False,
                    )
                ),
                requires_tool=bool(
                    item.get(
                        "requires_tool",
                        False,
                    )
                ),
                expected_tools=list(
                    item.get(
                        "expected_tools",
                        [],
                    )
                ),
                metadata=dict(
                    item.get(
                        "metadata",
                        {},
                    )
                ),
            )
        )

    return cases