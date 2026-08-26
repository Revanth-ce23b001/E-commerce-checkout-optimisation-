"""Unit tests for the GT-03 diagnostics harness.

GT-03 fails and ruling A50 refused to restate it, so the numbers this harness
produces are the evidence a future ruling will rest on. The same argument that
covers ``test_gt_recovery`` applies: if the harness is wrong, the diagnosis is
evidence about the harness rather than about the dataset.

Three things are pinned, because all three are places a plausible-looking
implementation would quietly report the wrong number:

* **Blocking.** A one-hot set is ONE confounder. Grading `geo_tier`'s four
  dummies separately would split its contribution four ways and understate every
  categorical against every scalar in the same table.
* **The A18 missingness indicator travels with its rate.** It exists only to say
  "this rate is unknown"; scored on its own it is a confounder that means nothing.
* **`closed()` is GT-03's own metric**, not the shrink-from-naive that the
  CLAUDE.md warning says gets confused with it. The two denominators differ and
  the test grades the selection-component one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis import gt03_diagnostics as D


def test_one_hot_set_is_one_confounder():
    blocks = D._blocks([
        "geo_tier_TIER1", "geo_tier_TIER2", "geo_tier_TIER3",
        "category_APPAREL", "order_value", "cart_size",
    ])
    assert blocks["geo_tier"] == [
        "geo_tier_TIER1", "geo_tier_TIER2", "geo_tier_TIER3"]
    assert blocks["category"] == ["category_APPAREL"]
    assert blocks["order_value"] == ["order_value"]


def test_missingness_indicator_travels_with_its_rate():
    blocks = D._blocks(["pit_cod_share", "pit_cod_share_missing", "cart_size"])
    assert blocks["pit_cod_share"] == ["pit_cod_share", "pit_cod_share_missing"]
    assert "pit_cod_share_missing" not in blocks


def test_every_column_lands_in_exactly_one_block():
    columns = ["geo_tier_TIER1", "geo_tier_TIER2", "pit_cod_share",
               "pit_cod_share_missing", "order_value", "device_type_IOS"]
    blocks = D._blocks(columns)
    flat = [c for group in blocks.values() for c in group]
    assert sorted(flat) == sorted(columns)
    assert len(flat) == len(set(flat))


def test_closed_is_the_selection_component_denominator():
    """GT-03's metric, not the shrink-from-naive it is routinely confused with.

    At the naive gap nothing is closed; at the AME everything is. The published
    70.9% is reproduced from the published estimate to pin the convention.
    """
    ame, naive = 9.992323058669193, 17.732568542515438
    assert D.closed(naive, ame, naive) == pytest.approx(0.0)
    assert D.closed(ame, ame, naive) == pytest.approx(1.0)
    assert D.closed(12.24, ame, naive) == pytest.approx(0.7096, abs=5e-4)

    # The OTHER denominator -- shrink from naive -- is a different number, and
    # the point of pinning it here is that they are not interchangeable.
    shrink_from_naive = (naive - 12.24) / naive
    assert shrink_from_naive == pytest.approx(0.3096, abs=5e-4)


def test_suspect_and_latents_are_named_not_inferred():
    """A50 suspects one feature by name; the exemption cannot widen silently."""
    assert D.SUSPECT == "pit_rto_rate_shrunk"
    assert set(D.LATENTS) == {"latent_intent", "latent_trust", "latent_liquidity"}
    assert D.CHOICE_CHANNEL not in D.LATENTS


def test_maximal_safe_set_excludes_the_outcome_and_its_filters():
    """The generous set is the conservative test -- but not THAT generous.

    Including ``rto_flag`` or its two censoring filters would let a latent be
    "reconstructed" from the outcome it causes, which is the leak the whole
    measurement exists to rule out.
    """
    for column in ("rto_flag", "is_shipped", "is_censored"):
        assert column in D._NOT_SAFE
    assert "customer_id" in D._NOT_SAFE
