"""公开控制输出的严格解析及同源 Schema 回归，不调用模型或数据库。"""

import json
import hashlib

import pytest
from jsonschema import Draft202012Validator

from app.modules.hcp_mad.application.facade import DebateOutputContract
from app.modules.hcp_mad.application.task.facade import AgentResultDecision, AgentResultValidationError


@pytest.mark.parametrize("decision,answer", [("continue_debate", None), ("finish_with_answer", "最终答案")])
def test_valid_outputs_share_generated_schema_and_parser(decision, answer):
    contract = DebateOutputContract()
    payload = {"decision": decision, "message": "完整公开立场", "final_answer": answer}
    schema = contract.json_schema()
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(payload)
    result = contract.parse_public_content(json.dumps(payload))
    assert result.decision == AgentResultDecision(decision) and result.final_answer == answer


@pytest.mark.parametrize("raw", [
    "not JSON SECRET_RESPONSE", "```json\n{}\n```", "{} trailing SECRET_RESPONSE", "[]", "null", "42",
    '{"decision":"continue_debate","message":"SECRET_RESPONSE","message":"duplicate","final_answer":null}',
    '{"decision":"unknown","message":"SECRET_RESPONSE","final_answer":null}',
    '{"decision":"continue_debate","message":"","final_answer":null}',
    '{"decision":"continue_debate","message":" \\n\\t","final_answer":null}',
    '{"decision":"continue_debate","message":42,"final_answer":null}',
    '{"decision":"continue_debate","message":"SECRET_RESPONSE","final_answer":"wrong"}',
    '{"decision":"finish_with_answer","message":"SECRET_RESPONSE","final_answer":null}',
    '{"decision":"finish_with_answer","message":"SECRET_RESPONSE","final_answer":"   "}',
    '{"decision":"finish_with_answer","message":"SECRET_RESPONSE"}',
    '{"decision":"continue_debate","message":"SECRET_RESPONSE","final_answer":null,"reasoning_content":"SECRET_RESPONSE"}',
    '{"decision":"continue_debate","message":"SECRET_RESPONSE","final_answer":null,"SECRET_RESPONSE":"extra"}',
    '{"decision":"continue_debate","message":"SECRET_RESPONSE","final_answer":NaN}',
    '{"decision":"continue_debate","message":"\\ud800","final_answer":null}',
    json.dumps({"decision": "continue_debate", "message": "x" * 12001, "final_answer": None}),
    json.dumps({"decision": "finish_with_answer", "message": "ok", "final_answer": "x" * 20001}),
    " " * 262145,
], ids=lambda raw: hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12])
def test_invalid_outputs_have_safe_errors_only(raw):
    with pytest.raises(AgentResultValidationError) as exc:
        DebateOutputContract().parse_public_content(raw)
    assert exc.value.issues
    assert "SECRET_RESPONSE" not in str(exc.value)
    assert "input_value" not in str(exc.value) and "reasoning_content" not in str(exc.value)
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


def test_valid_public_text_is_trimmed_without_interpreting_natural_language():
    contract = DebateOutputContract()
    result = contract.parse_public_content(json.dumps({
        "decision": "continue_debate", "message": " 已经达成共识 ", "final_answer": None,
    }))
    assert result.message == "已经达成共识" and result.decision == AgentResultDecision.CONTINUE
    with pytest.raises(AgentResultValidationError):
        contract.parse_public_content("已经达成共识，可以结束")
