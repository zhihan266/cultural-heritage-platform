
from app.modules.agent.llm_usage import (
    LlmRequestUsage,
    LlmUsage,
    parse_llm_usage,
)


def test_parse_llm_usage():

    payload = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 30,
            "total_tokens": 130,
            "prompt_cache_hit_tokens": 40,
            "prompt_cache_miss_tokens": 60,
        }
    }

    usage = parse_llm_usage(
        payload
    )

    assert usage is not None

    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 30
    assert usage.total_tokens == 130

    assert (
        usage.prompt_cache_hit_tokens
        == 40
    )

    assert (
        usage.prompt_cache_miss_tokens
        == 60
    )


def test_merge_llm_usage():

    first = LlmUsage(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        prompt_cache_hit_tokens=30,
        prompt_cache_miss_tokens=70,
    )

    second = LlmUsage(
        prompt_tokens=200,
        completion_tokens=40,
        total_tokens=240,
        prompt_cache_hit_tokens=50,
        prompt_cache_miss_tokens=150,
    )

    total = first + second

    assert total.prompt_tokens == 300
    assert total.completion_tokens == 60
    assert total.total_tokens == 360

    assert (
        total.prompt_cache_hit_tokens
        == 80
    )

    assert (
        total.prompt_cache_miss_tokens
        == 220
    )

    def test_build_request_usage_trace():
        usage = LlmUsage(
            prompt_tokens=500,
            completion_tokens=100,
            total_tokens=600,
            prompt_cache_hit_tokens=200,
            prompt_cache_miss_tokens=300,
        )

        trace = LlmRequestUsage.from_usage(
            request_index=2,
            usage=usage,
        )

        assert trace.request_index == 2

        assert trace.prompt_tokens == 500
        assert trace.completion_tokens == 100
        assert trace.total_tokens == 600

        assert (
                trace.prompt_cache_hit_tokens
                == 200
        )

        assert (
                trace.prompt_cache_miss_tokens
                == 300
        )


def test_missing_usage_returns_none():

    assert (
        parse_llm_usage({})
        is None
    )