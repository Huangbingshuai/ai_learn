import importlib
import json
import logging

import httpx
import pytest

from app.config import Settings
from app.domain.quizzes import validate_quiz
from app.worker.queue import _validation_issues
from tests.topic_fixtures import get_topic_version, load_fixed_quiz


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
    real_model = ai_module.QwenChatOpenAI

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
                    "model": "qwen-flash",
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

        transport = httpx.MockTransport(respond)
        client = httpx.Client(transport=transport)
        async_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr(
            ai_module,
            "QwenChatOpenAI",
            lambda **kwargs: real_model(
                http_client=client, http_async_client=async_client, **kwargs
            ),
        )
        return ai_module.QwenAI(api_key="test-key", model="qwen-flash", timeout=3), requests

    return configure


def test_qwen_client_uses_dashscope_compatible_endpoint(ai_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(ai_module, "QwenChatOpenAI", lambda **kwargs: captured.update(kwargs))

    adapter = ai_module.QwenAI("dashscope-key", "qwen-flash", 3)

    assert adapter.provider == "qwen"
    assert captured["model"] == "qwen-flash"
    assert captured["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert captured["extra_body"] == {"enable_thinking": False}
    assert captured["max_retries"] == 0


def test_deepseek_fallback_keeps_thinking_disabled(ai_module, monkeypatch):
    captured = {}
    monkeypatch.setattr(ai_module, "ChatDeepSeek", lambda **kwargs: captured.update(kwargs))

    adapter = ai_module.DeepSeekAI("deepseek-key", "deepseek-flash", 3)

    assert adapter.provider == "deepseek"
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_build_ai_selects_configured_provider(ai_module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ai_module, "QwenAI", lambda *args: calls.append(("qwen", args)) or "qwen-ai"
    )
    monkeypatch.setattr(
        ai_module, "DeepSeekAI", lambda *args: calls.append(("deepseek", args)) or "deepseek-ai"
    )

    qwen = Settings(
        _env_file=None,
        ai_provider="qwen",
        dashscope_api_key="dashscope-key",
        qwen_model="qwen-flash",
        model_timeout=3,
    )
    assert ai_module.build_ai(qwen) == "qwen-ai"
    assert calls[-1] == (
        "qwen",
        ("dashscope-key", "qwen-flash", 3, qwen.dashscope_base_url),
    )
    assert qwen.ai_model == "qwen-flash"

    deepseek = Settings(
        _env_file=None,
        ai_provider="deepseek",
        deepseek_api_key="deepseek-key",
        deepseek_model="deepseek-flash",
        model_timeout=3,
    )
    assert ai_module.build_ai(deepseek) == "deepseek-ai"
    assert calls[-1] == ("deepseek", ("deepseek-key", "deepseek-flash", 3))
    assert deepseek.ai_model == "deepseek-flash"


def test_build_ai_requires_the_selected_provider_key(ai_module):
    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        ai_module.build_ai(Settings(_env_file=None, ai_provider="qwen", dashscope_api_key=""))
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        ai_module.build_ai(Settings(_env_file=None, ai_provider="deepseek", deepseek_api_key=""))


def test_build_ai_falls_back_when_the_selected_key_is_missing(ai_module, monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(
        ai_module, "QwenAI", lambda *args: calls.append(("qwen", args)) or "qwen-ai"
    )
    monkeypatch.setattr(
        ai_module, "DeepSeekAI", lambda *args: calls.append(("deepseek", args)) or "deepseek-ai"
    )

    deepseek = Settings(
        _env_file=None,
        ai_provider="qwen",
        dashscope_api_key="",
        deepseek_api_key="deepseek-key",
        model_timeout=3,
    )
    with caplog.at_level(logging.WARNING):
        assert ai_module.build_ai(deepseek) == "deepseek-ai"
    assert calls[-1] == ("deepseek", ("deepseek-key", "deepseek-flash", 3))
    assert "DASHSCOPE_API_KEY 未配置" in caplog.text

    qwen = Settings(
        _env_file=None,
        ai_provider="deepseek",
        dashscope_api_key="dashscope-key",
        deepseek_api_key="",
        model_timeout=3,
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert ai_module.build_ai(qwen) == "qwen-ai"
    assert calls[-1][0] == "qwen"
    assert "DEEPSEEK_API_KEY 未配置" in caplog.text


def test_dashscope_base_url_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(_env_file=None, dashscope_base_url="http://dashscope.example.com")
    settings = Settings(_env_file=None, dashscope_base_url="https://dashscope.example.com/")
    assert settings.dashscope_base_url == "https://dashscope.example.com"


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
    assert request["model"] == "qwen-flash"
    assert request["response_format"] == {"type": "json_object"}
    assert request["enable_thinking"] is False
    assert "thinking" not in request
    assert request["max_tokens"] >= 6000
    assert "max_completion_tokens" not in request
    assert "tools" not in request
    assert "12" in request["messages"][0]["content"]
    data = json.loads(request["messages"][1]["content"])
    assert data["topic"]["evidence"] == [{"evidenceId": "e1", "text": "月球反射太阳光。"}]
    if task == "repair":
        assert data["quiz"] == candidate
        assert data["issues"] == ["q01 选项重复"]
    [usage] = [entry for entry in caplog.records if getattr(entry, "ai_task", None) == task]
    assert usage.total_tokens == 25
    assert "月球反射太阳光" not in caplog.text


@pytest.fixture
def live_candidate():
    topic = {**get_topic_version("seasons", "v1"), "scope": "理解季节形成"}
    candidate = load_fixed_quiz()
    for index, question in enumerate(candidate["questions"], 1):
        question["questionId"] = f"q{index:02d}"
    return topic, candidate


def test_live_repair_preserves_questions_not_flagged_by_reviewer(provider, live_candidate):
    topic, candidate = live_candidate
    changed = {**candidate["questions"][3], "explanation": "改写后的讲解。"}
    adapter, requests = provider(content=json.dumps({"questions": [changed], "takeaways": None}))

    repaired = adapter.repair(topic, candidate, ["q04 的讲解超出了原文依据。"])

    assert repaired["questions"] == [
        *candidate["questions"][:3],
        changed,
        *candidate["questions"][4:],
    ]
    assert repaired["takeaways"] == candidate["takeaways"]
    request = json.loads(requests[0]["messages"][1]["content"])
    assert request["targetQuestionIds"] == ["q04"]


def test_repair_selected_fixes_only_the_structurally_invalid_question(provider):
    topic = get_topic_version("seasons", "v1")
    candidate = load_fixed_quiz()
    for index, question in enumerate(candidate["questions"], 1):
        question["questionId"] = f"q{index:02d}"
    broken = {**candidate, "questions": [dict(question) for question in candidate["questions"]]}
    broken["questions"][4] = {
        **broken["questions"][4],
        "correctOptionIds": [broken["questions"][4]["correctOptionIds"][0]],
    }
    fixed = {**broken["questions"][4], "correctOptionIds": ["o1", "o2"]}
    adapter, requests = provider(content=json.dumps({"questions": [fixed], "takeaways": None}))

    with pytest.raises(ValueError) as failure:
        validate_quiz(broken, topic)
    issues = _validation_issues(failure.value, broken)
    assert issues[0].startswith("q05:")

    repaired = adapter.repair(topic, broken, issues)

    assert repaired["questions"][4] == fixed
    assert repaired["questions"][:4] == broken["questions"][:4]
    assert repaired["questions"][5:] == broken["questions"][5:]
    request = json.loads(requests[0]["messages"][1]["content"])
    assert request["targetQuestionIds"] == ["q05"]
    assert "json" in requests[0]["messages"][0]["content"].lower()


def test_live_repair_falls_back_when_patch_omits_target(provider, live_candidate):
    topic, candidate = live_candidate
    patch = {"questions": [candidate["questions"][4]], "takeaways": None}
    adapter, requests = provider(content=json.dumps(patch))

    repaired = adapter.repair(topic, candidate, ["q04 的讲解超出了原文依据。"])

    assert repaired == patch
    assert len(requests) == 2


def test_live_repair_accepts_empty_takeaways_when_unchanged(provider, live_candidate):
    topic, candidate = live_candidate
    changed = {**candidate["questions"][3], "explanation": "改写后的讲解。"}
    adapter, requests = provider(content=json.dumps({"questions": [changed], "takeaways": []}))

    repaired = adapter.repair(topic, candidate, ["q04 的讲解超出了原文依据。"])

    assert repaired["questions"][3] == changed
    assert repaired["takeaways"] == candidate["takeaways"]
    assert len(requests) == 1


def test_live_repair_replaces_only_takeaways_when_requested(provider, live_candidate):
    topic, candidate = live_candidate
    takeaways = [
        {"text": "地轴倾斜是四季形成的关键。", "evidenceIds": ["earth-tilt"]},
    ]
    adapter, requests = provider(content=json.dumps({"questions": [], "takeaways": takeaways}))

    repaired = adapter.repair(topic, candidate, ["知识要点缺少直接证据。"])

    assert repaired["takeaways"] == takeaways
    assert repaired["questions"] == candidate["questions"]
    assert len(requests) == 1


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
        ("", "stop", 400, "data_inspection_failed", False, "UNSUITABLE_CONTENT", False),
        ("", "stop", 402, None, False, "PROVIDER_QUOTA_EXHAUSTED", False),
        ("", "stop", 400, "Arrearage", False, "PROVIDER_QUOTA_EXHAUSTED", False),
        ("", "stop", 403, "AllocationQuota.FreeTierOnly", False, "PROVIDER_QUOTA_EXHAUSTED", False),
        ("", "stop", 429, "insufficient_quota", False, "PROVIDER_UNAVAILABLE", True),
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


def test_summary_normalizes_single_citation_ids(provider, record):
    result = {
        "knowledgeReview": [{"text": "月光来自反射。", "evidenceId": "e1"}],
        "mistakeNotes": [{"text": "复习月光来源。", "questionId": "q01"}],
        "reviewSuggestions": [{"text": "解释月光。", "questionId": "q01", "evidenceId": "e1"}],
    }
    adapter, _ = provider(content=json.dumps(result))

    normalized = adapter.summarize(record)

    assert normalized["knowledgeReview"][0]["evidenceIds"] == ["e1"]
    assert normalized["mistakeNotes"][0]["questionIds"] == ["q01"]
    assert normalized["mistakeNotes"][0]["evidenceIds"] == []
    assert normalized["reviewSuggestions"][0]["questionIds"] == ["q01"]
