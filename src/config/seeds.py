"""Seed substream harness — the reproducibility contract.

Business concept
----------------
Every random draw in this project comes from a *named, independent* stream rather
than one global generator. That is what makes sensitivity analysis interpretable:
changing ``n_products`` from 8,000 to 9,000 must not shift a single customer latent.
Without independent substreams, every parameter change reshuffles the whole
population and you can never tell whether a result moved because of the parameter
or because of the dice.

Spec references
---------------
- Spec §16.1  — seed architecture, ``SeedSequence`` spawning
- Spec §16.2  — the reproducibility contract (DQ-01)
- Brief §7    — "Do not use np.random.seed()"
- CLAUDE.md invariant 11 — new substreams append to the END of the list, never the middle

The two rules that matter
-------------------------
1. The seed controls *sampling*. ``params.yaml`` controls the *data-generating process*.
   Changing the seed must never change a coefficient; changing a coefficient must
   never require a new seed.
2. Substream order is fixed. Inserting a name in the middle shifts every downstream
   stream and silently invalidates every prior run. ``assert_append_only`` catches this.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
from numpy.random import Generator, SeedSequence, default_rng


class SubstreamError(RuntimeError):
    """Raised when a substream is requested that was never declared in params.yaml."""


@dataclass(frozen=True)
class SubstreamRegistry:
    """Named independent random generators, one per generation module.

    Built once at the start of a run from ``params.yaml``. Each generator module
    draws **only** from its own named substream, via :meth:`get`.
    """

    master_seed: int
    names: tuple[str, ...]
    _generators: dict[str, Generator]

    def get(self, name: str) -> Generator:
        """Return the generator for one named substream.

        Raises SubstreamError rather than silently creating a stream, because a
        typo that quietly spawned a new generator would break reproducibility
        without failing any test.
        """
        try:
            return self._generators[name]
        except KeyError:
            raise SubstreamError(
                f"Unknown substream {name!r}. Declared substreams are: "
                f"{', '.join(self.names)}. Add new substreams to the END of "
                f"seed.substreams in params.yaml — never the middle."
            ) from None

    def __contains__(self, name: str) -> bool:
        return name in self._generators

    @property
    def order_hash(self) -> str:
        """SHA-256 of the ordered substream list, recorded in the run manifest.

        If this hash changes between runs, the substream list was reordered and
        no prior output is reproducible.
        """
        return _hash_names(self.names)


def spawn_substreams(master_seed: int, names: list[str] | tuple[str, ...]) -> SubstreamRegistry:
    """Spawn one independent generator per named substream.

    Implements spec §16.1 exactly::

        root     = SeedSequence(master_seed)
        children = root.spawn(len(names))
        RNG      = {name: default_rng(child) for name, child in zip(names, children)}

    Because ``spawn`` derives each child deterministically from its *position*,
    appending a new name leaves every existing stream byte-identical, while
    inserting one shifts all downstream streams.
    """
    names = tuple(names)
    if not names:
        raise ValueError("seed.substreams is empty — at least one substream is required.")

    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ValueError(
            f"Duplicate substream name(s): {sorted(duplicates)}. "
            "Each generation module must own exactly one stream."
        )

    root = SeedSequence(master_seed)
    children = root.spawn(len(names))
    generators = {name: default_rng(child) for name, child in zip(names, children)}
    return SubstreamRegistry(master_seed=master_seed, names=names, _generators=generators)


def assert_append_only(
    current: list[str] | tuple[str, ...],
    previous: list[str] | tuple[str, ...],
) -> None:
    """Assert the substream list grew only at the end.

    Enforces CLAUDE.md invariant 11. Call this when a run manifest from an earlier
    run is available, so that a mid-list insertion fails loudly at load time rather
    than silently producing a different population.
    """
    current, previous = tuple(current), tuple(previous)
    if current[: len(previous)] != previous:
        for i, (now, before) in enumerate(zip(current, previous)):
            if now != before:
                raise ValueError(
                    f"Substream list changed at position {i}: was {before!r}, now {now!r}. "
                    "Substreams may only be APPENDED. Reordering or inserting shifts every "
                    "downstream stream and invalidates all prior runs (CLAUDE.md invariant 11)."
                )
        raise ValueError(
            f"Substream list shrank: had {len(previous)} entries, now {len(current)}. "
            "Substreams may only be appended, never removed."
        )


# ---------------------------------------------------------------------------
# Common random numbers — required for calibration to converge
# ---------------------------------------------------------------------------


def common_random_numbers(
    rng: Generator,
    n_entities: int,
    n_draws: int,
    *,
    dtype: type = np.float64,
) -> np.ndarray:
    """Pre-allocate a fixed block of uniform draws, indexed by entity.

    Why this exists
    ---------------
    Calibration solves an intercept by bisection, re-running the pipeline many
    times with only that one number changed. If randomness were consumed
    *sequentially* from a stream, a change in the intercept would change how many
    draws each session consumed, which would shift every subsequent draw. The
    realised outcome would then jump around non-monotonically and the bisection
    would become a random walk that never converges — the exact failure spec §7.3
    warns about with "run the full realisation pipeline WITHOUT resampling other
    randomness".

    Pre-allocating an ``(n_entities, n_draws)`` block and indexing into it by
    entity means entity *i* always sees the same uniforms regardless of what any
    other entity did, or of what the intercept is. The realised share then becomes
    a monotone step function of the intercept, which bisection can solve.

    Returns
    -------
    Array of shape ``(n_entities, n_draws)`` of uniforms in [0, 1).
    """
    if n_entities < 0 or n_draws < 0:
        raise ValueError("n_entities and n_draws must be non-negative.")
    return rng.random(size=(n_entities, n_draws), dtype=dtype)


def bernoulli_from_uniform(p: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Draw Bernoulli outcomes from pre-allocated uniforms (inverse-CDF).

    Business concept: **no outcome is ever assigned.** Every flag in this project —
    COD choice, conversion, RTO — is a draw from a computed probability. Using a
    fixed uniform makes that draw reproducible and monotone in the probability,
    which is what lets the calibrator move an intercept and see a predictable response.

    Spec: brief §9.8 ("Never assign COD directly"), §9.12 ("Never write
    ``if payment_method == 'COD': rto = True``").
    """
    p = np.asarray(p, dtype=np.float64)
    u = np.asarray(u, dtype=np.float64)
    if p.shape != u.shape:
        raise ValueError(f"Probability shape {p.shape} does not match uniform shape {u.shape}.")
    if np.any(p < 0.0) or np.any(p > 1.0):
        raise ValueError("Probabilities must lie in [0, 1].")
    return u < p


def _hash_names(names: tuple[str, ...]) -> str:
    payload = "\n".join(names).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
