import unicodedata
from datetime import date, timedelta
from hashlib import sha256
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

SAVED_SOURCE_MAX_AGE = timedelta(days=30)


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        ch for ch in folded if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )


def live_topic_key(title: str) -> str:
    return "live-" + sha256(normalize(title).encode()).hexdigest()[:59]


class EvidenceView(BaseModel):
    evidenceId: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=1600)
    sourceTitle: str = Field(min_length=1, max_length=200)
    sourceOrganization: str = Field(min_length=1, max_length=120)
    sourceUrl: str = Field(min_length=1, max_length=2048)
    verifiedAt: date

    @field_validator("sourceUrl")
    @classmethod
    def https_source(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Expected a public HTTPS source URL")
        return value


class TopicView(BaseModel):
    topicKey: str
    title: str
    originalInput: str | None = None
    art: Literal["topic"] = "topic"
    canDelete: bool = True
    attemptId: str | None = None
    quizId: str | None = None
    answeredCount: int | None = None
    totalQuestions: int | None = None
    attemptStatus: Literal["ACTIVE", "PASSED", "ABANDONED"] | None = None
