from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import (
    OPTIONAL_METRICS,
    canonical_json,
    parse_metric_snapshot,
    parse_publish_result,
    sha256_json,
)
from .candidate_decision import (
    CANDIDATE_DECISION_VERSION,
    CandidateDecisionError,
    parse_candidate_decision,
)
from .event_stream import parse_timestamp


POST_PUBLISH_LEARNING_VERSION = "growth.post_publish_learning.v1"
POST_PUBLISH_BRIEF_SEED_VERSION = "growth.post_publish_brief_seed.v1"
POST_PUBLISH_LEARNING_LEDGER_VERSION = (
    "growth.post_publish_learning_ledger.v1"
)
POST_PUBLISH_LEARNING_REPLAY_VERSION = (
    "growth.post_publish_learning_replay.r19.v1"
)

_SHA256 = set("0123456789abcdef")


class PostPublishLearningError(ValueError):
    pass


class PostPublishLearningConflictError(
    PostPublishLearningError
):
    pass


class PostPublishLearningOutOfOrder(
    PostPublishLearningError
):
    pass


class PostPublishLearningSyntheticLiveRejected(
    PostPublishLearningError
):
    pass


@dataclass(frozen=True)
class PostPublishLearningPolicy:
    min_views: int = 100
    stale_after_seconds: int = 21600
    max_hypotheses: int = 4

    def __post_init__(self) -> None:
        for value, field in (
            (self.min_views, "min_views"),
            (
                self.stale_after_seconds,
                "stale_after_seconds",
            ),
            (self.max_hypotheses, "max_hypotheses"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise PostPublishLearningError(
                    f"{field} must be positive integer"
                )
        if self.max_hypotheses > 6:
            raise PostPublishLearningError(
                "max_hypotheses must be bounded <= 6"
            )


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PostPublishLearningError(
            f"{field} must be non-empty string"
        )
    return value


def _digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in _SHA256 for ch in value)
    ):
        raise PostPublishLearningError(
            f"{field} must be lowercase SHA-256"
        )
    return value


def _positive_int(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise PostPublishLearningError(
            f"{field} must be integer >= 1"
        )
    return value


def _runtime_status(
    raw: Mapping[str, Any] | None,
    *,
    publish: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if raw is None:
        return {
            "state": "unavailable",
            "freshness": "unknown",
            "collector_state": "unknown",
            "lag_seconds": None,
            "error_classification": None,
            "backfill_recovery_state": "unknown",
            "unavailable_evidence": {
                "runtime_status":
                    "R17 runtime status was not supplied"
            },
        }
    if not isinstance(raw, Mapping):
        raise PostPublishLearningError(
            "runtime_status must be object or null"
        )
    required = {
        "platform",
        "post_id",
        "cycle_revision",
        "collector_state",
        "freshness",
        "lag_seconds",
        "last_success_at",
        "last_error",
        "error_classification",
        "backoff_until",
        "backfill_recovery_state",
        "latest_snapshot_digest",
        "latest_provider_revision",
        "unavailable_evidence",
        "lineage",
    }
    if set(raw) != required:
        raise PostPublishLearningError(
            "runtime_status fields do not match R17"
        )
    if (
        raw["platform"] != publish["platform"]
        or raw["post_id"] != publish["post_id"]
        or raw["cycle_revision"]
        != publish["cycle_revision"]
    ):
        raise PostPublishLearningError(
            "runtime status publish identity mismatch"
        )
    lineage = raw["lineage"]
    if (
        not isinstance(lineage, Mapping)
        or lineage.get("publish_result_id")
        != publish["publish_result_id"]
        or lineage.get("publish_result_digest")
        != publish["publish_result_digest"]
        or lineage.get("provider_receipt_digest")
        != publish["provenance"][
            "provider_receipt_digest"
        ]
        or lineage.get("media_render_sha256")
        != publish["artifact"][
            "media_artifact_digest"
        ]
        or lineage.get(
            "live_performance_claim_allowed"
        )
        != publish["provenance"][
            "live_performance_claim_allowed"
        ]
    ):
        raise PostPublishLearningError(
            "runtime status lineage mismatch"
        )
    if snapshot is not None:
        if (
            raw["latest_snapshot_digest"]
            != snapshot["snapshot_digest"]
        ):
            raise PostPublishLearningError(
                "runtime status does not bind metric snapshot"
            )
    lag = raw["lag_seconds"]
    if (
        isinstance(lag, bool)
        or not isinstance(lag, int)
        or lag < 0
    ):
        raise PostPublishLearningError(
            "runtime lag_seconds invalid"
        )
    unavailable = raw["unavailable_evidence"]
    if not isinstance(unavailable, Mapping):
        raise PostPublishLearningError(
            "runtime unavailable_evidence invalid"
        )
    return {
        "state": "available",
        "freshness": raw["freshness"],
        "collector_state": raw["collector_state"],
        "lag_seconds": lag,
        "error_classification":
            raw["error_classification"],
        "backfill_recovery_state":
            raw["backfill_recovery_state"],
        "unavailable_evidence":
            json.loads(canonical_json(
                dict(unavailable)
            )),
    }


def _bind_snapshot(
    snapshot_raw: Mapping[str, Any] | None,
    *,
    publish: Mapping[str, Any],
) -> dict[str, Any] | None:
    if snapshot_raw is None:
        return None
    try:
        snapshot = parse_metric_snapshot(
            snapshot_raw
        )
    except ValueError as exc:
        raise PostPublishLearningError(
            "invalid metric snapshot"
        ) from exc
    for field in (
        "publish_result_id",
        "publish_result_digest",
        "platform",
        "account_id",
        "post_id",
        "cycle_revision",
        "source_class",
    ):
        expected = (
            publish["source_class"]
            if field == "source_class"
            else publish[field]
        )
        if snapshot[field] != expected:
            raise PostPublishLearningError(
                f"metric snapshot {field} mismatch"
            )
    if (
        snapshot["live_performance_claim_allowed"]
        != publish["provenance"][
            "live_performance_claim_allowed"
        ]
    ):
        raise PostPublishLearningError(
            "metric snapshot live scope mismatch"
        )
    if (
        parse_timestamp(
            snapshot["window"]["start"]
        )
        < parse_timestamp(
            publish["published_at"]
        )
    ):
        raise PostPublishLearningError(
            "metric observation begins before publication"
        )
    return snapshot


def _separate_metrics(
    snapshot: Mapping[str, Any] | None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, str],
]:
    observed = {
        name: None
        for name in sorted(
            OPTIONAL_METRICS
        )
    }
    derived = {
        "average_watch_duration_seconds": None,
        "completion_rate": None,
        "retention_auc": None,
        "link_ctr": None,
    }
    unavailable: dict[str, str] = {}
    if snapshot is None:
        for name in observed:
            unavailable[
                f"observed_provider_metrics.{name}"
            ] = "metric_snapshot_unavailable"
        for name in derived:
            unavailable[
                f"derived_analytics.{name}"
            ] = "metric_snapshot_unavailable"
        return observed, derived, unavailable

    raw = snapshot["raw_metrics"]
    for name in observed:
        observed[name] = raw.get(name)
        if observed[name] is None:
            unavailable[
                f"observed_provider_metrics.{name}"
            ] = "provider_metric_unavailable"

    normalized = snapshot["normalized_metrics"]
    sources = snapshot[
        "normalization_sources"
    ]
    for name in derived:
        source = sources.get(
            name,
            "unavailable",
        )
        if (
            source == "provider_export"
            and name
            in {
                "average_watch_duration_seconds",
                "completion_rate",
            }
        ):
            derived[name] = {
                "value": normalized[name],
                "derivation": "provider_reported_normalized",
                "source": source,
            }
        elif source != "unavailable":
            derived[name] = {
                "value": normalized[name],
                "derivation": source,
                "source": source,
            }
        else:
            unavailable[
                f"derived_analytics.{name}"
            ] = "not_derivable_from_available_metrics"
    return observed, derived, unavailable


def _hypotheses(
    *,
    publish: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None,
    observed: Mapping[str, Any],
    derived: Mapping[str, Any],
    runtime: Mapping[str, Any],
    policy: PostPublishLearningPolicy,
) -> tuple[list[dict[str, Any]], list[str]]:
    blockers: list[str] = []
    if runtime["state"] == "available":
        if runtime["collector_state"] in {
            "broken_or_terminal",
            "backoff",
        }:
            blockers.append(
                "collector_not_healthy"
            )
        if runtime["freshness"] != "fresh":
            blockers.append(
                "metric_snapshot_not_fresh"
            )
    if snapshot is None:
        blockers.append(
            "metric_snapshot_unavailable"
        )
        return [{
            "hypothesis_id": "collect_more_evidence",
            "priority": 1,
            "testable_change":
                "Collect a complete, lineage-matched metric snapshot before changing the creative.",
            "target_metric":
                "views",
            "expected_direction": "unknown",
            "evidence_refs": [
                "unavailable_evidence.metric_snapshot"
            ],
            "certainty": "insufficient",
            "causal_claim": False,
        }], blockers

    views = observed.get("views")
    if views is None or views < policy.min_views:
        blockers.append(
            "minimum_views_not_met"
        )
        return [{
            "hypothesis_id": "collect_more_evidence",
            "priority": 1,
            "testable_change":
                "Hold the creative constant and collect a larger observation window before prioritizing an edit hypothesis.",
            "target_metric": "views",
            "expected_direction": "increase_evidence_volume",
            "evidence_refs": [
                (
                    "metric_snapshot:"
                    + snapshot["snapshot_digest"]
                    + "#raw_metrics.views"
                )
            ],
            "certainty": "insufficient",
            "causal_claim": False,
        }], blockers

    candidates: list[dict[str, Any]] = []
    duration = publish["artifact"][
        "media_duration_seconds"
    ]
    avg = derived[
        "average_watch_duration_seconds"
    ]
    if (
        isinstance(avg, Mapping)
        and avg["value"] is not None
        and duration > 0
        and avg["value"] / duration < 0.35
    ):
        candidates.append({
            "hypothesis_id":
                "test_stronger_first_3s_pacing",
            "priority": 1,
            "testable_change":
                "In the next batch, test a faster first-3-second hook while holding CTA and duration bucket constant.",
            "target_metric":
                "average_watch_duration_seconds",
            "expected_direction": "increase",
            "evidence_refs": [
                (
                    "metric_snapshot:"
                    + snapshot["snapshot_digest"]
                    + "#normalized_metrics.average_watch_duration_seconds"
                )
            ],
            "certainty":
                "directional_observational_not_causal",
            "causal_claim": False,
        })
    completion = derived[
        "completion_rate"
    ]
    if (
        isinstance(completion, Mapping)
        and completion["value"] is not None
        and completion["value"] < 0.25
    ):
        candidates.append({
            "hypothesis_id":
                "test_tighter_payoff_loop",
            "priority": 2,
            "testable_change":
                "Test a shorter payoff-to-loop ending while holding the opening hook and CTA constant.",
            "target_metric": "completion_rate",
            "expected_direction": "increase",
            "evidence_refs": [
                (
                    "metric_snapshot:"
                    + snapshot["snapshot_digest"]
                    + "#normalized_metrics.completion_rate"
                )
            ],
            "certainty":
                "directional_observational_not_causal",
            "causal_claim": False,
        })
    shares = observed.get("shares")
    if shares is not None and views > 0:
        share_rate = shares / views
        if share_rate >= 0.02:
            candidates.append({
                "hypothesis_id":
                    "preserve_shareable_hook_test_one_variable",
                "priority": 3,
                "testable_change":
                    "Preserve the current hook and vary only one downstream edit dimension to test whether the share signal persists.",
                "target_metric": "shares",
                "expected_direction":
                    "preserve_or_increase",
                "evidence_refs": [
                    (
                        "metric_snapshot:"
                        + snapshot["snapshot_digest"]
                        + "#raw_metrics.shares"
                    )
                ],
                "certainty":
                    "directional_observational_not_causal",
                "causal_claim": False,
            })
    follows = observed.get("follows")
    if (
        follows is not None
        and views > 0
        and follows / views < 0.005
    ):
        candidates.append({
            "hypothesis_id":
                "test_follow_cta",
            "priority": 4,
            "testable_change":
                "Test one explicit follow CTA variant while holding hook, duration, and edit style constant.",
            "target_metric": "follows",
            "expected_direction": "increase",
            "evidence_refs": [
                (
                    "metric_snapshot:"
                    + snapshot["snapshot_digest"]
                    + "#raw_metrics.follows"
                )
            ],
            "certainty":
                "directional_observational_not_causal",
            "causal_claim": False,
        })
    if not candidates:
        candidates.append({
            "hypothesis_id":
                "single_variable_preservation_test",
            "priority": 1,
            "testable_change":
                "Preserve the current structure and test exactly one creative dimension in the next batch.",
            "target_metric": "views",
            "expected_direction": "observe",
            "evidence_refs": [
                "metric_snapshot:"
                + snapshot["snapshot_digest"]
            ],
            "certainty":
                "directional_observational_not_causal",
            "causal_claim": False,
        })
    return candidates[
        : policy.max_hypotheses
    ], blockers


def _decision_link(
    raw: Mapping[str, Any] | None,
    *,
    publish: Mapping[str, Any],
) -> tuple[
    dict[str, Any] | None,
    dict[str, str],
]:
    if raw is None:
        return None, {
            "candidate_decision":
                "R18 candidate decision not supplied"
        }
    try:
        decision = parse_candidate_decision(
            raw
        )
    except CandidateDecisionError as exc:
        raise PostPublishLearningError(
            "invalid R18 candidate decision"
        ) from exc
    matching = [
        ref
        for ref in decision["candidate_refs"]
        if ref["render_sha256"]
        == publish["artifact"][
            "media_artifact_digest"
        ]
    ]
    if (
        decision["cycle_revision"]
        != publish["cycle_revision"]
    ):
        return None, {
            "candidate_decision":
                "R18 decision cycle revision does not match publish receipt"
        }
    if len(matching) != 1:
        return None, {
            "candidate_decision":
                "R18 decision does not uniquely reference published render SHA"
        }
    ref = matching[0]
    return {
        "contract_version":
            CANDIDATE_DECISION_VERSION,
        "decision_id": decision["decision_id"],
        "decision_digest":
            decision["decision_digest"],
        "decision_revision":
            decision["decision_revision"],
        "decision": decision["decision"],
        "winner_candidate_id":
            decision["winner_candidate_id"],
        "published_candidate_id":
            ref["candidate_id"],
        "published_candidate_was_winner":
            (
                decision["decision"] == "winner"
                and decision[
                    "winner_candidate_id"
                ] == ref["candidate_id"]
            ),
        "reedit_guidance": [
            item
            for item in decision[
                "reedit_guidance"
            ]
            if item["candidate_id"]
            == ref["candidate_id"]
        ],
        "human_preference_inferred": False,
    }, {}


def build_post_publish_learning(
    *,
    publish_result: Mapping[str, Any],
    metric_snapshot: Mapping[str, Any] | None,
    runtime_status: Mapping[str, Any] | None,
    learning_revision: int,
    next_cycle_id: str,
    candidate_decision: Mapping[str, Any] | None = None,
    policy: PostPublishLearningPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or PostPublishLearningPolicy()
    try:
        publish = parse_publish_result(
            publish_result
        )
    except ValueError as exc:
        raise PostPublishLearningError(
            "invalid publish result"
        ) from exc
    learning_revision = _positive_int(
        learning_revision,
        "learning_revision",
    )
    _nonempty(next_cycle_id, "next_cycle_id")
    snapshot = _bind_snapshot(
        metric_snapshot,
        publish=publish,
    )
    if (
        publish["source_class"]
        == "synthetic_fixture"
        and snapshot is not None
        and snapshot[
            "live_performance_claim_allowed"
        ] is True
    ):
        raise PostPublishLearningSyntheticLiveRejected(
            "synthetic evidence cannot claim live performance"
        )
    runtime = _runtime_status(
        runtime_status,
        publish=publish,
        snapshot=snapshot,
    )
    observed, derived, unavailable = (
        _separate_metrics(snapshot)
    )
    if snapshot is None:
        unavailable["metric_snapshot"] = (
            "No complete metric snapshot is available yet"
        )
    if runtime["state"] == "available":
        if runtime["error_classification"]:
            unavailable["collector"] = (
                "R17 collector reported "
                + str(
                    runtime[
                        "error_classification"
                    ]
                )
            )
        if runtime["freshness"] != "fresh":
            unavailable["freshness"] = (
                "R17 collector freshness is "
                + str(runtime["freshness"])
            )
    decision_ref, decision_unavailable = (
        _decision_link(
            candidate_decision,
            publish=publish,
        )
    )
    unavailable.update(
        decision_unavailable
    )
    hypotheses, blockers = _hypotheses(
        publish=publish,
        snapshot=snapshot,
        observed=observed,
        derived=derived,
        runtime=runtime,
        policy=policy,
    )
    observation_window = (
        None
        if snapshot is None
        else dict(snapshot["window"])
    )
    source_class = publish["source_class"]
    live = (
        source_class == "platform_export"
        and snapshot is not None
        and snapshot["source_class"]
        == "platform_export"
        and publish["provenance"][
            "live_performance_claim_allowed"
        ] is True
        and snapshot[
            "live_performance_claim_allowed"
        ] is True
    )
    if source_class == "synthetic_fixture":
        live = False
    material = {
        "contract_version":
            POST_PUBLISH_LEARNING_VERSION,
        "learning_id": "",
        "learning_digest": "",
        "learning_revision":
            learning_revision,
        "source_class": source_class,
        "live_performance_claim_allowed":
            live,
        "next_cycle_id": next_cycle_id,
        "lineage": {
            "publish_result_id":
                publish["publish_result_id"],
            "publish_result_digest":
                publish["publish_result_digest"],
            "platform": publish["platform"],
            "account_id":
                publish["account_id"],
            "post_id": publish["post_id"],
            "cycle_revision":
                publish["cycle_revision"],
            "provider_receipt_digest":
                publish["provenance"][
                    "provider_receipt_digest"
                ],
            "media_artifact_id":
                publish["artifact"][
                    "media_artifact_id"
                ],
            "media_render_sha256":
                publish["artifact"][
                    "media_artifact_digest"
                ],
            "metric_snapshot_digest": (
                None
                if snapshot is None
                else snapshot[
                    "snapshot_digest"
                ]
            ),
            "selected_metrics_event_id": (
                None
                if snapshot is None
                else snapshot[
                    "selected_metrics_event_id"
                ]
            ),
            "observation_window":
                observation_window,
        },
        "observed_provider_metrics":
            observed,
        "derived_analytics": derived,
        "speculative_hypotheses":
            hypotheses,
        "runtime_state": runtime,
        "candidate_decision":
            decision_ref,
        "unavailable_evidence":
            unavailable,
        "evidence_state": (
            "insufficient_or_delayed"
            if blockers
            else "directional_observational"
        ),
        "evidence_blockers": blockers,
        "causality": {
            "historical_performance_may_prioritize_hypotheses":
                True,
            "historical_performance_proves_causality":
                False,
            "retroactive_human_preference_inference":
                False,
            "hypotheses_are_speculative":
                True,
        },
        "authority": {
            "advisory_only": True,
            "provider_mutation": False,
            "publish_authorized": False,
            "creator_mutation": False,
            "media_mutation": False,
            "release_authorized": False,
        },
        "interpretation": (
            "Observed provider metrics, derived analytics, and speculative "
            "next-cycle hypotheses are separate evidence layers. Historical "
            "performance is directional and cannot establish causal lift or "
            "retroactively create human preference labels."
        ),
    }
    material["learning_id"] = (
        "gppl1:"
        + sha256_json({
            "publish_result_digest":
                publish[
                    "publish_result_digest"
                ],
            "metric_snapshot_digest":
                material["lineage"][
                    "metric_snapshot_digest"
                ],
            "learning_revision":
                learning_revision,
            "next_cycle_id":
                next_cycle_id,
        })
    )
    digest_material = dict(material)
    digest_material["learning_digest"] = ""
    material["learning_digest"] = (
        sha256_json(digest_material)
    )
    return parse_post_publish_learning(
        material
    )


def parse_post_publish_learning(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "contract_version",
        "learning_id",
        "learning_digest",
        "learning_revision",
        "source_class",
        "live_performance_claim_allowed",
        "next_cycle_id",
        "lineage",
        "observed_provider_metrics",
        "derived_analytics",
        "speculative_hypotheses",
        "runtime_state",
        "candidate_decision",
        "unavailable_evidence",
        "evidence_state",
        "evidence_blockers",
        "causality",
        "authority",
        "interpretation",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != required
    ):
        raise PostPublishLearningError(
            "post-publish learning fields must match v1 exactly"
        )
    if (
        payload["contract_version"]
        != POST_PUBLISH_LEARNING_VERSION
    ):
        raise PostPublishLearningError(
            "unsupported post-publish learning version"
        )
    _positive_int(
        payload["learning_revision"],
        "learning_revision",
    )
    _nonempty(
        payload["next_cycle_id"],
        "next_cycle_id",
    )
    if payload["source_class"] not in {
        "platform_export",
        "synthetic_fixture",
    }:
        raise PostPublishLearningError(
            "unsupported source_class"
        )
    if (
        payload["source_class"]
        == "synthetic_fixture"
        and payload[
            "live_performance_claim_allowed"
        ] is not False
    ):
        raise PostPublishLearningSyntheticLiveRejected(
            "synthetic learning cannot be live"
        )
    lineage = payload["lineage"]
    if (
        not isinstance(lineage, Mapping)
        or set(lineage)
        != {
            "publish_result_id",
            "publish_result_digest",
            "platform",
            "account_id",
            "post_id",
            "cycle_revision",
            "provider_receipt_digest",
            "media_artifact_id",
            "media_render_sha256",
            "metric_snapshot_digest",
            "selected_metrics_event_id",
            "observation_window",
        }
    ):
        raise PostPublishLearningError(
            "learning lineage fields invalid"
        )
    _digest(
        lineage["publish_result_digest"],
        "publish_result_digest",
    )
    _digest(
        lineage["media_render_sha256"],
        "media_render_sha256",
    )
    if (
        lineage[
            "metric_snapshot_digest"
        ]
        is not None
    ):
        _digest(
            lineage[
                "metric_snapshot_digest"
            ],
            "metric_snapshot_digest",
        )
        window = lineage[
            "observation_window"
        ]
        if (
            not isinstance(window, Mapping)
            or set(window)
            != {"start", "end"}
            or parse_timestamp(
                window["start"]
            )
            >= parse_timestamp(
                window["end"]
            )
        ):
            raise PostPublishLearningError(
                "observation window invalid"
            )
    if not isinstance(
        payload[
            "observed_provider_metrics"
        ],
        Mapping,
    ):
        raise PostPublishLearningError(
            "observed_provider_metrics must be object"
        )
    if set(
        payload[
            "observed_provider_metrics"
        ]
    ) != set(OPTIONAL_METRICS):
        raise PostPublishLearningError(
            "observed provider metric set invalid"
        )
    if not isinstance(
        payload["derived_analytics"],
        Mapping,
    ):
        raise PostPublishLearningError(
            "derived_analytics must be object"
        )
    if set(
        payload["derived_analytics"]
    ) != {
        "average_watch_duration_seconds",
        "completion_rate",
        "retention_auc",
        "link_ctr",
    }:
        raise PostPublishLearningError(
            "derived analytics set invalid"
        )
    hypotheses = payload[
        "speculative_hypotheses"
    ]
    if (
        not isinstance(hypotheses, list)
        or not hypotheses
        or len(hypotheses) > 6
    ):
        raise PostPublishLearningError(
            "bounded hypotheses required"
        )
    for item in hypotheses:
        if (
            not isinstance(item, Mapping)
            or set(item)
            != {
                "hypothesis_id",
                "priority",
                "testable_change",
                "target_metric",
                "expected_direction",
                "evidence_refs",
                "certainty",
                "causal_claim",
            }
        ):
            raise PostPublishLearningError(
                "hypothesis fields invalid"
            )
        _nonempty(
            item["hypothesis_id"],
            "hypothesis_id",
        )
        _positive_int(
            item["priority"],
            "hypothesis.priority",
        )
        _nonempty(
            item["testable_change"],
            "hypothesis.testable_change",
        )
        _nonempty(
            item["target_metric"],
            "hypothesis.target_metric",
        )
        if item["causal_claim"] is not False:
            raise PostPublishLearningError(
                "hypothesis cannot claim causality"
            )
    decision = payload[
        "candidate_decision"
    ]
    if decision is not None:
        if (
            not isinstance(decision, Mapping)
            or decision.get(
                "human_preference_inferred"
            ) is not False
        ):
            raise PostPublishLearningError(
                "candidate decision link invalid"
            )
    causality = payload["causality"]
    if causality != {
        "historical_performance_may_prioritize_hypotheses":
            True,
        "historical_performance_proves_causality":
            False,
        "retroactive_human_preference_inference":
            False,
        "hypotheses_are_speculative":
            True,
    }:
        raise PostPublishLearningError(
            "causality boundary invalid"
        )
    authority = payload["authority"]
    if authority != {
        "advisory_only": True,
        "provider_mutation": False,
        "publish_authorized": False,
        "creator_mutation": False,
        "media_mutation": False,
        "release_authorized": False,
    }:
        raise PostPublishLearningError(
            "authority boundary invalid"
        )
    _digest(
        payload["learning_digest"],
        "learning_digest",
    )
    expected_id = (
        "gppl1:"
        + sha256_json({
            "publish_result_digest":
                lineage[
                    "publish_result_digest"
                ],
            "metric_snapshot_digest":
                lineage[
                    "metric_snapshot_digest"
                ],
            "learning_revision":
                payload[
                    "learning_revision"
                ],
            "next_cycle_id":
                payload["next_cycle_id"],
        })
    )
    if payload["learning_id"] != expected_id:
        raise PostPublishLearningError(
            "learning identity mismatch"
        )
    material = dict(payload)
    material["learning_digest"] = ""
    if (
        sha256_json(material)
        != payload["learning_digest"]
    ):
        raise PostPublishLearningError(
            "learning digest mismatch"
        )
    return json.loads(
        canonical_json(dict(payload))
    )


def build_post_publish_brief_seed(
    learning: Mapping[str, Any],
) -> dict[str, Any]:
    parsed = parse_post_publish_learning(
        learning
    )
    seed = {
        "contract_version":
            POST_PUBLISH_BRIEF_SEED_VERSION,
        "seed_id": "",
        "seed_digest": "",
        "next_cycle_id":
            parsed["next_cycle_id"],
        "source_class":
            parsed["source_class"],
        "creator_cycle_eligible": (
            parsed["source_class"]
            == "platform_export"
            and parsed[
                "live_performance_claim_allowed"
            ] is True
            and parsed["evidence_state"]
            == "directional_observational"
        ),
        "learning_ref": {
            "learning_id":
                parsed["learning_id"],
            "learning_digest":
                parsed["learning_digest"],
            "learning_revision":
                parsed["learning_revision"],
            "publish_result_digest":
                parsed["lineage"][
                    "publish_result_digest"
                ],
            "media_render_sha256":
                parsed["lineage"][
                    "media_render_sha256"
                ],
            "metric_snapshot_digest":
                parsed["lineage"][
                    "metric_snapshot_digest"
                ],
        },
        "brief_guidance": [
            {
                "hypothesis_id":
                    item["hypothesis_id"],
                "testable_change":
                    item["testable_change"],
                "target_metric":
                    item["target_metric"],
                "expected_direction":
                    item[
                        "expected_direction"
                    ],
                "certainty":
                    item["certainty"],
                "hold_constant":
                    "All other creative dimensions not named by the hypothesis.",
            }
            for item in parsed[
                "speculative_hypotheses"
            ]
        ],
        "unavailable_evidence":
            parsed["unavailable_evidence"],
        "authority": {
            "advisory_only": True,
            "auto_publish": False,
            "external_mutation": False,
            "release_authorized": False,
        },
        "interpretation": (
            "Brief guidance is a bounded set of testable hypotheses from "
            "post-publish observations. It is not a causal conclusion or "
            "human-preference label."
        ),
    }
    seed["seed_id"] = (
        "gppbs1:"
        + sha256_json({
            "next_cycle_id":
                seed["next_cycle_id"],
            "learning_digest":
                parsed["learning_digest"],
        })
    )
    digest_material = dict(seed)
    digest_material["seed_digest"] = ""
    seed["seed_digest"] = (
        sha256_json(digest_material)
    )
    return parse_post_publish_brief_seed(
        seed
    )


def parse_post_publish_brief_seed(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "contract_version",
        "seed_id",
        "seed_digest",
        "next_cycle_id",
        "source_class",
        "creator_cycle_eligible",
        "learning_ref",
        "brief_guidance",
        "unavailable_evidence",
        "authority",
        "interpretation",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != required
    ):
        raise PostPublishLearningError(
            "brief seed fields must match v1 exactly"
        )
    if (
        payload["contract_version"]
        != POST_PUBLISH_BRIEF_SEED_VERSION
    ):
        raise PostPublishLearningError(
            "unsupported brief seed version"
        )
    if (
        payload["source_class"]
        == "synthetic_fixture"
        and payload[
            "creator_cycle_eligible"
        ] is not False
    ):
        raise PostPublishLearningSyntheticLiveRejected(
            "synthetic brief seed cannot be Creator-cycle eligible"
        )
    ref = payload["learning_ref"]
    if not isinstance(ref, Mapping):
        raise PostPublishLearningError(
            "learning_ref missing"
        )
    _digest(
        ref["learning_digest"],
        "learning_ref.learning_digest",
    )
    if (
        not isinstance(
            payload["brief_guidance"],
            list,
        )
        or not payload[
            "brief_guidance"
        ]
    ):
        raise PostPublishLearningError(
            "brief guidance must be non-empty"
        )
    if payload["authority"] != {
        "advisory_only": True,
        "auto_publish": False,
        "external_mutation": False,
        "release_authorized": False,
    }:
        raise PostPublishLearningError(
            "brief seed authority invalid"
        )
    _digest(
        payload["seed_digest"],
        "seed_digest",
    )
    expected_id = (
        "gppbs1:"
        + sha256_json({
            "next_cycle_id":
                payload["next_cycle_id"],
            "learning_digest":
                ref["learning_digest"],
        })
    )
    if payload["seed_id"] != expected_id:
        raise PostPublishLearningError(
            "brief seed identity mismatch"
        )
    material = dict(payload)
    material["seed_digest"] = ""
    if (
        sha256_json(material)
        != payload["seed_digest"]
    ):
        raise PostPublishLearningError(
            "brief seed digest mismatch"
        )
    return json.loads(
        canonical_json(dict(payload))
    )


class PostPublishLearningLedger:
    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path)
        self._rows: list[
            dict[str, Any]
        ] = []
        self._latest: dict[
            str,
            dict[str, Any],
        ] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_number, line in enumerate(
            self.path.read_text(
                encoding="utf-8"
            ).splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PostPublishLearningConflictError(
                    "invalid learning ledger JSON "
                    f"line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "learning",
                }
                or row["ledger_version"]
                != POST_PUBLISH_LEARNING_LEDGER_VERSION
                or row["sequence"]
                != len(self._rows) + 1
            ):
                raise PostPublishLearningConflictError(
                    "learning ledger row invalid"
                )
            learning = (
                parse_post_publish_learning(
                    row["learning"]
                )
            )
            key = learning["lineage"][
                "publish_result_id"
            ]
            previous = self._latest.get(
                key
            )
            if (
                previous is not None
                and learning[
                    "learning_revision"
                ]
                <= previous[
                    "learning_revision"
                ]
            ):
                raise PostPublishLearningConflictError(
                    "learning revisions are not strictly increasing"
                )
            if previous is not None:
                previous_window = previous[
                    "lineage"
                ][
                    "observation_window"
                ]
                current_window = learning[
                    "lineage"
                ][
                    "observation_window"
                ]
                if (
                    previous_window is not None
                    and current_window is not None
                    and parse_timestamp(
                        current_window["end"]
                    )
                    < parse_timestamp(
                        previous_window["end"]
                    )
                ):
                    raise PostPublishLearningOutOfOrder(
                        "observation window moved backwards"
                    )
            self._rows.append(dict(row))
            self._latest[key] = learning

    def record(
        self,
        learning: Mapping[str, Any],
    ) -> str:
        parsed = parse_post_publish_learning(
            learning
        )
        key = parsed["lineage"][
            "publish_result_id"
        ]
        previous = self._latest.get(key)
        if previous is not None:
            revision = parsed[
                "learning_revision"
            ]
            previous_revision = previous[
                "learning_revision"
            ]
            if revision < previous_revision:
                raise PostPublishLearningOutOfOrder(
                    "learning revision older than durable state"
                )
            if revision == previous_revision:
                if (
                    parsed["learning_digest"]
                    != previous[
                        "learning_digest"
                    ]
                ):
                    raise PostPublishLearningConflictError(
                        "same learning revision changed payload"
                    )
                return "duplicate"
            previous_window = previous[
                "lineage"
            ][
                "observation_window"
            ]
            current_window = parsed[
                "lineage"
            ][
                "observation_window"
            ]
            if (
                previous_window is not None
                and current_window is not None
                and parse_timestamp(
                    current_window["end"]
                )
                < parse_timestamp(
                    previous_window["end"]
                )
            ):
                raise PostPublishLearningOutOfOrder(
                    "metric observation window moved backwards"
                )
        row = {
            "ledger_version":
                POST_PUBLISH_LEARNING_LEDGER_VERSION,
            "sequence":
                len(self._rows) + 1,
            "learning": parsed,
        }
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(
                canonical_json(row) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        self._rows.append(row)
        self._latest[key] = parsed
        return "accepted"

    def latest(
        self,
        publish_result_id: str,
    ) -> dict[str, Any] | None:
        row = self._latest.get(
            publish_result_id
        )
        return (
            None
            if row is None
            else json.loads(
                canonical_json(row)
            )
        )
