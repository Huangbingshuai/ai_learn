from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.api.dependencies import DB, CurrentUser
from app.api.errors import ErrorView, not_found
from app.db import Record, User

router = APIRouter(responses={401: {"model": ErrorView}, 422: {"model": ErrorView}})


class ProfileView(BaseModel):
    userId: str
    nickname: str | None
    coinBalance: int


class ProfileUpdate(BaseModel):
    nickname: str | None = Field(max_length=30)

    @field_validator("nickname")
    @classmethod
    def nickname_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("昵称不能为空")
        return value


class ProfileStats(BaseModel):
    completedSets: int
    answeredQuestions: int
    correctAnswers: int
    accuracyPercent: int


@router.get("/me")
def get_profile(db: DB, user_id: CurrentUser) -> ProfileView:
    user = db.get(User, user_id)
    if user is None:
        raise not_found()
    return ProfileView(userId=user.id, nickname=user.nickname, coinBalance=user.coin_balance)


@router.get("/me/stats")
def get_profile_stats(db: DB, user_id: CurrentUser) -> ProfileStats:
    completed, answered, correct = db.execute(
        select(
            func.count(Record.id),
            func.coalesce(func.sum(Record.total_questions), 0),
            func.coalesce(func.sum(Record.correct_count), 0),
        ).where(Record.user_id == user_id)
    ).one()
    answered = int(answered)
    correct = int(correct)
    return ProfileStats(
        completedSets=int(completed),
        answeredQuestions=answered,
        correctAnswers=correct,
        accuracyPercent=round(correct / answered * 100) if answered else 0,
    )


@router.patch("/me")
def update_profile(body: ProfileUpdate, db: DB, user_id: CurrentUser) -> ProfileView:
    with db.begin():
        user = db.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise not_found()
        user.nickname = body.nickname
        return ProfileView(userId=user.id, nickname=user.nickname, coinBalance=user.coin_balance)
