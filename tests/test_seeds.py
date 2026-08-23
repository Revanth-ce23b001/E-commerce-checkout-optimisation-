"""Unit tests for the seed substream harness.

The headline test here is ``test_changing_n_products_does_not_shift_customer_latents``,
which is the Stage 2 checkpoint required by brief §14 and spec §20.1.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.config.seeds import (
    SubstreamError,
    assert_append_only,
    bernoulli_from_uniform,
    common_random_numbers,
    spawn_substreams,
)

# A representative substream list. Structure only — the real list lives in params.yaml.
SUBSTREAMS = [
    "date", "geography", "seller", "product", "customer", "latent", "history",
    "session", "cod", "payment", "conversion", "order", "cancel", "rto",
    "delivery", "reason", "economics",
]
MASTER = 20260115


# --- the Stage 2 checkpoint -------------------------------------------------


def test_changing_n_products_does_not_shift_customer_latents():
    """Brief §7: 'changing n_products from 8,000 to 9,000 must not shift the customer latents.'

    This is the property that makes sensitivity analysis interpretable. Without it,
    every parameter change reshuffles the whole population and you cannot tell whether
    a result moved because of the parameter or because of the dice.
    """
    def draw_latents(n_products: int) -> np.ndarray:
        registry = spawn_substreams(MASTER, SUBSTREAMS)
        # The product module consumes a different amount of randomness in each run...
        registry.get("product").normal(size=(n_products, 4))
        # ...but the latent module draws from its own independent stream.
        return registry.get("latent").normal(size=(55_000, 4))

    baseline = draw_latents(8_000)
    changed = draw_latents(9_000)

    np.testing.assert_array_equal(
        baseline, changed,
        err_msg="Customer latents shifted when n_products changed — the substreams are "
                "not independent and no sensitivity analysis is interpretable.",
    )


def test_every_substream_is_independent_of_every_other():
    """Consuming from any one stream must not disturb any other."""
    reference = spawn_substreams(MASTER, SUBSTREAMS)
    expected = {name: reference.get(name).normal(size=50) for name in SUBSTREAMS}

    for disturbed in SUBSTREAMS:
        registry = spawn_substreams(MASTER, SUBSTREAMS)
        registry.get(disturbed).normal(size=9_999)  # consume a different amount
        for name in SUBSTREAMS:
            if name == disturbed:
                continue
            np.testing.assert_array_equal(
                registry.get(name).normal(size=50), expected[name],
                err_msg=f"Consuming from {disturbed!r} shifted {name!r}.",
            )


# --- reproducibility contract ----------------------------------------------


def test_same_master_seed_gives_identical_streams():
    a = spawn_substreams(MASTER, SUBSTREAMS)
    b = spawn_substreams(MASTER, SUBSTREAMS)
    for name in SUBSTREAMS:
        np.testing.assert_array_equal(a.get(name).normal(size=100), b.get(name).normal(size=100))


def test_different_master_seed_gives_different_streams():
    """A new seed must produce a different sample from the same DGP (spec §16.2)."""
    a = spawn_substreams(MASTER, SUBSTREAMS)
    b = spawn_substreams(MASTER + 1, SUBSTREAMS)
    assert not np.array_equal(a.get("latent").normal(size=100), b.get("latent").normal(size=100))


def test_appending_a_substream_leaves_existing_streams_untouched():
    """CLAUDE.md invariant 11: new substreams append to the END of the list."""
    before = spawn_substreams(MASTER, SUBSTREAMS)
    after = spawn_substreams(MASTER, SUBSTREAMS + ["experiment"])
    for name in SUBSTREAMS:
        np.testing.assert_array_equal(
            before.get(name).normal(size=100), after.get(name).normal(size=100),
            err_msg=f"Appending a substream shifted {name!r}.",
        )


def test_inserting_a_substream_in_the_middle_shifts_downstream_streams():
    """The failure this harness is designed to make visible, demonstrated."""
    before = spawn_substreams(MASTER, SUBSTREAMS)
    inserted = SUBSTREAMS[:3] + ["intruder"] + SUBSTREAMS[3:]
    after = spawn_substreams(MASTER, inserted)
    assert not np.array_equal(
        before.get("customer").normal(size=100), after.get("customer").normal(size=100)
    ), "Mid-list insertion did not shift downstream streams — the test is not exercising the risk."


# --- guards -----------------------------------------------------------------


def test_unknown_substream_raises_rather_than_silently_creating_one():
    registry = spawn_substreams(MASTER, SUBSTREAMS)
    with pytest.raises(SubstreamError, match="Unknown substream"):
        registry.get("typo_stream")


def test_duplicate_substream_names_rejected():
    with pytest.raises(ValueError, match="Duplicate substream"):
        spawn_substreams(MASTER, ["customer", "latent", "customer"])


def test_empty_substream_list_rejected():
    with pytest.raises(ValueError, match="empty"):
        spawn_substreams(MASTER, [])


def test_assert_append_only_accepts_an_append():
    assert_append_only(SUBSTREAMS + ["experiment"], SUBSTREAMS)


def test_assert_append_only_rejects_an_insertion():
    inserted = SUBSTREAMS[:3] + ["intruder"] + SUBSTREAMS[3:]
    with pytest.raises(ValueError, match="position 3"):
        assert_append_only(inserted, SUBSTREAMS)


def test_assert_append_only_rejects_a_removal():
    with pytest.raises(ValueError, match="shrank"):
        assert_append_only(SUBSTREAMS[:-1], SUBSTREAMS)


def test_order_hash_is_stable_and_order_sensitive():
    a = spawn_substreams(MASTER, SUBSTREAMS)
    b = spawn_substreams(MASTER, SUBSTREAMS)
    reordered = spawn_substreams(MASTER, SUBSTREAMS[1:] + SUBSTREAMS[:1])
    assert a.order_hash == b.order_hash
    assert a.order_hash != reordered.order_hash


# --- common random numbers (calibration convergence) ------------------------


def test_common_random_numbers_are_indexed_by_entity_not_consumption_order():
    """The property that makes calibration bisection converge (spec §7.3).

    Entity i must see the same uniforms regardless of what any other entity did.
    """
    a = common_random_numbers(spawn_substreams(MASTER, SUBSTREAMS).get("cod"), 1_000, 4)
    b = common_random_numbers(spawn_substreams(MASTER, SUBSTREAMS).get("cod"), 1_000, 4)
    np.testing.assert_array_equal(a, b)
    assert a.shape == (1_000, 4)
    assert np.all((a >= 0.0) & (a < 1.0))


def test_bernoulli_draw_is_monotone_in_probability():
    """Raising p can only turn outcomes on, never off.

    This is what makes the realised COD share a monotone step function of the
    intercept, which is what bisection needs.
    """
    u = common_random_numbers(spawn_substreams(MASTER, SUBSTREAMS).get("cod"), 5_000, 1)[:, 0]
    low = bernoulli_from_uniform(np.full(5_000, 0.40), u)
    high = bernoulli_from_uniform(np.full(5_000, 0.60), u)
    assert np.all(high >= low), "A higher probability turned an outcome off."
    assert low.sum() < high.sum()


def test_bernoulli_rejects_probabilities_outside_the_unit_interval():
    u = np.full(10, 0.5)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        bernoulli_from_uniform(np.full(10, 1.5), u)


def test_bernoulli_rejects_shape_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        bernoulli_from_uniform(np.full(10, 0.5), np.full(9, 0.5))
