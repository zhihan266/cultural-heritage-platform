"""RAG composition root. HTTP and tasks share build_rag_retriever as the public policy."""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.modules.rag.application.combined_retriever import CombinedRetriever
from app.modules.rag.application.dense_retriever import DenseRetriever
from app.modules.rag.application.embedding_service import EmbeddingService
from app.modules.rag.application.hybrid_retriever import HybridRetriever
from app.modules.rag.application.indexing_service import IndexingService
from app.modules.rag.application.preprocessing_service import PreprocessingService
from app.modules.rag.application.rag_answer_service import RagAnswerService
from app.modules.rag.application.rag_context_service import RagContextService
from app.modules.rag.application.reranked_retriever import RerankedRetriever
from app.modules.rag.application.retrieval_config import RetrievalConfig
from app.modules.rag.application.temporary_rag_service import TemporaryRagService
from app.modules.rag.application.temporary_retriever import TemporaryRetriever
from app.modules.rag.domain.embedding_provider import EmbeddingProvider
from app.modules.rag.domain.reranker import Reranker
from app.modules.rag.domain.retriever import Retrieval
from app.modules.rag.domain.vector_store import VectorStore
from app.modules.rag.infrastructure.agent_answer_generator import AgentAnswerGenerator
from app.modules.rag.infrastructure.embedding.openai_compatible_provider import OpenAICompatibleEmbeddingProvider
from app.modules.rag.infrastructure.ingestion.parser_factory import ParserFactory
from app.modules.rag.infrastructure.reranking.vllm_reranker import VllmReranker
from app.modules.rag.infrastructure.vectorstore.config import MilvusConfig
from app.modules.rag.infrastructure.vectorstore.milvus_vector_store import MilvusVectorStore
from app.modules.rag.application.bm25_retriever import (
    BM25Retriever,
)


def build_preprocessing_service() -> PreprocessingService:
    return PreprocessingService(parser_resolver=ParserFactory.get_parser)


def build_embedding_provider(http_client: httpx.AsyncClient) -> EmbeddingProvider:
    cfg = get_settings()
    return OpenAICompatibleEmbeddingProvider(
        client=http_client, base_url=cfg.embedding_base_url,
        api_key=cfg.embedding_api_key.get_secret_value(),
        model_name=cfg.embedding_model, dimension=cfg.embedding_dimension,
        query_instruction=cfg.embedding_query_instruction,
        normalized=cfg.embedding_normalized,
        timeout_seconds=cfg.embedding_timeout_seconds,
    )


def build_embedding_service(http_client: httpx.AsyncClient) -> EmbeddingService:
    return EmbeddingService(
        provider=build_embedding_provider(http_client),
        batch_size=get_settings().embedding_batch_size,
    )


def build_vector_store() -> MilvusVectorStore:
    cfg = get_settings()
    return MilvusVectorStore(config=MilvusConfig(
        uri=cfg.rag_milvus_uri, collection_name=cfg.rag_milvus_collection_name,
        dimension=cfg.embedding_dimension, metric_type=cfg.rag_milvus_metric_type,
        token=cfg.rag_milvus_token.get_secret_value(),
    ))


def build_dense_retriever(
    http_client: httpx.AsyncClient, *, vector_store: VectorStore | None = None,
) -> DenseRetriever:
    """Dense-only baseline for explicit experiments, never the implicit task policy."""
    return DenseRetriever(
        embedding_service=build_embedding_service(http_client),
        vector_store=build_vector_store() if vector_store is None else vector_store,
        config=RetrievalConfig(top_k=get_settings().rag_retrieval_top_k),
    )


def build_hybrid_retriever(
    http_client: httpx.AsyncClient, *, vector_store: VectorStore | None = None,
) -> HybridRetriever:
    return HybridRetriever(
        embedding_service=build_embedding_service(http_client),
        vector_store=build_vector_store() if vector_store is None else vector_store,
        default_top_k=get_settings().reranker_candidate_top_k,
    )


def build_reranker(http_client: httpx.AsyncClient) -> Reranker:
    cfg = get_settings()
    return VllmReranker(
        http_client=http_client, base_url=cfg.reranker_base_url,
        model_name=cfg.reranker_model, timeout_seconds=cfg.reranker_timeout_seconds,
    )


def build_rag_retriever(
    http_client: httpx.AsyncClient, *, vector_store: VectorStore | None = None,
) -> Retrieval:
    """Default online policy: Hybrid(Dense + BM25/RRF) followed by reranking."""
    cfg = get_settings()
    return RerankedRetriever(
        base_retriever=build_hybrid_retriever(http_client, vector_store=vector_store),
        reranker=build_reranker(http_client),
        candidate_top_k=cfg.reranker_candidate_top_k,
        default_top_k=cfg.reranker_top_k,
    )


def build_rag_answer_service(
    http_client: httpx.AsyncClient, *, vector_store: VectorStore | None = None,
) -> RagAnswerService:
    return RagAnswerService(
        retriever=build_rag_retriever(http_client, vector_store=vector_store),
        answer_generator=AgentAnswerGenerator(),
    )


def build_indexing_service(
    http_client: httpx.AsyncClient, *, vector_store: VectorStore | None = None,
) -> IndexingService:
    return IndexingService(
        preprocessing_service=build_preprocessing_service(),
        embedding_service=build_embedding_service(http_client),
        vector_store=build_vector_store() if vector_store is None else vector_store,
    )


def build_temporary_rag_service(http_client: httpx.AsyncClient) -> TemporaryRagService:
    return TemporaryRagService(
        preprocessing_service=build_preprocessing_service(),
        embedding_service=build_embedding_service(http_client),
    )


def build_combined_retriever(
    http_client: httpx.AsyncClient, *, vector_store: VectorStore | None = None,
    public_retriever: Retrieval | None = None,
) -> CombinedRetriever:
    return CombinedRetriever(
        public_retriever=(
            build_rag_retriever(http_client, vector_store=vector_store)
            if public_retriever is None else public_retriever
        ),
        embedding_service=build_embedding_service(http_client),
        temporary_retriever=TemporaryRetriever(),
    )


def build_rag_context_service(
    http_client: httpx.AsyncClient, *, vector_store: VectorStore | None = None,
) -> RagContextService:
    retriever = build_rag_retriever(http_client, vector_store=vector_store)
    return RagContextService(
        retriever=retriever,
        temporary_service=build_temporary_rag_service(http_client),
        combined_retriever=build_combined_retriever(
            http_client, public_retriever=retriever,
        ),
        public_top_k=get_settings().rag_task_retrieval_top_k,
    )

def build_bm25_retriever(
    *,
    vector_store: VectorStore | None = None,
) -> BM25Retriever:

    return BM25Retriever(
        vector_store=(
            build_vector_store()
            if vector_store is None
            else vector_store
        ),
        default_top_k=(
            get_settings().rag_retrieval_top_k
        ),
    )
