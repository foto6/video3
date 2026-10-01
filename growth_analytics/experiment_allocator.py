from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Sequence

from .autonomous_reels import (
    canonical_json,
    parse_metric_snapshot,
    sha256_json,
    validate_next_cycle_seed,
)


ALLOCATOR_PLAN_VERSION = "growth.shortform_experiment_plan.v1"
ALLOCATOR_EVIDENCE_VERSION = "growth.shortform_allocator_evidence.v1"
ALLOCATOR_LEDGER_VERSION = "growth.shortform_experiment_allocator_ledger.v1"
ALLOCATOR_REPLAY_VERSION = "growth.shortform_experiment_allocator_replay.v1"

DIMENSION_VALUES: dict[str, tuple[str, ...]] = {
    "hook_type": (
        "question",
        "contrarian",
        "result_first",
        "curiosity_gap",
    ),
    "first_3s_pacing": ("fast", "moderate", "deliberate"),
    "caption_density": ("sparse", "medium", "dense"),
    "cta": ("follow", "save", "comment", "link", "none"),
    "duration_bucket": (
        "under_15s",
        "15_30s",
        "30_45s",
        "45_60s",
    ),
    "loop_ending": ("none", "soft_loop", "hard_loop"),
    "broll_density": ("low", "medium", "high"),
    "edit_style": ("clean", "kinetic", "documentary", "meme"),
}
DIMENSIONS = tuple(DIMENSION_VALUES)
EVIDENCE_KINDS = frozenset({"observational", "randomized"})


class ExperimentAllocatorError(ValueError):
    pass


class AllocatorConflictError(ExperimentAllocatorError):
    pass


class InjectedAllocatorFault(RuntimeError):
    pass


@dataclass(frozen=True)
class ExperimentAllocatorPolicy:
    min_history_posts: int = 30
    min_value_observations: int = 5
    min_randomized_observations: int = 8
    max_cells: int = 4
    holdback_fraction: float = 0.25
    exploration_fraction: float = 0.25
    max_reissues_per_signature: int = 2

    def __post_init__(self) -> None:
        for value, field in (
            (self.min_history_posts, "min_history_posts"),
            (
                self.min_value_observations,
                "min_value_observations",
            ),
            (
                self.min_randomized_observations,
                "min_randomized_observations",
            ),
            (self.max_cells, "max_cells"),
            (
                self.max_reissues_per_signature,
                "max_reissues_per_signature",
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise ExperimentAllocatorError(
                    f"{field} must be a positive integer"
                )
        if self.max_cells < 2:
            raise ExperimentAllocatorError(
                "max_cells must allow control plus treatment"
            )
        for value, field in (
            (self.holdback_fraction, "holdback_fraction"),
            (self.exploration_fraction, "exploration_fraction"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 < float(value) < 1
            ):
                raise ExperimentAllocatorError(
                    f"{field} must be in (0,1)"
                )
        if (
            self.holdback_fraction
            + self.exploration_fraction
            >= 1.0
        ):
            raise ExperimentAllocatorError(
                "holdback plus exploration must leave treatment capacity"
            )


def _digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise ExperimentAllocatorError(
            f"{field} must be lowercase SHA-256 hex"
        )
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExperimentAllocatorError(
            f"{field} must be a non-empty string"
        )
    return value


def _dimensions(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(DIMENSIONS):
        raise ExperimentAllocatorError(
            "creative_dimensions must contain every allocator dimension"
        )
    normalized: dict[str, str] = {}
    for dimension in DIMENSIONS:
        selected = value[dimension]
        if selected not in DIMENSION_VALUES[dimension]:
            raise ExperimentAllocatorError(
                f"unsupported {dimension} value"
            )
        normalized[dimension] = selected
    return normalized


def _basis(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value)
        != {
            "kind",
            "experiment_id",
            "variant_id",
            "assignment_digest",
            "registry_freeze_hash",
        }
    ):
        raise ExperimentAllocatorError(
            "evidence_basis fields invalid"
        )
    kind = value["kind"]
    if kind not in EVIDENCE_KINDS:
        raise ExperimentAllocatorError(
            "unsupported evidence basis kind"
        )
    result = dict(value)
    if kind == "observational":
        if any(
            result[field] is not None
            for field in (
                "experiment_id",
                "variant_id",
                "assignment_digest",
                "registry_freeze_hash",
            )
        ):
            raise ExperimentAllocatorError(
                "observational evidence cannot claim randomized assignment"
            )
    else:
        _nonempty(result["experiment_id"], "experiment_id")
        _nonempty(result["variant_id"], "variant_id")
        _digest(
            result["assignment_digest"],
            "assignment_digest",
        )
        _digest(
            result["registry_freeze_hash"],
            "registry_freeze_hash",
        )
    return result


def build_allocator_evidence(
    *,
    metric_snapshot: Mapping[str, Any],
    creative_dimensions: Mapping[str, str],
    evidence_basis: Mapping[str, Any],
) -> dict[str, Any]:
    snapshot = parse_metric_snapshot(metric_snapshot)
    dimensions = _dimensions(creative_dimensions)
    basis = _basis(evidence_basis)
    completion = snapshot["normalized_metrics"]["completion_rate"]
    views = snapshot["normalized_metrics"]["views"]
    material = {
        "evidence_version": ALLOCATOR_EVIDENCE_VERSION,
        "source_class": snapshot["source_class"],
        "platform": snapshot["platform"],
        "post_id": snapshot["post_id"],
        "cycle_revision": snapshot["cycle_revision"],
        "metric_snapshot_digest": snapshot["snapshot_digest"],
        "metric_window": dict(snapshot["window"]),
        "metric_snapshot": snapshot,
        "creative_dimensions": dimensions,
        "evidence_basis": basis,
        "outcome": {
            "primary_metric": "completion_rate",
            "value": completion,
            "views": views,
        },
    }
    identity = {
        "platform": material["platform"],
        "post_id": material["post_id"],
        "metric_snapshot_digest":
            material["metric_snapshot_digest"],
        "creative_dimensions": dimensions,
        "evidence_basis": basis,
    }
    material["evidence_id"] = "gae1:" + sha256_json(identity)
    material["evidence_digest"] = sha256_json(material)
    return json.loads(canonical_json(material))


def parse_allocator_evidence(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "evidence_version",
        "evidence_id",
        "evidence_digest",
        "source_class",
        "platform",
        "post_id",
        "cycle_revision",
        "metric_snapshot_digest",
        "metric_window",
        "metric_snapshot",
        "creative_dimensions",
        "evidence_basis",
        "outcome",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise ExperimentAllocatorError(
            "allocator evidence fields must match v1 exactly"
        )
    if payload["evidence_version"] != ALLOCATOR_EVIDENCE_VERSION:
        raise ExperimentAllocatorError(
            "unsupported allocator evidence version"
        )
    if payload["source_class"] not in {
        "platform_export",
        "synthetic_fixture",
    }:
        raise ExperimentAllocatorError(
            "unsupported allocator evidence source_class"
        )
    _nonempty(payload["platform"], "platform")
    _nonempty(payload["post_id"], "post_id")
    revision = payload["cycle_revision"]
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
    ):
        raise ExperimentAllocatorError(
            "cycle_revision must be integer >= 1"
        )
    _digest(
        payload["metric_snapshot_digest"],
        "metric_snapshot_digest",
    )
    snapshot = parse_metric_snapshot(payload["metric_snapshot"])
    if (
        snapshot["snapshot_digest"]
        != payload["metric_snapshot_digest"]
        or snapshot["source_class"] != payload["source_class"]
        or snapshot["platform"] != payload["platform"]
        or snapshot["post_id"] != payload["post_id"]
        or snapshot["cycle_revision"] != revision
    ):
        raise ExperimentAllocatorError(
            "allocator evidence is not bound to metric snapshot"
        )
    window = payload["metric_window"]
    if (
        not isinstance(window, Mapping)
        or set(window) != {"start", "end"}
        or not all(isinstance(window[k], str) for k in window)
    ):
        raise ExperimentAllocatorError(
            "metric_window fields invalid"
        )
    if dict(window) != snapshot["window"]:
        raise ExperimentAllocatorError(
            "allocator metric window differs from bound snapshot"
        )
    dimensions = _dimensions(payload["creative_dimensions"])
    basis = _basis(payload["evidence_basis"])
    outcome = payload["outcome"]
    if (
        not isinstance(outcome, Mapping)
        or set(outcome) != {"primary_metric", "value", "views"}
        or outcome["primary_metric"] != "completion_rate"
    ):
        raise ExperimentAllocatorError(
            "allocator outcome fields invalid"
        )
    value = outcome["value"]
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= 1
    ):
        raise ExperimentAllocatorError(
            "completion_rate must be null or in [0,1]"
        )
    views = outcome["views"]
    if views is not None and (
        isinstance(views, bool)
        or not isinstance(views, int)
        or views < 0
    ):
        raise ExperimentAllocatorError(
            "views must be null or non-negative integer"
        )
    if (
        value != snapshot["normalized_metrics"]["completion_rate"]
        or views != snapshot["normalized_metrics"]["views"]
    ):
        raise ExperimentAllocatorError(
            "allocator outcome differs from bound snapshot"
        )
    rebuilt = {
        "evidence_version": ALLOCATOR_EVIDENCE_VERSION,
        "source_class": payload["source_class"],
        "platform": payload["platform"],
        "post_id": payload["post_id"],
        "cycle_revision": revision,
        "metric_snapshot_digest": payload["metric_snapshot_digest"],
        "metric_window": dict(window),
        "metric_snapshot": snapshot,
        "creative_dimensions": dimensions,
        "evidence_basis": basis,
        "outcome": {
            "primary_metric": "completion_rate",
            "value": None if value is None else round(float(value), 8),
            "views": views,
        },
    }
    identity = {
        "platform": rebuilt["platform"],
        "post_id": rebuilt["post_id"],
        "metric_snapshot_digest":
            rebuilt["metric_snapshot_digest"],
        "creative_dimensions": dimensions,
        "evidence_basis": basis,
    }
    rebuilt["evidence_id"] = "gae1:" + sha256_json(identity)
    rebuilt["evidence_digest"] = sha256_json(rebuilt)
    if rebuilt != payload:
        raise ExperimentAllocatorError(
            "allocator evidence identity or digest mismatch"
        )
    return json.loads(canonical_json(rebuilt))


def _evidence_set(
    history: Sequence[Mapping[str, Any]],
    *,
    source_class: str,
) -> tuple[dict[str, Any], ...]:
    by_id: dict[str, dict[str, Any]] = {}
    post_snapshot: dict[tuple[str, str], str] = {}
    for raw in history:
        item = parse_allocator_evidence(raw)
        if item["source_class"] != source_class:
            raise ExperimentAllocatorError(
                "historical evidence source_class differs from consumed seed"
            )
        identity = (item["platform"], item["post_id"])
        previous_snapshot = post_snapshot.get(identity)
        if (
            previous_snapshot is not None
            and previous_snapshot != item["metric_snapshot_digest"]
        ):
            raise ExperimentAllocatorError(
                "history must contain one selected snapshot per post"
            )
        post_snapshot[identity] = item["metric_snapshot_digest"]
        previous = by_id.get(item["evidence_id"])
        if previous is not None and previous != item:
            raise ExperimentAllocatorError(
                "conflicting duplicate allocator evidence"
            )
        by_id[item["evidence_id"]] = item
    return tuple(
        by_id[key]
        for key in sorted(by_id)
    )


def _control_dimensions(
    history: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    control: dict[str, str] = {}
    for dimension in DIMENSIONS:
        counts = Counter(
            item["creative_dimensions"][dimension]
            for item in history
        )
        if not counts:
            control[dimension] = DIMENSION_VALUES[dimension][0]
            continue
        high = max(counts.values())
        control[dimension] = sorted(
            value for value, count in counts.items()
            if count == high
        )[0]
    return control


def _signature(
    control: Mapping[str, str],
    treatment: Mapping[str, str],
) -> str:
    changed = [
        {
            "dimension": dimension,
            "control": control[dimension],
            "treatment": treatment[dimension],
        }
        for dimension in DIMENSIONS
        if treatment[dimension] != control[dimension]
    ]
    return "gas1:" + sha256_json(changed)


def _candidate_stats(
    history: Sequence[Mapping[str, Any]],
    control: Mapping[str, str],
    policy: ExperimentAllocatorPolicy,
    prior_signature_counts: Mapping[str, int],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in history:
        if item["outcome"]["value"] is None:
            continue
        for dimension in DIMENSIONS:
            grouped[
                (dimension, item["creative_dimensions"][dimension])
            ].append(item)

    candidates: list[dict[str, Any]] = []
    for dimension in DIMENSIONS:
        control_rows = grouped.get(
            (dimension, control[dimension]),
            [],
        )
        control_values = [
            float(row["outcome"]["value"])
            for row in control_rows
        ]
        control_mean = (
            fmean(control_values) if control_values else None
        )
        for value in DIMENSION_VALUES[dimension]:
            if value == control[dimension]:
                continue
            rows = grouped.get((dimension, value), [])
            values = [
                float(row["outcome"]["value"])
                for row in rows
            ]
            treatment = dict(control)
            treatment[dimension] = value
            signature = _signature(control, treatment)
            reissues = prior_signature_counts.get(signature, 0)
            randomized_count = sum(
                1
                for row in rows
                if row["evidence_basis"]["kind"] == "randomized"
            )
            if (
                len(values) >= policy.min_value_observations
                and len(control_values)
                >= policy.min_value_observations
            ):
                directional_delta = round(
                    fmean(values) - float(control_mean),
                    8,
                )
                eligible_directional = (
                    reissues < policy.max_reissues_per_signature
                )
            else:
                directional_delta = None
                eligible_directional = False
            candidates.append({
                "dimension": dimension,
                "value": value,
                "treatment": treatment,
                "signature": signature,
                "observations": len(values),
                "control_observations": len(control_values),
                "randomized_observations": randomized_count,
                "directional_delta": directional_delta,
                "eligible_directional": eligible_directional,
                "reissue_count": reissues,
            })
    return candidates


def _cell(
    *,
    cell_id: str,
    role: str,
    dimensions: Mapping[str, str],
    sample_target: int,
    evidence_state: str,
    uncertainty_state: str,
    signature: str | None,
    historical_observations: int,
    randomized_observations: int,
    rationale: str,
) -> dict[str, Any]:
    return {
        "cell_id": cell_id,
        "role": role,
        "creative_dimensions": dict(dimensions),
        "sample_target": sample_target,
        "evidence_state": evidence_state,
        "uncertainty_state": uncertainty_state,
        "experiment_signature": signature,
        "historical_observations": historical_observations,
        "randomized_observations": randomized_observations,
        "rationale": rationale,
    }


def build_experiment_plan(
    *,
    seed: Mapping[str, Any],
    historical_evidence: Sequence[Mapping[str, Any]],
    batch_id: str,
    batch_size: int,
    prior_signature_counts: Mapping[str, int] | None = None,
    policy: ExperimentAllocatorPolicy | None = None,
    allow_synthetic_fixture: bool = False,
) -> dict[str, Any]:
    policy = policy or ExperimentAllocatorPolicy()
    _nonempty(batch_id, "batch_id")
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size < 2
    ):
        raise ExperimentAllocatorError(
            "batch_size must be integer >= 2"
        )
    parsed_seed = validate_next_cycle_seed(
        seed,
        expected_cycle_revision=seed["cycle_revision"],
        allow_synthetic_fixture=allow_synthetic_fixture,
    )
    source_class = parsed_seed["source_class"]
    history = _evidence_set(
        historical_evidence,
        source_class=source_class,
    )
    if len(history) > 5000:
        raise ExperimentAllocatorError(
            "historical evidence exceeds bounded allocator limit"
        )
    prior_counts = {
        str(key): int(value)
        for key, value in (prior_signature_counts or {}).items()
    }
    if any(value < 0 for value in prior_counts.values()):
        raise ExperimentAllocatorError(
            "prior signature counts cannot be negative"
        )
    control = _control_dimensions(history)
    candidates = _candidate_stats(
        history,
        control,
        policy,
        prior_counts,
    )
    sufficient_history = len(history) >= policy.min_history_posts
    ranked = sorted(
        (
            item
            for item in candidates
            if item["eligible_directional"]
            and item["directional_delta"] is not None
        ),
        key=lambda item: (
            -item["directional_delta"],
            -item["randomized_observations"],
            -item["observations"],
            item["reissue_count"],
            item["dimension"],
            item["value"],
        ),
    )
    exploratory = sorted(
        (
            item
            for item in candidates
            if item["reissue_count"]
            < policy.max_reissues_per_signature
        ),
        key=lambda item: (
            item["observations"],
            item["reissue_count"],
            item["dimension"],
            item["value"],
        ),
    )

    holdback = max(
        1,
        int(math.ceil(batch_size * policy.holdback_fraction)),
    )
    exploration = max(
        1,
        int(math.floor(batch_size * policy.exploration_fraction)),
    )
    if holdback + exploration >= batch_size:
        exploration = max(1, batch_size - holdback - 1)
    treatment_target = batch_size - holdback - exploration

    cells: list[dict[str, Any]] = [
        _cell(
            cell_id="control",
            role="control_holdback",
            dimensions=control,
            sample_target=holdback,
            evidence_state="control_reference",
            uncertainty_state=(
                "bounded_observational"
                if sufficient_history
                else "insufficient_history"
            ),
            signature=None,
            historical_observations=len(history),
            randomized_observations=sum(
                1
                for item in history
                if item["evidence_basis"]["kind"] == "randomized"
            ),
            rationale=(
                "Holdback preserves a stable reference configuration; "
                "historical performance is not treated as causal lift."
            ),
        )
    ]
    used_signatures: set[str] = set()

    if sufficient_history and ranked and treatment_target > 0:
        best = ranked[0]
        randomized_supported = (
            best["randomized_observations"]
            >= policy.min_randomized_observations
        )
        cells.append(
            _cell(
                cell_id="directional-1",
                role="directional_test",
                dimensions=best["treatment"],
                sample_target=treatment_target,
                evidence_state=(
                    "randomized_assignment_supported"
                    if randomized_supported
                    else "historical_observational_directional"
                ),
                uncertainty_state=(
                    "randomized_history_not_reestimated_here"
                    if randomized_supported
                    else "directional_not_causal"
                ),
                signature=best["signature"],
                historical_observations=best["observations"],
                randomized_observations=
                    best["randomized_observations"],
                rationale=(
                    f"Test {best['dimension']}={best['value']}; "
                    f"historical completion-rate difference="
                    f"{best['directional_delta']:+.8f}. "
                    "This plan does not claim causal lift."
                ),
            )
        )
        used_signatures.add(best["signature"])
    else:
        exploration += treatment_target
        treatment_target = 0

    explore = next(
        (
            item
            for item in exploratory
            if item["signature"] not in used_signatures
        ),
        None,
    )
    if explore is None:
        exploration += treatment_target
        treatment_target = 0
        explore_dimensions = dict(control)
        explore_dimension = DIMENSIONS[0]
        values = DIMENSION_VALUES[explore_dimension]
        current_index = values.index(control[explore_dimension])
        explore_dimensions[explore_dimension] = values[
            (current_index + 1) % len(values)
        ]
        explore_signature = _signature(
            control,
            explore_dimensions,
        )
        explore_observations = 0
        explore_reissues = prior_counts.get(explore_signature, 0)
    else:
        explore_dimensions = explore["treatment"]
        explore_dimension = explore["dimension"]
        explore_signature = explore["signature"]
        explore_observations = explore["observations"]
        explore_reissues = explore["reissue_count"]

    if (
        explore_reissues >= policy.max_reissues_per_signature
        and all(
            item["reissue_count"]
            >= policy.max_reissues_per_signature
            for item in candidates
        )
    ):
        cells[0]["sample_target"] += exploration
        exploration = 0
    elif exploration > 0:
        cells.append(
            _cell(
                cell_id="exploration-1",
                role="exploration",
                dimensions=explore_dimensions,
                sample_target=exploration,
                evidence_state="exploration_quota",
                uncertainty_state="high_uncertainty",
                signature=explore_signature,
                historical_observations=explore_observations,
                randomized_observations=(
                    0 if explore is None
                    else explore["randomized_observations"]
                ),
                rationale=(
                    f"Exploration quota tests {explore_dimension} "
                    "without interpreting historical association as causal."
                ),
            )
        )

    cells = cells[:policy.max_cells]
    assigned = sum(cell["sample_target"] for cell in cells)
    if assigned != batch_size:
        cells[0]["sample_target"] += batch_size - assigned

    evidence_digest = sha256_json([
        {
            "evidence_id": item["evidence_id"],
            "evidence_digest": item["evidence_digest"],
        }
        for item in history
    ])
    policy_material = {
        "min_history_posts": policy.min_history_posts,
        "min_value_observations": policy.min_value_observations,
        "min_randomized_observations":
            policy.min_randomized_observations,
        "max_cells": policy.max_cells,
        "holdback_fraction": policy.holdback_fraction,
        "exploration_fraction": policy.exploration_fraction,
        "max_reissues_per_signature":
            policy.max_reissues_per_signature,
    }
    plan = {
        "contract_version": ALLOCATOR_PLAN_VERSION,
        "plan_id": "",
        "plan_digest": "",
        "batch_id": batch_id,
        "batch_size": batch_size,
        "source_class": source_class,
        "cycle_revision": parsed_seed["cycle_revision"],
        "seed": {
            "idempotency_key": parsed_seed["idempotency_key"],
            "seed_digest": parsed_seed["seed_digest"],
        },
        "historical_evidence": {
            "count": len(history),
            "evidence_set_digest": evidence_digest,
            "source_class": source_class,
            "observational_count": sum(
                1
                for item in history
                if item["evidence_basis"]["kind"] == "observational"
            ),
            "randomized_count": sum(
                1
                for item in history
                if item["evidence_basis"]["kind"] == "randomized"
            ),
            "minimum_gate_met": sufficient_history,
        },
        "allocator_policy": policy_material,
        "cells": cells,
        "authority": {
            "advisory_only": True,
            "auto_publish": False,
            "external_mutation": False,
            "publish_authorized": False,
            "release_authorized": False,
            "requires_creator_release_authorization": True,
        },
        "interpretation": {
            "observational_history": (
                "Directional associations may prioritize tests but are "
                "not causal lift estimates."
            ),
            "randomized_history": (
                "Randomized assignment support is labeled separately; "
                "this allocator does not re-estimate causal effects."
            ),
            "plan_role": (
                "Cells are advisory test instructions for Creator and "
                "grant no provider or publishing authority."
            ),
        },
    }
    identity = {
        "batch_id": batch_id,
        "batch_size": batch_size,
        "seed_digest": parsed_seed["seed_digest"],
        "evidence_set_digest": evidence_digest,
        "policy": policy_material,
        "cells": cells,
    }
    plan["plan_id"] = "gap1:" + sha256_json(identity)
    digest_material = dict(plan)
    digest_material["plan_digest"] = ""
    plan["plan_digest"] = sha256_json(digest_material)
    return parse_experiment_plan(plan)


def parse_experiment_plan(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "contract_version",
        "plan_id",
        "plan_digest",
        "batch_id",
        "batch_size",
        "source_class",
        "cycle_revision",
        "seed",
        "historical_evidence",
        "allocator_policy",
        "cells",
        "authority",
        "interpretation",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise ExperimentAllocatorError(
            "experiment plan fields must match v1 exactly"
        )
    if payload["contract_version"] != ALLOCATOR_PLAN_VERSION:
        raise ExperimentAllocatorError(
            "unsupported experiment allocator plan version"
        )
    _nonempty(payload["batch_id"], "batch_id")
    batch_size = payload["batch_size"]
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size < 2
    ):
        raise ExperimentAllocatorError("invalid batch_size")
    if payload["source_class"] not in {
        "platform_export",
        "synthetic_fixture",
    }:
        raise ExperimentAllocatorError("invalid source_class")
    revision = payload["cycle_revision"]
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
    ):
        raise ExperimentAllocatorError("invalid cycle_revision")
    seed_ref = payload["seed"]
    if (
        not isinstance(seed_ref, Mapping)
        or set(seed_ref) != {"idempotency_key", "seed_digest"}
    ):
        raise ExperimentAllocatorError("seed reference fields invalid")
    _nonempty(seed_ref["idempotency_key"], "seed.idempotency_key")
    _digest(seed_ref["seed_digest"], "seed.seed_digest")
    history = payload["historical_evidence"]
    if (
        not isinstance(history, Mapping)
        or set(history)
        != {
            "count",
            "evidence_set_digest",
            "source_class",
            "observational_count",
            "randomized_count",
            "minimum_gate_met",
        }
    ):
        raise ExperimentAllocatorError(
            "historical_evidence fields invalid"
        )
    if history["source_class"] != payload["source_class"]:
        raise ExperimentAllocatorError(
            "historical evidence source scope mismatch"
        )
    for field in ("count", "observational_count", "randomized_count"):
        value = history[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise ExperimentAllocatorError(
                f"{field} must be non-negative integer"
            )
    if (
        history["observational_count"]
        + history["randomized_count"]
        != history["count"]
    ):
        raise ExperimentAllocatorError(
            "historical evidence counts do not sum"
        )
    _digest(
        history["evidence_set_digest"],
        "evidence_set_digest",
    )
    if not isinstance(history["minimum_gate_met"], bool):
        raise ExperimentAllocatorError(
            "minimum_gate_met must be boolean"
        )
    policy = payload["allocator_policy"]
    if (
        not isinstance(policy, Mapping)
        or set(policy)
        != {
            "min_history_posts",
            "min_value_observations",
            "min_randomized_observations",
            "max_cells",
            "holdback_fraction",
            "exploration_fraction",
            "max_reissues_per_signature",
        }
    ):
        raise ExperimentAllocatorError(
            "allocator_policy fields invalid"
        )
    ExperimentAllocatorPolicy(
        min_history_posts=policy["min_history_posts"],
        min_value_observations=
            policy["min_value_observations"],
        min_randomized_observations=
            policy["min_randomized_observations"],
        max_cells=policy["max_cells"],
        holdback_fraction=policy["holdback_fraction"],
        exploration_fraction=policy["exploration_fraction"],
        max_reissues_per_signature=
            policy["max_reissues_per_signature"],
    )
    cells = payload["cells"]
    if (
        not isinstance(cells, list)
        or len(cells) < 1
        or len(cells) > policy["max_cells"]
    ):
        raise ExperimentAllocatorError(
            "cells violate allocator bounds"
        )
    seen_ids: set[str] = set()
    seen_signatures: set[str] = set()
    sample_total = 0
    control_count = 0
    for cell in cells:
        if (
            not isinstance(cell, Mapping)
            or set(cell)
            != {
                "cell_id",
                "role",
                "creative_dimensions",
                "sample_target",
                "evidence_state",
                "uncertainty_state",
                "experiment_signature",
                "historical_observations",
                "randomized_observations",
                "rationale",
            }
        ):
            raise ExperimentAllocatorError(
                "cell fields invalid"
            )
        cell_id = _nonempty(cell["cell_id"], "cell_id")
        if cell_id in seen_ids:
            raise ExperimentAllocatorError(
                "cell ids must be unique"
            )
        seen_ids.add(cell_id)
        if cell["role"] not in {
            "control_holdback",
            "directional_test",
            "exploration",
        }:
            raise ExperimentAllocatorError(
                "unsupported cell role"
            )
        if cell["role"] == "control_holdback":
            control_count += 1
            if cell["experiment_signature"] is not None:
                raise ExperimentAllocatorError(
                    "control cell cannot have experiment signature"
                )
        else:
            signature = cell["experiment_signature"]
            _nonempty(signature, "experiment_signature")
            if signature in seen_signatures:
                raise ExperimentAllocatorError(
                    "experiment signatures must be unique in plan"
                )
            seen_signatures.add(signature)
        _dimensions(cell["creative_dimensions"])
        sample_target = cell["sample_target"]
        if (
            isinstance(sample_target, bool)
            or not isinstance(sample_target, int)
            or sample_target < 1
        ):
            raise ExperimentAllocatorError(
                "sample_target must be positive integer"
            )
        sample_total += sample_target
        for field in (
            "historical_observations",
            "randomized_observations",
        ):
            value = cell[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ExperimentAllocatorError(
                    f"{field} invalid"
                )
        _nonempty(cell["evidence_state"], "evidence_state")
        _nonempty(
            cell["uncertainty_state"],
            "uncertainty_state",
        )
        _nonempty(cell["rationale"], "rationale")
    if control_count != 1:
        raise ExperimentAllocatorError(
            "plan must contain exactly one control holdback cell"
        )
    if sample_total != batch_size:
        raise ExperimentAllocatorError(
            "cell sample targets must equal batch_size"
        )
    authority = payload["authority"]
    if (
        not isinstance(authority, Mapping)
        or authority
        != {
            "advisory_only": True,
            "auto_publish": False,
            "external_mutation": False,
            "publish_authorized": False,
            "release_authorized": False,
            "requires_creator_release_authorization": True,
        }
    ):
        raise ExperimentAllocatorError(
            "allocator authority boundary invalid"
        )
    interpretation = payload["interpretation"]
    if (
        not isinstance(interpretation, Mapping)
        or set(interpretation)
        != {
            "observational_history",
            "randomized_history",
            "plan_role",
        }
        or any(
            not isinstance(value, str) or not value
            for value in interpretation.values()
        )
    ):
        raise ExperimentAllocatorError(
            "allocator interpretation fields invalid"
        )
    expected_id = "gap1:" + sha256_json({
        "batch_id": payload["batch_id"],
        "batch_size": batch_size,
        "seed_digest": seed_ref["seed_digest"],
        "evidence_set_digest": history["evidence_set_digest"],
        "policy": dict(policy),
        "cells": cells,
    })
    if payload["plan_id"] != expected_id:
        raise ExperimentAllocatorError(
            "experiment plan identity mismatch"
        )
    _digest(payload["plan_digest"], "plan_digest")
    digest_material = dict(payload)
    digest_material["plan_digest"] = ""
    if sha256_json(digest_material) != payload["plan_digest"]:
        raise ExperimentAllocatorError(
            "experiment plan digest mismatch"
        )
    return json.loads(canonical_json(dict(payload)))


class ExperimentAllocatorLedger:
    """Durable exactly-once seed consumption and plan preparation."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._sequence = 0
        self._consumed: dict[str, str] = {}
        self._plans: dict[str, dict[str, Any]] = {}
        self._seed_plans: dict[str, str] = {}
        self._signature_counts: Counter[str] = Counter()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AllocatorConflictError(
                    f"invalid allocator ledger JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "event_type",
                    "identity",
                    "payload_digest",
                    "payload",
                }
            ):
                raise AllocatorConflictError(
                    "allocator ledger row fields invalid"
                )
            if row["ledger_version"] != ALLOCATOR_LEDGER_VERSION:
                raise AllocatorConflictError(
                    "unsupported allocator ledger version"
                )
            if row["sequence"] != self._sequence + 1:
                raise AllocatorConflictError(
                    "allocator ledger sequence not contiguous"
                )
            event_type = row["event_type"]
            if event_type == "consume_seed":
                payload = row["payload"]
                if (
                    not isinstance(payload, Mapping)
                    or set(payload)
                    != {"idempotency_key", "seed_digest"}
                    or row["identity"] != payload["idempotency_key"]
                    or row["payload_digest"] != payload["seed_digest"]
                ):
                    raise AllocatorConflictError(
                        "seed-consume ledger binding invalid"
                    )
                previous = self._consumed.get(row["identity"])
                if previous is not None:
                    raise AllocatorConflictError(
                        "duplicate durable seed consumption"
                    )
                self._consumed[row["identity"]] = row["payload_digest"]
            elif event_type == "prepare_plan":
                plan = parse_experiment_plan(row["payload"])
                if (
                    row["identity"] != plan["batch_id"]
                    or row["payload_digest"] != plan["plan_digest"]
                ):
                    raise AllocatorConflictError(
                        "plan ledger binding invalid"
                    )
                if row["identity"] in self._plans:
                    raise AllocatorConflictError(
                        "duplicate durable batch plan"
                    )
                seed_key = plan["seed"]["idempotency_key"]
                if seed_key in self._seed_plans:
                    raise AllocatorConflictError(
                        "consumed seed produced more than one durable plan"
                    )
                if seed_key not in self._consumed:
                    raise AllocatorConflictError(
                        "plan prepared before durable seed consumption"
                    )
                if (
                    self._consumed[seed_key]
                    != plan["seed"]["seed_digest"]
                ):
                    raise AllocatorConflictError(
                        "plan seed digest differs from consumed seed"
                    )
                self._plans[row["identity"]] = plan
                self._seed_plans[seed_key] = row["identity"]
                for cell in plan["cells"]:
                    signature = cell["experiment_signature"]
                    if signature is not None:
                        self._signature_counts[signature] += 1
            else:
                raise AllocatorConflictError(
                    "unknown allocator ledger event_type"
                )
            self._sequence += 1

    def _append(
        self,
        event_type: str,
        identity: str,
        payload_digest: str,
        payload: Mapping[str, Any],
    ) -> None:
        row = {
            "ledger_version": ALLOCATOR_LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "event_type": event_type,
            "identity": identity,
            "payload_digest": payload_digest,
            "payload": json.loads(canonical_json(dict(payload))),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._sequence += 1

    def consume_seed(
        self,
        seed: Mapping[str, Any],
        *,
        allow_synthetic_fixture: bool,
    ) -> str:
        parsed = validate_next_cycle_seed(
            seed,
            expected_cycle_revision=seed["cycle_revision"],
            allow_synthetic_fixture=allow_synthetic_fixture,
        )
        key = parsed["idempotency_key"]
        digest = parsed["seed_digest"]
        previous = self._consumed.get(key)
        if previous is not None:
            if previous != digest:
                raise AllocatorConflictError(
                    "consumed seed identity changed payload"
                )
            return "duplicate"
        self._append(
            "consume_seed",
            key,
            digest,
            {
                "idempotency_key": key,
                "seed_digest": digest,
            },
        )
        self._consumed[key] = digest
        return "consumed"

    def prepare_plan(
        self,
        plan: Mapping[str, Any],
    ) -> str:
        parsed = parse_experiment_plan(plan)
        batch_id = parsed["batch_id"]
        previous = self._plans.get(batch_id)
        if previous is not None:
            if previous["plan_digest"] != parsed["plan_digest"]:
                raise AllocatorConflictError(
                    "batch identity changed experiment plan"
                )
            return "duplicate"
        seed_key = parsed["seed"]["idempotency_key"]
        prior_batch = self._seed_plans.get(seed_key)
        if prior_batch is not None:
            raise AllocatorConflictError(
                "consumed seed is already bound to another batch plan"
            )
        consumed_digest = self._consumed.get(seed_key)
        if consumed_digest != parsed["seed"]["seed_digest"]:
            raise AllocatorConflictError(
                "plan requires matching durable seed consumption"
            )
        self._append(
            "prepare_plan",
            batch_id,
            parsed["plan_digest"],
            parsed,
        )
        self._plans[batch_id] = parsed
        self._seed_plans[seed_key] = batch_id
        for cell in parsed["cells"]:
            signature = cell["experiment_signature"]
            if signature is not None:
                self._signature_counts[signature] += 1
        return "prepared"

    def plan(self, batch_id: str) -> dict[str, Any] | None:
        plan = self._plans.get(batch_id)
        return (
            None
            if plan is None
            else json.loads(canonical_json(plan))
        )

    def signature_counts(self) -> dict[str, int]:
        return dict(self._signature_counts)

    @property
    def consumed_seed_count(self) -> int:
        return len(self._consumed)

    @property
    def plan_count(self) -> int:
        return len(self._plans)

    @property
    def row_count(self) -> int:
        return self._sequence


class DurableExperimentAllocator:
    """Restart-safe advisory allocator. It grants no publish authority."""

    def __init__(
        self,
        ledger: ExperimentAllocatorLedger,
        *,
        policy: ExperimentAllocatorPolicy | None = None,
    ) -> None:
        self.ledger = ledger
        self.policy = policy or ExperimentAllocatorPolicy()

    def allocate(
        self,
        *,
        seed: Mapping[str, Any],
        historical_evidence: Sequence[Mapping[str, Any]],
        batch_id: str,
        batch_size: int,
        allow_synthetic_fixture: bool = False,
        inject_fault: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        existing = self.ledger.plan(batch_id)
        if existing is not None:
            parsed_seed = validate_next_cycle_seed(
                seed,
                expected_cycle_revision=seed["cycle_revision"],
                allow_synthetic_fixture=allow_synthetic_fixture,
            )
            if (
                existing["seed"]["seed_digest"]
                != parsed_seed["seed_digest"]
            ):
                raise AllocatorConflictError(
                    "existing batch plan belongs to another seed"
                )
            return "duplicate", existing

        self.ledger.consume_seed(
            seed,
            allow_synthetic_fixture=allow_synthetic_fixture,
        )
        if inject_fault == "after_seed_consume":
            raise InjectedAllocatorFault(
                "fault after durable seed consumption"
            )

        plan = build_experiment_plan(
            seed=seed,
            historical_evidence=historical_evidence,
            batch_id=batch_id,
            batch_size=batch_size,
            prior_signature_counts=self.ledger.signature_counts(),
            policy=self.policy,
            allow_synthetic_fixture=allow_synthetic_fixture,
        )
        status = self.ledger.prepare_plan(plan)
        if inject_fault == "after_plan_prepare":
            raise InjectedAllocatorFault(
                "fault after durable plan preparation"
            )
        return status, plan
