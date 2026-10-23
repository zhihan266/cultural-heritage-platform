"""Smoke-check the configured reranker against a small Chinese example."""
import asyncio
import httpx

from app.core.config import get_settings
from app.modules.rag.composition import build_reranker


async def main() -> None:
    documents = ["故宫是明清两代的皇家宫殿。",
                 "天坛是明清时期皇帝祭天和祈谷的重要场所。",
                 "长城是中国古代大型军事防御工程。"]
    async with httpx.AsyncClient(timeout=get_settings().reranker_timeout_seconds, trust_env=False) as client:
        results = await build_reranker(client).rerank(
            query="哪一处北京皇家建筑主要用于皇帝祭天和祈谷？", documents=documents, top_k=3,
        )
    for result in results:
        print(f"{result.score:.4f}: {documents[result.index]}")
    assert len(results) == 3 and results[0].index == 1
    assert results[0].score > results[-1].score
    print("Reranker smoke check passed")


if __name__ == "__main__":
    asyncio.run(main())
