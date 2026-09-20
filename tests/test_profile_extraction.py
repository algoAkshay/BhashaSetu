"""Offline provider-contract tests. Fixtures do not prove live model accuracy."""
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import httpx
from pydantic import ValidationError

from backend.config import ConfigurationError, LLMSettings
from backend.profile_schemas import ProfileExtractionResult, UserProfile
from backend.services.profile_extraction_service import (
    GeminiProfileExtractor, ProfileExtractionError, SYSTEM_INSTRUCTION,
)


@pytest.mark.parametrize("text,values", [
    ("मैं पच्चीस साल का लड़का हूं और मेरी वार्षिक आय पाँच हजार है", (25, "Male", 5000)),
    ("मै एक पच्चीस साल का लड़का हूं और मेरी वार्षिक आय पाँच हजार है", (25, "Male", 5000)),
    ("मेरी सालाना आय एक लाख रुपये है", (None, None, 100000)),
    ("meri age twenty five hai, main male hoon aur income one lakh hai", (25, "Male", 100000)),
    ("I am 25 years old and my annual income is 100000 rupees", (25, None, 100000)),
    ("मेरी उम्र पच्चीस है", (25, None, None)),
    ("मुझे सरकारी योजना चाहिए", (None, None, None)),
    ("मेरी आय डेढ़ लाख है", (None, None, 150000)),
    ("income 2.5 lakh hai", (None, None, 250000)),
    ("मेरी वार्षिक आय दो लाख पचास हजार है", (None, None, 250000)),
    ("मेरी income कम है", (None, None, None)),
    ("मैं जवान हूं", (None, None, None)),
    ("मेरे परिवार में पुरुष हैं", (None, None, None)),
    ("मेरी मासिक आय पाँच हजार है", (None, None, None)),
    ("नहीं मेरी उम्र 26 है", (26, None, None)),
    ("income one lakh nahi two lakh hai", (None, None, 200000)),
])
def test_structured_sdk_contract(text, values):
    expected = dict(zip(("age", "gender", "annual_income"), values))
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(text=json.dumps(expected))
    with patch("google.genai.Client") as constructor:
        constructor.return_value.__enter__.return_value = client
        result = GeminiProfileExtractor(LLMSettings(api_key="test-key")).extract_profile(text)
    assert result.model_dump() == UserProfile(**expected).model_dump()
    call = client.models.generate_content.call_args.kwargs
    assert call["model"] == "gemini-3.5-flash-lite"
    assert json.loads(call["contents"])["utterance"] == text
    assert json.loads(call["contents"])["current_profile"] == UserProfile().model_dump()
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema == ProfileExtractionResult.model_json_schema()
    assert call["config"].system_instruction == SYSTEM_INSTRUCTION
    assert call["config"].tools is None
    assert call["config"].automatic_function_calling.disable is True
    assert constructor.call_args.kwargs["http_options"].timeout == 30000
    assert constructor.call_args.kwargs["http_options"].retry_options.attempts == 1
    assert constructor.call_args.kwargs["vertexai"] is False
    constructor.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("field,value", [
    ("age", -5), ("age", 121), ("age", True), ("age", "25"), ("age", 25.5),
    ("annual_income", -1000), ("annual_income", False), ("annual_income", "one lakh"),
    ("annual_income", 1000.5), ("gender", "male"), ("gender", "unknown"),
])
def test_strict_schema_rejects_impossible_values(field, value):
    values = {"age": None, "gender": None, "annual_income": None, field: value}
    with pytest.raises(ValidationError):
        ProfileExtractionResult.model_validate(values)


@pytest.mark.parametrize("raw", [
    "", "The age is 25", '```json\n{"age":25}\n```', '{"age":25}',
    '{"age":-20,"gender":null,"annual_income":null}',
    '{"age":25,"gender":"Male","annual_income":5000,"eligible":true}',
])
def test_malformed_provider_response_is_sanitized(raw, caplog):
    with patch("google.genai.Client") as constructor:
        constructor.return_value.__enter__.return_value.models.generate_content.return_value = SimpleNamespace(text=raw)
        with pytest.raises(ProfileExtractionError) as caught:
            GeminiProfileExtractor(LLMSettings(api_key="private-key")).extract_profile("private utterance")
    assert caught.value.reason == "invalid_output"
    assert "private" not in caplog.text
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("error", [TimeoutError("private payload"), ConnectionError("private key"),
                                        RuntimeError("429 secret quota"), RuntimeError("503 secret outage")])
def test_provider_failures_are_sanitized(error, caplog):
    with patch("google.genai.Client") as constructor:
        constructor.return_value.__enter__.return_value.models.generate_content.side_effect = error
        with pytest.raises(ProfileExtractionError) as caught:
            GeminiProfileExtractor(LLMSettings(api_key="private-key")).extract_profile("private utterance")
    assert caught.value.reason == "provider_unavailable"
    assert "private" not in caplog.text and "secret" not in caplog.text
    assert "private" not in str(caught.value) and "secret" not in str(caught.value)


def test_missing_key_does_not_create_client():
    with patch.dict(os.environ, {"GEMINI_API_KEY": "", "LLM_PROVIDER": "gemini"}), \
            patch("google.genai.Client") as constructor:
        with pytest.raises(ProfileExtractionError, match="unavailable"):
            GeminiProfileExtractor().extract_profile("hello")
    constructor.assert_not_called()


def test_current_profile_is_context_and_nulls_remain_null():
    known = UserProfile(age=25, gender="Male")
    with patch("google.genai.Client") as constructor:
        client = constructor.return_value.__enter__.return_value
        client.models.generate_content.return_value = SimpleNamespace(
            text='{"age":null,"gender":null,"annual_income":100000}')
        result = GeminiProfileExtractor(LLMSettings(api_key="test-key")).extract_profile("एक लाख", known)
    assert json.loads(client.models.generate_content.call_args.kwargs["contents"])["current_profile"] == known.model_dump()
    assert result.age is None and result.gender is None
    assert result.annual_income == 100000


def test_logs_contain_only_field_names(caplog):
    caplog.set_level("INFO", logger="backend.services.profile_extraction_service")
    with patch("google.genai.Client") as constructor:
        constructor.return_value.__enter__.return_value.models.generate_content.return_value = SimpleNamespace(
            text='{"age":25,"gender":"Male","annual_income":54321}')
        GeminiProfileExtractor(LLMSettings(api_key="private-key")).extract_profile("private transcript")
    assert "extracted_fields=['age', 'gender', 'annual_income']" in caplog.text
    assert all(value not in caplog.text for value in ["25", "54321", "Male", "private"])
    assert "private-key" not in repr(LLMSettings(api_key="private-key"))


@pytest.mark.parametrize("environment", [{"LLM_PROVIDER": "regex"}, {"LLM_MODEL": ""},
    {"LLM_TIMEOUT_MS": "bad"}, {"LLM_TIMEOUT_MS": "0"}])
def test_invalid_config_is_not_a_fallback(environment):
    with patch.dict(os.environ, environment):
        with pytest.raises(ConfigurationError):
            LLMSettings.from_environment()
        with pytest.raises(ProfileExtractionError):
            GeminiProfileExtractor().extract_profile("age 25")


def test_real_sdk_serialization_over_offline_transport():
    """Exercise actual SDK serialization and response parsing without network/quota."""
    from google import genai
    from google.genai import types

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [
            {"text": '{"age":25,"gender":"Male","annual_income":5000}'}
        ]}, "finishReason": "STOP"}]})

    # An injected transport is caller-owned; unlike SDK-created clients, it must
    # be closed by the test itself (per the SDK client lifecycle contract).
    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        client = genai.Client(api_key="offline-test-key", vertexai=False,
                             http_options=types.HttpOptions(httpx_client=http_client))
        with patch("google.genai.Client", return_value=client):
            result = GeminiProfileExtractor(LLMSettings(api_key="offline-test-key")).extract_profile(
                "मैं पच्चीस साल का लड़का हूं और मेरी वार्षिक आय पाँच हजार है")
    assert result.model_dump() == UserProfile(age=25, gender="Male", annual_income=5000).model_dump()
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert requests[0].url.path.endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["responseJsonSchema"]["required"] == ["age", "gender", "annual_income"]
    assert payload["systemInstruction"]["parts"][0]["text"] == SYSTEM_INSTRUCTION
    assert http_client.is_closed
