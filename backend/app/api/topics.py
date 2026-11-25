from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, tuple_, update

from app.api.attempts import QuizView
from app.api.dependencies import DB, CurrentUser
from app.api.errors import ApiError, ErrorView, not_found
from app.db import Attempt, Quiz, SavedTopic, utcnow
from app.domain.topics import TopicView

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
    attempts = db.execute(
        select(Attempt, Quiz)
        .join(Quiz, Quiz.id == Attempt.quiz_id)
        .where(Attempt.user_id == user_id)
        .order_by(Attempt.created_at.desc())
    ).all()
    latest: dict[str, tuple[Attempt, Quiz]] = {}
    for attempt, quiz in attempts:
        latest.setdefault(quiz.topic_key, (attempt, quiz))
    items = []
    for row in rows[:limit]:
        recent = latest.get(row.topic_key)
        attempt, quiz = recent if recent else (None, None)
        items.append(TopicView(
            topicKey=row.topic_key,
            title=row.title,
            originalInput=row.original_input,
            art="topic",
            canDelete=True,
            attemptId=attempt.id if attempt else None,
            quizId=quiz.id if quiz else None,
            answeredCount=attempt.answered_count if attempt else None,
            totalQuestions=len(quiz.content["questions"]) if quiz else None,
            attemptStatus=attempt.status if attempt else None,
        ))
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
        if db.get(SavedTopic, (user_id, topic_key)) is None:
            raise not_found()
        quiz = db.scalar(
            select(Quiz)
            .where(Quiz.user_id == user_id, Quiz.topic_key == topic_key)
            .order_by(Quiz.created_at.desc(), Quiz.id.desc())
            .limit(1)
            .with_for_update()
        )
        if quiz is None:
            raise ApiError(404, "SAVED_QUIZ_UNAVAILABLE", "这组题尚未保存，请重新生成。")
        now = utcnow()
        if quiz.expires_at <= now:
            db.execute(
                update(Attempt)
                .where(Attempt.quiz_id == quiz.id, Attempt.status == "ACTIVE")
                .values(status="ABANDONED")
            )
            quiz.expires_at = now + timedelta(hours=request.app.state.settings.quiz_hours)
        return QuizView(quizId=quiz.id, quizExpiresAt=quiz.expires_at)
