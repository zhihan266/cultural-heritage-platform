"""Smoke-check the configured embedding server's shape and example ranking."""
import asyncio
import math
import httpx

from app.core.config import get_settings
from app.modules.rag.composition import build_embedding_provider


async def main() -> None:
    documents = ["故宫是明清两代皇帝居住和处理政务的宫殿。",
                 "天坛是明清时期皇帝祭天和祈谷的重要场所。",
                 "长城是中国古代大型军事防御工程。"]
    async with httpx.AsyncClient(trust_env=False) as client:
        provider = build_embedding_provider(client)
        query = await provider.embed_query("哪一处北京皇家建筑主要用于皇帝祭天和祈谷？")
        vectors = await provider.embed_documents(documents)
    assert len(query) == get_settings().embedding_dimension and len(vectors) == 3
    query_norm = math.sqrt(sum(value * value for value in query))
    similarities = [sum(a * b for a, b in zip(query, vector)) /
                    (query_norm * math.sqrt(sum(value * value for value in vector))) for vector in vectors]
    for index in sorted(range(3), key=lambda index: similarities[index], reverse=True):
        print(f"{similarities[index]:.4f}: {documents[index]}")
    assert max(range(3), key=lambda index: similarities[index]) == 1
    print("Embedding smoke check passed")


if __name__ == "__main__":
    asyncio.run(main())
