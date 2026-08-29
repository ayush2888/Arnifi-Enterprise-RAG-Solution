from app.services.retrieval.engine import (
    build_search_text,
    history_for_generation,
    is_elliptical_follow_up,
)


def test_build_search_text_without_history():
    assert build_search_text("UAE golden visa") == "UAE golden visa"


def test_build_search_text_enriches_elliptical_follow_up():
    history = [
        {"role": "user", "content": "What is UAE golden visa?"},
        {"role": "assistant", "content": "It is a long-term residency program."},
    ]
    text = build_search_text("what about the cost?", history)
    assert "What is UAE golden visa?" in text
    assert "what about the cost?" in text


def test_build_search_text_uses_latest_topic_for_follow_up():
    history = [
        {"role": "user", "content": "Malaysia VAT refund blog"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "What are the IQAMA Medical fees in KSA?"},
        {"role": "assistant", "content": "550 / 450"},
    ]
    text = build_search_text("how much is it?", history)
    assert "IQAMA Medical" in text
    assert "Malaysia VAT" not in text


def test_standalone_topic_switch_does_not_prepend_old_question():
    history = [
        {"role": "user", "content": "What does the Arnifi blog say about Malaysia VAT refund?"},
        {"role": "assistant", "content": "SST refund steps..."},
    ]
    q2 = "What about IQAMA Medical fees?"
    assert not is_elliptical_follow_up(q2)
    assert build_search_text(q2, history) == q2

    q3 = "What are the IQAMA Medical fees in KSA?"
    assert build_search_text(q3, history) == q3


def test_history_for_generation_dropped_on_topic_switch():
    history = [
        {"role": "user", "content": "What does the Arnifi blog say about Malaysia VAT refund?"},
        {"role": "assistant", "content": "SST refund steps..."},
    ]
    assert history_for_generation("What about IQAMA Medical fees?", history) is None
    assert history_for_generation("What are the IQAMA Medical fees in KSA?", history) is None


def test_history_for_generation_kept_for_elliptical_follow_up():
    history = [
        {"role": "user", "content": "What are the IQAMA Medical fees in KSA?"},
        {"role": "assistant", "content": "550 / 450 SAR."},
    ]
    kept = history_for_generation("what about the cost?", history)
    assert kept is not None
    assert kept[0]["content"].startswith("What are the IQAMA")


def test_build_search_text_ignores_bad_turns():
    history = [
        {"role": "system", "content": "ignore me"},
        {"role": "user", "content": "Cayman company setup"},
    ]
    # Elliptical doc follow-up still enriches from latest user topic.
    text = build_search_text("documents required?", history)
    assert "Cayman company setup" in text
    assert "documents required?" in text
