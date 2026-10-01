from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import (
    canonical_json,
    sha256_json,
    validate_next_cycle_seed,
)
from .experiment_allocator import (
    ALLOCATOR_PLAN_VERSION,
    DIMENSIONS,
    DIMENSION_VALUES,
    ExperimentAllocatorError,
    parse_allocator_evidence,
    parse_experiment_plan,
)


CREATOR_BATCH_HANDOFF_VERSION = (
    "growth.creator_batch_experiment_handoff.v1"
)
CREATOR_BATCH_OUTBOX_VERSION = (
    "growth.creator_batch_experiment_handoff_outbox.v1"
)
CREATOR_BATCH_CONSUMER_LEDGER_VERSION = (
    "growth.creator_batch_experiment_consumer_ledger.v1"
)
ALLOCATOR_IMPLEMENTATION_REVISION = 1
R13_SOURCE_COMMIT = "d4e610e43759c9892309f878d3524220f21be76c"
R13_ALLOCATOR_BLOB_SHA1 = "ea3eecd7605936fc7dc1c0414f8b448f50f7ef55"


class CreatorBatchHandoffError(ValueError):
    pass


class CreatorBatchHandoffConflictError(CreatorBatchHandoffError):
    pass


class StaleCreatorCampaignRevision(CreatorBatchHandoffError):
    pass


class FutureCreatorCampaignRevision(CreatorBatchHandoffError):
    pass


class StaleCreatorCycleRevision(CreatorBatchHandoffError):
    pass


class FutureCreatorCycleRevision(CreatorBatchHandoffError):
    pass


class CreatorBatchSyntheticRejected(CreatorBatchHandoffError):
    pass


class InjectedCreatorBatchDeliveryFault(RuntimeError):
    pass


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CreatorBatchHandoffError(
            f"{field} must be a non-empty string"
        )
    return value


def _positive_int(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise CreatorBatchHandoffError(
            f"{field} must be an integer >= 1"
        )
    return value


def _digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise CreatorBatchHandoffError(
            f"{field} must be lowercase SHA-256 hex"
        )
    return value


def _allocator_evidence_refs(
    historical_evidence: Sequence[Mapping[str, Any]],
    *,
    source_class: str,
) -> tuple[dict[str, Any], ...]:
    by_id: dict[str, dict[str, Any]] = {}
    seen_posts: dict[tuple[str, str], str] = {}
    for raw in historical_evidence:
        try:
            item = parse_allocator_evidence(raw)
        except ExperimentAllocatorError as exc:
            raise CreatorBatchHandoffError(
                "invalid allocator evidence"
            ) from exc
        if item["source_class"] != source_class:
            raise CreatorBatchHandoffError(
                "allocator evidence source class mismatch"
            )
        post_key = (item["platform"], item["post_id"])
        prior_snapshot = seen_posts.get(post_key)
        if (
            prior_snapshot is not None
            and prior_snapshot != item["metric_snapshot_digest"]
        ):
            raise CreatorBatchHandoffError(
                "multiple selected snapshots for one source post"
            )
        seen_posts[post_key] = item["metric_snapshot_digest"]
        basis = item["evidence_basis"]
        ref = {
            "evidence_id": item["evidence_id"],
            "evidence_digest": item["evidence_digest"],
            "platform": item["platform"],
            "post_id": item["post_id"],
            "metric_snapshot_digest":
                item["metric_snapshot_digest"],
            "evidence_kind": basis["kind"],
            "experiment_id": basis["experiment_id"],
            "variant_id": basis["variant_id"],
            "assignment_digest": basis["assignment_digest"],
            "registry_freeze_hash":
                basis["registry_freeze_hash"],
        }
        previous = by_id.get(ref["evidence_id"])
        if previous is not None and previous != ref:
            raise CreatorBatchHandoffError(
                "conflicting duplicate allocator evidence"
            )
        by_id[ref["evidence_id"]] = ref
    return tuple(by_id[key] for key in sorted(by_id))


def _evidence_set_digest(
    refs: Sequence[Mapping[str, Any]],
) -> str:
    return sha256_json([
        {
            "evidence_id": item["evidence_id"],
            "evidence_digest": item["evidence_digest"],
        }
        for item in refs
    ])


def _validate_source_ref(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "evidence_id",
        "evidence_digest",
        "platform",
        "post_id",
        "metric_snapshot_digest",
        "evidence_kind",
        "experiment_id",
        "variant_id",
        "assignment_digest",
        "registry_freeze_hash",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise CreatorBatchHandoffError(
            "source evidence reference fields invalid"
        )
    result = dict(raw)
    _nonempty(result["evidence_id"], "evidence_id")
    _digest(result["evidence_digest"], "evidence_digest")
    _nonempty(result["platform"], "platform")
    _nonempty(result["post_id"], "post_id")
    _digest(
        result["metric_snapshot_digest"],
        "metric_snapshot_digest",
    )
    kind = result["evidence_kind"]
    if kind == "observational":
        for field in (
            "experiment_id",
            "variant_id",
            "assignment_digest",
            "registry_freeze_hash",
        ):
            if result[field] is not None:
                raise CreatorBatchHandoffError(
                    "observational source reference carries randomized fields"
                )
    elif kind == "randomized":
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
    else:
        raise CreatorBatchHandoffError(
            "unsupported source evidence kind"
        )
    return result


def _creator_cells(
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "cell_id": cell["cell_id"],
            "role": cell["role"],
            "target_count": cell["sample_target"],
            "creative_dimensions":
                dict(cell["creative_dimensions"]),
            "evidence_state": cell["evidence_state"],
            "uncertainty_state":
                cell["uncertainty_state"],
            "experiment_signature":
                cell["experiment_signature"],
        }
        for cell in plan["cells"]
    ]


def _batch_constraints(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    policy = plan["allocator_policy"]
    cells = plan["cells"]
    control_target = sum(
        cell["sample_target"]
        for cell in cells
        if cell["role"] == "control_holdback"
    )
    exploration_target = sum(
        cell["sample_target"]
        for cell in cells
        if cell["role"] == "exploration"
    )
    return {
        "batch_size": plan["batch_size"],
        "max_cells": policy["max_cells"],
        "actual_cells": len(cells),
        "control_quota": {
            "target_count": control_target,
            "minimum_fraction":
                policy["holdback_fraction"],
        },
        "exploration_quota": {
            "target_count": exploration_target,
            "minimum_fraction":
                policy["exploration_fraction"],
        },
        "minimum_evidence_gate": {
            "required_posts":
                policy["min_history_posts"],
            "observed_posts":
                plan["historical_evidence"]["count"],
            "met":
                plan["historical_evidence"][
                    "minimum_gate_met"
                ],
        },
        "per_cell_targets": [
            {
                "cell_id": cell["cell_id"],
                "role": cell["role"],
                "target_count": cell["sample_target"],
            }
            for cell in cells
        ],
        "allowed_dimensions": list(DIMENSIONS),
        "mutually_exclusive_constraints": [
            {
                "constraint": "one_cell_per_batch_item",
                "max_memberships_per_item": 1,
            },
            {
                "constraint":
                    "single_changed_dimension_per_non_control_cell",
                "max_changed_dimensions": 1,
            },
            {
                "constraint": "exactly_one_control_cell",
                "required_count": 1,
            },
        ],
    }


def _consumer_eligibility(
    *,
    source_class: str,
    minimum_evidence_gate_met: bool,
) -> dict[str, Any]:
    reasons: list[str] = []
    if source_class != "platform_export":
        reasons.append("synthetic_fixture_conformance_only")
    if not minimum_evidence_gate_met:
        reasons.append("minimum_evidence_gate_not_met")
    return {
        "eligible": not reasons,
        "source_scope": (
            "live_source_bound"
            if source_class == "platform_export"
            else "synthetic_conformance_only"
        ),
        "requires_exact_campaign_binding": True,
        "requires_exact_cycle_revision": True,
        "requires_release_authorization": True,
        "ineligible_reasons": reasons,
    }


def _single_dimension_constraints(
    cells: Sequence[Mapping[str, Any]],
) -> None:
    controls = [
        cell for cell in cells
        if cell["role"] == "control_holdback"
    ]
    if len(controls) != 1:
        raise CreatorBatchHandoffError(
            "handoff requires exactly one control cell"
        )
    control = controls[0]["creative_dimensions"]
    for cell in cells:
        if cell["role"] == "control_holdback":
            continue
        changed = sum(
            1
            for dimension in DIMENSIONS
            if cell["creative_dimensions"][dimension]
            != control[dimension]
        )
        if changed != 1:
            raise CreatorBatchHandoffError(
                "non-control cell must change exactly one dimension"
            )


def build_creator_batch_experiment_handoff(
    *,
    seed: Mapping[str, Any],
    experiment_plan: Mapping[str, Any],
    historical_evidence: Sequence[Mapping[str, Any]],
    campaign_id: str,
    campaign_revision: int,
) -> dict[str, Any]:
    _nonempty(campaign_id, "campaign_id")
    campaign_revision = _positive_int(
        campaign_revision,
        "campaign_revision",
    )
    try:
        plan = parse_experiment_plan(experiment_plan)
    except ExperimentAllocatorError as exc:
        raise CreatorBatchHandoffError(
            "invalid allocator experiment plan"
        ) from exc
    parsed_seed = validate_next_cycle_seed(
        seed,
        expected_cycle_revision=seed["cycle_revision"],
        allow_synthetic_fixture=True,
    )
    if (
        plan["seed"]["idempotency_key"]
        != parsed_seed["idempotency_key"]
        or plan["seed"]["seed_digest"]
        != parsed_seed["seed_digest"]
    ):
        raise CreatorBatchHandoffError(
            "allocator plan is not bound to supplied next-cycle seed"
        )
    if plan["source_class"] != parsed_seed["source_class"]:
        raise CreatorBatchHandoffError(
            "allocator plan and source seed source_class mismatch"
        )
    refs = _allocator_evidence_refs(
        historical_evidence,
        source_class=plan["source_class"],
    )
    if len(refs) != plan["historical_evidence"]["count"]:
        raise CreatorBatchHandoffError(
            "allocator history count does not match source evidence"
        )
    evidence_set_digest = _evidence_set_digest(refs)
    if (
        evidence_set_digest
        != plan["historical_evidence"]["evidence_set_digest"]
    ):
        raise CreatorBatchHandoffError(
            "allocator history digest does not match source evidence"
        )

    cells = _creator_cells(plan)
    _single_dimension_constraints(cells)
    constraints = _batch_constraints(plan)
    eligibility = _consumer_eligibility(
        source_class=plan["source_class"],
        minimum_evidence_gate_met=
            constraints["minimum_evidence_gate"]["met"],
    )
    handoff = {
        "contract_version": CREATOR_BATCH_HANDOFF_VERSION,
        "handoff_id": "",
        "handoff_digest": "",
        "idempotency_key": "",
        "campaign": {
            "campaign_id": campaign_id,
            "campaign_revision": campaign_revision,
            "batch_id": plan["batch_id"],
            "batch_size": plan["batch_size"],
            "source_cycle_revision":
                parsed_seed["cycle_revision"],
        },
        "source_class": plan["source_class"],
        "allocator_revision": {
            "contract_version": ALLOCATOR_PLAN_VERSION,
            "implementation_revision":
                ALLOCATOR_IMPLEMENTATION_REVISION,
            "plan_id": plan["plan_id"],
            "plan_digest": plan["plan_digest"],
            "producer_commit": R13_SOURCE_COMMIT,
            "implementation_blob_sha1":
                R13_ALLOCATOR_BLOB_SHA1,
        },
        "source_seed": parsed_seed,
        "source_evidence": list(refs),
        "source_evidence_set_digest": evidence_set_digest,
        "experiment_plan": plan,
        "batch_constraints": constraints,
        "creator_cells": cells,
        "creator_consumer": eligibility,
        "authority": {
            "advisory_only": True,
            "auto_publish": False,
            "external_mutation": False,
            "provider_mutation": False,
            "publish_authorized": False,
            "release_authorized": False,
            "requires_creator_release_authorization": True,
        },
        "interpretation": (
            "This handoff carries source-bound advisory experiment cells. "
            "Observational history is directional, not causal; randomized "
            "assignment support remains explicitly labeled by source evidence. "
            "The handoff grants no provider mutation or publish authority."
        ),
    }
    handoff_identity = {
        "campaign_id": campaign_id,
        "campaign_revision": campaign_revision,
        "batch_id": plan["batch_id"],
        "source_cycle_revision":
            parsed_seed["cycle_revision"],
        "seed_digest": parsed_seed["seed_digest"],
        "plan_digest": plan["plan_digest"],
        "source_evidence_set_digest": evidence_set_digest,
    }
    handoff["handoff_id"] = "gcbh1:" + sha256_json(
        handoff_identity
    )
    handoff["idempotency_key"] = (
        "gcbh1-delivery:"
        + sha256_json({
            "campaign_id": campaign_id,
            "campaign_revision": campaign_revision,
            "batch_id": plan["batch_id"],
            "plan_digest": plan["plan_digest"],
        })
    )
    digest_material = dict(handoff)
    digest_material["handoff_digest"] = ""
    handoff["handoff_digest"] = sha256_json(digest_material)
    return parse_creator_batch_experiment_handoff(handoff)


def parse_creator_batch_experiment_handoff(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "contract_version",
        "handoff_id",
        "handoff_digest",
        "idempotency_key",
        "campaign",
        "source_class",
        "allocator_revision",
        "source_seed",
        "source_evidence",
        "source_evidence_set_digest",
        "experiment_plan",
        "batch_constraints",
        "creator_cells",
        "creator_consumer",
        "authority",
        "interpretation",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise CreatorBatchHandoffError(
            "creator batch handoff fields must match v1 exactly"
        )
    if payload["contract_version"] != CREATOR_BATCH_HANDOFF_VERSION:
        raise CreatorBatchHandoffError(
            "unsupported creator batch handoff version"
        )
    campaign = payload["campaign"]
    if (
        not isinstance(campaign, Mapping)
        or set(campaign)
        != {
            "campaign_id",
            "campaign_revision",
            "batch_id",
            "batch_size",
            "source_cycle_revision",
        }
    ):
        raise CreatorBatchHandoffError(
            "campaign binding fields invalid"
        )
    _nonempty(campaign["campaign_id"], "campaign_id")
    _positive_int(
        campaign["campaign_revision"],
        "campaign_revision",
    )
    _nonempty(campaign["batch_id"], "batch_id")
    _positive_int(campaign["batch_size"], "batch_size")
    cycle_revision = _positive_int(
        campaign["source_cycle_revision"],
        "source_cycle_revision",
    )
    try:
        plan = parse_experiment_plan(payload["experiment_plan"])
    except ExperimentAllocatorError as exc:
        raise CreatorBatchHandoffError(
            "handoff contains invalid allocator plan"
        ) from exc
    parsed_seed = validate_next_cycle_seed(
        payload["source_seed"],
        expected_cycle_revision=cycle_revision,
        allow_synthetic_fixture=True,
    )
    if payload["source_class"] != plan["source_class"]:
        raise CreatorBatchHandoffError(
            "handoff source class differs from allocator plan"
        )
    if payload["source_class"] != parsed_seed["source_class"]:
        raise CreatorBatchHandoffError(
            "handoff source class differs from source seed"
        )
    if (
        campaign["batch_id"] != plan["batch_id"]
        or campaign["batch_size"] != plan["batch_size"]
    ):
        raise CreatorBatchHandoffError(
            "campaign batch binding differs from allocator plan"
        )
    if (
        plan["cycle_revision"] != cycle_revision
        or parsed_seed["cycle_revision"] != cycle_revision
    ):
        raise CreatorBatchHandoffError(
            "campaign cycle revision differs from source plan"
        )
    if (
        plan["seed"]["idempotency_key"]
        != parsed_seed["idempotency_key"]
        or plan["seed"]["seed_digest"]
        != parsed_seed["seed_digest"]
    ):
        raise CreatorBatchHandoffError(
            "source seed differs from allocator plan seed reference"
        )

    allocator_revision = payload["allocator_revision"]
    if (
        not isinstance(allocator_revision, Mapping)
        or set(allocator_revision)
        != {
            "contract_version",
            "implementation_revision",
            "plan_id",
            "plan_digest",
            "producer_commit",
            "implementation_blob_sha1",
        }
    ):
        raise CreatorBatchHandoffError(
            "allocator revision fields invalid"
        )
    if (
        allocator_revision["contract_version"]
        != ALLOCATOR_PLAN_VERSION
        or allocator_revision["implementation_revision"]
        != ALLOCATOR_IMPLEMENTATION_REVISION
        or allocator_revision["plan_id"] != plan["plan_id"]
        or allocator_revision["plan_digest"]
        != plan["plan_digest"]
        or allocator_revision["producer_commit"]
        != R13_SOURCE_COMMIT
        or allocator_revision["implementation_blob_sha1"]
        != R13_ALLOCATOR_BLOB_SHA1
    ):
        raise CreatorBatchHandoffError(
            "allocator revision binding invalid"
        )

    raw_refs = payload["source_evidence"]
    if not isinstance(raw_refs, list) or not raw_refs:
        raise CreatorBatchHandoffError(
            "source evidence must be a non-empty array"
        )
    refs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_posts: set[tuple[str, str]] = set()
    for raw in raw_refs:
        ref = _validate_source_ref(raw)
        if ref["evidence_id"] in seen_ids:
            raise CreatorBatchHandoffError(
                "duplicate source evidence id"
            )
        post_key = (ref["platform"], ref["post_id"])
        if post_key in seen_posts:
            raise CreatorBatchHandoffError(
                "duplicate source post evidence"
            )
        seen_ids.add(ref["evidence_id"])
        seen_posts.add(post_key)
        refs.append(ref)
    refs = sorted(refs, key=lambda item: item["evidence_id"])
    if refs != raw_refs:
        raise CreatorBatchHandoffError(
            "source evidence must be canonical evidence-id order"
        )
    if len(refs) != plan["historical_evidence"]["count"]:
        raise CreatorBatchHandoffError(
            "source evidence count differs from allocator plan"
        )
    set_digest = _evidence_set_digest(refs)
    _digest(
        payload["source_evidence_set_digest"],
        "source_evidence_set_digest",
    )
    if (
        set_digest != payload["source_evidence_set_digest"]
        or set_digest
        != plan["historical_evidence"]["evidence_set_digest"]
    ):
        raise CreatorBatchHandoffError(
            "source evidence set digest mismatch"
        )
    observed = sum(
        1 for ref in refs
        if ref["evidence_kind"] == "observational"
    )
    randomized = len(refs) - observed
    if (
        observed
        != plan["historical_evidence"]["observational_count"]
        or randomized
        != plan["historical_evidence"]["randomized_count"]
    ):
        raise CreatorBatchHandoffError(
            "source evidence kind counts differ from allocator plan"
        )

    constraints = payload["batch_constraints"]
    expected_constraints = _batch_constraints(plan)
    if constraints != expected_constraints:
        raise CreatorBatchHandoffError(
            "batch constraints differ from allocator plan"
        )
    if constraints["allowed_dimensions"] != list(DIMENSIONS):
        raise CreatorBatchHandoffError(
            "unsupported allowed dimensions"
        )

    cells = payload["creator_cells"]
    if not isinstance(cells, list) or not cells:
        raise CreatorBatchHandoffError(
            "creator cells must be non-empty"
        )
    raw_cell_fields = {
        "cell_id",
        "role",
        "target_count",
        "creative_dimensions",
        "evidence_state",
        "uncertainty_state",
        "experiment_signature",
    }
    for cell in cells:
        if (
            not isinstance(cell, Mapping)
            or set(cell) != raw_cell_fields
        ):
            raise CreatorBatchHandoffError(
                "creator cell fields invalid"
            )
        dimensions = cell["creative_dimensions"]
        if (
            not isinstance(dimensions, Mapping)
            or set(dimensions) != set(DIMENSIONS)
        ):
            raise CreatorBatchHandoffError(
                "creator cell dimensions invalid"
            )
        for dimension in DIMENSIONS:
            if dimensions[dimension] not in DIMENSION_VALUES[dimension]:
                raise CreatorBatchHandoffError(
                    f"unsupported creator cell {dimension}"
                )
    _single_dimension_constraints(cells)
    if cells != _creator_cells(plan):
        raise CreatorBatchHandoffError(
            "creator cells differ from allocator plan"
        )
    target_total = sum(
        cell["target_count"] for cell in cells
    )
    if target_total != campaign["batch_size"]:
        raise CreatorBatchHandoffError(
            "creator cell targets do not equal batch size"
        )

    expected_consumer = _consumer_eligibility(
        source_class=payload["source_class"],
        minimum_evidence_gate_met=
            constraints["minimum_evidence_gate"]["met"],
    )
    if payload["creator_consumer"] != expected_consumer:
        raise CreatorBatchHandoffError(
            "creator consumer eligibility is not derived correctly"
        )
    authority = payload["authority"]
    expected_authority = {
        "advisory_only": True,
        "auto_publish": False,
        "external_mutation": False,
        "provider_mutation": False,
        "publish_authorized": False,
        "release_authorized": False,
        "requires_creator_release_authorization": True,
    }
    if authority != expected_authority:
        raise CreatorBatchHandoffError(
            "creator batch handoff authority boundary invalid"
        )
    _nonempty(payload["interpretation"], "interpretation")

    expected_identity = {
        "campaign_id": campaign["campaign_id"],
        "campaign_revision": campaign["campaign_revision"],
        "batch_id": campaign["batch_id"],
        "source_cycle_revision": cycle_revision,
        "seed_digest": parsed_seed["seed_digest"],
        "plan_digest": plan["plan_digest"],
        "source_evidence_set_digest": set_digest,
    }
    expected_handoff_id = (
        "gcbh1:" + sha256_json(expected_identity)
    )
    if payload["handoff_id"] != expected_handoff_id:
        raise CreatorBatchHandoffError(
            "handoff identity mismatch"
        )
    expected_key = (
        "gcbh1-delivery:"
        + sha256_json({
            "campaign_id": campaign["campaign_id"],
            "campaign_revision":
                campaign["campaign_revision"],
            "batch_id": campaign["batch_id"],
            "plan_digest": plan["plan_digest"],
        })
    )
    if payload["idempotency_key"] != expected_key:
        raise CreatorBatchHandoffError(
            "handoff idempotency key mismatch"
        )
    _digest(payload["handoff_digest"], "handoff_digest")
    digest_material = dict(payload)
    digest_material["handoff_digest"] = ""
    if sha256_json(digest_material) != payload["handoff_digest"]:
        raise CreatorBatchHandoffError(
            "handoff digest mismatch"
        )
    return json.loads(canonical_json(dict(payload)))


def validate_creator_batch_experiment_handoff(
    payload: Mapping[str, Any],
    *,
    expected_campaign_id: str,
    expected_campaign_revision: int,
    expected_cycle_revision: int,
    allow_synthetic_fixture: bool = False,
) -> dict[str, Any]:
    _nonempty(expected_campaign_id, "expected_campaign_id")
    expected_campaign_revision = _positive_int(
        expected_campaign_revision,
        "expected_campaign_revision",
    )
    expected_cycle_revision = _positive_int(
        expected_cycle_revision,
        "expected_cycle_revision",
    )
    parsed = parse_creator_batch_experiment_handoff(payload)
    if parsed["campaign"]["campaign_id"] != expected_campaign_id:
        raise CreatorBatchHandoffError(
            "handoff belongs to wrong campaign"
        )
    actual_campaign_revision = parsed["campaign"][
        "campaign_revision"
    ]
    if actual_campaign_revision < expected_campaign_revision:
        raise StaleCreatorCampaignRevision(
            "handoff campaign revision is stale"
        )
    if actual_campaign_revision > expected_campaign_revision:
        raise FutureCreatorCampaignRevision(
            "handoff campaign revision is from the future"
        )
    actual_cycle = parsed["campaign"]["source_cycle_revision"]
    if actual_cycle < expected_cycle_revision:
        raise StaleCreatorCycleRevision(
            "handoff source cycle revision is stale"
        )
    if actual_cycle > expected_cycle_revision:
        raise FutureCreatorCycleRevision(
            "handoff source cycle revision is from the future"
        )
    if parsed["source_class"] == "synthetic_fixture":
        if parsed["creator_consumer"]["eligible"] is not False:
            raise CreatorBatchSyntheticRejected(
                "synthetic handoff cannot be Creator eligible"
            )
        if not allow_synthetic_fixture:
            raise CreatorBatchSyntheticRejected(
                "synthetic handoff is conformance-only"
            )
    elif parsed["source_class"] == "platform_export":
        if (
            parsed["batch_constraints"][
                "minimum_evidence_gate"
            ]["met"]
            and parsed["creator_consumer"]["eligible"] is not True
        ):
            raise CreatorBatchHandoffError(
                "eligible live handoff marked ineligible"
            )
    else:
        raise CreatorBatchHandoffError(
            "unsupported handoff source class"
        )
    return parsed


class CreatorBatchHandoffOutbox:
    """Append-only prepare/ack outbox for exactly-once handoff delivery."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._sequence = 0
        self._prepared: dict[str, dict[str, Any]] = {}
        self._acked: set[str] = set()
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
                raise CreatorBatchHandoffConflictError(
                    f"invalid handoff outbox JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "event_type",
                    "idempotency_key",
                    "handoff_digest",
                    "handoff",
                }
            ):
                raise CreatorBatchHandoffConflictError(
                    "handoff outbox row fields invalid"
                )
            if row["ledger_version"] != CREATOR_BATCH_OUTBOX_VERSION:
                raise CreatorBatchHandoffConflictError(
                    "unsupported handoff outbox version"
                )
            if row["sequence"] != self._sequence + 1:
                raise CreatorBatchHandoffConflictError(
                    "handoff outbox sequence not contiguous"
                )
            key = row["idempotency_key"]
            if row["event_type"] == "prepare":
                handoff = parse_creator_batch_experiment_handoff(
                    row["handoff"]
                )
                if (
                    handoff["idempotency_key"] != key
                    or handoff["handoff_digest"]
                    != row["handoff_digest"]
                ):
                    raise CreatorBatchHandoffConflictError(
                        "handoff prepare binding mismatch"
                    )
                if key in self._prepared:
                    raise CreatorBatchHandoffConflictError(
                        "duplicate durable handoff prepare"
                    )
                self._prepared[key] = handoff
            elif row["event_type"] == "ack":
                prepared = self._prepared.get(key)
                if prepared is None:
                    raise CreatorBatchHandoffConflictError(
                        "handoff ack without prepare"
                    )
                if row["handoff"] is not None:
                    raise CreatorBatchHandoffConflictError(
                        "handoff ack cannot contain payload"
                    )
                if (
                    prepared["handoff_digest"]
                    != row["handoff_digest"]
                ):
                    raise CreatorBatchHandoffConflictError(
                        "handoff ack digest mismatch"
                    )
                if key in self._acked:
                    raise CreatorBatchHandoffConflictError(
                        "duplicate durable handoff ack"
                    )
                self._acked.add(key)
            else:
                raise CreatorBatchHandoffConflictError(
                    "unknown handoff outbox event type"
                )
            self._sequence += 1

    def _append(self, row: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._sequence += 1

    def prepare(
        self,
        handoff: Mapping[str, Any],
    ) -> str:
        parsed = parse_creator_batch_experiment_handoff(handoff)
        key = parsed["idempotency_key"]
        existing = self._prepared.get(key)
        if existing is not None:
            if (
                existing["handoff_digest"]
                != parsed["handoff_digest"]
            ):
                raise CreatorBatchHandoffConflictError(
                    "handoff identity changed payload bytes"
                )
            return "duplicate"
        row = {
            "ledger_version": CREATOR_BATCH_OUTBOX_VERSION,
            "sequence": self._sequence + 1,
            "event_type": "prepare",
            "idempotency_key": key,
            "handoff_digest": parsed["handoff_digest"],
            "handoff": parsed,
        }
        self._append(row)
        self._prepared[key] = parsed
        return "prepared"

    def acknowledge(
        self,
        idempotency_key: str,
        handoff_digest: str,
    ) -> str:
        prepared = self._prepared.get(idempotency_key)
        if prepared is None:
            raise CreatorBatchHandoffConflictError(
                "cannot acknowledge unknown handoff"
            )
        if prepared["handoff_digest"] != handoff_digest:
            raise CreatorBatchHandoffConflictError(
                "handoff acknowledgement digest mismatch"
            )
        if idempotency_key in self._acked:
            return "duplicate"
        row = {
            "ledger_version": CREATOR_BATCH_OUTBOX_VERSION,
            "sequence": self._sequence + 1,
            "event_type": "ack",
            "idempotency_key": idempotency_key,
            "handoff_digest": handoff_digest,
            "handoff": None,
        }
        self._append(row)
        self._acked.add(idempotency_key)
        return "acknowledged"

    def pending(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            json.loads(canonical_json(self._prepared[key]))
            for key in sorted(self._prepared)
            if key not in self._acked
        )

    @property
    def logical_handoff_count(self) -> int:
        return len(self._prepared)


class ReferenceCreatorBatchHandoffConsumer:
    """Growth-owned reference validator/consumer; no Creator repo mutation."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows: dict[str, dict[str, Any]] = {}
        self._sequence = 0
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
                raise CreatorBatchHandoffConflictError(
                    f"invalid consumer ledger JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "idempotency_key",
                    "handoff_digest",
                    "campaign_id",
                    "campaign_revision",
                    "source_cycle_revision",
                }
            ):
                raise CreatorBatchHandoffConflictError(
                    "consumer ledger row fields invalid"
                )
            if (
                row["ledger_version"]
                != CREATOR_BATCH_CONSUMER_LEDGER_VERSION
            ):
                raise CreatorBatchHandoffConflictError(
                    "unsupported consumer ledger version"
                )
            if row["sequence"] != self._sequence + 1:
                raise CreatorBatchHandoffConflictError(
                    "consumer ledger sequence not contiguous"
                )
            key = row["idempotency_key"]
            if key in self._rows:
                raise CreatorBatchHandoffConflictError(
                    "duplicate durable consumer acceptance"
                )
            self._rows[key] = dict(row)
            self._sequence += 1

    def accept(
        self,
        handoff: Mapping[str, Any],
        *,
        expected_campaign_id: str,
        expected_campaign_revision: int,
        expected_cycle_revision: int,
        allow_synthetic_fixture: bool = False,
    ) -> str:
        parsed = validate_creator_batch_experiment_handoff(
            handoff,
            expected_campaign_id=expected_campaign_id,
            expected_campaign_revision=
                expected_campaign_revision,
            expected_cycle_revision=expected_cycle_revision,
            allow_synthetic_fixture=allow_synthetic_fixture,
        )
        key = parsed["idempotency_key"]
        existing = self._rows.get(key)
        if existing is not None:
            if (
                existing["handoff_digest"]
                != parsed["handoff_digest"]
            ):
                raise CreatorBatchHandoffConflictError(
                    "consumer handoff identity changed bytes"
                )
            return "duplicate"
        row = {
            "ledger_version":
                CREATOR_BATCH_CONSUMER_LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "idempotency_key": key,
            "handoff_digest": parsed["handoff_digest"],
            "campaign_id":
                parsed["campaign"]["campaign_id"],
            "campaign_revision":
                parsed["campaign"]["campaign_revision"],
            "source_cycle_revision":
                parsed["campaign"]["source_cycle_revision"],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._rows[key] = row
        self._sequence += 1
        return "accepted"

    @property
    def logical_acceptance_count(self) -> int:
        return len(self._rows)


def deliver_creator_batch_handoff(
    *,
    outbox: CreatorBatchHandoffOutbox,
    consumer: ReferenceCreatorBatchHandoffConsumer,
    handoff: Mapping[str, Any],
    expected_campaign_id: str,
    expected_campaign_revision: int,
    expected_cycle_revision: int,
    allow_synthetic_fixture: bool = False,
    inject_fault: str | None = None,
) -> dict[str, str]:
    parsed = parse_creator_batch_experiment_handoff(handoff)
    prepare_status = outbox.prepare(parsed)
    consumer_status = consumer.accept(
        parsed,
        expected_campaign_id=expected_campaign_id,
        expected_campaign_revision=expected_campaign_revision,
        expected_cycle_revision=expected_cycle_revision,
        allow_synthetic_fixture=allow_synthetic_fixture,
    )
    if inject_fault == "after_consumer_accept":
        raise InjectedCreatorBatchDeliveryFault(
            "fault after durable Creator-side acceptance"
        )
    ack_status = outbox.acknowledge(
        parsed["idempotency_key"],
        parsed["handoff_digest"],
    )
    return {
        "prepare_status": prepare_status,
        "consumer_status": consumer_status,
        "ack_status": ack_status,
    }
