"""Agent 模型请求的鉴权与生成参数配置。

为普通回答、工具调用和健康检查统一构造 Header 与请求配置，
校验生成参数白名单及 reasoning_effort 冲突，并对错误中的凭证脱敏。
本模块仅支持已实现的凭证引用，不负责发送 HTTP 请求。
"""

from typing import Any

from app.core.config import get_settings
from app.modules.agent.models import AiAgent

_GENERATION_FIELDS = frozenset({
    "temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty", "stop",
    "reasoning_effort",
})
ALLOWED_REASONING_EFFORTS = frozenset({"none", "low", "high", "max"})
_THINKING_CONFLICT_FIELDS = frozenset({"temperature", "frequency_penalty", "presence_penalty"})


class LlmRequestConfigurationError(ValueError):
    """An Agent request cannot be sent with its configured credentials or payload."""


def build_auth_headers(ai: AiAgent) -> dict[str, str]:
    """Resolve a supported credential reference without using the provider name."""
    headers = {"Content-Type": "application/json"}
    if ai.auth_type == "none":
        return headers
    if ai.auth_type != "bearer":
        raise LlmRequestConfigurationError("Unsupported AI auth_type; expected 'none' or 'bearer'")
    if not ai.credential_ref or not ai.credential_ref.strip():
        raise LlmRequestConfigurationError("Bearer authentication requires credential_ref")
    if ai.credential_ref != "DEEPSEEK_API_KEY":
        raise LlmRequestConfigurationError("Unsupported AI credential_ref")
    api_key = get_settings().deepseek_api_key.get_secret_value().strip()
    if not api_key:
        raise LlmRequestConfigurationError("DEEPSEEK_API_KEY is not configured")
    headers["Authorization"] = f"Bearer {api_key}"
    return headers


def build_llm_payload(
    ai: AiAgent, payload: dict[str, Any], *, apply_generation_config: bool = True,
) -> dict[str, Any]:
    """Keep legacy defaults while allowing only explicit generation parameters."""
    config = ai.generation_config
    if config is not None:
        if not isinstance(config, dict):
            raise LlmRequestConfigurationError("AI generation_config must be a dict or None")
        if any(field not in _GENERATION_FIELDS for field in config):
            raise LlmRequestConfigurationError(
                "AI generation_config contains unsupported or reserved fields"
            )
        if "reasoning_effort" in config:
            effort = config["reasoning_effort"]
            if not isinstance(effort, str) or effort not in ALLOWED_REASONING_EFFORTS:
                raise LlmRequestConfigurationError(
                    "AI reasoning_effort must be one of: none, low, high, max"
                )
            if effort != "none" and _THINKING_CONFLICT_FIELDS.intersection(config):
                raise LlmRequestConfigurationError(
                    "AI reasoning_effort low/high/max cannot be combined with "
                    "temperature, frequency_penalty or presence_penalty"
                )
    result = dict(payload)
    if ai.model_name is not None and ai.model_name.strip():
        result["model"] = ai.model_name
    if apply_generation_config and config is not None:
        for field in _GENERATION_FIELDS:
            if field in config:
                result[field] = config[field]
    return result


def redact_request_error(message: str, headers: dict[str, str]) -> str:
    """Do not expose a Bearer credential echoed by a remote service or transport."""
    authorization = headers.get("Authorization")
    if authorization:
        message = message.replace(authorization, "Bearer [REDACTED]")
        message = message.replace(authorization.removeprefix("Bearer "), "[REDACTED]")
    return message
