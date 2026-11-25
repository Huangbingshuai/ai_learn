from copy import deepcopy

import pytest

from app.domain.summaries import validate_summary


@pytest.fixture
def record():
    return {
        "answers": [
            {
                "question": {"questionId": "wrong"},
                "isCorrect": False,
                "evidence": [{"evidenceId": "e-wrong"}],
            },
            {
                "question": {"questionId": "right"},
                "isCorrect": True,
                "evidence": [{"evidenceId": "e-right"}],
            },
        ]
    }


@pytest.fixture
def summary():
    item = {"text": "复习本题对应的知识点。", "questionIds": ["wrong"], "evidenceIds": ["e-wrong"]}
    return {
        "knowledgeReview": [deepcopy(item)],
        "mistakeNotes": [deepcopy(item)],
        "reviewSuggestions": [deepcopy(item)],
    }


def test_correctly_cited_mistake_is_accepted(summary, record):
    assert validate_summary(summary, record).mistakeNotes[0].questionIds == ["wrong"]


@pytest.mark.parametrize(
    "defect",
    [
        "missing_mistake",
        "perfect_score",
        "right_answer",
        "unknown_evidence",
        "unrelated_evidence",
        "empty_reference",
        "duplicate_citation",
    ],
)
def test_summary_citations_are_bounded_to_the_finished_attempt(summary, record, defect):
    if defect == "missing_mistake":
        summary["mistakeNotes"] = []
    if defect == "perfect_score":
        record["answers"][0]["isCorrect"] = True
    if defect == "right_answer":
        summary["mistakeNotes"][0]["questionIds"] = ["right"]
    if defect == "unknown_evidence":
        summary["mistakeNotes"][0]["evidenceIds"] = ["invented"]
    if defect == "unrelated_evidence":
        summary["mistakeNotes"][0]["evidenceIds"] = ["e-right"]
    if defect == "empty_reference":
        summary["knowledgeReview"][0].update(questionIds=[], evidenceIds=[])
    if defect == "duplicate_citation":
        summary["knowledgeReview"][0]["questionIds"] = ["wrong", "wrong"]
    with pytest.raises(ValueError):
        validate_summary(summary, record)
