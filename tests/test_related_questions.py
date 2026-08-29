from app.services.bedrock.llm import parse_related_questions


def test_parse_related_questions_plain_json():
    raw = '["Who qualifies for a golden visa?", "How long does approval take?", "What documents are required?"]'
    qs = parse_related_questions(raw)
    assert len(qs) == 3
    assert "golden visa" in qs[0].lower()


def test_parse_related_questions_fenced():
    raw = """```json
["Cost of IFZA setup?", "Mainland vs freezone?", "Visa quota for staff?"]
```"""
    qs = parse_related_questions(raw)
    assert len(qs) == 3


def test_parse_related_questions_filters_junk():
    raw = '["ok", "This is a proper follow-up question about visas?", 12, ""]'
    qs = parse_related_questions(raw)
    assert qs == ["This is a proper follow-up question about visas?"]
