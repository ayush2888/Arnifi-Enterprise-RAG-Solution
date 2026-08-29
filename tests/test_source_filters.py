"""Tests for query source filters."""

from __future__ import annotations

import pytest

from app.services.retrieval.filters import (
    build_source_filter,
    empty_result_message,
    normalize_source,
)


def test_normalize_source_defaults_to_all() -> None:
    assert normalize_source(None) == "all"
    assert normalize_source("DRIVE") == "drive"
    assert normalize_source("blog") == "website"
    assert normalize_source("website") == "website"


def test_normalize_source_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        normalize_source("telegram")


def test_build_source_filter_all() -> None:
    assert build_source_filter("all") is None


def test_build_source_filter_drive() -> None:
    assert build_source_filter("drive") == {"source_type": {"$eq": "drive"}}


def test_build_source_filter_whatsapp() -> None:
    assert build_source_filter("whatsapp") == {"source_type": {"$eq": "whatsapp"}}


def test_build_source_filter_website() -> None:
    filt = build_source_filter("website")
    assert filt is not None
    assert "$or" in filt
    types = {item.get("source_type", {}).get("$eq") for item in filt["$or"] if "source_type" in item}
    assert "blog" in types
    assert "website" in types
    assert build_source_filter("blog") == filt


def test_empty_result_message() -> None:
    assert "Drive" in empty_result_message("drive")
    assert "website" in empty_result_message("blog").lower()
    assert "WhatsApp" in empty_result_message("whatsapp")
    assert "knowledge" in empty_result_message("all")
