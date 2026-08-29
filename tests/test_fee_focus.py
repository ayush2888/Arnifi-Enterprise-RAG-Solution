"""Tests for fee/payer retrieval focus (drop gateway noise, prefer Pricing)."""

from __future__ import annotations

from app.models.schemas import RetrievedChunk
from app.services.retrieval.diversify import diversify_chunks
from app.services.retrieval.fee_focus import (
    apply_fee_focus,
    apply_service_code_boost,
    extract_service_codes,
    is_fee_or_payer_query,
    is_payment_gateway_noise,
    is_pricing_row_chunk,
    protected_chunk_ids_for_codes,
)


def _chunk(
    *,
    chunk_id: str,
    score: float,
    title: str,
    text: str,
    url: str = "https://drive.google.com/file/d/x/view",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        score=score,
        source_url=url,
        doc_title=title,
        heading_path="Pricing Master Sheet" if "Pricing" in title else "Document",
        chunk_text=text,
    )


def test_detects_fee_and_payer_queries() -> None:
    assert is_fee_or_payer_query(
        "Does Arnifi handle payment for the IQAMA Medical Authority fee?"
    )
    assert is_fee_or_payer_query("What are the IQAMA Medical fees in KSA?")
    assert not is_fee_or_payer_query("What is Arnifi's CEO salary?")


def test_pricing_row_vs_gateway_noise() -> None:
    pricing = _chunk(
        chunk_id="p",
        score=0.5,
        title="Pricing Master Sheet - KSA Services.csv",
        text="PM ID: PM1134\nTitle: IQAMA Medical\nAuthority fees: 550\nArnifi fees: 450",
    )
    gateway = _chunk(
        chunk_id="g",
        score=0.9,
        title="fine.pdf",
        text="RESOLVED to take Payment Gateway services from Razorpay Software Private Limited",
        url="https://drive.google.com/file/d/fine/view",
    )
    assert is_pricing_row_chunk(pricing)
    assert is_payment_gateway_noise(gateway)
    assert not is_payment_gateway_noise(pricing)


def test_apply_fee_focus_drops_razorpay_and_prefers_pricing() -> None:
    pricing = _chunk(
        chunk_id="p",
        score=0.58,
        title="Pricing Master Sheet - KSA Services.csv",
        text="PM ID: PM1134\nTitle: IQAMA Medical\nAuthority fees: 550\nArnifi fees: 450",
    )
    gateway = _chunk(
        chunk_id="g",
        score=0.49,
        title="fine.pdf",
        text="Razorpay Software Private Limited payment gateway resolution",
        url="https://drive.google.com/file/d/fine/view",
    )
    other = _chunk(
        chunk_id="o",
        score=0.55,
        title="handbook.pdf",
        text="General onboarding notes",
        url="https://drive.google.com/file/d/hand/view",
    )
    out = apply_fee_focus(
        "Does Arnifi handle payment for the IQAMA Medical Authority fee?",
        [gateway, other, pricing],
    )
    ids = [c.chunk_id for c in out]
    assert "g" not in ids
    assert ids[0] == "p"


def test_non_fee_query_unchanged() -> None:
    gateway = _chunk(
        chunk_id="g",
        score=0.9,
        title="fine.pdf",
        text="Razorpay payment gateway",
    )
    out = apply_fee_focus("Summarize the company board resolutions", [gateway])
    assert len(out) == 1
    assert out[0].chunk_id == "g"


def test_extract_service_codes_normalizes() -> None:
    assert extract_service_codes("Arnifi fee for PM1122?") == {"PM1122"}
    assert extract_service_codes("codes pm-1122 and PM 1134") == {"PM1122", "PM1134"}
    assert extract_service_codes("no code here") == set()


def test_service_code_boost_puts_exact_match_first() -> None:
    sheet = "https://drive.google.com/file/d/hr/view"
    c1123 = _chunk(
        chunk_id="1123",
        score=0.70,
        title="Pricing Master Sheet - HR Compliance.csv",
        text="PM ID: PM1123\nTitle: Full-Suite HR\nArnifi fees: 1499",
        url=sheet,
    )
    c1121 = _chunk(
        chunk_id="1121",
        score=0.68,
        title="Pricing Master Sheet - HR Compliance.csv",
        text="PM ID: PM1121\nTitle: HR & Payroll Essentials\nArnifi fees: 299",
        url=sheet,
    )
    c1122 = _chunk(
        chunk_id="1122",
        score=0.66,
        title="Pricing Master Sheet - HR Compliance.csv",
        text="PM ID: PM1122\nTitle: Advanced HR & Compliance\nArnifi fees: 999",
        url=sheet,
    )
    q = "What is the Arnifi fee for service PM1122?"
    focused = apply_fee_focus(q, [c1123, c1121, c1122])
    boosted = apply_service_code_boost(q, focused)
    assert boosted[0].chunk_id == "1122"
    assert "999" in boosted[0].chunk_text


def test_diversify_keeps_protected_exact_code_despite_url_cap() -> None:
    sheet = "https://drive.google.com/file/d/hr/view"
    c1123 = _chunk(
        chunk_id="1123",
        score=0.70,
        title="Pricing Master Sheet - HR Compliance.csv",
        text="PM ID: PM1123\nArnifi fees: 1499",
        url=sheet,
    )
    c1121 = _chunk(
        chunk_id="1121",
        score=0.68,
        title="Pricing Master Sheet - HR Compliance.csv",
        text="PM ID: PM1121\nArnifi fees: 299",
        url=sheet,
    )
    c1122 = _chunk(
        chunk_id="1122",
        score=0.66,
        title="Pricing Master Sheet - HR Compliance.csv",
        text="PM ID: PM1122\nArnifi fees: 999",
        url=sheet,
    )
    q = "Arnifi fee PM1122"
    # Without boost, URL cap of 2 would keep 1123+1121 only.
    raw = diversify_chunks(
        [c1123, c1121, c1122],
        max_chunks_returned=5,
        max_chunks_per_source_url=2,
    )
    assert [c.chunk_id for c in raw] == ["1123", "1121"]

    boosted = apply_service_code_boost(q, [c1123, c1121, c1122])
    protected = protected_chunk_ids_for_codes(q, boosted)
    out = diversify_chunks(
        boosted,
        max_chunks_returned=5,
        max_chunks_per_source_url=2,
        protected_chunk_ids=protected,
    )
    ids = [c.chunk_id for c in out]
    assert "1122" in ids
    assert ids[0] == "1122"


def test_no_service_code_boost_when_question_has_no_pm_id() -> None:
    a = _chunk(
        chunk_id="a",
        score=0.9,
        title="Pricing Master Sheet - HR Compliance.csv",
        text="PM ID: PM1123\nArnifi fees: 1499",
    )
    b = _chunk(
        chunk_id="b",
        score=0.5,
        title="Pricing Master Sheet - HR Compliance.csv",
        text="PM ID: PM1122\nArnifi fees: 999",
    )
    out = apply_service_code_boost("HR Compliance packages in UAE", [a, b])
    assert [c.chunk_id for c in out] == ["a", "b"]
