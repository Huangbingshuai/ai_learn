import importlib
import json
import logging

import httpx
import pytest


@pytest.fixture
def ai_module():
    return importlib.import_module("app.worker.ai")


@pytest.fixture
def topic():
    return {
        "topicKey": "moon",
        "title": "月相",
        "version": "1",
        "evidence": [{"evidenceId": "e1", "text": "月球反射太阳光。", "url": "unused"}],
    }


@pytest.fixture
def record():
    return {
        "topicTitle": "月相",
        "userId": "private-record-user",
        "takeaways": [
            {"text": "月球反射太阳光。", "evidenceIds": ["e2"], "private": "private-takeaway"}
        ],
        "takeawayEvidence": [
            {"evidenceId": "e2", "text": "月光来自反射。", "private": "private-takeaway-source"}
        ],
        "answers": [
            {
                "question": {
                    "questionId": "q01",
                    "type": "judge",
                    "stem": "月球反射太阳光吗？",
                    "options": [
                        {"optionId": "A", "text": "正确", "private": "private-option"},
                        {"optionId": "B", "text": "错误"},
                    ],
                    "private": "private-question",
                },
                "selectedOptionIds": ["B"],
                "correctOptionIds": ["A"],
                "isCorrect": False,
                "explanation": "月光是月球反射的太阳光。",
                "evidence": [
                    {"evidenceId": "e1", "text": "月球反射太阳光。", "private": "private-source"}
                ],
                "private": "private-answer",
            }
        ],
    }


@pytest.fixture
def provider(ai_module, monkeypatch):
    real_model = ai_module.ChatDeepSeek

    def configure(*, content="", finish_reason="stop", status=200, error_code=None, timeout=False):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            if timeout:
                raise httpx.ReadTimeout("private response body", request=request)
            if status != 200:
                return httpx.Response(
                    status, json={"error": {"message": "private response body", "code": error_code}}
                )
            return httpx.Response(
                200,
                json={
                    "id": "test-completion",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": finish_reason,
                        }
                    ],
                    "usage": {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25},
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(respond))
        monkeypatch.setattr(
            ai_module, "ChatDeepSeek", lambda **kwargs: real_model(http_client=client, **kwargs)
        )
        return ai_module.DeepSeekAI(api_key="test-key", model="deepseek-flash", timeout=3), requests

    return configure


@pytest.mark.parametrize("task", ["generate", "repair"])
def test_quiz_methods_send_one_evidence_bound_json_request(provider, topic, caplog, task):
    candidate = {"title": "月相", "takeaways": [], "questions": []}
    adapter, requests = provider(content=json.dumps(candidate))
    with caplog.at_level(logging.INFO):
        if task == "generate":
            result = adapter.generate(topic)
        else:
            result = adapter.repair(topic, candidate, ["q01 选项重复"])

    assert result == candidate
    assert len(requests) == 1
    request = requests[0]
    assert request["response_format"] == {"type": "json_object"}
    assert request["thinking"] == {"type": "disabled"}
    assert request["max_tokens"] >= 6000
    assert "tools" not in request
    assert "20" in request["messages"][0]["content"]
    data = json.loads(request["messages"][1]["content"])
    assert data["topic"]["evidence"] == [{"evidenceId": "e1", "text": "月球反射太阳光。"}]
    if task == "repair":
        assert data["quiz"] == candidate
        assert data["issues"] == ["q01 选项重复"]
    [usage] = [entry for entry in caplog.records if getattr(entry, "ai_task", None) == task]
    assert usage.total_tokens == 25
    assert "月球反射太阳光" not in caplog.text


@pytest.mark.parametrize(
    ("content", "finish_reason", "status", "error_code", "timeout", "code", "retryable"),
    [
        ("   ", "stop", 200, None, False, "PROVIDER_INVALID_OUTPUT", True),
        (
            '{"title":"valid until truncated"}',
            "length",
            200,
            None,
            False,
            "PROVIDER_INVALID_OUTPUT",
            True,
        ),
        ('[{"title":"array"}]', "stop", 200, None, False, "PROVIDER_INVALID_OUTPUT", True),
        (
            '{"title":"one","title":"two"}',
            "stop",
            200,
            None,
            False,
            "PROVIDER_INVALID_OUTPUT",
            True,
        ),
        ("{}", "stop", 200, None, False, "PROVIDER_INVALID_OUTPUT", True),
        ("{broken}", "stop", 200, None, False, "PROVIDER_INVALID_OUTPUT", True),
        ("", "content_filter", 200, None, False, "UNSUITABLE_CONTENT", False),
        ("", "stop", 400, "content_filter", False, "UNSUITABLE_CONTENT", False),
        ("", "stop", 401, None, False, "PROVIDER_CONFIGURATION", False),
        ("", "stop", 429, None, False, "PROVIDER_UNAVAILABLE", True),
        ("", "stop", 503, None, False, "PROVIDER_UNAVAILABLE", True),
        ("", "stop", 200, None, True, "PROVIDER_TIMEOUT", True),
    ],
)
def test_generation_reports_sanitized_failure_once(
    ai_module, provider, topic, content, finish_reason, status, error_code, timeout, code, retryable
):
    adapter, requests = provider(
        content=content,
        finish_reason=finish_reason,
        status=status,
        error_code=error_code,
        timeout=timeout,
    )
    with pytest.raises(ai_module.AIError) as raised:
        adapter.generate(topic)

    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert "private response body" not in str(raised.value)
    assert "private response body" not in raised.value.message
    assert len(requests) == 1


@pytest.mark.parametrize(
    "result",
    [
        {"accepted": "true", "issues": []},
        {"accepted": True, "issues": ["q01 的答案没有依据"]},
        {"accepted": False, "issues": []},
        {"accepted": False, "issues": ["   "]},
        {"accepted": True, "issues": [], "unreviewed": True},
    ],
)
def test_review_rejects_ambiguous_or_inconsistent_verdict(ai_module, provider, topic, result):
    adapter, requests = provider(content=json.dumps(result))
    with pytest.raises(ai_module.AIError) as raised:
        adapter.review(topic, {"questions": []})

    assert raised.value.code == "PROVIDER_INVALID_OUTPUT"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "result",
    [{"accepted": True, "issues": []}, {"accepted": False, "issues": ["q01 的答案没有依据"]}],
)
def test_review_returns_strict_verdict_for_the_supplied_quiz(provider, topic, result):
    adapter, requests = provider(content=json.dumps(result))
    candidate = {"title": "月相", "questions": [{"questionId": "q01"}]}
    assert adapter.review(topic, candidate) == result
    assert len(requests) == 1
    assert json.loads(requests[0]["messages"][1]["content"])["quiz"] == candidate


def test_summary_sends_only_the_learning_snapshot_and_accepts_citations(provider, record):
    result = {
        "knowledgeReview": [
            {"text": "月球反射太阳光。", "questionIds": ["q01"], "evidenceIds": ["e1"]}
        ],
        "mistakeNotes": [{"text": "复习月光来源。", "questionIds": ["q01"], "evidenceIds": ["e1"]}],
        "reviewSuggestions": [
            {"text": "试着解释月光。", "questionIds": ["q01"], "evidenceIds": ["e1"]}
        ],
    }
    adapter, requests = provider(content=json.dumps(result))

    assert adapter.summarize(record) == result
    assert len(requests) == 1
    prompt = requests[0]["messages"][1]["content"]
    assert "private-" not in prompt
    snapshot = json.loads(prompt)
    assert set(snapshot) == {"topicTitle", "takeaways", "takeawayEvidence", "answers"}
    assert snapshot["takeawayEvidence"] == [{"evidenceId": "e2", "text": "月光来自反射。"}]
    assert snapshot["answers"][0]["question"]["options"][0] == {"optionId": "A", "text": "正确"}
    assert snapshot["answers"][0]["selectedOptionIds"] == ["B"]
    assert snapshot["answers"][0]["isCorrect"] is False
    assert snapshot["answers"][0]["evidence"] == [{"evidenceId": "e1", "text": "月球反射太阳光。"}]


def test_summary_rejects_wrong_field_types(ai_module, provider, record):
    adapter, requests = provider(
        content=json.dumps(
            {
                "knowledgeReview": [
                    {"text": "复习月光", "questionIds": "q01", "evidenceIds": ["e1"]}
                ],
                "mistakeNotes": [],
                "reviewSuggestions": [],
            }
        )
    )
    with pytest.raises(ai_module.AIError) as raised:
        adapter.summarize(record)

    assert raised.value.code == "PROVIDER_INVALID_OUTPUT"
    assert len(requests) == 1
