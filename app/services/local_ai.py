"""调用无需 API Key 的 OpenAI 兼容本地大模型接口。

本模块负责组装对话请求、发送 HTTP 请求，并从标准响应中提取模型答案。
同步网络请求通过工作线程执行，避免阻塞 FastAPI 的事件循环。
"""

import asyncio
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException, status

from app.models.domain import AiAgent, Task


async def request_local_ai(ai: AiAgent, task: Task, timeout: float = 120.0) -> str:
    """根据任务构造提示词，并调用通用本地 AI 请求函数。

    AI 的 name 字段作为 model，api_url 应指向兼容接口，例如：
    http://127.0.0.1:11434/v1/chat/completions
    """
    system_prompt, user_prompt = _build_task_prompts(task)
    return await request_local_ai_raw(
        ai,
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        timeout=timeout,
    )


async def request_local_ai_raw(
    ai: AiAgent,
    user_prompt: str,
    system_prompt: str,
    timeout: float = 120.0,
) -> str:
    """使用 OpenAI Chat Completions 格式调用任意本地 AI。

    普通回答、聚合和评分统一复用此函数，避免重复实现网络请求、错误处理
    和响应解析逻辑。
    """
    if not ai.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "AI service is disabled")
    if not ai.api_url:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "AI api_url is not configured",
        )

    payload = {
        "model": ai.name,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "stream": False,
    }

    # urllib 为同步实现，放入线程可避免阻塞其他异步请求。
    try:
        response_data = await asyncio.to_thread(
            _post_json,
            ai.api_url,
            payload,
            timeout,
        )

    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Local AI returned HTTP {exc.code}: {detail[:500]}",
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Cannot connect to local AI: {exc}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Local AI response is not valid JSON",
        ) from exc

    answer = _extract_answer(response_data)
    if not answer:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Local AI response does not contain choices[0].message.content",
        )
    return answer


def _build_task_prompts(task: Task) -> tuple[str, str]:
    """把普通任务转换为系统提示词和用户提示词。"""
    user_content = (
        f"问题：{task.question}\n\n"
        f"回答格式要求：{task.answer_format}\n\n"
        "请严格按照回答格式要求作答。"
        "不要输出思考过程，不要解释你的推理过程，只输出最终答案。\n"

    )
    return "你是一名文化遗产领域的专业问答助手。", user_content


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """发送不带 Authorization 的 JSON POST 请求，并解析对象响应。"""
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise json.JSONDecodeError("JSON root must be an object", str(data), 0)
        return data


def _extract_answer(data: dict[str, Any]) -> str | None:
    """提取 OpenAI Chat Completions 响应中的文本答案。

    同时兼容旧式 Completions 接口的 choices[0].text 字段。
    """
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()

            # 兼容旧式 OpenAI Completions 接口。
            text = first_choice.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()

    return None
