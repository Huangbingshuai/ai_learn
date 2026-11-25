from copy import deepcopy

import pytest

from app.domain.quizzes import validate_quiz
from tests.topic_fixtures import get_topic_version, load_fixed_quiz


def test_topic_picker_lists_only_topics_saved_by_the_user(client, login):
    assert client.get("/v1/topics").status_code == 401
    result = client.get("/v1/topics", headers=login()).json()
    assert result == {"items": []}


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
    raw = deepcopy(load_fixed_quiz())
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
        validate_quiz(raw, get_topic_version("seasons", "v1"))


def test_quiz_validation_keeps_legacy_twenty_question_sets_readable():
    raw = deepcopy(load_fixed_quiz())
    source = get_topic_version("seasons", "v1")
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


def test_removed_builtin_topic_keys_cannot_open_quizzes(client, login):
    headers = login()
    assert client.post("/v1/topics/seasons/quiz", headers=headers).status_code == 404
    assert client.post("/v1/topics/moon/quiz", headers=headers).status_code == 404


def test_user_can_delete_only_their_saved_topic(client, login, database):
    from datetime import UTC, datetime

    from app.db import SavedTopic

    headers = login()
    other = login("other-delete-topic")
    user_id = client.get("/v1/me", headers=headers).json()["userId"]
    topic_key = "live-" + "a" * 59
    with database.begin() as db:
        db.add(
            SavedTopic(
                user_id=user_id,
                topic_key=topic_key,
                title="我的主题",
                scope="只测试删除行为",
                evidence=[],
                evidence_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )

    assert client.delete(f"/v1/topics/{topic_key}", headers=other).status_code == 404
    assert client.delete("/v1/topics/seasons", headers=headers).status_code == 404
    deleted = client.delete(f"/v1/topics/{topic_key}", headers=headers)
    assert deleted.status_code == 204
    assert topic_key not in {
        item["topicKey"] for item in client.get("/v1/topics", headers=headers).json()["items"]
    }
    assert client.delete(f"/v1/topics/{topic_key}", headers=headers).status_code == 404
