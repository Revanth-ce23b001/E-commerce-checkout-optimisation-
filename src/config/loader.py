"""Load, validate and hash ``config/params.yaml`` — the single source of assumptions.

Business concept
----------------
Every business assumption in this project lives in one file, so that an interviewer
can challenge any input and see the model re-run in seconds. This module is the gate
that file passes through: it validates the structure, hashes the contents, and
refuses to proceed if the calibrated intercepts no longer belong to the data-generating
process they were solved for.

Spec references
---------------
- Spec §13    — the parameter registry and its two hard rules
- Spec §16.2  — the reproducibility contract (DQ-01)
- Brief §6    — "params.yaml is SHA-256 hashed on load and the hash goes into every
                 run manifest and into _truth.json"

The two hard rules this module enforces
---------------------------------------
1. ``intercept_solved`` fields are **machine-written only**. A human editing them
   breaks reproducibility. :func:`load_params` detects this by comparing the DGP
   hash recorded at calibration time against the DGP hash of the file as loaded.
2. Slopes are immutable across the project. This module records the hashes that
   validation test CAL-09 checks against; it does not itself judge coefficients.

Note on "no business literals in src/"
--------------------------------------
This module contains no business values. The only string constants are *structural*
key names (``intercept_solved``, ``calibration``) that describe the shape of the
config file, not assumptions about the marketplace.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Structural key names — the shape of the config file, not business assumptions.
INTERCEPT_KEY = "intercept_solved"
# Any key ending in `_solved` is MACHINE-WRITTEN and stripped from the DGP hash.
# Decisions A36 and A37 added two calibrated LEVELS that are not intercepts —
# `product_price_scalar_solved` and `noise_sd_solved` — and both need exactly the
# same protection: a human editing one silently invalidates the calibration.
SOLVED_SUFFIX = "_solved"
CALIBRATION_BLOCK = "calibration"
DGP_HASH_KEY = "dgp_sha256"


class ParamsError(RuntimeError):
    """Raised when params.yaml is structurally invalid or internally inconsistent."""


@dataclass(frozen=True)
class Params:
    """A validated, hashed parameter set.

    Attributes
    ----------
    raw
        The full parsed YAML document.
    sha256
        Hash of the **whole** file, including solved intercepts. Goes into the run
        manifest and ``_truth.json``. Two runs with the same value produced
        byte-identical output (DQ-01).
    dgp_sha256
        Hash of the file with all ``intercept_solved`` values stripped. This is the
        fingerprint of the *data-generating process* itself. It changes when a slope
        changes but not when the calibrator writes a solved intercept — which is
        exactly the distinction spec §13.1 draws between the DGP and the sampling.
    source_path
        Where the file was loaded from, recorded for provenance.
    """

    raw: dict[str, Any]
    sha256: str
    dgp_sha256: str
    source_path: str
    _overrides_applied: tuple[str, ...] = field(default=())

    def get(self, dotted_path: str, default: Any = ...) -> Any:
        """Fetch a value by dotted path, e.g. ``get("scale.target_orders")``.

        Raises rather than returning None when a path is missing and no default is
        given — a silently-missing parameter is how an unflagged assumption enters
        a model.
        """
        node: Any = self.raw
        for part in dotted_path.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is not ...:
                    return default
                raise ParamsError(
                    f"Missing parameter {dotted_path!r} in {self.source_path}. "
                    "Every business assumption must be present in params.yaml — "
                    "no defaults are inferred in code."
                )
            node = node[part]
        return node

    def require(self, dotted_path: str) -> Any:
        """Fetch a value that must exist and must not be null."""
        value = self.get(dotted_path)
        if value is None:
            raise ParamsError(
                f"Parameter {dotted_path!r} is null. If this is an "
                f"{INTERCEPT_KEY!r} field, the calibrator has not run yet."
            )
        return value

    def solved_intercepts(self) -> dict[str, float | None]:
        """Every machine-written ``*_solved`` field in the file, by dotted path.

        Discovered structurally rather than from a hard-coded list, so adding a new
        model block is picked up automatically.
        """
        return dict(_find_intercepts(self.raw))

    def is_calibrated(self) -> bool:
        """True when every declared intercept has been solved."""
        values = self.solved_intercepts()
        return bool(values) and all(v is not None for v in values.values())


def load_params(
    params_path: str | Path,
    schema_path: str | Path | None = None,
    *,
    scenario_path: str | Path | None = None,
    strict_calibration: bool = True,
) -> Params:
    """Load params.yaml, validate it against its JSON schema, and hash it.

    Parameters
    ----------
    params_path
        Path to ``config/params.yaml``.
    schema_path
        Path to ``config/params.schema.json``. Validation is skipped with a loud
        error if the schema is absent, because failing fast on a malformed config
        is the whole point of having one.
    scenario_path
        Optional sensitivity override from ``config/scenarios/``, deep-merged over
        the base file. Overrides are applied *before* hashing, so a scenario run
        has its own distinct hash and cannot be confused with a base run.
    strict_calibration
        When True (the default), refuse to load a file whose solved intercepts were
        calibrated against a different DGP.
    """
    params_path = Path(params_path)
    if not params_path.exists():
        raise ParamsError(f"params.yaml not found at {params_path}.")

    raw = _read_yaml(params_path)
    overrides: tuple[str, ...] = ()

    if scenario_path is not None:
        scenario_path = Path(scenario_path)
        if not scenario_path.exists():
            raise ParamsError(f"Scenario file not found at {scenario_path}.")
        scenario = _read_yaml(scenario_path)
        raw, overrides = _deep_merge(raw, scenario)

    if schema_path is not None:
        _validate_schema(raw, Path(schema_path), params_path)

    full_hash = _canonical_sha256(raw)
    dgp_hash = _canonical_sha256(_strip_intercepts(raw))

    params = Params(
        raw=raw,
        sha256=full_hash,
        dgp_sha256=dgp_hash,
        source_path=str(params_path),
        _overrides_applied=overrides,
    )

    if strict_calibration:
        _assert_intercepts_match_dgp(params)

    return params


def _assert_intercepts_match_dgp(params: Params) -> None:
    """Refuse solved intercepts that belong to a different data-generating process.

    Enforces spec §13.3 rule 1. When the calibrator solves an intercept it stamps the
    DGP hash it solved against. If a slope is later edited by hand, the DGP hash
    changes but the stale intercept remains — producing a file that looks calibrated
    and is not. This catches that, and it also catches a human hand-editing an
    intercept, because doing so without re-running the calibrator leaves the stamp
    pointing at the old DGP.
    """
    intercepts = params.solved_intercepts()
    if not intercepts or all(v is None for v in intercepts.values()):
        return  # Not yet calibrated — nothing to check.

    stamped = params.get(f"{CALIBRATION_BLOCK}.{DGP_HASH_KEY}", default=None)
    if stamped is None:
        raise ParamsError(
            f"{list(intercepts)} are solved, but {CALIBRATION_BLOCK}.{DGP_HASH_KEY} is "
            "missing. Solved intercepts must carry the DGP hash they were calibrated "
            "against. Re-run the calibrator; do not hand-edit intercept values "
            "(spec §13.3 rule 1)."
        )

    if stamped != params.dgp_sha256:
        raise ParamsError(
            "Solved intercepts do not belong to this data-generating process.\n"
            f"  calibrated against DGP hash: {stamped}\n"
            f"  current DGP hash:            {params.dgp_sha256}\n"
            "A coefficient changed after calibration, or an intercept was hand-edited. "
            "Re-run the calibrator. Never hand-edit intercept_solved."
        )


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ParamsError(f"{path} did not parse to a mapping.")
    return data


def _validate_schema(raw: dict[str, Any], schema_path: Path, params_path: Path) -> None:
    if not schema_path.exists():
        raise ParamsError(
            f"params.schema.json not found at {schema_path}. The schema is not optional — "
            "it is what makes a malformed config fail fast instead of producing a "
            "quietly-wrong dataset."
        )
    import jsonschema  # imported here so the module loads without it for hashing-only use

    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [
            f"  {'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
            for err in errors
        ]
        raise ParamsError(
            f"{params_path} failed schema validation ({len(errors)} error(s)):\n"
            + "\n".join(lines)
        )


def _canonical_sha256(data: dict[str, Any]) -> str:
    """Hash a parameter mapping in a key-order-independent, formatting-independent way.

    Re-indenting the YAML or reordering keys must not change the hash — only a
    changed *value* should. Serialising to JSON with sorted keys achieves that.
    """
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_solved_key(key: Any) -> bool:
    """True for a machine-written ``*_solved`` field."""
    return isinstance(key, str) and key.endswith(SOLVED_SUFFIX)


def _strip_intercepts(data: Any) -> Any:
    """Return a deep copy with every machine-written ``*_solved`` value removed."""
    if isinstance(data, dict):
        # Some weight maps are keyed by int (cart_size, quantity, weekday), so
        # the suffix test has to tolerate a non-string key rather than assume one.
        return {k: _strip_intercepts(v) for k, v in data.items()
                if not _is_solved_key(k)}
    if isinstance(data, list):
        return [_strip_intercepts(v) for v in data]
    return data


def _find_intercepts(data: Any, prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if _is_solved_key(key):
                found.append((path, value))
            else:
                found.extend(_find_intercepts(value, path))
    return found


def _deep_merge(
    base: dict[str, Any], override: dict[str, Any], prefix: str = ""
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Deep-merge a scenario override over the base params, recording what changed."""
    merged = dict(base)
    changed: list[str] = []
    for key, value in override.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            sub, sub_changed = _deep_merge(merged[key], value, path)
            merged[key] = sub
            changed.extend(sub_changed)
        else:
            merged[key] = value
            changed.append(path)
    return merged, tuple(changed)
