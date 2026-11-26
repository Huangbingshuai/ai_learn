import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "topics"


def load_fixed_quiz() -> dict:
    return json.loads((FIXTURE_DIR / "fixed-seasons.json").read_text(encoding="utf-8"))


def get_topic_version(key: str, version: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{key}.{version}.json").read_text(encoding="utf-8"))


def create_test_quiz(client, headers: dict[str, str]) -> dict[str, str]:
    from app.db import GenerationJob, Quiz, SavedTopic, utcnow
    from app.domain.quizzes import RULES_VERSION, validate_quiz

    user_id = client.get("/v1/me", headers=headers).json()["userId"]
    now = utcnow()
    topic = get_topic_version("seasons", "v1")
    quiz_id = str(uuid4())
    job_id = str(uuid4())
    settings = client.app.state.settings
    with client.app.state.session_factory.begin() as db:
        if db.get(SavedTopic, (user_id, "test-topic")) is None:
            db.add(SavedTopic(
                user_id=user_id, topic_key="test-topic", title="测试练习", original_input="测试练习",
                scope="Test scope", evidence=topic["evidence"], evidence_at=now, updated_at=now,
            ))
        db.add(GenerationJob(
            id=job_id, user_id=user_id, source="TOPIC", topic_key="test-topic", topic_version=None,
            input_digest="b" * 64, idempotency_key=f"test-{uuid4()}", status="SUCCEEDED",
            next_run_at=now, created_at=now, expires_at=now + timedelta(hours=1),
            model_name="test-fixture", rules_version=RULES_VERSION,
        ))
        db.add(Quiz(
            id=quiz_id, job_id=job_id, user_id=user_id, topic_key="test-topic", topic_title="测试练习",
            content=validate_quiz(load_fixed_quiz(), topic).model_dump(mode="json"),
            evidence=topic["evidence"], ai_generated=False, created_at=now,
            expires_at=now + timedelta(hours=settings.quiz_hours), rules_version=RULES_VERSION,
        ))
    return {"quizId": quiz_id}
