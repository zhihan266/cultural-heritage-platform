"""
Evaluate an explicit RAG retrieval strategy
without overwriting historical reports.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.modules.rag.domain.retriever import Retrieval
from app.modules.rag.domain.vector_models import VectorSearchHit

from evaluation.metrics.retrieval_metrics import (
    hit_at_k,
    mean,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
)
from evaluation.models.retrieval import (
    RetrievalCaseResult,
    RetrievalEvaluationCase,
    RetrievalEvaluationSummary,
)
from evaluation.reports.report_writer import (
    save_evaluation_report,
)

from app.modules.rag.composition import (
    build_bm25_retriever,
    build_dense_retriever,
    build_hybrid_retriever,
    build_rag_retriever,
    build_vector_store,
)




TOP_K_VALUES = [1, 3, 5, 10]

MAX_TOP_K = max(TOP_K_VALUES)

DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "datasets"
    / "rag_eval_v1.json"
)


def load_evaluation_cases(
    path: Path = DATASET_PATH,
) -> list[RetrievalEvaluationCase]:
    """
    加载 Retrieval Evaluation Dataset。

    同时兼容：

    新格式：
        id
        query

    旧格式：
        case_id
        question
    """
    raw_cases = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(raw_cases, list):
        raise ValueError(
            "Evaluation dataset must be a JSON list"
        )

    cases: list[
        RetrievalEvaluationCase
    ] = []

    for item in raw_cases:

        case_id = (
            item.get("id")
            or item.get("case_id")
        )

        question = (
            item.get("query")
            or item.get("question")
        )

        if not case_id:
            raise ValueError(
                f"Missing id/case_id: {item}"
            )

        if not question:
            raise ValueError(
                f"Missing query/question: {item}"
            )

        relevant_document_ids = item.get(
            "relevant_document_ids",
            [],
        )

        relevant_chunk_ids = item.get(
            "relevant_chunk_ids",
            [],
        )

        # 正式 rag_eval_v1 必须具有 chunk-level Ground Truth
        if not relevant_chunk_ids:
            raise ValueError(
                f"{case_id} has no relevant_chunk_ids. "
                "Formal benchmark requires chunk-level ground truth."
            )

        if not relevant_document_ids:
            raise ValueError(
                f"{case_id} has no relevant_document_ids"
            )

        cases.append(
            RetrievalEvaluationCase(
                case_id=case_id,
                question=question,
                category=item.get(
                    "category",
                    "unknown",
                ),
                difficulty=item.get(
                    "difficulty",
                    "unknown",
                ),
                relevant_sources=item.get(
                    "relevant_sources",
                    [],
                ),
                relevant_document_ids=(
                    relevant_document_ids
                ),
                relevant_chunk_ids=(
                    relevant_chunk_ids
                ),
                reference_answer=item.get(
                    "reference_answer"
                ),
            )
        )

    return cases


def unique_chunk_ids(
    hits: list[VectorSearchHit],
) -> list[str]:
    """
    保留 Retriever 的排名顺序，
    同时去除重复 chunk_id。
    """

    chunk_ids: list[str] = []

    for hit in hits:

        chunk_id = getattr(
            hit,
            "chunk_id",
            None,
        )

        if not chunk_id:
            raise AttributeError(
                "VectorSearchHit does not contain "
                "'chunk_id'. "
                "Please check VectorSearchHit definition."
            )

        if chunk_id not in chunk_ids:
            chunk_ids.append(
                chunk_id
            )

    return chunk_ids


def unique_document_ids(
    hits: list[VectorSearchHit],
) -> list[str]:
    """
    保留文档首次出现的排名顺序。

    document-level 只作为辅助指标。
    """
    return list(
        dict.fromkeys(
            hit.document_id
            for hit in hits
        )
    )

def print_rerank_trace(
    retriever: Retrieval,
    case: RetrievalEvaluationCase,
) -> None:
    """
    打印 Reranker 的完整排名变化。

    只用于 evaluation/debug。
    """

    trace = getattr(
        retriever,
        "last_trace",
        None,
    )

    if not trace:
        print(
            f"No rerank trace available "
            f"for {case.case_id}"
        )
        return

    relevant_chunk_ids = set(
        case.relevant_chunk_ids
    )

    print()
    print("=" * 100)
    print(
        f"RERANK TRACE: {case.case_id}"
    )
    print(
        f"Query: {case.question}"
    )
    print("=" * 100)

    for item in trace:

        chunk_id = item["chunk_id"]

        is_relevant = (
            chunk_id
            in relevant_chunk_ids
        )

        print(
            f"original_rank="
            f"{item['original_rank']:>2} "
            f"-> "
            f"final_rank="
            f"{item['final_rank']:>2} "
            f"delta="
            f"{item['rank_delta']:>3} "
            f"score="
            f"{item['reranker_score']:.6f} "
            f"relevant="
            f"{is_relevant}"
        )

        print(
            f"chunk_id="
            f"{chunk_id}"
        )

        print(
            f"document_id="
            f"{item['document_id']}"
        )

        content = str(
            item["content"]
        )

        print(
            "content="
            + content[:500]
        )

        print("-" * 100)

    print()

async def evaluate_case(
    retriever: Retrieval,
    case: RetrievalEvaluationCase,
) -> RetrievalCaseResult:

    started_at = time.perf_counter()

    hits = await retriever.retrieve(
        query=case.question,
        top_k=MAX_TOP_K,
        document_id=None,
    )

    latency_ms = (
        time.perf_counter()
        - started_at
    ) * 1000

    retrieved_chunk_ids = (
        unique_chunk_ids(hits)
    )

    retrieved_document_ids = (
        unique_document_ids(hits)
    )

    return RetrievalCaseResult(

        case_id=case.case_id,

        question=case.question,

        category=case.category,

        difficulty=case.difficulty,

        # =========================
        # Chunk-level
        # =========================

        relevant_chunk_ids=(
            case.relevant_chunk_ids
        ),

        retrieved_chunk_ids=(
            retrieved_chunk_ids
        ),

        chunk_recall_at_k={
            k: recall_at_k(
                case.relevant_chunk_ids,
                retrieved_chunk_ids,
                k=k,
            )
            for k in TOP_K_VALUES
        },

        chunk_hit_at_k={
            k: hit_at_k(
                case.relevant_chunk_ids,
                retrieved_chunk_ids,
                k=k,
            )
            for k in TOP_K_VALUES
        },

        chunk_ndcg_at_k={
            k: ndcg_at_k(
                case.relevant_chunk_ids,
                retrieved_chunk_ids,
                k=k,
            )
            for k in TOP_K_VALUES
        },

        chunk_reciprocal_rank=(
            reciprocal_rank(
                case.relevant_chunk_ids,
                retrieved_chunk_ids,
            )
        ),

        # =========================
        # Document-level
        # =========================

        relevant_document_ids=(
            case.relevant_document_ids
        ),

        retrieved_document_ids=(
            retrieved_document_ids
        ),

        document_recall_at_k={
            k: recall_at_k(
                case.relevant_document_ids,
                retrieved_document_ids,
                k=k,
            )
            for k in TOP_K_VALUES
        },

        document_hit_at_k={
            k: hit_at_k(
                case.relevant_document_ids,
                retrieved_document_ids,
                k=k,
            )
            for k in TOP_K_VALUES
        },

        document_reciprocal_rank=(
            reciprocal_rank(
                case.relevant_document_ids,
                retrieved_document_ids,
            )
        ),

        latency_ms=latency_ms,
    )


def summarize_results(
    results: list[RetrievalCaseResult],
) -> dict:
    """
    给一组 Case Result 计算统一 Summary。

    既可用于：

        全部数据

    也可用于：

        某个 category
        某个 difficulty
    """

    if not results:
        return {}

    latencies = [
        result.latency_ms
        for result in results
    ]

    return {

        "total_cases": len(results),

        # =========================
        # Chunk-level
        # =========================

        "chunk_recall_at_k": {
            k: mean([
                result.chunk_recall_at_k[k]
                for result in results
            ])
            for k in TOP_K_VALUES
        },

        "chunk_hit_at_k": {
            k: mean([
                result.chunk_hit_at_k[k]
                for result in results
            ])
            for k in TOP_K_VALUES
        },

        "chunk_ndcg_at_k": {
            k: mean([
                result.chunk_ndcg_at_k[k]
                for result in results
            ])
            for k in TOP_K_VALUES
        },

        "chunk_mrr": mean([
            result.chunk_reciprocal_rank
            for result in results
        ]),

        # =========================
        # Document-level
        # =========================

        "document_recall_at_k": {
            k: mean([
                result.document_recall_at_k[k]
                for result in results
            ])
            for k in TOP_K_VALUES
        },

        "document_hit_at_k": {
            k: mean([
                result.document_hit_at_k[k]
                for result in results
            ])
            for k in TOP_K_VALUES
        },

        "document_mrr": mean([
            result.document_reciprocal_rank
            for result in results
        ]),

        # =========================
        # Latency
        # =========================

        "average_latency_ms": mean(
            latencies
        ),

        "p95_latency_ms": percentile(
            latencies,
            95,
        ),
    }


def build_category_summary(
    results: list[RetrievalCaseResult],
) -> dict[str, dict]:

    grouped: dict[
        str,
        list[RetrievalCaseResult],
    ] = defaultdict(list)

    for result in results:
        grouped[
            result.category
        ].append(result)

    return {
        category: summarize_results(items)
        for category, items
        in grouped.items()
    }


def build_difficulty_summary(
    results: list[RetrievalCaseResult],
) -> dict[str, dict]:

    grouped: dict[
        str,
        list[RetrievalCaseResult],
    ] = defaultdict(list)

    for result in results:
        grouped[
            result.difficulty
        ].append(result)

    return {
        difficulty: summarize_results(items)
        for difficulty, items
        in grouped.items()
    }


async def main(
    strategy: str,
    dataset: Path,
    report_dir: Path | None = None,
    rerank_metadata: bool = False,
) -> None:

    cases = load_evaluation_cases(
        dataset
    )

    if not cases:
        raise RuntimeError(
            "Evaluation dataset is empty"
        )

    cfg = get_settings()

    store = await asyncio.to_thread(
        build_vector_store
    )

    try:

        async with httpx.AsyncClient(
            timeout=(
                cfg.embedding_timeout_seconds
            ),
            trust_env=False,
        ) as client:

            if strategy == "bm25":
                retriever = build_bm25_retriever(
                    vector_store=store,
                )
            else:
                builders = {
                    "dense": build_dense_retriever,
                    "hybrid": build_hybrid_retriever,
                    "hybrid_rerank": build_rag_retriever,
                }

                retriever = builders[strategy](
                    client,
                    vector_store=store,
                )

            if strategy == "hybrid_rerank":

                if not hasattr(
                        retriever,
                        "rerank_with_metadata",
                ):
                    raise RuntimeError(
                        "RerankedRetriever does not support "
                        "rerank_with_metadata"
                    )

                retriever.rerank_with_metadata = (
                    rerank_metadata
                )

                print(
                    "Rerank metadata enrichment: "
                    f"{'enabled' if rerank_metadata else 'disabled'}"
                )

            # 添加热身检索减少刚开始的慢查询影响结果
            print("Warming up retriever...")

            warmup_queries = [
                "故宫有哪些代表性建筑？",
                "都江堰的主要作用是什么？",
                "京剧有哪些主要行当？",
            ]

            for query in warmup_queries:
                await retriever.retrieve(
                    query=query,
                    top_k=MAX_TOP_K,
                    document_id=None,
                )

            print("Warm-up completed.")

            results: list[
                RetrievalCaseResult
            ] = []

            for index, case in enumerate(
                    cases,
                    start=1,
            ):

                # =========================================
                # Reranker Debug Trace
                # 目前只跟踪 hard_009
                # =========================================

                trace_this_case = (
                        strategy == "hybrid_rerank"
                        and case.case_id == "hard_009"
                )

                if hasattr(
                        retriever,
                        "trace_enabled",
                ):
                    retriever.trace_enabled = (
                        trace_this_case
                    )

                # =========================================
                # 正常 Evaluation
                # =========================================

                result = await evaluate_case(
                    retriever,
                    case,
                )

                results.append(
                    result
                )

                print(
                    f"[{index}/{len(cases)}] "
                    f"{case.case_id} "
                    f"chunk_RR="
                    f"{result.chunk_reciprocal_rank:.4f} "
                    f"doc_RR="
                    f"{result.document_reciprocal_rank:.4f} "
                    f"latency="
                    f"{result.latency_ms:.2f}ms"
                )

                # =========================================
                # 只打印 hard_009 的完整排名变化
                # =========================================

                if trace_this_case:
                    print_rerank_trace(
                        retriever,
                        case,
                    )

    finally:

        await asyncio.to_thread(
            store.close
        )

    overall = summarize_results(
        results
    )

    summary = RetrievalEvaluationSummary(

        experiment_name=strategy,

        total_cases=len(results),

        chunk_recall_at_k=(
            overall[
                "chunk_recall_at_k"
            ]
        ),

        chunk_hit_at_k=(
            overall[
                "chunk_hit_at_k"
            ]
        ),

        chunk_ndcg_at_k=(
            overall[
                "chunk_ndcg_at_k"
            ]
        ),

        chunk_mrr=(
            overall[
                "chunk_mrr"
            ]
        ),

        document_recall_at_k=(
            overall[
                "document_recall_at_k"
            ]
        ),

        document_hit_at_k=(
            overall[
                "document_hit_at_k"
            ]
        ),

        document_mrr=(
            overall[
                "document_mrr"
            ]
        ),

        average_latency_ms=(
            overall[
                "average_latency_ms"
            ]
        ),

        p95_latency_ms=(
            overall[
                "p95_latency_ms"
            ]
        ),
    )

    report = {

        "summary":
            asdict(summary),

        "category_summary":
            build_category_summary(
                results
            ),

        "difficulty_summary":
            build_difficulty_summary(
                results
            ),

        "cases": [
            asdict(result)
            for result in results
        ],

        "configuration": {

            "dataset_sha256":
                hashlib.sha256(
                    dataset.read_bytes()
                ).hexdigest(),

            "evaluation_level":
                "chunk_primary_document_auxiliary",

            "top_k_values":
                TOP_K_VALUES,

            "embedding_model":
                cfg.embedding_model,

            "embedding_dimension":
                cfg.embedding_dimension,

            "query_instruction":
                cfg.embedding_query_instruction,

            "reranker_model":
                (
                    cfg.reranker_model
                    if strategy
                    == "hybrid_rerank"
                    else None
                ),

            "candidate_top_k":
                (
                    cfg.reranker_candidate_top_k
                    if strategy
                    == "hybrid_rerank"
                    else None
                ),

            "rerank_metadata_enabled": (
                rerank_metadata
                if strategy == "hybrid_rerank"
                else None
            ),

            "rrf_k":
                (
                    60
                    if strategy
                    in {
                        "hybrid",
                        "hybrid_rerank",
                    }
                    else None
                ),
        },
    }

    report_variant = strategy

    if (
            strategy == "hybrid_rerank"
            and rerank_metadata
    ):
        report_variant = (
            "hybrid_rerank_metadata"
        )

    path = save_evaluation_report(
        category="rag",
        variant=report_variant,
        report=report,
        report_dir=report_dir,
    )

    print(
        json.dumps(
            asdict(summary),
            ensure_ascii=False,
            indent=2,
        )
    )

    print(
        f"Report saved: {path}"
    )


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description=__doc__,
    )

    parser.add_argument(
        "--strategy",
        choices=[
            "dense",
            "bm25",
            "hybrid",
            "hybrid_rerank",
        ],
        default="dense",
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
        "--rerank-metadata",
        action="store_true",
        help=(
            "Include document metadata in "
            "reranker input."
        ),
    )

    args = parser.parse_args()

    asyncio.run(
        main(
            args.strategy,
            args.dataset,
            args.report_dir,
            args.rerank_metadata,
        )
    )