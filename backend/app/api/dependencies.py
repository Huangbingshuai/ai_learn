from hashlib import sha256
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import unauthenticated
from app.db import SessionRow, utcnow

bearer = HTTPBearer(auto_error=False)


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        yield db


def get_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None or len(credentials.credentials) > 128:
        raise unauthenticated()
    with request.app.state.session_factory() as db:
        user_id = db.scalar(
            select(SessionRow.user_id).where(
                SessionRow.token_digest == digest(credentials.credentials),
                SessionRow.expires_at > utcnow(),
            )
        )
    if user_id is None:
        raise unauthenticated()
    return user_id


DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[str, Depends(get_user)]
IdempotencyKey = Annotated[str, Header(min_length=1, max_length=128, pattern=r"^[!-~]+$")]
