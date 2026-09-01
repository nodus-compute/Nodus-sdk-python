"""Tests for the quote arithmetic.

The bug these exist for: performance-factor keys name both sides of a
comparison ("perf.H100-80.vs.A100-80"), so a substring match finds A100 inside
the H100 row and prices an A100 as though it ran at H100 speed. It was invisible
in the cost column — that comes from the committed sheet — and showed up only in
the hours, which is the kind of wrong number that gets read past.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nodus import _pricebook


BOOK = {
    "as_of": "2026-08-03",
    "captured_at": "2026-08-03T20:00:00Z",
    "generated_by": "./bin/pricebook publish",
    "measured": False,
    "reference_brief": {"peak_memory_gb": 68, "expected_runtime_hours": 12},
    "assumptions": [
        {"key": "perf.H100-80.vs.A100-80", "value": 0.45, "speedup_x": 2.2222, "basis": "assumed"},
        {"key": "perf.A100-80.baseline", "value": 1, "speedup_x": 1, "basis": "definition"},
    ],
    "rates": [
        {"id": "aws:p5.48xlarge:h100-80", "seller": "AWS", "sku": "p5.48xlarge", "tier": "on_demand",
         "fit_class": "H100", "device_memory_gb": 80, "usd_per_gpu_hour": 6.88,
         "quoted_usd_hour": 55.04, "gpus_per_unit": 8, "normalization": "divided",
         "source_url": "https://example.invalid/aws"},
        {"id": "cw:a100", "seller": "CoreWeave", "sku": "a100", "tier": "on_demand",
         "fit_class": "A100", "device_memory_gb": 80, "usd_per_gpu_hour": 2.70,
         "quoted_usd_hour": 21.60, "gpus_per_unit": 8, "normalization": "divided",
         "source_url": "https://example.invalid/cw"},
        {"id": "cw:a100-40", "seller": "CoreWeave", "sku": "a100-40", "tier": "on_demand",
         "fit_class": "A100", "device_memory_gb": 40, "usd_per_gpu_hour": 1.10,
         "quoted_usd_hour": 8.80, "gpus_per_unit": 8, "normalization": "divided",
         "source_url": "https://example.invalid/cw40"},
        {"id": "cw:h100-spot", "seller": "CoreWeave", "sku": "h100-spot", "tier": "spot",
         "fit_class": "H100", "device_memory_gb": 80, "usd_per_gpu_hour": 0.99,
         "quoted_usd_hour": 7.92, "gpus_per_unit": 8, "normalization": "divided",
         "source_url": "https://example.invalid/cwspot"},
    ],
    "sheets": [
        {"seller": "AWS", "a100_usd_per_gpu_hour": 3.430881, "h100_usd_per_gpu_hour": 6.88,
         "a100_cost_to_complete_usd": 41.170572, "h100_cost_to_complete_usd": 37.152,
         "router_picks": "H100", "breakeven_speedup_x": 2.005315},
        {"seller": "CoreWeave", "a100_usd_per_gpu_hour": 2.70, "h100_usd_per_gpu_hour": 6.155,
         "a100_cost_to_complete_usd": 32.40, "h100_cost_to_complete_usd": 33.237,
         "router_picks": "A100", "breakeven_speedup_x": 2.279629},
    ],
    "caveats": ["published prices, not a measurement"],
    "breakeven_span": {"low_x": 1.43, "high_x": 3.0, "low_seller": "Lambda", "high_seller": "Azure"},
}


@pytest.fixture
def book(tmp_path: Path) -> _pricebook.Book:
    p = tmp_path / "book.json"
    p.write_text(json.dumps(BOOK))
    return _pricebook.load(p)


def test_perf_factor_does_not_match_the_other_side_of_a_comparison(book):
    """A100 must not pick up the H100 row's factor just by appearing in its key."""
    assert book.perf_factor("A100") == (1.0, "definition")
    assert book.perf_factor("H100") == (0.45, "assumed")


def test_perf_factor_defaults_to_baseline_for_unknown_parts(book):
    # An unknown part is assumed no faster than baseline, never optimistically scaled.
    assert book.perf_factor("MI300X") == (1.0, "definition")


def test_reference_brief_returns_the_committed_sheets_untouched(book):
    quotes = {q.seller: q for q in _pricebook.quote(book, 68, 12)}
    assert quotes["AWS"].cost_usd == pytest.approx(37.152)
    assert quotes["CoreWeave"].cost_usd == pytest.approx(32.40)


def test_rate_times_hours_reproduces_the_cost_on_every_row(book):
    """The table has to survive mental arithmetic in a room."""
    for q in _pricebook.quote(book, 68, 12):
        assert q.rate_usd_hour * q.hours == pytest.approx(q.cost_usd, rel=1e-3), q.seller


def test_a100_is_priced_for_the_full_declared_runtime(book):
    cw = next(q for q in _pricebook.quote(book, 68, 12) if q.seller == "CoreWeave")
    assert cw.picks == "A100"
    assert cw.hours == pytest.approx(12.0)  # not 5.4 — that was the bug


def test_offbook_brief_recomputes_with_the_same_formula(book):
    quotes = {q.seller: q for q in _pricebook.quote(book, 68, 6)}
    assert quotes["AWS"].cost_usd == pytest.approx(6.88 * 6 * 0.45)
    assert quotes["CoreWeave"].cost_usd == pytest.approx(2.70 * 6 * 1.0)


def test_offbook_brief_excludes_capacity_with_too_little_memory(book):
    # The 40GB CoreWeave row is cheaper and must never be selected for a 68GB brief.
    cw = next(q for q in _pricebook.quote(book, 68, 6) if q.seller == "CoreWeave")
    assert cw.rate_usd_hour == pytest.approx(2.70)
    assert all(q.rate_usd_hour != pytest.approx(1.10) for q in _pricebook.quote(book, 68, 6))


def test_interruptible_capacity_is_excluded_by_default(book):
    # The spot row is the cheapest thing in the book and is priced as though it
    # were never interrupted, so quoting it against on-demand flatters us.
    for q in _pricebook.quote(book, 68, 6):
        assert q.rate_usd_hour != pytest.approx(0.99)


def test_derivation_shows_the_division_for_multi_gpu_instances(book):
    aws = next(r for r in book.rates if r.seller == "AWS")
    assert aws.derivation == "55.040000 / 8"
    assert aws.usd_per_gpu_hour == pytest.approx(aws.quoted_usd_hour / aws.gpus_per_unit)


def test_every_rate_carries_a_source_url(book):
    assert all(r.source_url.startswith("http") for r in book.rates)


def test_missing_book_names_the_paths_it_tried(tmp_path):
    with pytest.raises(_pricebook.PricebookMissing) as exc:
        _pricebook.load(tmp_path / "absent.json")
    assert "absent.json" in str(exc.value)
