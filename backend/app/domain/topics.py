import json
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "topics"
CURRENT_VERSIONS = {"seasons": "v1", "moon": "v1"}
SOURCE_MAX_AGE = timedelta(days=365)
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


class Evidence(EvidenceView):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    usageScope: str = Field(min_length=1, max_length=240)


class TopicView(BaseModel):
    topicKey: str
    title: str
    art: Literal["earth", "moon", "topic"]
    canDelete: bool = False


class Topic(TopicView):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    aliases: list[str]
    version: str
    evidence: list[Evidence]

    @model_validator(mode="after")
    def unique_evidence(self):
        if len({item.evidenceId for item in self.evidence}) != len(self.evidence):
            raise ValueError("Duplicate source identifier")
        return self


def load_topics() -> list[Topic]:
    return [_load_version(key, version) for key, version in CURRENT_VERSIONS.items()]


def _load_version(key: str, version: str) -> Topic:
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", key) or not re.fullmatch(r"v[0-9]+", version):
        raise ValueError("Invalid source version")
    try:
        payload = (DATA_DIR / f"{key}.{version}.json").read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError("Source version unavailable") from error
    topic = Topic.model_validate_json(payload)
    if (topic.topicKey, topic.version) != (key, version):
        raise ValueError("Source version mismatch")
    return topic


def get_topic_version(key: str, version: str) -> dict:
    topic = _load_version(key, version)
    today = datetime.now(UTC).date()
    # Source dates are calendar dates from the editor, potentially a day ahead of UTC.
    latest = today + timedelta(days=1)
    if len(topic.evidence) < 5 or any(
        not today - SOURCE_MAX_AGE <= item.verifiedAt <= latest for item in topic.evidence
    ):
        raise ValueError("Insufficient or outdated sources")
    return topic.model_dump(mode="json")


def match_topic(value: str) -> Topic | None:
    normalized = normalize(value)
    if not normalized:
        return None
    return next(
        (
            topic
            for topic in load_topics()
            if normalized in {normalize(title) for title in [topic.title, *topic.aliases]}
        ),
        None,
    )


def load_fixed_quiz() -> dict:
    return json.loads((DATA_DIR / "fixed-seasons.json").read_text(encoding="utf-8"))
