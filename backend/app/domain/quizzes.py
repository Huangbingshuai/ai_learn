from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domain.topics import normalize

TOTAL_QUESTIONS = 20
RULES_VERSION = "quiz-v1"
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
QuestionType = Literal["single", "multi", "judge"]


class QuizModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Option(QuizModel):
    optionId: Identifier
    text: str = Field(min_length=1, max_length=120)


class Takeaway(QuizModel):
    text: str = Field(min_length=1, max_length=240)
    evidenceIds: list[Identifier] = Field(min_length=1, max_length=8)


class Question(QuizModel):
    questionId: Identifier
    type: QuestionType
    stem: str = Field(min_length=1, max_length=300)
    options: list[Option] = Field(min_length=2, max_length=6)
    correctOptionIds: list[Identifier] = Field(min_length=1, max_length=6)
    explanation: str = Field(min_length=1, max_length=500)
    evidenceIds: list[Identifier] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def valid_options(self):
        ids = {option.optionId for option in self.options}
        texts = {normalize(option.text) for option in self.options}
        correct = set(self.correctOptionIds)
        if len(ids) != len(self.options) or len(texts) != len(ids) or "" in texts:
            raise ValueError(f"{self.questionId} has duplicate or empty options")
        if len(correct) != len(self.correctOptionIds) or not correct <= ids:
            raise ValueError(f"{self.questionId} has invalid correct options")
        if self.type == "judge" and [option.text for option in self.options] != ["正确", "错误"]:
            raise ValueError(f"{self.questionId} must list 正确 and 错误")
        if self.type == "multi" and not 2 <= len(correct) < len(ids):
            raise ValueError(f"{self.questionId} requires two correct options and one distractor")
        if self.type != "multi" and len(correct) != 1:
            raise ValueError(f"{self.questionId} must have exactly one correct option")
        return self


class QuizSpec(QuizModel):
    title: str = Field(min_length=1, max_length=100)
    takeaways: list[Takeaway] = Field(min_length=1, max_length=8)
    questions: list[Question] = Field(min_length=TOTAL_QUESTIONS, max_length=TOTAL_QUESTIONS)


def validate_quiz(raw: dict, topic: dict) -> QuizSpec:
    spec = QuizSpec.model_validate(raw)
    if {q.type for q in spec.questions} != {"single", "multi", "judge"}:
        raise ValueError("Include single, multi and judge questions")
    stems = {normalize(q.stem) for q in spec.questions}
    if (
        len({q.questionId for q in spec.questions}) != TOTAL_QUESTIONS
        or len(stems) != TOTAL_QUESTIONS
        or "" in stems
    ):
        raise ValueError("Every question must have a unique ID and stem")
    source_ids = {item["evidenceId"] for item in topic["evidence"]}
    for item in [*spec.questions, *spec.takeaways]:
        citations = set(item.evidenceIds)
        if not citations <= source_ids or len(citations) != len(item.evidenceIds):
            raise ValueError("Every citation must be unique and reference the bound topic")
    return spec
