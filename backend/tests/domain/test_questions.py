from app.domain.questions import question_fingerprint


def test_question_fingerprint_ignores_case_unicode_width_and_spacing():
    first = {
        "type": "single",
        "stem": "  Ｗｈａｔ  IS   2? ",
        "options": [{"text": "No"}, {"text": "Yes"}],
    }
    equivalent = {
        "type": "single",
        "stem": "what is 2?",
        "options": [{"text": " YES "}, {"text": "no"}],
    }

    assert question_fingerprint(first) == question_fingerprint(equivalent)


def test_question_fingerprint_changes_when_type_stem_or_option_text_changes():
    question = {
        "type": "single",
        "stem": "What is 2?",
        "options": [{"text": "No"}, {"text": "Yes"}],
    }
    variants = [
        {**question, "type": "multi"},
        {**question, "stem": "What is 3?"},
        {**question, "options": [{"text": "Maybe"}, {"text": "Yes"}]},
    ]

    assert all(question_fingerprint(question) != question_fingerprint(item) for item in variants)
