"""RAG HTTP 请求与响应的数据模型。

定义检索、问答和公共文档入库接口的数据契约，
校验查询与 TopK 等输入，并描述命中切片、答案和来源字段。
"""

from  __future__ import annotations

from pydantic import BaseModel, Field


"""

    检索相关
    
"""
class RagRetrieveRequest(BaseModel):
    """
    RAG 的检索请求
    目前只有Retrieval，不调用最终回答LLM
    """

    # 用户自然语言问题
    query: str = Field(
        min_length=1,
        description="用户检索问题"
    )

    # 返回最相关的前K个chunk
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="返回的最大检索结合数量"
    )
    # 可选：如果指定document_id,就只在这个文档中搜索
    document_id: str | None = None


class RagRetrieveHit(BaseModel):
    # 一条检索结果

    chunk_id: str
    document_id: str

    content: str

    source: str

    # 在原始文档里是第几个切片
    chunk_index: int

    # Original retrieval score: RRF in the default pipeline.
    # Reranker relevance determines order and is kept separately.
    score: float

class RagRetrieveResponse(BaseModel):
    """
      RAG 检索接口返回值。
    """

    query: str

    hits: list[RagRetrieveHit]


"""

   回答相关
   
"""

class RagAnswerRequest(BaseModel):
    """
    完整 RAG 问答请求。
    """

    query: str = Field(
        min_length=1,
        description="用户提出的问题"
    )
    # 指定现有 Agent 表中的 AI。
    ai_id: str = Field(
        min_length=1,
        description="负责生成最终答案的 AI ID",
    )

    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
    )

    document_id: str | None = None

class RagAnswerSource(BaseModel):
    """
    最终答案使用的一条知识来源。
    """

    chunk_id: str

    document_id: str

    source: str

    chunk_index: int

    score: float

class RagAnswerResponse(BaseModel):
    """
    完整 RAG 问答响应。
    """

    query: str

    ai_id: str

    answer: str

    sources: list[RagAnswerSource]


class RagDocumentIndexResponse(BaseModel):
    """
    管理员将文档永久写入公共 RAG 知识库后的响应。
    """

    filename: str

    # 文件内容生成的稳定 document_id。
    # 同一文件重复索引时 document_id 保持一致。
    document_id: str

    message: str = "Document indexed into shared knowledge base"
