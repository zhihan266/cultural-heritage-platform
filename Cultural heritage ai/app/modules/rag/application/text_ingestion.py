"""文本入库的可序列化切片计划。

复用 Parser、Cleaner、Splitter 生成一次固定计划；后续重试不重新切片。
来源 URI 写入现有 source 字段，避免依赖 Milvus 未保存的动态 metadata。
"""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.core.config import get_settings
from app.modules.rag import composition
from app.modules.rag.domain.models import RagChunk


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def profile() -> dict[str, Any]:
    cfg = get_settings()
    preprocessing = composition.build_preprocessing_service()
    return {
        "version": "text-plan-v1",
        "cleaner": asdict(preprocessing.cleaner.config),
        "splitter": asdict(preprocessing.splitter.config),
        "splitter_version": preprocessing.splitter.SPLITTER_VERSION,
        "embedding_model": cfg.embedding_model,
        "dimension": cfg.embedding_dimension,
        "embedding_endpoint": hashlib.sha256(cfg.embedding_base_url.encode()).hexdigest(),
        "normalized": cfg.embedding_normalized,
        "target": hashlib.sha256(cfg.rag_milvus_uri.encode()).hexdigest(),
        "collection": cfg.rag_milvus_collection_name,
    }


def prepare(text: str, source: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if not text.strip() or not source.strip() or len(source.encode("utf-8")) > 1024:
        raise ValueError("Text and a bounded source are required")
    document_id = hashlib.sha256((source + "\n" + text).encode("utf-8")).hexdigest()
    with TemporaryDirectory(prefix="rag-text-") as directory:
        path = Path(directory) / "knowledge.txt"
        path.write_text(text, encoding="utf-8")
        chunks = composition.build_preprocessing_service().process(
            path,
            document_id=document_id,
            source=source,
            metadata={**(metadata or {}), "source_id": source},
        )
    # JSON round trip also canonicalizes tuple configuration fields across DB drivers.
    plan = json.loads(
        json.dumps(
            {
                "document_id": document_id,
                "source": source,
                "profile": profile(),
                "chunks": [asdict(chunk) for chunk in chunks],
            },
            ensure_ascii=False,
        )
    )
    plan["digest"] = digest(plan)
    return plan


def restore(plan: dict[str, Any]) -> list[RagChunk]:
    body = {key: value for key, value in plan.items() if key != "digest"}
    if (
        plan.get("digest") != digest(body)
        or digest(plan.get("profile")) != digest(profile())
        or not isinstance(plan.get("chunks"), list)
        or not plan["chunks"]
    ):
        raise ValueError("Ingestion plan integrity or active indexing profile changed")
    chunks = [RagChunk(**item) for item in plan["chunks"]]
    if len({x.chunk_id for x in chunks}) != len(chunks) or any(
        x.document_id != plan["document_id"] or x.source != plan["source"] for x in chunks
    ):
        raise ValueError("Ingestion plan has inconsistent chunk identities")
    return chunks
