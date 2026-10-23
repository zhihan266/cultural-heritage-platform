"""OpenAI 兼容 AI 接口的健康检查服务。

本模块负责发送最小请求、判断接口是否可用，并将检测结果写回数据库。
所有网络请求都会在线程中执行，避免阻塞 FastAPI 的异步事件循环。
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.modules.agent.repository import list_enabled_ai_agents, update_ai_health
from app.modules.agent.models import AiAgent
from app.modules.agent.request_config import (
    build_auth_headers, build_llm_payload, redact_request_error,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)



async def check_ai_health(ai: AiAgent) -> tuple[bool, str | None]:
    """检测 AI 是否能返回有效的 OpenAI 对话响应。

    返回值的第一个元素表示是否可用，第二个元素保存失败原因。
    本函数只负责检测，不直接修改数据库。
    """
    if not ai.api_url:
        return False, "api_url is not configured"

    payload = {
        "model": ai.name,
        "messages": [
            {
                "role": "user",
                "content": "hello",
            }
        ],
        "stream": False,
        "max_tokens": 1
    }

    headers: dict[str, str] = {}
    # urllib 是同步网络库，因此通过 asyncio.to_thread 放到工作线程中执行。
    try:
        headers = build_auth_headers(ai)
        # 健康检查保留 max_tokens=1，不应用正式生成参数，但仍检查配置是否合法。
        payload = build_llm_payload(ai, payload, apply_generation_config=False)
        data = await asyncio.to_thread(
            _post_health_request,
            ai.api_url,
            payload,
            headers,
        )
    except HTTPError as exc:
        detail = redact_request_error(
            exc.read().decode("utf-8", errors="replace").strip(), headers,
        )
        message = f"HTTP {exc.code}"
        if detail:
            message = f"{message}: {detail[:500]}"
        return False, message
    except (URLError, TimeoutError) as exc:
        return False, redact_request_error(str(exc), headers)
    except json.JSONDecodeError:
        return False, "response is not valid JSON"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {redact_request_error(str(exc), headers)}"

    # 需求只要求响应存在非空 choices[0]，不强制模型必须返回文本。
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return False, "response does not contain choices[0]"

    return True, None


async def check_and_store_ai_health(ai: AiAgent) -> AiAgent:
    """检测单个 AI，并把检测时间、可用状态和错误原因写入数据库。"""
    available, error = await check_ai_health(ai)
    checked_at = datetime.now(timezone.utc)
    return await asyncio.to_thread(
        update_ai_health,
        ai.id,
        available,
        checked_at,
        error,
    )


async def check_enabled_ai_agents() -> None:
    """并发检测所有已启用 AI，且不让单个失败影响其他检测。

    此函数供应用启动时创建后台任务使用。数据库读取与写入均放到线程中，
    因此不会阻塞 FastAPI 接收请求。
    """
    try:
        agents = await asyncio.to_thread(list_enabled_ai_agents)
    except Exception:
        logger.exception("Failed to load enabled AI agents for health checks")
        return

    # return_exceptions=True 保证单个 AI 异常不会中断整批检测。
    results = await asyncio.gather(
        *(check_and_store_ai_health(ai) for ai in agents),
        return_exceptions=True,
    )
    for ai, result in zip(agents, results):
        if isinstance(result, Exception):
            logger.error(
                "AI health check failed unexpectedly for %s: %s",
                ai.id,
                result,
            )


def _post_health_request(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """发送最小 OpenAI 兼容请求，并要求服务返回 HTTP 200。"""
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers if headers is not None else {"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=get_settings().llm_health_timeout_seconds) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status {response.status}")
        data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise json.JSONDecodeError("JSON root must be an object", str(data), 0)
        return data
