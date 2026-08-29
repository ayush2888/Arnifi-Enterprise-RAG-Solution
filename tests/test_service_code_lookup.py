"""Tests for lexical PM#### / Title rescue from Drive extract artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from app.models.schemas import RetrievedChunk
from app.services.retrieval.fee_focus import (
    chunk_title_matches_question,
    prefer_title_matches_for_fees,
)
from app.services.retrieval.service_code_lookup import (
    build_service_code_index,
    export_pricing_lexical_index,
    lookup_service_code_chunks,
    lookup_title_chunks,
    merge_lexical_service_code_hits,
    merge_lexical_title_hits,
    reset_service_code_index_cache,
)


def _write_extract(folder: Path, file_id: str, name: str, rows: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "file_id": file_id,
        "name": name,
        "folder_path": "Pricing Master Sheet",
        "row_texts": rows,
    }
    (folder / f"{file_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_lookup_finds_pm1122(tmp_path: Path) -> None:
    reset_service_code_index_cache()
    extracted = tmp_path / "drive" / "extracted"
    _write_extract(
        extracted,
        "hr1",
        "Pricing Master Sheet - HR Compliance.csv",
        [
            "PM ID: PM1121\nArnifi fees: 299\nCurrency: AED",
            "PM ID: PM1122\nTitle: Advanced HR & Compliance\nArnifi fees: 999\nCurrency: AED",
            "PM ID: PM1123\nArnifi fees: 1499\nCurrency: AED",
        ],
    )
    build_service_code_index(tmp_path, force=True)
    hits = lookup_service_code_chunks(
        "What is the Arnifi fee for service PM1122?",
        artifacts_dir=tmp_path,
    )
    assert len(hits) == 1
    assert "PM1122" in hits[0].chunk_text
    assert "999" in hits[0].chunk_text


def test_bundled_index_works_without_extracts(tmp_path: Path) -> None:
    """Lambda path: only bundled JSON exists under config/, no /tmp extracts."""
    reset_service_code_index_cache()
    extracts_root = tmp_path / "with_extracts"
    extracted = extracts_root / "drive" / "extracted"
    _write_extract(
        extracted,
        "hr1",
        "Pricing Master Sheet - HR Compliance.csv",
        [
            "PM ID: PM1122\nTitle: Advanced HR & Compliance\nArnifi fees: 999\nCurrency: AED",
        ],
    )
    bundled = tmp_path / "pricing_lexical.json"
    export_pricing_lexical_index(extracts_root, bundled)
    reset_service_code_index_cache()

    empty_artifacts = tmp_path / "lambda_empty"
    empty_artifacts.mkdir()
    hits = lookup_service_code_chunks(
        "Arnifi fee for PM1122?",
        artifacts_dir=empty_artifacts,
        bundled_index_path=bundled,
    )
    assert len(hits) == 1
    assert "999" in hits[0].chunk_text

    title_hits = lookup_title_chunks(
        "Advanced HR & Compliance fee?",
        artifacts_dir=empty_artifacts,
        bundled_index_path=bundled,
    )
    assert len(title_hits) == 1
    assert "PM1122" in title_hits[0].chunk_text


def test_merge_keeps_only_exact_code_for_fee_query() -> None:
    lexical = [
        RetrievedChunk(
            chunk_id="lex",
            score=1.0,
            source_url="https://drive.google.com/file/d/hr/view",
            doc_title="Pricing Master Sheet - HR Compliance.csv",
            heading_path="Pricing Master Sheet",
            chunk_text="PM ID: PM1122\nArnifi fees: 999\nCurrency: AED",
        )
    ]
    vector = [
        RetrievedChunk(
            chunk_id="wa",
            score=0.63,
            source_url="whatsapp://x",
            doc_title="NAPA",
            heading_path="",
            chunk_text="Arnifi fee options 11750",
        ),
        RetrievedChunk(
            chunk_id="my",
            score=0.55,
            source_url="https://drive.google.com/file/d/post/view",
            doc_title="Pricing Master Sheet - Post Setup.csv",
            heading_path="Pricing Master Sheet",
            chunk_text="Malaysia Corporate Compliance\nArnifi fees: 6,869.00\nCurrency: USD",
        ),
    ]
    out = merge_lexical_service_code_hits(
        "What is the Arnifi fee for service PM1122?",
        vector,
        lexical,
    )
    assert len(out) == 1
    assert out[0].chunk_id == "lex"
    assert "999" in out[0].chunk_text
    assert "6869" not in out[0].chunk_text


def test_title_match_distinguishes_near_twin_malaysia_rows(tmp_path: Path) -> None:
    reset_service_code_index_cache()
    extracted = tmp_path / "drive" / "extracted"
    _write_extract(
        extracted,
        "post1",
        "Pricing Master Sheet - Post Setup.csv",
        [
            "Title: Malaysia Corporate Compliance and Tax Services\n"
            "Type: Post Setup\nAuthority fees: 0.00\nArnifi fees: 6,869.00\nCurrency: USD",
        ],
    )
    _write_extract(
        extracted,
        "acc1",
        "Pricing Master Sheet - Accounting & Bookkeeping.csv",
        [
            "Title: Malaysia Corporate Tax and SST Compliance Services\n"
            "Type: Accounting & Bookkeeping\nAuthority fees: 0.00\n"
            "Arnifi fees: 699.00\nCurrency: USD",
        ],
    )
    build_service_code_index(tmp_path, force=True)
    q = "Malaysia Corporate Compliance and Tax Services fee?"
    hits = lookup_title_chunks(q, artifacts_dir=tmp_path)
    assert len(hits) == 1
    assert "6,869" in hits[0].chunk_text
    assert "699" not in hits[0].chunk_text


def test_prefer_title_drops_near_twin_for_fee_query() -> None:
    wrong = RetrievedChunk(
        chunk_id="sst",
        score=0.72,
        source_url="https://drive.google.com/file/d/acc/view",
        doc_title="Pricing Master Sheet - Accounting & Bookkeeping.csv",
        heading_path="Pricing Master Sheet",
        chunk_text=(
            "Title: Malaysia Corporate Tax and SST Compliance Services\n"
            "Arnifi fees: 699.00\nCurrency: USD"
        ),
    )
    right = RetrievedChunk(
        chunk_id="corp",
        score=0.66,
        source_url="https://drive.google.com/file/d/post/view",
        doc_title="Pricing Master Sheet - Post Setup.csv",
        heading_path="Pricing Master Sheet",
        chunk_text=(
            "Title: Malaysia Corporate Compliance and Tax Services\n"
            "Arnifi fees: 6,869.00\nCurrency: USD"
        ),
    )
    q = "Malaysia Corporate Compliance and Tax Services fee?"
    assert chunk_title_matches_question(q, right)
    assert not chunk_title_matches_question(q, wrong)
    out = prefer_title_matches_for_fees(q, [wrong, right])
    assert len(out) == 1
    assert out[0].chunk_id == "corp"


def test_merge_title_hits_keeps_only_named_service() -> None:
    lexical = [
        RetrievedChunk(
            chunk_id="corp",
            score=1.0,
            source_url="https://drive.google.com/file/d/post/view",
            doc_title="Pricing Master Sheet - Post Setup.csv",
            heading_path="Pricing Master Sheet",
            chunk_text=(
                "Title: Malaysia Corporate Compliance and Tax Services\n"
                "Arnifi fees: 6,869.00\nCurrency: USD"
            ),
        )
    ]
    vector = [
        RetrievedChunk(
            chunk_id="sst",
            score=0.72,
            source_url="https://drive.google.com/file/d/acc/view",
            doc_title="Pricing Master Sheet - Accounting.csv",
            heading_path="Pricing Master Sheet",
            chunk_text=(
                "Title: Malaysia Corporate Tax and SST Compliance Services\n"
                "Arnifi fees: 699.00\nCurrency: USD"
            ),
        )
    ]
    out = merge_lexical_title_hits(
        "Malaysia Corporate Compliance and Tax Services fee?",
        vector,
        lexical,
    )
    assert len(out) == 1
    assert "6,869" in out[0].chunk_text
