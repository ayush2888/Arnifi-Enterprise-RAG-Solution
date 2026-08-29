"""Offline validation of data/eval/cases.jsonl schema (no Bedrock)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.eval.cases import load_cases

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "data" / "eval" / "cases.jsonl"

REQUIRED = ("id", "query", "ground_truth", "tags")
ALLOWED_SOURCES = {None, "all", "drive", "blog", "website", "whatsapp"}


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    if not CASES_PATH.is_file():
        pytest.skip(f"Missing eval cases at {CASES_PATH}")
    return load_cases(CASES_PATH)


def test_cases_file_exists() -> None:
    assert CASES_PATH.is_file(), f"Expected {CASES_PATH}"


def test_load_cases_non_empty(cases: list[dict]) -> None:
    assert len(cases) >= 5


def test_required_fields_and_types(cases: list[dict]) -> None:
    ids: set[str] = set()
    for case in cases:
        for key in REQUIRED:
            assert key in case, f"{case.get('id')}: missing {key}"
        assert isinstance(case["id"], str) and case["id"].strip()
        assert case["id"] not in ids, f"duplicate id: {case['id']}"
        ids.add(case["id"])
        assert isinstance(case["query"], str) and case["query"].strip()
        assert isinstance(case["ground_truth"], str) and case["ground_truth"].strip()
        assert isinstance(case["tags"], list) and case["tags"]
        assert all(isinstance(t, str) and t.strip() for t in case["tags"])

        urls = case.get("reference_source_urls", [])
        must = case.get("reference_must_include", [])
        assert urls is None or isinstance(urls, list)
        assert must is None or isinstance(must, list)
        if isinstance(urls, list):
            assert all(isinstance(u, str) for u in urls)
        if isinstance(must, list):
            assert all(isinstance(m, str) for m in must)

        expected = case.get("expected_source")
        assert expected in ALLOWED_SOURCES or expected is None


def test_smoke_subset_present(cases: list[dict]) -> None:
    smoke = [c for c in cases if "smoke" in (c.get("tags") or [])]
    assert len(smoke) >= 5, "Need a smoke subset for the pre-deploy gate"


def test_iqama_case_present(cases: list[dict]) -> None:
    iqama = next((c for c in cases if c["id"] == "pricing-iqama-medical"), None)
    assert iqama is not None
    must = iqama.get("reference_must_include") or []
    assert "PM1134" in must
    assert "550" in must


def test_each_line_is_json_object() -> None:
    raw = CASES_PATH.read_text(encoding="utf-8")
    for i, line in enumerate(raw.splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        obj = json.loads(text)
        assert isinstance(obj, dict), f"line {i} must be a JSON object"
