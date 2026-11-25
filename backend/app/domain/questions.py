"""Stable question identity shared by learning and review features."""

import json
import unicodedata
from hashlib import sha256


def _normalized_question_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def question_fingerprint(question: dict) -> str:
    identity = {
        "type": question["type"],
        "stem": _normalized_question_text(question["stem"]),
        "options": sorted(
            _normalized_question_text(option["text"]) for option in question["options"]
        ),
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()
