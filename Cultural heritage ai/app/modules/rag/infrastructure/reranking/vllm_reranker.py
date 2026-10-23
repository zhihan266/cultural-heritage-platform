"""HTTP adapter for the vLLM-compatible rerank response contract."""

from __future__ import annotations

import math
from typing import Sequence

import httpx

from app.modules.rag.domain.reranker import RerankResult, Reranker


class VllmReranker(Reranker):
    def __init__(
        self, *, http_client: httpx.AsyncClient, base_url: str,
        model_name: str, timeout_seconds: float,
    ) -> None:
        if not base_url.strip() or not model_name.strip():
            raise ValueError("Reranker URL and model must not be empty")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Reranker timeout must be finite and greater than 0")
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds

    async def rerank(
        self, *, query: str, documents: Sequence[str], top_k: int,
    ) -> list[RerankResult]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")
        if not documents:
            return []
        document_list = list(documents)
        final_top_k = min(top_k, len(document_list))
        try:
            response = await self._http_client.post(
                f"{self._base_url}/v1/rerank",
                json={
                    "model": self._model_name, "query": query.strip(),
                    "documents": document_list, "top_n": final_top_k,
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RuntimeError("Reranker request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Reranker returned HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError("Cannot connect to the reranker service") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Reranker returned invalid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise RuntimeError("Reranker response must contain a results list")
        results = []
        indexes: set[int] = set()
        for item in data["results"]:
            if not isinstance(item, dict):
                raise RuntimeError("Invalid reranker result item")
            index, score = item.get("index"), item.get("relevance_score")
            if type(index) is not int or not 0 <= index < len(document_list):
                raise RuntimeError("Invalid reranker result index")
            if index in indexes:
                raise RuntimeError("Duplicate reranker result index")
            if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
                raise RuntimeError("Invalid reranker relevance score")
            indexes.add(index)
            results.append(RerankResult(index=index, score=float(score)))
        return sorted(results, key=lambda result: result.score, reverse=True)[:final_top_k]
