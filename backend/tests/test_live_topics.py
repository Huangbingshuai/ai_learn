import json
from copy import deepcopy
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from app.db import GenerationJob, Quiz, SavedTopic, utcnow
from app.domain.topics import get_topic_version, load_fixed_quiz
from app.worker.queue import Worker
from app.worker.search import SEARCH_URL, TavilySearch


def test_search_accepts_only_passages_present_in_raw_content():
    excerpt = "Direct source evidence with an identifiable publication and enough text to review."
    invented = "A summary that cannot be found anywhere in the returned source page text."

    def respond(request):
        assert str(request.url) == SEARCH_URL
        assert request.headers["Authorization"] == "Bearer test-token"
        payload = json.loads(request.content)
        assert payload["include_raw_content"] == "text"
        assert payload["include_answer"] is False
        result = {
            "url": "https://science.example.org/article#intro",
            "title": "Source article",
            "score": 0.9,
            "content": f"{excerpt} [...] {invented}",
            "raw_content": excerpt,
        }
        return httpx.Response(
            200,
            json={
                "results": [result, {**result, "url": "https://127.0.0.1/article"}],
                "answer": invented,
            },
        )

    search = TavilySearch("test-token", 1, httpx.MockTransport(respond))
    result = search.passages(["sample"])
    assert [(item["sourceUrl"], item["text"]) for item in result] == [
        ("https://science.example.org/article", excerpt),
    ]


def test_live_job_tracks_actual_phases_and_publishes_only_verified_set(
    client, login, database, settings
):
    headers = login()
    job_id = client.post(
        "/v1/generation-jobs",
        json={"topic": "入门了解地轴倾斜"},
        headers={**headers, "Idempotency-Key": "live-topic"},
    ).json()["jobId"]
    path = f"/v1/generation-jobs/{job_id}"
    phases = []
    evidence = get_topic_version("seasons", "v1")["evidence"]
    assert len({item["sourceUrl"] for item in evidence}) >= 2

    def phase():
        phases.append(client.get(path, headers=headers).json()["phase"])

    class Search:
        def passages(self, queries):
            assert queries == ["地轴倾斜"]
            phase()
            return evidence

    class Model:
        def resolve_topic(self, value):
            assert value == "入门了解地轴倾斜"
            phase()
            return {
                "title": "地轴倾斜",
                "scope": "理解地轴倾斜与季节",
                "queries": ["地轴倾斜"],
                "needsClarification": False,
            }

        def select_sources(self, intent, passages):
            assert intent["title"] == "地轴倾斜"
            phase()
            return {"evidenceIds": [item["evidenceId"] for item in passages]}

        def generate(self, topic):
            assert topic["scope"] == "理解地轴倾斜与季节"
            phase()
            return deepcopy(load_fixed_quiz())

        def review(self, topic, quiz):
            assert len(quiz["questions"]) == 20
            phase()
            return {"accepted": True, "issues": []}

    assert Worker(settings, database, Model(), Search()).process_once()
    result = client.get(path, headers=headers).json()
    assert result["status"] == "SUCCEEDED" and result["quizId"]
    assert result["topicTitle"] == "地轴倾斜"
    assert phases == [
        "RESOLVING_TOPIC",
        "READING_SOURCES",
        "READING_SOURCES",
        "GENERATING",
        "VERIFYING",
    ]
    with database() as db:
        job = db.get(GenerationJob, job_id)
        assert job.search_count == 1
        assert job.checkpoint is None and job.topic_input is None

    topic = client.get("/v1/topics", headers=headers).json()["items"][0]
    assert topic["title"] == "地轴倾斜" and topic["art"] == "topic"
    with database.begin() as db:
        db.get(Quiz, result["quizId"]).expires_at = utcnow() - timedelta(days=2)
    Worker(settings, database, Model()).cleanup()
    opened = client.post(f"/v1/topics/{topic['topicKey']}/quiz", headers=headers)
    assert opened.status_code == 200
    assert opened.json()["quizId"] == result["quizId"]
    other = login("second-device-unique")
    assert client.post(f"/v1/topics/{topic['topicKey']}/quiz", headers=other).status_code == 404
    assert topic not in client.get("/v1/topics", headers=other).json()["items"]
    assert (
        client.post(
            "/v1/generation-jobs",
            json={"topicKey": topic["topicKey"]},
            headers={**other, "Idempotency-Key": "private-topic"},
        ).status_code
        == 404
    )
    repeat = client.post(
        "/v1/generation-jobs",
        json={"topicKey": topic["topicKey"]},
        headers={**headers, "Idempotency-Key": "repeat-topic"},
    )
    assert repeat.status_code == 202
    assert Worker(settings, database, Model()).process_once()
    repeat_result = client.get(
        f"/v1/generation-jobs/{repeat.json()['jobId']}", headers=headers
    ).json()
    assert repeat_result["status"] == "SUCCEEDED"
    assert repeat_result["quizId"] != result["quizId"]
    with database() as db:
        assert db.scalar(select(func.count()).select_from(SavedTopic)) == 1


def test_insufficient_sources_does_not_create_saved_topic(client, login, database, settings):
    headers = login()
    queued = client.post(
        "/v1/generation-jobs",
        json={"topic": "新的主题"},
        headers={**headers, "Idempotency-Key": "no-sources"},
    ).json()

    class Model:
        def resolve_topic(self, _value):
            return {
                "title": "新主题",
                "scope": "测试资料不足",
                "queries": ["新主题"],
                "needsClarification": False,
            }

    class Search:
        def passages(self, _queries):
            return []

    assert Worker(settings, database, Model(), Search()).process_once()
    result = client.get(f"/v1/generation-jobs/{queued['jobId']}", headers=headers).json()
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "SOURCE_INSUFFICIENT"
    with database() as db:
        assert db.scalar(select(func.count()).select_from(SavedTopic)) == 0
