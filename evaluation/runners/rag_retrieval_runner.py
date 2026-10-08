"""Evaluate an explicit retrieval strategy without overwriting historical reports."""
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
from app.modules.rag.composition import (
    build_dense_retriever, build_hybrid_retriever, build_rag_retriever, build_vector_store,
)
from app.modules.rag.domain.retriever import Retrieval
from app.modules.rag.domain.vector_models import VectorSearchHit
from evaluation.metrics.retrieval_metrics import mean, recall_at_k, reciprocal_rank
from evaluation.models.retrieval import (
    RetrievalCaseResult, RetrievalEvaluationCase, RetrievalEvaluationSummary,
)
from evaluation.reports.report_writer import save_evaluation_report

TOP_K_VALUES = [1, 3, 5]
MAX_TOP_K = max(TOP_K_VALUES)
DATASET_PATH = Path(__file__).resolve().parents[1] / "datasets" / "rag_retrieval_cases.json"


def load_evaluation_cases(path: Path = DATASET_PATH) -> list[RetrievalEvaluationCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_cases, list):
        raise ValueError("Evaluation dataset must be a JSON list")
    return [RetrievalEvaluationCase(
        case_id=item["case_id"], question=item["question"], category=item["category"],
        relevant_document_ids=item["relevant_document_ids"],
    ) for item in raw_cases]


def unique_document_ids(hits: list[VectorSearchHit]) -> list[str]:
    # Metrics remain document-level, even when several returned chunks share a document.
    return list(dict.fromkeys(hit.document_id for hit in hits))


async def evaluate_case(retriever: Retrieval, case: RetrievalEvaluationCase) -> RetrievalCaseResult:
    started_at = time.perf_counter()
    hits = await retriever.retrieve(query=case.question, top_k=MAX_TOP_K, document_id=None)
    latency_ms = (time.perf_counter() - started_at) * 1000
    document_ids = unique_document_ids(hits)
    return RetrievalCaseResult(
        case_id=case.case_id, question=case.question, category=case.category,
        relevant_document_ids=case.relevant_document_ids, retrieved_document_ids=document_ids,
        recall_at_k={k: recall_at_k(case.relevant_document_ids, document_ids, k=k) for k in TOP_K_VALUES},
        reciprocal_rank=reciprocal_rank(case.relevant_document_ids, document_ids),
        latency_ms=latency_ms,
    )


def build_category_summary(results: list[RetrievalCaseResult]) -> dict[str, dict]:
    grouped: dict[str, list[RetrievalCaseResult]] = defaultdict(list)
    for result in results:
        grouped[result.category].append(result)
    return {category: {
        "total_cases": len(items),
        "recall_at_k": {k: mean([item.recall_at_k[k] for item in items]) for k in TOP_K_VALUES},
        "mrr": mean([item.reciprocal_rank for item in items]),
        "average_latency_ms": mean([item.latency_ms for item in items]),
    } for category, items in grouped.items()}


async def main(strategy: str, dataset: Path, report_dir: Path | None = None) -> None:
    cases = load_evaluation_cases(dataset)
    if not cases:
        raise RuntimeError("Evaluation dataset is empty")
    cfg = get_settings()
    store = await asyncio.to_thread(build_vector_store)
    try:
        async with httpx.AsyncClient(timeout=cfg.embedding_timeout_seconds, trust_env=False) as client:
            builders = {"dense": build_dense_retriever, "hybrid": build_hybrid_retriever,
                        "hybrid_rerank": build_rag_retriever}
            retriever = builders[strategy](client, vector_store=store)
            results = []
            for index, case in enumerate(cases, start=1):
                result = await evaluate_case(retriever, case)
                results.append(result)
                print(f"[{index}/{len(cases)}] {case.case_id}: RR={result.reciprocal_rank:.4f}")
    finally:
        await asyncio.to_thread(store.close)
    summary = RetrievalEvaluationSummary(
        experiment_name=strategy, total_cases=len(results),
        recall_at_k={k: mean([result.recall_at_k[k] for result in results]) for k in TOP_K_VALUES},
        mrr=mean([result.reciprocal_rank for result in results]),
        average_latency_ms=mean([result.latency_ms for result in results]),
    )
    report = {
        "summary": asdict(summary), "category_summary": build_category_summary(results),
        "cases": [asdict(result) for result in results],
        # Deliberately whitelist reproducibility data; never serialize Settings or service URLs.
        "configuration": {
            "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "top_k_values": TOP_K_VALUES, "embedding_model": cfg.embedding_model,
            "embedding_dimension": cfg.embedding_dimension,
            "query_instruction": cfg.embedding_query_instruction,
            "reranker_model": cfg.reranker_model if strategy == "hybrid_rerank" else None,
            "candidate_top_k": cfg.reranker_candidate_top_k if strategy != "dense" else MAX_TOP_K,
            "rrf_k": 60 if strategy != "dense" else None,
        },
    }
    path = save_evaluation_report(category="rag", variant=strategy, report=report, report_dir=report_dir)
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    print(f"Report saved: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=["dense", "hybrid", "hybrid_rerank"], default="dense")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args()
    asyncio.run(main(args.strategy, args.dataset, args.report_dir))
