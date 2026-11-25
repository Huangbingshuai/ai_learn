from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, tuple_, update

from app.api.attempts import QuizView
from app.api.dependencies import DB, CurrentUser
from app.api.errors import ApiError, ErrorView, not_found
from app.db import Attempt, Quiz, SavedTopic, utcnow
from app.domain.topics import CURRENT_VERSIONS, TopicView, load_topics

router = APIRouter(
    responses={401: {"model": ErrorView}, 404: {"model": ErrorView}, 422: {"model": ErrorView}}
)


class TopicList(BaseModel):
    items: list[TopicView]
    nextCursor: str | None = None


@router.get("/topics", response_model_exclude_none=True)
def list_topics(
    db: DB,
    user_id: CurrentUser,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> TopicList:
    statement = select(SavedTopic).where(SavedTopic.user_id == user_id)
    if cursor is not None:
        try:
            stamp, key = cursor.split("|")
            updated_at = datetime.fromisoformat(stamp)
            if updated_at.tzinfo is None or not key:
                raise ValueError("Invalid cursor")
        except ValueError:
            raise ApiError(422, "INVALID_INPUT", "主题列表分页已失效，请重新加载。") from None
        statement = statement.where(
            tuple_(SavedTopic.updated_at, SavedTopic.topic_key) < (updated_at, key)
        )
    rows = db.scalars(
        statement.order_by(SavedTopic.updated_at.desc(), SavedTopic.topic_key.desc()).limit(
            limit + 1
        )
    ).all()
    items = [
        TopicView(topicKey=row.topic_key, title=row.title, art="topic", canDelete=True)
        for row in rows[:limit]
    ]
    if cursor is None:
        # Initial examples are available to each session without writing shared saved topics.
        try:
            items.extend(load_topics())
        except ValueError:
            pass
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = f"{last.updated_at.isoformat()}|{last.topic_key}"
    return TopicList(items=items, nextCursor=next_cursor)


@router.delete("/topics/{topic_key}", status_code=204)
def delete_saved_topic(topic_key: str, db: DB, user_id: CurrentUser) -> Response:
    with db.begin():
        topic = db.scalar(
            select(SavedTopic)
            .where(SavedTopic.user_id == user_id, SavedTopic.topic_key == topic_key)
            .with_for_update()
        )
        if topic is None:
            raise not_found()
        db.delete(topic)
    return Response(status_code=204)


@router.post("/topics/{topic_key}/quiz")
def open_saved_quiz(topic_key: str, db: DB, user_id: CurrentUser, request: Request) -> QuizView:
    with db.begin():
        if topic_key not in CURRENT_VERSIONS and db.get(SavedTopic, (user_id, topic_key)) is None:
            raise not_found()
        quiz = db.scalar(
            select(Quiz)
            .where(Quiz.user_id == user_id, Quiz.topic_key == topic_key)
            .order_by(Quiz.created_at.desc(), Quiz.id.desc())
            .limit(1)
            .with_for_update()
        )
        if quiz is None and topic_key != "seasons":
            raise ApiError(404, "SAVED_QUIZ_UNAVAILABLE", "这组题尚未保存，请重新生成。")
        if quiz is None:
            return _create_initial_seasons(db, user_id, request)
        now = utcnow()
        if quiz.expires_at <= now:
            db.execute(
                update(Attempt)
                .where(Attempt.quiz_id == quiz.id, Attempt.status == "ACTIVE")
                .values(status="ABANDONED")
            )
            quiz.expires_at = now + timedelta(hours=request.app.state.settings.quiz_hours)
        return QuizView(quizId=quiz.id, quizExpiresAt=quiz.expires_at)


def _create_initial_seasons(db: DB, user_id: str, request: Request) -> QuizView:
    from app.api.dependencies import digest
    from app.db import GenerationJob, new_id
    from app.domain.quizzes import RULES_VERSION, validate_quiz
    from app.domain.topics import get_topic_version, load_fixed_quiz

    now = utcnow()
    try:
        topic = get_topic_version("seasons", "v1")
        content = validate_quiz(load_fixed_quiz(), topic).model_dump(mode="json")
    except (ValueError, OSError) as error:
        raise ApiError(503, "SOURCE_INSUFFICIENT", "预置题组暂时不可用。") from error
    job = GenerationJob(
        user_id=user_id,
        source="FIXED",
        topic_key="seasons",
        topic_version="v1",
        input_digest=digest("fixed-seasons"),
        idempotency_key=new_id(),
        status="SUCCEEDED",
        next_run_at=now,
        created_at=now,
        expires_at=now + timedelta(minutes=request.app.state.settings.job_minutes),
        model_name="fixed",
        rules_version=RULES_VERSION,
    )
    db.add(job)
    db.flush()
    quiz = Quiz(
        job_id=job.id,
        user_id=user_id,
        topic_key="seasons",
        topic_title=topic["title"],
        content=content,
        evidence=topic["evidence"],
        ai_generated=False,
        created_at=now,
        expires_at=now + timedelta(hours=request.app.state.settings.quiz_hours),
        rules_version=RULES_VERSION,
    )
    db.add(quiz)
    db.flush()
    return QuizView(quizId=quiz.id, quizExpiresAt=quiz.expires_at)
