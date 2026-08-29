"""Offline tests for the eval smoke gate comparator."""

from __future__ import annotations

from app.services.eval.gate import compare_to_baseline


def test_pass_when_equal() -> None:
    result = compare_to_baseline(
        {"hit_at_k": 1.0, "context_recall_proxy": 1.0, "faithfulness": None},
        {"hit_at_k": 1.0, "context_recall_proxy": 1.0, "faithfulness": None},
        max_drop=0.05,
    )
    assert result["ok"] is True
    assert result["failures"] == []
    skipped = [c for c in result["checks"] if c["status"] == "skipped"]
    assert any(c["metric"] == "faithfulness" for c in skipped)


def test_fail_when_hit_drops_beyond_threshold() -> None:
    result = compare_to_baseline(
        {"hit_at_k": 0.9, "context_recall_proxy": 1.0},
        {"hit_at_k": 1.0, "context_recall_proxy": 1.0},
        max_drop=0.05,
    )
    assert result["ok"] is False
    assert "hit_at_k" in result["failures"]


def test_pass_when_drop_within_tolerance() -> None:
    result = compare_to_baseline(
        {"hit_at_k": 0.96, "context_recall_proxy": 1.0},
        {"hit_at_k": 1.0, "context_recall_proxy": 1.0},
        max_drop=0.05,
    )
    assert result["ok"] is True


def test_fail_on_faithfulness_when_present() -> None:
    result = compare_to_baseline(
        {"hit_at_k": 1.0, "faithfulness": 0.7},
        {"hit_at_k": 1.0, "faithfulness": 0.9},
        max_drop=0.05,
    )
    assert result["ok"] is False
    assert "faithfulness" in result["failures"]
