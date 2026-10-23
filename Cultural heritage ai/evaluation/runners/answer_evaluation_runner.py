"""Run controlled end-to-end answer evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings

from app.modules.rag.composition import (
    build_rag_retriever,
    build_vector_store,
)

from app.modules.tool.application.facade import ToolFacade

from evaluation.config.answer_variants import (
    ANSWER_VARIANTS,
)

from evaluation.datasets.answer_dataset import (
    load_answer_evaluation_cases,
)

from evaluation.reports.report_writer import (
    save_evaluation_report,
)

from evaluation.services.answer_variant_executor import (
    AnswerVariantExecutor,
)


# =========================================================
# 注意：
# 把这里改成你刚刚贴出来的 get_ai_agent()
# 实际所在文件。
#
# 如果你的文件路径例如：
#
# app/modules/agent/service.py
#
# 就写：
#
# from app.modules.agent.service import get_ai_agent
#
# 如果实际是 repository.py，则改成 repository。
# =========================================================

from app.modules.agent.application.facade import agent_facade

get_ai_agent = agent_facade.get_agent
build_tool_registry = ToolFacade.configured
build_tool_executor = ToolFacade.from_catalog


DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "datasets"
    / "answer_eval_v1.json"
)
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2, 4)


def _run_key(
    variant_name: str,
    case_id: str,
) -> str:
    return f"{variant_name}::{case_id}"


def _build_checkpoint_path(
    *,
    report_dir: Path | None,
    dataset_path: Path,
    ai_id: str,
    variant_name: str,
    limit: int | None,
    case_id: str | None,
) -> Path:
    actual_report_dir = (
        report_dir
        if report_dir is not None
        else Path(__file__).resolve().parents[1] / "reports"
    )

    if case_id is not None:
        scope = f"case-{case_id}"
    elif limit is not None:
        scope = f"limit-{limit}"
    else:
        scope = "all"

    return (
        actual_report_dir
        / "checkpoints"
        / (
            f"{dataset_path.stem}"
            f"__{ai_id}"
            f"__{variant_name}"
            f"__{scope}"
            ".checkpoint.json"
        )
    )


def _save_checkpoint(
    checkpoint_path: Path,
    *,
    ai_id: str,
    variant_name: str,
    dataset_path: Path,
    results: list[dict[str, Any]],
    run_errors: list[dict[str, Any]],
) -> None:
    checkpoint_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "experiment": {
            "type": "answer_evaluation_checkpoint",
            "ai_id": ai_id,
            "variant_name": variant_name,
            "dataset": str(dataset_path.resolve()),
        },
        "successful_run_count": len(results),
        "failed_run_count": len(run_errors),
        "results": results,
        "run_errors": run_errors,
    }

    # 先写临时文件再替换，避免程序恰好在写 checkpoint
    # 时退出，留下半截 JSON。
    temp_path = checkpoint_path.with_suffix(
        checkpoint_path.suffix + ".tmp"
    )

    temp_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    temp_path.replace(
        checkpoint_path
    )


def _load_checkpoint(
    checkpoint_path: Path,
    *,
    ai_id: str,
    variant_name: str,
    dataset_path: Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    payload = json.loads(
        checkpoint_path.read_text(
            encoding="utf-8"
        )
    )

    experiment = payload.get(
        "experiment",
        {},
    )

    expected_dataset = str(
        dataset_path.resolve()
    )

    if experiment.get("ai_id") != ai_id:
        raise RuntimeError(
            "Checkpoint AI ID does not match "
            f"current AI: {checkpoint_path}"
        )

    if (
        experiment.get("variant_name")
        != variant_name
    ):
        raise RuntimeError(
            "Checkpoint variant does not match "
            f"current variant: {checkpoint_path}"
        )

    if (
        experiment.get("dataset")
        != expected_dataset
    ):
        raise RuntimeError(
            "Checkpoint dataset does not match "
            f"current dataset: {checkpoint_path}"
        )

    results = list(
        payload.get(
            "results",
            [],
        )
    )

    run_errors = list(
        payload.get(
            "run_errors",
            [],
        )
    )

    return results, run_errors


async def main(
    *,
    ai_id: str,
    variant_name: str,
    dataset_path: Path,
    report_dir: Path | None,
    limit: int | None,
    case_id: str | None,
    resume: bool,
) -> None:

    # =====================================================
    # 1. Load Dataset
    # =====================================================

    cases = load_answer_evaluation_cases(
        dataset_path
    )

    if case_id is not None:
        cases = [
            case
            for case in cases
            if case.case_id == case_id
        ]

        if not cases:
            raise ValueError(
                f"Case not found: {case_id}"
            )

    if not cases:
        raise RuntimeError(
            "Answer evaluation dataset is empty"
        )

    if limit is not None:

        if limit <= 0:
            raise ValueError(
                "limit must be greater than 0"
            )

        cases = cases[:limit]

    # =====================================================
    # 2. Load exactly one AI
    #
    # 四组 Variant 都使用这个相同 AiAgent。
    # =====================================================

    ai = get_ai_agent(
        ai_id
    )

    if not ai.enabled:
        raise RuntimeError(
            f"AI is disabled: {ai_id}"
        )

    if not ai.api_url:
        raise RuntimeError(
            f"AI api_url is empty: {ai_id}"
        )

    model_name = (
        ai.model_name
        if (
            ai.model_name
            and ai.model_name.strip()
        )
        else ai.name
    )

    print("=" * 80)
    print("Answer Evaluation")
    print("=" * 80)

    print(
        f"AI ID: {ai.id}"
    )

    print(
        f"AI Name: {ai.name}"
    )

    print(
        f"Model: {model_name}"
    )

    print(
        f"Provider: {ai.provider}"
    )

    print(
        f"Credential: {ai.credential_ref}"
    )

    print(
        f"Generation config: "
        f"{ai.generation_config}"
    )

    print(
        f"Dataset: {dataset_path}"
    )

    print(
        f"Cases: {len(cases)}"
    )

    print("=" * 80)

    # =====================================================
    # 3. Resolve Variants
    # =====================================================

    if variant_name == "all":

        variants = list(
            ANSWER_VARIANTS.values()
        )

    else:

        variant = ANSWER_VARIANTS.get(
            variant_name
        )

        if variant is None:
            raise ValueError(
                f"Unknown variant: {variant_name}"
            )

        variants = [
            variant
        ]

    # =====================================================
    # 4. Build real production dependencies
    # =====================================================

    cfg = get_settings()

    store = await asyncio.to_thread(
        build_vector_store
    )

    tool_registry = (
        build_tool_registry()
    )

    tool_executor = (
        build_tool_executor(
            tool_registry
        )
    )

    checkpoint_path = _build_checkpoint_path(
        report_dir=report_dir,
        dataset_path=dataset_path,
        ai_id=ai_id,
        variant_name=variant_name,
        limit=limit,
        case_id=case_id,
    )

    all_results: list[dict[str, Any]] = []
    run_errors: list[dict[str, Any]] = []

    completed_keys: set[str] = set()

    if resume:

        if checkpoint_path.exists():

            (
                all_results,
                run_errors,
            ) = _load_checkpoint(
                checkpoint_path,
                ai_id=ai_id,
                variant_name=variant_name,
                dataset_path=dataset_path,
            )

            completed_keys = {
                _run_key(
                    str(result["variant"]),
                    str(result["case_id"]),
                )
                for result in all_results
            }

            print()
            print(
                f"Resume checkpoint: "
                f"{checkpoint_path}"
            )

            print(
                f"Loaded successful runs: "
                f"{len(all_results)}"
            )

            print(
                f"Previous failed runs: "
                f"{len(run_errors)}"
            )

        else:

            print()
            print(
                "Resume requested, but no "
                "checkpoint was found."
            )

            print(
                "Starting a fresh evaluation."
            )

    else:

        # 不带 --resume 表示主动开启一轮新实验。
        # 避免误读之前残留的 checkpoint。
        if checkpoint_path.exists():
            checkpoint_path.unlink()

    try:

        async with httpx.AsyncClient(
            timeout=(
                cfg.embedding_timeout_seconds
            ),
            trust_env=False,
        ) as client:

            # =============================================
            # 正式 RAG：
            #
            # Hybrid
            # -> Rerank
            # -> Final TopK
            # =============================================

            retriever = build_rag_retriever(
                client,
                vector_store=store,
            )

            executor = AnswerVariantExecutor(
                ai=ai,
                retriever=retriever,
                tool_registry=tool_registry,
                tool_executor=tool_executor,
                rag_top_k=5,
                tool_max_steps=5,
            )

            # =============================================
            # 5. Run
            # =============================================

            for variant in variants:

                print()
                print("=" * 80)

                print(
                    f"Variant: {variant.name}"
                )

                print(
                    f"RAG: "
                    f"{variant.use_rag}"
                )

                print(
                    f"Tool: "
                    f"{variant.use_tool}"
                )

                print("=" * 80)

                for index, case in enumerate(
                        cases,
                        start=1,
                ):

                    current_key = _run_key(
                        variant.name,
                        case.case_id,
                    )

                    # =========================================
                    # Resume:
                    # 已经成功完成的 run 直接跳过。
                    #
                    # 注意：
                    # 失败过的 run 不在 completed_keys 中，
                    # 所以 resume 时会再次执行。
                    # =========================================

                    if current_key in completed_keys:
                        print(
                            f"[{index}/{len(cases)}] "
                            f"{case.case_id} "
                            f"variant={variant.name} "
                            f"SKIPPED (checkpoint)"
                        )

                        continue

                    result = None
                    last_error: Exception | None = None

                    # =========================================
                    # Retry one case
                    #
                    # attempt 1
                    #   ↓ failed
                    # wait 2s
                    #
                    # attempt 2
                    #   ↓ failed
                    # wait 4s
                    #
                    # attempt 3
                    # =========================================

                    for attempt in range(
                            1,
                            MAX_ATTEMPTS + 1,
                    ):

                        try:

                            result = await executor.run(
                                case=case,
                                variant=variant,
                            )

                            break

                        except Exception as exc:

                            last_error = exc

                            print(
                                f"[{index}/{len(cases)}] "
                                f"{case.case_id} "
                                f"variant={variant.name} "
                                f"attempt={attempt} "
                                f"FAILED"
                            )

                            print(
                                f"Error: "
                                f"{type(exc).__name__}: "
                                f"{exc}"
                            )

                            if attempt < MAX_ATTEMPTS:
                                wait_seconds = (
                                    RETRY_BACKOFF_SECONDS[
                                        attempt - 1
                                        ]
                                )

                                print(
                                    f"Retrying in "
                                    f"{wait_seconds}s..."
                                )

                                await asyncio.sleep(
                                    wait_seconds
                                )

                    # =========================================
                    # Three attempts all failed:
                    #
                    # record error
                    # checkpoint
                    # continue next case
                    # =========================================

                    if result is None:
                        error_record = {
                            "case_id":
                                case.case_id,

                            "variant":
                                variant.name,

                            "error_type":
                                (
                                    type(last_error).__name__
                                    if last_error is not None
                                    else "UnknownError"
                                ),

                            "error":
                                (
                                    str(last_error)
                                    if last_error is not None
                                    else "Unknown error"
                                ),

                            "attempts":
                                MAX_ATTEMPTS,
                        }

                        # 如果这是 resume 后再次失败，
                        # 替换旧 error，避免同一 case 重复记录。
                        run_errors = [
                            error
                            for error in run_errors
                            if _run_key(
                                str(error["variant"]),
                                str(error["case_id"]),
                            )
                               != current_key
                        ]

                        run_errors.append(
                            error_record
                        )

                        _save_checkpoint(
                            checkpoint_path,
                            ai_id=ai_id,
                            variant_name=variant_name,
                            dataset_path=dataset_path,
                            results=all_results,
                            run_errors=run_errors,
                        )

                        print(
                            f"[{index}/{len(cases)}] "
                            f"{case.case_id} "
                            f"variant={variant.name} "
                            f"FAILED after "
                            f"{MAX_ATTEMPTS} attempts"
                        )

                        print(
                            "Continuing with next case."
                        )

                        print("-" * 80)

                        continue

                    # =========================================
                    # Success
                    # =========================================

                    result_dict = asdict(
                        result
                    )

                    all_results.append(
                        result_dict
                    )

                    completed_keys.add(
                        current_key
                    )

                    # 如果这个 case 曾经失败过，
                    # 现在已经成功，就移除旧的 error。
                    run_errors = [
                        error
                        for error in run_errors
                        if _run_key(
                            str(error["variant"]),
                            str(error["case_id"]),
                        )
                           != current_key
                    ]

                    # =========================================
                    # Every successful run is checkpointed
                    # immediately.
                    # =========================================

                    _save_checkpoint(
                        checkpoint_path,
                        ai_id=ai_id,
                        variant_name=variant_name,
                        dataset_path=dataset_path,
                        results=all_results,
                        run_errors=run_errors,
                    )

                    print(
                        f"[{index}/{len(cases)}] "
                        f"{case.case_id} "
                        f"variant={variant.name} "
                        f"latency={result.latency_ms:.2f}ms "
                        f"llm_requests={result.llm_request_count} "
                        f"tools={len(result.tool_calls)} "
                        f"tokens="
                        f"{result.input_tokens}/"
                        f"{result.output_tokens}/"
                        f"{result.total_tokens}"
                    )

                    print(
                        "Answer:"
                    )

                    print(
                        result.answer
                    )

                    if result.tool_calls:

                        print(
                            "Tool calls:"
                        )

                        for call in result.tool_calls:
                            print(
                                f"  - "
                                f"{call.tool_name} "
                                f"success="
                                f"{call.success}"
                            )

                    print("-" * 80)

    finally:

        await asyncio.to_thread(
            store.close
        )

    # =====================================================
    # 6. Build Report
    # =====================================================

    report = {

        "experiment": {
            "type":
                "answer_evaluation",

            "ai_id":
                ai.id,

            "ai_name":
                ai.name,

            "model_name":
                model_name,

            "provider":
                ai.provider,

            "credential_ref":
                ai.credential_ref,

            "generation_config":
                ai.generation_config,

            "dataset":
                str(dataset_path),

            "case_count":
                len(cases),

            "variants": [
                variant.name
                for variant in variants
            ],
        },

        "variant_configuration": {
            variant.name: {
                "use_rag":
                    variant.use_rag,

                "use_tool":
                    variant.use_tool,
            }
            for variant in variants
        },

        "successful_run_count":
            len(all_results),

        "failed_run_count":
            len(run_errors),

        "expected_run_count":
            len(cases) * len(variants),

        "results":
            all_results,

        "run_errors":
            run_errors,
    }

    # =====================================================
    # 7. Save Report
    # =====================================================

    report_variant = (
        "all"
        if variant_name == "all"
        else variant_name
    )

    path = save_evaluation_report(
        category="answer",
        variant=report_variant,
        report=report,
        report_dir=report_dir,
    )

    print()
    print("=" * 80)

    print(
        f"Successful runs: "
        f"{len(all_results)}"
    )

    print(
        f"Failed runs: "
        f"{len(run_errors)}"
    )

    print(
        f"Report saved: {path}"
    )

    print(
        f"Checkpoint: {checkpoint_path}"
    )

    print("=" * 80)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description=__doc__,
    )

    parser.add_argument(
        "--ai-id",
        required=True,
        help=(
            "AiAgent ID used by every "
            "evaluation variant."
        ),
    )

    parser.add_argument(
        "--variant",
        choices=[
            "llm_only",
            "llm_rag",
            "llm_tool",
            "llm_rag_tool",
            "all",
        ],
        default="all",
    )

    parser.add_argument(
        "--dataset",
        type=Path,
        default=DATASET_PATH,
    )

    parser.add_argument(
        "--report-dir",
        type=Path,
    )

    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Only run the first N cases. "
            "Useful for smoke tests."
        ),
    )

    parser.add_argument(
        "--case-id",
        help=(
            "Run only one evaluation case "
            "with the specified case ID."
        ),
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume from the checkpoint and "
            "skip already completed runs."
        ),
    )

    args = parser.parse_args()

    asyncio.run(
        main(
            ai_id=args.ai_id,
            variant_name=args.variant,
            dataset_path=args.dataset,
            report_dir=args.report_dir,
            limit=args.limit,
            case_id=args.case_id,
            resume=args.resume,
        )
    )
