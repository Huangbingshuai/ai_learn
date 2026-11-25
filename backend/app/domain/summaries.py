from pydantic import Field, model_validator

from app.domain.quizzes import Identifier, QuizModel

RULES_VERSION = "summary-v1"


class SummaryItem(QuizModel):
    text: str = Field(min_length=1, max_length=500)
    questionIds: list[Identifier] = Field(max_length=20)
    evidenceIds: list[Identifier] = Field(max_length=20)

    @model_validator(mode="after")
    def has_reference(self):
        if not self.questionIds and not self.evidenceIds:
            raise ValueError("Reference a question or source")
        return self


class SummaryContent(QuizModel):
    knowledgeReview: list[SummaryItem] = Field(min_length=1, max_length=12)
    mistakeNotes: list[SummaryItem] = Field(max_length=20)
    reviewSuggestions: list[SummaryItem] = Field(min_length=1, max_length=12)


def validate_summary(raw: dict, record: dict) -> SummaryContent:
    summary = SummaryContent.model_validate(raw)
    answers = {answer["question"]["questionId"]: answer for answer in record["answers"]}
    mistake_ids = {
        question_id for question_id, answer in answers.items() if not answer["isCorrect"]
    }
    evidence_ids = {
        source["evidenceId"] for answer in answers.values() for source in answer["evidence"]
    }
    evidence_ids.update(source["evidenceId"] for source in record.get("takeawayEvidence", []))
    if bool(summary.mistakeNotes) != bool(mistake_ids):
        raise ValueError("Mistake notes must reflect the actual score")
    for entry in [*summary.knowledgeReview, *summary.mistakeNotes, *summary.reviewSuggestions]:
        questions = set(entry.questionIds)
        sources = set(entry.evidenceIds)
        if (
            len(questions) != len(entry.questionIds)
            or len(sources) != len(entry.evidenceIds)
            or not questions <= answers.keys()
            or not sources <= evidence_ids
        ):
            raise ValueError("Invalid learning record citation")
    for entry in summary.mistakeNotes:
        cited_questions = set(entry.questionIds)
        if not cited_questions or not cited_questions <= mistake_ids:
            raise ValueError("A mistake note must only reference actual mistakes")
        cited_sources = {
            source["evidenceId"]
            for question_id in cited_questions
            for source in answers[question_id]["evidence"]
        }
        if not set(entry.evidenceIds) <= cited_sources:
            raise ValueError("Mistake evidence must correspond to the cited wrong answers")
    return summary
