"""Offline unit tests for eval metrics (no Bedrock / Pinecone)."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.eval.metrics import (
    answer_must_include_rate,
    chunk_is_hit,
    context_recall_proxy,
    hit_at_k,
    mean_skip_none,
    url_is_match,
)


def _chunk(url: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(source_url=url, chunk_text=text)


def test_url_prefix_match() -> None:
    assert url_is_match(
        "https://drive.google.com/file/d/abc123/view",
        ["https://drive.google.com/file/d/"],
    )
    assert not url_is_match(
        "https://arnifi.com/blog/post/",
        ["https://drive.google.com/file/d/"],
    )


def test_hit_at_k_via_must_include() -> None:
    chunks = [
        _chunk("https://arnifi.com/blog/a/", "unrelated"),
        _chunk(
            "https://drive.google.com/file/d/ksa1/view",
            "Service Code: PM1134\nService: IQAMA Medical\nAuthority fees: 550",
        ),
    ]
    assert (
        hit_at_k(
            chunks,
            reference_source_urls=[],
            reference_must_include=["PM1134", "550"],
        )
        == 1.0
    )


def test_hit_at_k_miss() -> None:
    chunks = [_chunk("https://arnifi.com/blog/a/", "hello world")]
    assert (
        hit_at_k(
            chunks,
            reference_source_urls=["https://drive.google.com/file/d/"],
            reference_must_include=["PM1134"],
        )
        == 0.0
    )


def test_hit_at_k_none_when_no_refs() -> None:
    chunks = [_chunk("https://arnifi.com/blog/a/", "anything")]
    assert hit_at_k(chunks, reference_source_urls=[], reference_must_include=[]) is None


def test_context_recall_proxy_partial() -> None:
    chunks = [
        _chunk(
            "https://drive.google.com/file/d/x/view",
            "PM1134 IQAMA Medical Authority fees: 550",
        )
    ]
    score = context_recall_proxy(
        chunks,
        reference_must_include=["PM1134", "550", "450"],
    )
    assert score == 2 / 3


def test_answer_must_include_rate() -> None:
    rate = answer_must_include_rate(
        "IQAMA Medical is PM1134 with Authority 550 SAR.",
        reference_must_include=["PM1134", "550", "450"],
    )
    assert rate == 2 / 3


def test_chunk_is_hit_url_only() -> None:
    c = _chunk("https://drive.google.com/file/d/abc/view", "no needles here")
    assert chunk_is_hit(
        c,
        reference_source_urls=["https://drive.google.com/file/d/"],
        reference_must_include=["PM1134"],
    )


def test_mean_skip_none() -> None:
    assert mean_skip_none([1.0, None, 0.0]) == 0.5
    assert mean_skip_none([None, None]) is None


def test_answer_forbid_ok() -> None:
    from app.services.eval.metrics import answer_forbid_ok

    assert (
        answer_forbid_ok(
            "Payer is not stated. Authority fee 550 SAR.",
            answer_must_not_include=["Razorpay", "payment gateway"],
        )
        == 1.0
    )
    assert (
        answer_forbid_ok(
            "Arnifi uses Razorpay for payments.",
            answer_must_not_include=["Razorpay"],
        )
        == 0.0
    )
    assert answer_forbid_ok("anything", answer_must_not_include=[]) is None
