"""RAG 检索与问答的 HTTP 接口。

通过依赖注入获取检索器和问答服务，将请求传入应用层，
并把检索命中、最终回答与来源信息转换为 API 响应模型。
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
)

from app.modules.rag.application.rag_answer_service import RagAnswerService
from app.modules.rag.api.dependencies import (
    get_rag_retriever, get_rag_answer_service,
)
from app.modules.rag.domain.retriever import (
    Retrieval,
)
from app.modules.rag.api.schemas import (
    RagRetrieveHit,
    RagRetrieveRequest,
    RagRetrieveResponse, RagAnswerResponse, RagAnswerRequest, RagAnswerSource,
)

router = APIRouter(
    prefix="/rag",
    tags=["rag"],
)

@router.post(
    "/retrieve",
    response_model=RagRetrieveResponse,
)
async def retrieve(
        request: RagRetrieveRequest,

        retriever: Retrieval = Depends(get_rag_retriever),

) -> RagRetrieveResponse:
    # 只做RAG的工作，不调用模型
    # Default retrieval is selected centrally by composition.
    hits = await retriever.retrieve(
        query=request.query,
        top_k=request.top_k,
        document_id=request.document_id,
    )
    # 2、转化为apiresponse
    response_hits = [
        RagRetrieveHit(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            content=hit.content,
            source=hit.source,
            chunk_index=hit.chunk_index,
            score=hit.score,

        )
        for hit in hits
    ]

    return RagRetrieveResponse(
        query=request.query,
        hits=response_hits,
    )

@router.post(
    "/answer",
    response_model=RagAnswerResponse,
)
async def answer(
        request: RagAnswerRequest,

        rag_service: RagAnswerService = Depends(
            get_rag_answer_service
        ),
) -> RagAnswerResponse:
    # 1、执行完整的RAG
    result = await rag_service.answer(
        query=request.query,
        ai_id=request.ai_id,
        top_k=request.top_k,
        document_id=request.document_id,
    )
    # 2、把检索结果转化成来源信息
    sources = [
        RagAnswerSource(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            source=hit.source,
            chunk_index=hit.chunk_index,
            score=hit.score,
        )
        for hit in result.hits
    ]
    # 3、返回最终答案和来源
    return RagAnswerResponse(
        query=request.query,
        ai_id=request.ai_id,
        answer=result.answer,
        sources=sources,
    )
