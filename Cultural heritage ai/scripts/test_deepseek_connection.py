from __future__ import annotations

import asyncio

import httpx

from app.core.config import get_settings


async def main() -> None:
    settings = get_settings()

    api_key = (
        settings.deepseek_api_key
        .get_secret_value()
    )

    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY 未配置"
        )

    # 先使用你数据库测试 Agent 当前配置的 URL / model。
    url = (
        "https://api.deepseek.com/v1/chat/completions"
    )

    payload = {
        "model": "deepseek-flash",
        "messages": [
            {
                "role": "user",
                "content": "请只回答：连接成功",
            }
        ],
        "max_tokens": 32,
    }

    headers = {
        "Authorization": (
            f"Bearer {api_key}"
        ),
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(
        timeout=60.0
    ) as client:

        response = await client.post(
            url,
            headers=headers,
            json=payload,
        )

        print(
            "status =",
            response.status_code,
        )

        print(
            "response =",
            response.text,
        )

        response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())