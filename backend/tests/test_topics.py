import json
from copy import deepcopy

import pytest

from app.domain import topics
from app.domain.quizzes import validate_quiz


def test_topic_picker_requires_login_and_lists_the_prototype_topics(client, login):
    assert client.get("/v1/topics").status_code == 401
    result = client.get("/v1/topics", headers=login()).json()
    assert result == {
        "items": [
            {"topicKey": "seasons", "title": "四季成因", "art": "earth"},
            {"topicKey": "moon", "title": "月相变化", "art": "moon"},
        ]
    }


def test_matching_uses_whitelisted_names_and_normalized_aliases():
    assert topics.match_topic("　为什么会有 四季？　").topicKey == "seasons"
    assert topics.match_topic("月亮圆缺").topicKey == "moon"
    assert topics.match_topic("地理") is None
    assert topics.match_topic("？ ！") is None


def test_versioned_sources_do_not_change_when_catalog_advances(monkeypatch, tmp_path):
    original = topics.get_topic_version("seasons", "v1")
    path = tmp_path / "seasons.v1.json"
    path.write_text(json.dumps(original))
    revised = {**original, "version": "v2"}
    (tmp_path / "seasons.v2.json").write_text(json.dumps(revised))
    monkeypatch.setattr(topics, "DATA_DIR", tmp_path)
    monkeypatch.setattr(topics, "CURRENT_VERSIONS", {"seasons": "v2"})
    assert topics.match_topic("四季").version == "v2"
    assert topics.get_topic_version("seasons", "v1") == original
    with pytest.raises(ValueError):
        topics.get_topic_version("seasons", "v3")


@pytest.mark.parametrize("tamper", ["too_few", "stale", "duplicate", "plain_http"])
def test_bad_source_versions_are_rejected(monkeypatch, tmp_path, tamper):
    payload = topics.get_topic_version("seasons", "v1")
    if tamper == "too_few":
        payload["evidence"] = payload["evidence"][:2]
    if tamper == "stale":
        payload["evidence"][0]["verifiedAt"] = "2000-01-01"
    if tamper == "duplicate":
        payload["evidence"].append(payload["evidence"][0])
    if tamper == "plain_http":
        payload["evidence"][0]["sourceUrl"] = "http://example.com/"
    (tmp_path / "seasons.v1.json").write_text(json.dumps(payload))
    monkeypatch.setattr(topics, "DATA_DIR", tmp_path)
    with pytest.raises(ValueError):
        topics.get_topic_version("seasons", "v1")


@pytest.mark.parametrize(
    "tamper",
    [
        "wrong_count",
        "wrong_id",
        "duplicate_option",
        "duplicate_question",
        "wrong_evidence",
        "no_multi",
        "bad_judge",
    ],
)
def test_generated_quiz_must_be_valid_and_grounded(tamper):
    raw = deepcopy(topics.load_fixed_quiz())
    if tamper == "wrong_count":
        raw["questions"].pop()
    if tamper == "wrong_id":
        raw["questions"][0]["correctOptionIds"] = ["made-up"]
    if tamper == "duplicate_option":
        raw["questions"][0]["options"][1]["text"] = raw["questions"][0]["options"][0]["text"] + "。"
    if tamper == "duplicate_question":
        raw["questions"][1]["questionId"] = raw["questions"][0]["questionId"]
    if tamper == "wrong_evidence":
        raw["takeaways"][0]["evidenceIds"] = ["moon-light"]
    if tamper == "no_multi":
        for question in raw["questions"]:
            if question["type"] == "multi":
                question["type"] = "single"
                question["correctOptionIds"] = question["correctOptionIds"][:1]
    if tamper == "bad_judge":
        judge = next(question for question in raw["questions"] if question["type"] == "judge")
        judge["options"][0]["text"] = "是"
    with pytest.raises(ValueError):
        validate_quiz(raw, topics.get_topic_version("seasons", "v1"))


def test_quiz_validation_keeps_legacy_twenty_question_sets_readable():
    raw = deepcopy(topics.load_fixed_quiz())
    source = topics.get_topic_version("seasons", "v1")
    while len(raw["questions"]) < 20:
        index = len(raw["questions"])
        question = deepcopy(raw["questions"][index % 12])
        question["questionId"] = f"legacy-{index + 1}"
        question["stem"] += f"（旧题组 {index + 1}）"
        raw["questions"].append(question)
    assert len(validate_quiz(raw, source).questions) == 20

    raw["questions"].pop()
    with pytest.raises(ValueError):
        validate_quiz(raw, source)


def test_seed_topics_open_saved_quiz_without_generation(client, login, database):
    from app.db import Quiz

    headers = login()
    first = client.post("/v1/topics/seasons/quiz", headers=headers)
    assert first.status_code == 200
    assert client.post("/v1/topics/seasons/quiz", headers=headers).json() == first.json()
    other = login("other-user-seed")
    assert client.post("/v1/topics/moon/quiz", headers=other).status_code == 404
    with database.begin() as session:
        session.get(Quiz, first.json()["quizId"]).topic_key = "moon"
    moon = client.post("/v1/topics/moon/quiz", headers=headers)
    assert moon.status_code == 200
    assert moon.json()["quizId"] == first.json()["quizId"]
