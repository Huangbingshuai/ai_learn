from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import RateLimit


class QuotaReached(Exception):
    pass


def reserve_quota(db: Session, key: str, start: datetime, end: datetime, limit: int) -> None:
    if limit == 0:
        return
    count = db.scalar(
        insert(RateLimit)
        .values(key=key, window_start=start, count=1, expires_at=end)
        .on_conflict_do_update(
            index_elements=[RateLimit.key, RateLimit.window_start],
            set_={"count": RateLimit.count + 1},
            where=RateLimit.count < limit,
        )
        .returning(RateLimit.count)
    )
    if count is None:
        raise QuotaReached


def _summary_budgets(user_id: str, settings: Settings) -> dict[str, int]:
    return {
        "summary:global": settings.global_daily_summary_limit,
        f"summary:user:{user_id}": settings.user_daily_summary_limit,
    }


def _day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def reserve_summary_quota(db: Session, user_id: str, now: datetime, settings: Settings) -> None:
    start = _day(now)
    end = start + timedelta(days=1)
    for key, limit in _summary_budgets(user_id, settings).items():
        reserve_quota(db, key, start, end, limit)


def summary_quota_full(db: Session, user_id: str, now: datetime, settings: Settings) -> bool:
    budgets = _summary_budgets(user_id, settings)
    rows = db.execute(
        select(RateLimit.key, RateLimit.count).where(
            RateLimit.window_start == _day(now), RateLimit.key.in_(budgets)
        )
    )
    return any(budgets[key] > 0 and count >= budgets[key] for key, count in rows)
