"""OpenAI 兼容 Embedding 服务的 HTTP 适配器。

实现文档与查询向量生成，查询端附加检索指令，
并校验响应向量的数量、索引、维度与有限数值，供应用层向量服务使用。
"""

from __future__ import annotations

import math

import httpx
from pydantic import ValidationError

from app.modules.rag.infrastructure.embedding.schemas import EmbeddingApiResponse
from app.modules.rag.domain.embedding_provider import EmbeddingProvider


class OpenAICompatibleEmbeddingProvider(
    EmbeddingProvider
):
    """
    调用 OpenAI 兼容的 Embedding 服务。

    当前底层使用 vLLM 提供：

    POST /v1/embeddings
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model_name: str,
        dimension: int,
        query_instruction: str,
        normalized: bool = True,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url 不能为空")

        if not model_name.strip():
            raise ValueError("model_name 不能为空")

        if dimension <= 0:
            raise ValueError("dimension 必须大于 0")

        if not query_instruction.strip():
            raise ValueError(
                "query_instruction 不能为空"
            )
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds 必须大于 0"
            )

        self.client = client
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._model_name = model_name
        self._dimension = dimension
        self._normalized = normalized
        self.query_instruction = (
            query_instruction.strip()
        )
        self.timeout_seconds = (
            timeout_seconds
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def normalized(self) -> bool:
        return self._normalized

    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """
        文档不添加查询指令，
        直接使用切片正文生成向量。
        """

        if not texts:
            return []

        self._validate_texts(texts)

        return await self._request_embeddings(
            texts
        )

    async def embed_query(
        self,
        query: str,
    ) -> list[float]:
        """
        查询端添加 Qwen3 Embedding 检索指令。
        """

        if not isinstance(query, str):
            raise TypeError("query 必须是字符串")

        query = query.strip()

        if not query:
            raise ValueError("query 不能为空")

        instructed_query = (
            f"Instruct: {self.query_instruction}\n"
            f"Query: {query}"
        )

        vectors = await self._request_embeddings(
            [instructed_query]
        )

        return vectors[0]

    async def _request_embeddings(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        try:
            response = await self.client.post(
                f"{self.base_url}/v1/embeddings",
                headers=self._build_headers(),
                timeout=self.timeout_seconds,
                json={
                    "model": self.model_name,
                    "input": texts,
                    "encoding_format": "float",
                },
            )

            response.raise_for_status()

        except httpx.TimeoutException as exc:
            raise RuntimeError(
                "Embedding 服务请求超时"
            ) from exc

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                "Embedding 服务返回错误："
                f"{exc.response.status_code} "
                f"{exc.response.text}"
            ) from exc

        except httpx.RequestError as exc:
            raise RuntimeError(
                "无法连接 Embedding 服务："
                f"{self.base_url}"
            ) from exc

        try:
            result = EmbeddingApiResponse.model_validate(
                response.json()
            )
        except (ValidationError, ValueError) as exc:
            raise RuntimeError(
                "Embedding 服务响应格式错误"
            ) from exc

        return self._validate_and_order_vectors(
            result=result,
            expected_count=len(texts),
        )

    def _validate_and_order_vectors(
        self,
        *,
        result: EmbeddingApiResponse,
        expected_count: int,
    ) -> list[list[float]]:
        if len(result.data) != expected_count:
            raise RuntimeError(
                "返回向量数量与输入数量不一致："
                f"预期 {expected_count}，"
                f"实际 {len(result.data)}"
            )

        # 根据 index 排序，不能盲目信任返回顺序。
        sorted_items = sorted(
            result.data,
            key=lambda item: item.index,
        )

        expected_indexes = list(
            range(expected_count)
        )

        actual_indexes = [
            item.index
            for item in sorted_items
        ]

        if actual_indexes != expected_indexes:
            raise RuntimeError(
                "Embedding 返回的 index 不连续："
                f"{actual_indexes}"
            )

        vectors: list[list[float]] = []

        for item in sorted_items:
            vector = item.embedding

            if len(vector) != self.dimension:
                raise RuntimeError(
                    f"第 {item.index} 个向量维度错误："
                    f"预期 {self.dimension}，"
                    f"实际 {len(vector)}"
                )

            if not all(
                math.isfinite(value)
                for value in vector
            ):
                raise RuntimeError(
                    f"第 {item.index} 个向量中"
                    "存在 NaN 或无穷值"
                )

            vectors.append(vector)

        return vectors

    @staticmethod
    def _validate_texts(
        texts: list[str],
    ) -> None:
        for index, text in enumerate(texts):
            if not isinstance(text, str):
                raise TypeError(
                    f"第 {index} 个输入不是字符串"
                )

            if not text.strip():
                raise ValueError(
                    f"第 {index} 个输入文本为空"
                )

    def _build_headers(
            self,
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }

        if self.api_key.strip():
            headers["Authorization"] = (
                f"Bearer {self.api_key}"
            )

        return headers
