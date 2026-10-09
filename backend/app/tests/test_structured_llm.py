import json
from types import SimpleNamespace
import pytest
import requests
from backend.app.agent.models import TicketAnalysis, InvestigationDecision
from backend.app.models.provider_groq import GroqHostedProvider
from backend.app.models.provider import LLMError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema,stage,payload",
    [
        (
            TicketAnalysis,
            "analyze_ticket",
            {
                "issue_type": "vpn_authentication",
                "symptoms": "Authentication failed",
                "user_name": "Jake Peralta",
            },
        ),
        (
            InvestigationDecision,
            "reason",
            {
                "evidence_summary": "Site alert correlates with recent change",
                "enough_information": False,
                "confidence": 0.4,
                "question_for_human": "Which site?",
                "human_field": "current_site",
            },
        ),
    ],
)
async def test_structured_groq_parsing(monkeypatch, schema, stage, payload):
    def post(url, **kwargs):
        assert kwargs["json"]["response_format"] == {"type": "json_object"}
        assert kwargs["json"]["messages"][0]["content"]
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": json.dumps(payload)}}]},
        )

    monkeypatch.setattr(requests, "post", post)
    result = await GroqHostedProvider("test", "test-model").generate_structured(
        stage, {"ticket": {"summary": "VPN"}}, schema
    )
    assert isinstance(result, schema)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    ["not JSON", "{}", '{"evidence_summary":"bad","enough_information":true,"confidence":1.5}'],
)
async def test_invalid_structured_output(monkeypatch, content):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": content}}]},
        ),
    )
    with pytest.raises(LLMError, match="invalid structured"):
        await GroqHostedProvider("test", "test-model").generate_structured(
            "reason", {}, InvestigationDecision
        )


@pytest.mark.asyncio
async def test_hosted_network_failure_is_explicit(monkeypatch):
    def fail(*a, **k):
        raise requests.Timeout("test")

    monkeypatch.setattr(requests, "post", fail)
    with pytest.raises(LLMError, match="Groq request failed"):
        await GroqHostedProvider("test", "test-model").generate_structured(
            "reason", {}, InvestigationDecision
        )


@pytest.mark.asyncio
async def test_current_groq_model_uses_json_schema_without_hidden_reasoning(monkeypatch):
    payload = {
        "evidence_summary": "Supported assessment",
        "enough_information": True,
        "confidence": 0.6,
    }

    def post(url, **kwargs):
        data = kwargs["json"]
        assert data["response_format"]["type"] == "json_schema"
        assert data["response_format"]["json_schema"]["schema"]["properties"]["confidence"]
        assert data["include_reasoning"] is False
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": json.dumps(payload)}}]},
        )

    monkeypatch.setattr(requests, "post", post)
    result = await GroqHostedProvider("test", "openai/gpt-oss-20b").generate_structured(
        "reason", {}, InvestigationDecision
    )
    assert result.confidence == 0.6


def test_final_decision_clears_unused_human_field():
    decision = InvestigationDecision(
        evidence_summary="Human confirmed the required update is installed.",
        enough_information=True,
        confidence=0.7,
        question_for_human=None,
        human_field="required_update_installed",
    )
    assert decision.question_for_human is None and decision.human_field is None


def test_human_question_requires_an_answer_field():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="human_field"):
        InvestigationDecision(
            evidence_summary="Need user input.",
            enough_information=False,
            confidence=0,
            question_for_human="Which site?",
            human_field=None,
        )


@pytest.mark.asyncio
async def test_format_correction_identifies_missing_human_predicate(monkeypatch):
    calls = []
    payload = {
        "evidence_summary": "Need the reported MFA error.",
        "enough_information": False,
        "confidence": 0.5,
        "question_for_human": "What error does the MFA prompt show?",
    }

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        if len(calls) == 2:
            assert "Human questions require a human_field" in calls[-1]["messages"][-1]["content"]
            payload["human_field"] = "mfa_error"
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": json.dumps(payload)}}]},
        )

    monkeypatch.setattr(requests, "post", post)
    result = await GroqHostedProvider("test", "test-model").generate_structured(
        "reason", {}, InvestigationDecision
    )
    assert len(calls) == 2 and result.human_field == "mfa_error"


@pytest.mark.asyncio
async def test_rate_limit_reports_quota_without_external_private_details(monkeypatch):
    response = requests.Response()
    response.status_code = 429
    response.headers["retry-after"] = "60"
    response._content = json.dumps({"error": {
        "message": "Rate limit for private-org-id on tokens per day (TPD)."
    }}).encode()
    monkeypatch.setattr(requests, "post", lambda *a, **k: response)
    with pytest.raises(LLMError) as failure:
        await GroqHostedProvider("test", "test-model").generate_structured(
            "reason", {}, InvestigationDecision
        )
    assert "TPD quota limit" in str(failure.value) and "60 seconds" in str(failure.value)
    assert "private-org-id" not in str(failure.value)
