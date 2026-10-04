from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import (
    METRIC_SNAPSHOT_VERSION,
    PUBLISH_RESULT_VERSION,
    build_metric_snapshot,
    build_platform_metrics_event,
    build_publish_result,
    canonical_json,
    parse_metric_snapshot,
    parse_publish_result,
    sha256_json,
)
from .event_stream import parse_timestamp

CONTRACT_VERSION = "growth.postpublish_learning.r31.v1"
AUTHORITY_VERSION = "growth.postpublish_learning_authority.r31.v1"
POLICY_VERSION = "growth.postpublish_learning_policy.r31.v1"
OBSERVATION_VERSION = "growth.postpublish_observation.r31.v1"
EXPERIMENT_VERSION = "growth.randomized_assignment_exposure.r31.v1"
SUGGESTION_VERSION = "growth.future_edit_policy_suggestion.r31.v1"
REPORT_VERSION = "growth.postpublish_learning.r31.readiness.v1"

PARENT_SHA = "9313c984932d9b1bbf18f7c382533f830337971e"
PARENT_CI = 37202813275
PARENT_ARTIFACT_ID = 11303044114
PARENT_ARTIFACT_DIGEST = (
    "sha256:0d8a0ff48c5ade7ec4d2440227f4409d63f1b67153de1308cd53d1a4b3f57646"
)
PARENT_EXTERNAL_CONTRACT = "growth.consensus_review.r30.v1"
PARENT_EXTERNAL_CONTRACT_BLOB = "d1f399dbc1cd3b10d52c123e24f53db83595d40a"

STATUSES = {
    "LEARNING_SOURCE_READY",
    "WAITING_PARENT_QA",
    "INSUFFICIENT_EVIDENCE",
    "EXPERIMENT_EVIDENCE_READY",
}
COUNT_METRICS = (
    "impressions",
    "views",
    "likes",
    "comments",
    "shares",
    "saves",
    "follows",
    "link_clicks",
)
COMPARABLE_METRICS = (
    "impressions",
    "views",
    "reach",
    "watch_time_seconds",
    "average_watch_duration_seconds",
    "completion_rate",
    "likes",
    "comments",
    "shares",
    "saves",
    "follows",
    "subscribes",
    "link_clicks",
)
UPSTREAM_METRIC_KEYS = {
    "impressions",
    "views",
    "watch_time_seconds",
    "average_watch_duration_seconds",
    "completion_rate",
    "likes",
    "comments",
    "shares",
    "saves",
    "follows",
    "link_clicks",
}
FORBIDDEN_CONCLUSIONS = (
    "observational_performance_proves_causal_effect",
    "historical_best_guarantees_future_best",
    "unavailable_metric_equals_zero",
    "cross_platform_metric_substitution",
    "human_ground_truth",
    "human_parity",
)


class R31Error(ValueError):
    pass


class AuthorityDrift(R31Error):
    pass


class ObservationConflict(R31Error):
    pass


class ExperimentInvalid(R31Error):
    pass


class DriftBlocked(R31Error):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R31Error(f"{field} must be lowercase SHA-256")
    return value


def _git_sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R31Error(f"{field} must be exact Git SHA")
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise R31Error(f"{field} must be non-empty string")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R31Error(f"{field} must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise R31Error(f"{field} must be finite")
    return value


def default_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.postpublish_learning.r31.v1"
        / "authority.json"
    )


def default_policy() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.postpublish_learning.r31.v1"
        / "policy.json"
    )


def validate_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "growth_r30_parent",
        "upstream_metrics",
        "evidence_boundary",
    }:
        raise AuthorityDrift("R31 authority fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R31 authority contract mismatch")
    parent = value["growth_r30_parent"]
    if parent != {
        "repository": "foto6/video3",
        "producer_sha": PARENT_SHA,
        "ci_run_id": PARENT_CI,
        "artifact_id": PARENT_ARTIFACT_ID,
        "artifact_name": "growth-r30-consensus-review-oracle",
        "artifact_digest": PARENT_ARTIFACT_DIGEST,
        "external_contract": PARENT_EXTERNAL_CONTRACT,
        "external_contract_blob": PARENT_EXTERNAL_CONTRACT_BLOB,
        "parent_qa_state": "WAITING_PARENT_QA",
        "authoritative_integration_allowed": False,
    }:
        raise AuthorityDrift("exact R30 parent authority drift")
    upstream = value["upstream_metrics"]
    if (
        upstream.get("publish_contract") != PUBLISH_RESULT_VERSION
        or upstream.get("metric_snapshot_contract") != METRIC_SNAPSHOT_VERSION
        or upstream.get("platforms")
        != ["instagram_reels", "tiktok", "youtube_shorts"]
        or upstream.get("accepted_provider_schema_versions")
        != {
            "instagram_reels": "instagram-insights-v26.0",
            "tiktok": "tiktok-video-query-v2",
            "youtube_shorts": "youtube-analytics-v2",
        }
    ):
        raise AuthorityDrift("R31 upstream metric authority drift")
    if value["evidence_boundary"] != {
        "observational_performance_is_causal_effect": False,
        "historical_best_guarantees_future_best": False,
        "model_consensus_is_human_ground_truth": False,
        "human_ground_truth": False,
        "live_publish": False,
        "platform_api_call_in_ci": False,
        "automatic_irreversible_mutation": False,
        "merge": False,
    }:
        raise AuthorityDrift("R31 evidence boundary drift")
    return _clone(value)


def validate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("contract_version") != POLICY_VERSION:
        raise R31Error("R31 policy contract mismatch")
    if value.get("windows") != {
        "early": {"duration_seconds": 3600},
        "mature": {"duration_seconds": 604800},
    }:
        raise R31Error("R31 predeclared windows drift")
    if value.get("primary_metric") != "completion_rate":
        raise R31Error("R31 primary metric drift")
    if value.get("minimum_independent_observations_per_strategy") != 3:
        raise R31Error("R31 observational sample gate drift")
    if value.get("minimum_randomized_exposures_per_arm") != 4:
        raise R31Error("R31 experiment sample gate drift")
    if value.get("randomized_confidence_level") != 0.95:
        raise R31Error("R31 randomized confidence level drift")
    if value.get("maximum_evidence_age_days") != 45:
        raise R31Error("R31 maximum evidence age drift")
    if value.get("stale_confidence_half_life_days") != 21:
        raise R31Error("R31 confidence half-life drift")
    if value.get("outlier_policy") != {
        "location": "median",
        "flag_if_distance_from_median_exceeds": 0.35,
        "single_observation_can_set_rank": False,
    }:
        raise R31Error("R31 outlier policy drift")
    if value.get("exploration") != {
        "minimum_share": 0.2,
        "maximum_single_strategy_prior": 0.7,
        "minimum_active_strategy_prior": 0.1,
        "deterministic_assignment": "sha256(seed|unit|strategy)",
    }:
        raise R31Error("R31 exploration policy drift")
    if value.get("drift") != {
        "platform_distribution_total_variation_warn": 0.35,
        "topic_distribution_total_variation_warn": 0.4,
        "source_distribution_total_variation_warn": 0.4,
        "season_shift_days_warn": 30,
        "metric_schema_change_is_hard_block": True,
    }:
        raise R31Error("R31 drift policy drift")
    if value.get("forbidden_conclusions") != list(FORBIDDEN_CONCLUSIONS):
        raise R31Error("R31 forbidden conclusions drift")
    return _clone(value)


def authority_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority(value))


def policy_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_policy(value))


def _window_seconds(snapshot: Mapping[str, Any]) -> int:
    start = parse_timestamp(snapshot["window"]["start"])
    end = parse_timestamp(snapshot["window"]["end"])
    return int((end - start).total_seconds())


def _metric_entry(
    name: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    if name in {"reach", "subscribes"}:
        return {
            "available": False,
            "value": None,
            "source": "upstream_schema_not_exposed",
        }
    raw = snapshot["raw_metrics"]
    normalized = snapshot["normalized_metrics"]
    if name in {
        "average_watch_duration_seconds",
        "completion_rate",
    }:
        value = normalized.get(name)
        source = snapshot["normalization_sources"].get(name, "unavailable")
    elif name == "watch_time_seconds":
        value = normalized.get(name)
        source = "provider_export" if value is not None else "unavailable"
    else:
        value = raw.get(name)
        source = "provider_export" if value is not None else "unavailable"
    if value is None:
        return {
            "available": False,
            "value": None,
            "source": (
                "provider_metric_unavailable"
                if name in UPSTREAM_METRIC_KEYS
                else "upstream_schema_not_exposed"
            ),
        }
    return {
        "available": True,
        "value": value,
        "source": source,
    }


def _outcome_scalar(
    metrics: Mapping[str, Mapping[str, Any]],
    *,
    duration_seconds: float,
) -> tuple[float | None, str | None]:
    completion = metrics["completion_rate"]
    if completion["available"]:
        return max(0.0, min(1.0, float(completion["value"]))), "completion_rate"
    average = metrics["average_watch_duration_seconds"]
    if average["available"] and duration_seconds > 0:
        value = float(average["value"]) / duration_seconds
        return max(0.0, min(1.0, value)), "average_watch_fraction"
    views = metrics["views"]
    if views["available"] and float(views["value"]) > 0:
        engagement = 0.0
        available = False
        for name in ("likes", "comments", "shares", "saves"):
            item = metrics[name]
            if item["available"]:
                engagement += float(item["value"])
                available = True
        if available:
            return max(
                0.0,
                min(1.0, engagement / float(views["value"])),
            ), "engagement_per_view"
    return None, None


def normalize_observation(
    raw: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    required = {
        "contract_version",
        "observation_id",
        "publish_result",
        "metric_snapshot",
        "prior_metric_snapshot",
        "lineage",
        "strategy",
        "metric_definition",
        "topic_source",
        "window_class",
        "fixture",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise ObservationConflict("R31 observation fields invalid")
    if raw["contract_version"] != OBSERVATION_VERSION:
        raise ObservationConflict("R31 observation contract mismatch")
    observation_id = _nonempty(raw["observation_id"], "observation_id")
    try:
        publish = parse_publish_result(raw["publish_result"])
        snapshot = parse_metric_snapshot(raw["metric_snapshot"])
    except ValueError as exc:
        raise ObservationConflict("invalid publish/metric evidence") from exc
    for field in (
        "publish_result_id",
        "publish_result_digest",
        "platform",
        "account_id",
        "post_id",
        "cycle_revision",
        "source_class",
    ):
        expected = publish["source_class"] if field == "source_class" else publish[field]
        if snapshot[field] != expected:
            raise ObservationConflict(f"snapshot {field} does not bind publish receipt")
    if snapshot["live_performance_claim_allowed"] != publish["provenance"][
        "live_performance_claim_allowed"
    ]:
        raise ObservationConflict("snapshot/publish live-scope mismatch")
    if raw["fixture"] is not (publish["source_class"] == "synthetic_fixture"):
        raise ObservationConflict("fixture flag/source_class mismatch")

    lineage = raw["lineage"]
    if not isinstance(lineage, Mapping) or set(lineage) != {
        "creator_session_id",
        "creator_tournament_id",
        "winner_candidate_id",
        "winner_render_sha256",
        "winner_envelope_digest",
        "operation_graph_digest",
        "publish_transaction_id",
        "external_post_id",
        "account_pseudonym",
    }:
        raise ObservationConflict("creator/publish lineage fields invalid")
    for field in (
        "creator_session_id",
        "creator_tournament_id",
        "winner_candidate_id",
        "publish_transaction_id",
        "external_post_id",
        "account_pseudonym",
    ):
        _nonempty(lineage[field], f"lineage.{field}")
    for field in (
        "winner_render_sha256",
        "winner_envelope_digest",
        "operation_graph_digest",
    ):
        _sha(lineage[field], f"lineage.{field}")
    if lineage["winner_render_sha256"] != publish["artifact"]["media_artifact_digest"]:
        raise ObservationConflict("winner render hash changed after publish receipt")
    if lineage["external_post_id"] != publish["post_id"]:
        raise ObservationConflict("external post ID mismatch")
    if lineage["account_pseudonym"] != publish["account_id"]:
        raise ObservationConflict("account pseudonym mismatch")

    strategy = raw["strategy"]
    if not isinstance(strategy, Mapping) or set(strategy) != {
        "strategy_id",
        "operation_graph_digest",
        "operation_classes",
    }:
        raise ObservationConflict("strategy fields invalid")
    _nonempty(strategy["strategy_id"], "strategy.strategy_id")
    _sha(strategy["operation_graph_digest"], "strategy.operation_graph_digest")
    if strategy["operation_graph_digest"] != lineage["operation_graph_digest"]:
        raise ObservationConflict("strategy operation graph lineage mismatch")
    if (
        not isinstance(strategy["operation_classes"], list)
        or not strategy["operation_classes"]
        or any(not isinstance(item, str) or not item for item in strategy["operation_classes"])
    ):
        raise ObservationConflict("strategy operation_classes invalid")

    metric_def = raw["metric_definition"]
    if not isinstance(metric_def, Mapping) or set(metric_def) != {
        "provider_schema_version",
        "normalized_schema_version",
    }:
        raise ObservationConflict("metric definition fields invalid")
    platform = publish["platform"]
    accepted_schema = authority["upstream_metrics"][
        "accepted_provider_schema_versions"
    ][platform]
    if metric_def["provider_schema_version"] != accepted_schema:
        raise DriftBlocked("stale or changed platform metric definition")
    if metric_def["normalized_schema_version"] != METRIC_SNAPSHOT_VERSION:
        raise DriftBlocked("normalized metric schema drift")

    topic = raw["topic_source"]
    if not isinstance(topic, Mapping) or set(topic) != {
        "topic_cluster",
        "source_sha256",
    }:
        raise ObservationConflict("topic/source fields invalid")
    _nonempty(topic["topic_cluster"], "topic_source.topic_cluster")
    _sha(topic["source_sha256"], "topic_source.source_sha256")

    window_class = raw["window_class"]
    windows = policy["windows"]
    if window_class not in windows:
        raise ObservationConflict("unknown predeclared window class")
    expected_seconds = windows[window_class]["duration_seconds"]
    if _window_seconds(snapshot) != expected_seconds:
        raise ObservationConflict(
            f"metric window is not exact predeclared {window_class} window"
        )
    if parse_timestamp(snapshot["window"]["start"]) < parse_timestamp(
        publish["published_at"]
    ):
        raise ObservationConflict("metric window starts before publication")

    prior_raw = raw["prior_metric_snapshot"]
    if prior_raw is not None:
        try:
            prior = parse_metric_snapshot(prior_raw)
        except ValueError as exc:
            raise ObservationConflict("invalid prior metric snapshot") from exc
        for field in ("publish_result_id", "platform", "account_id", "post_id"):
            if prior[field] != snapshot[field]:
                raise ObservationConflict("prior snapshot identity mismatch")
        if parse_timestamp(prior["window"]["end"]) >= parse_timestamp(
            snapshot["window"]["end"]
        ):
            raise ObservationConflict("prior snapshot must precede current snapshot")
        for metric in COUNT_METRICS:
            before = prior["raw_metrics"].get(metric)
            after = snapshot["raw_metrics"].get(metric)
            if before is not None and after is not None and after < before:
                raise ObservationConflict(
                    f"metric counter decreased unexpectedly: {metric}"
                )

    metrics = {
        name: _metric_entry(name, snapshot)
        for name in COMPARABLE_METRICS
    }
    for name, item in metrics.items():
        if not item["available"] and item["value"] is not None:
            raise ObservationConflict(f"unavailable metric treated as value: {name}")
    scalar, scalar_source = _outcome_scalar(
        metrics,
        duration_seconds=float(publish["artifact"]["media_duration_seconds"]),
    )
    censored = scalar is None or not metrics["views"]["available"]
    captured_at = parse_timestamp(snapshot["window"]["end"])
    return _clone(
        {
            "observation_id": observation_id,
            "observation_digest": sha256_json(raw),
            "publish_result_id": publish["publish_result_id"],
            "publish_result_digest": publish["publish_result_digest"],
            "snapshot_digest": snapshot["snapshot_digest"],
            "platform": platform,
            "account_pseudonym": publish["account_id"],
            "post_id": publish["post_id"],
            "source_class": publish["source_class"],
            "fixture": bool(raw["fixture"]),
            "published_at": publish["published_at"],
            "captured_at": snapshot["window"]["end"],
            "capture_age_anchor": captured_at.isoformat(),
            "window_class": window_class,
            "window": snapshot["window"],
            "provider_schema_version": metric_def["provider_schema_version"],
            "strategy_id": strategy["strategy_id"],
            "operation_graph_digest": strategy["operation_graph_digest"],
            "operation_classes": list(strategy["operation_classes"]),
            "topic_cluster": topic["topic_cluster"],
            "source_sha256": topic["source_sha256"],
            "creator_session_id": lineage["creator_session_id"],
            "creator_tournament_id": lineage["creator_tournament_id"],
            "winner_candidate_id": lineage["winner_candidate_id"],
            "winner_render_sha256": lineage["winner_render_sha256"],
            "winner_envelope_digest": lineage["winner_envelope_digest"],
            "publish_transaction_id": lineage["publish_transaction_id"],
            "metrics": metrics,
            "outcome_scalar": scalar,
            "outcome_scalar_source": scalar_source,
            "censored_or_incomplete": censored,
            "observational": True,
            "causal_claim_allowed": False,
        }
    )


def validate_dataset(
    raw_observations: Sequence[Mapping[str, Any]],
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(raw_observations, Sequence) or isinstance(
        raw_observations, (str, bytes)
    ):
        raise ObservationConflict("observations must be array")
    if not raw_observations:
        raise ObservationConflict("at least one observation required")
    parsed = [
        normalize_observation(raw, authority=authority, policy=policy)
        for raw in raw_observations
    ]
    posts: set[tuple[str, str, str]] = set()
    snapshots: set[str] = set()
    observation_ids: dict[str, str] = {}
    accounts = set()
    windows = set()
    schemas = defaultdict(set)
    for item in parsed:
        post_key = (
            item["platform"],
            item["account_pseudonym"],
            item["post_id"],
        )
        if item["snapshot_digest"] in snapshots:
            raise ObservationConflict("same metrics snapshot replayed under new ID")
        snapshots.add(item["snapshot_digest"])
        if post_key in posts:
            raise ObservationConflict("duplicate provider post")
        posts.add(post_key)
        previous = observation_ids.get(item["observation_id"])
        if previous is not None and previous != item["observation_digest"]:
            raise ObservationConflict("observation ID changed bytes")
        if previous is not None:
            raise ObservationConflict("duplicate observation ID")
        observation_ids[item["observation_id"]] = item["observation_digest"]
        accounts.add(item["account_pseudonym"])
        windows.add(item["window_class"])
        schemas[item["platform"]].add(item["provider_schema_version"])
    if len(accounts) != 1:
        raise ObservationConflict("cross-account contamination")
    if len(windows) != 1:
        raise ObservationConflict("mixed predeclared metric windows")
    if any(len(versions) != 1 for versions in schemas.values()):
        raise DriftBlocked("metric-definition/schema drift inside evidence set")
    return sorted(
        parsed,
        key=lambda item: (
            item["platform"],
            item["strategy_id"],
            item["post_id"],
            item["observation_id"],
        ),
    )


def _distribution(values: Sequence[str]) -> dict[str, float]:
    total = len(values)
    if total == 0:
        return {}
    counts = Counter(values)
    return {
        key: round(count / total, 8)
        for key, count in sorted(counts.items())
    }


def _tv(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = set(a) | set(b)
    return round(
        0.5 * sum(abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) for k in keys),
        8,
    )


def detect_drift(
    observations: Sequence[Mapping[str, Any]],
    *,
    baseline: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
    as_of: str,
) -> dict[str, Any]:
    policy = validate_policy(policy)
    parse_timestamp(as_of)
    current = {
        "platform_distribution": _distribution([o["platform"] for o in observations]),
        "topic_distribution": _distribution([o["topic_cluster"] for o in observations]),
        "source_distribution": _distribution([o["source_sha256"] for o in observations]),
        "schema_versions": sorted(
            {f"{o['platform']}:{o['provider_schema_version']}" for o in observations}
        ),
    }
    warnings: list[str] = []
    scores = {
        "platform_distribution_shift": 0.0,
        "topic_distribution_shift": 0.0,
        "source_distribution_shift": 0.0,
        "season_time_shift_days": 0.0,
    }
    if baseline is not None:
        required = {
            "platform_distribution",
            "topic_distribution",
            "source_distribution",
            "schema_versions",
            "as_of",
        }
        if not isinstance(baseline, Mapping) or set(baseline) != required:
            raise DriftBlocked("baseline drift profile invalid")
        if sorted(baseline["schema_versions"]) != current["schema_versions"]:
            raise DriftBlocked("metric-definition/schema drift against baseline")
        scores["platform_distribution_shift"] = _tv(
            current["platform_distribution"],
            baseline["platform_distribution"],
        )
        scores["topic_distribution_shift"] = _tv(
            current["topic_distribution"],
            baseline["topic_distribution"],
        )
        scores["source_distribution_shift"] = _tv(
            current["source_distribution"],
            baseline["source_distribution"],
        )
        days = abs(
            (
                parse_timestamp(as_of)
                - parse_timestamp(_nonempty(baseline["as_of"], "baseline.as_of"))
            ).total_seconds()
        ) / 86400
        scores["season_time_shift_days"] = round(days, 8)
        drift_policy = policy["drift"]
        if scores["platform_distribution_shift"] > drift_policy[
            "platform_distribution_total_variation_warn"
        ]:
            warnings.append("platform_distribution_shift")
        if scores["topic_distribution_shift"] > drift_policy[
            "topic_distribution_total_variation_warn"
        ]:
            warnings.append("content_topic_shift")
        if scores["source_distribution_shift"] > drift_policy[
            "source_distribution_total_variation_warn"
        ]:
            warnings.append("content_source_shift")
        if days > drift_policy["season_shift_days_warn"]:
            warnings.append("season_time_window_shift")
    return {
        "current": current,
        "scores": scores,
        "warnings": sorted(warnings),
        "hard_block": False,
    }


def _stale_decay(
    captured_at: str,
    *,
    as_of: str,
    policy: Mapping[str, Any],
) -> tuple[float, bool, float]:
    age_days = max(
        0.0,
        (
            parse_timestamp(as_of) - parse_timestamp(captured_at)
        ).total_seconds()
        / 86400,
    )
    half_life = float(policy["stale_confidence_half_life_days"])
    decay = 0.5 ** (age_days / half_life)
    stale = age_days > policy["maximum_evidence_age_days"]
    return round(decay, 8), stale, round(age_days, 8)


def robust_strategy_priors(
    observations: Sequence[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
    as_of: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in observations:
        if item["outcome_scalar"] is not None:
            groups[item["strategy_id"]].append(item)
    rows = []
    for strategy_id in sorted(groups):
        items = groups[strategy_id]
        values = [float(item["outcome_scalar"]) for item in items]
        median = float(statistics.median(values))
        outliers = [
            item["observation_id"]
            for item in items
            if abs(float(item["outcome_scalar"]) - median)
            > policy["outlier_policy"]["flag_if_distance_from_median_exceeds"]
        ]
        decays = [
            _stale_decay(
                item["captured_at"],
                as_of=as_of,
                policy=policy,
            )
            for item in items
        ]
        confidence = sum(row[0] for row in decays) / len(decays)
        stale_count = sum(1 for row in decays if row[1])
        gate = len(items) >= policy["minimum_independent_observations_per_strategy"]
        rows.append(
            {
                "strategy_id": strategy_id,
                "independent_sample_count": len(items),
                "robust_location": round(median, 8),
                "outlier_observation_ids": sorted(outliers),
                "outlier_count": len(outliers),
                "confidence_decay": round(confidence, 8),
                "stale_evidence_count": stale_count,
                "minimum_sample_gate_met": gate,
                "causal_interpretation": False,
                "evidence_ids": sorted(item["observation_id"] for item in items),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            not row["minimum_sample_gate_met"],
            -row["robust_location"],
            row["strategy_id"],
        ),
    )


def deterministic_assignment(
    *,
    seed: str,
    unit_id: str,
    strategies: Sequence[str],
) -> str:
    if not strategies:
        raise R31Error("at least one strategy required")
    scored = []
    for strategy in sorted(strategies):
        digest = hashlib.sha256(
            f"{seed}|{unit_id}|{strategy}".encode("utf-8")
        ).hexdigest()
        scored.append((digest, strategy))
    return min(scored)[1]


def exploration_prior(
    priors: Sequence[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    active = [row for row in priors if row["minimum_sample_gate_met"]]
    if not active:
        return []
    scores = [max(0.0, float(row["robust_location"])) for row in active]
    if sum(scores) <= 0:
        scores = [1.0 for _ in active]
    raw = [value / sum(scores) for value in scores]
    max_share = policy["exploration"]["maximum_single_strategy_prior"]
    min_share = policy["exploration"]["minimum_active_strategy_prior"]
    bounded = [min(max_share, max(min_share, value)) for value in raw]
    total = sum(bounded)
    bounded = [value / total for value in bounded]
    if len(bounded) > 1:
        leader = max(range(len(bounded)), key=lambda i: bounded[i])
        if bounded[leader] > max_share:
            excess = bounded[leader] - max_share
            bounded[leader] = max_share
            others = [i for i in range(len(bounded)) if i != leader]
            for i in others:
                bounded[i] += excess / len(others)
    return [
        {
            "strategy_id": row["strategy_id"],
            "prior": round(bounded[index], 8),
            "robust_location": row["robust_location"],
            "causal_interpretation": False,
        }
        for index, row in enumerate(active)
    ]


def _assignment_digest(material: Mapping[str, Any]) -> str:
    return sha256_json(material)


def validate_experiment_records(
    records: Sequence[Mapping[str, Any]],
    *,
    observations: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not records:
        return None
    by_publish = {item["publish_result_id"]: item for item in observations}
    exposures: set[str] = set()
    assignments: set[str] = set()
    experiment_ids: set[str] = set()
    treatments: dict[str, list[float]] = defaultdict(list)
    evidence_ids: list[str] = []
    primary_metric = policy["primary_metric"]
    for raw in records:
        required = {
            "contract_version",
            "experiment_id",
            "assignment_id",
            "assignment_digest",
            "exposure_id",
            "exposure_digest",
            "publish_result_id",
            "randomization_seed",
            "randomization_method",
            "allocation_ratio",
            "treatment",
            "assignment_at",
            "exposure_at",
            "predeclared_window_class",
            "predeclared_primary_metric",
            "assigned_render_sha256",
            "observed_render_sha256",
            "treatment_visible_before_assignment",
            "creative_changed_after_assignment",
            "posthoc_window_selected",
            "source_class",
        }
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise ExperimentInvalid("randomized assignment/exposure fields invalid")
        if raw["contract_version"] != EXPERIMENT_VERSION:
            raise ExperimentInvalid("randomized experiment contract mismatch")
        experiment_ids.add(_nonempty(raw["experiment_id"], "experiment_id"))
        assignment_id = _nonempty(raw["assignment_id"], "assignment_id")
        exposure_id = _nonempty(raw["exposure_id"], "exposure_id")
        if assignment_id in assignments:
            raise ExperimentInvalid("duplicate assignment")
        if exposure_id in exposures:
            raise ExperimentInvalid("duplicate exposure")
        assignments.add(assignment_id)
        exposures.add(exposure_id)
        observation = by_publish.get(raw["publish_result_id"])
        if observation is None:
            raise ExperimentInvalid("experiment exposure has no bound observation")
        if raw["treatment"] not in {"control", "treatment"}:
            raise ExperimentInvalid("experiment treatment invalid")
        if raw["randomization_method"] != "deterministic_sha256_bucket":
            raise ExperimentInvalid("non-random historical comparison mislabeled experiment")
        if raw["allocation_ratio"] != 0.5:
            raise ExperimentInvalid("experiment allocation ratio drift")
        if raw["predeclared_window_class"] != observation["window_class"]:
            raise ExperimentInvalid("post-hoc metric-window selection")
        if raw["predeclared_primary_metric"] != primary_metric:
            raise ExperimentInvalid("post-hoc primary metric selection")
        if raw["treatment_visible_before_assignment"] is not False:
            raise ExperimentInvalid("treatment leakage")
        if raw["creative_changed_after_assignment"] is not False:
            raise ExperimentInvalid("creative changed after assignment")
        if raw["posthoc_window_selected"] is not False:
            raise ExperimentInvalid("post-hoc winner/window selection")
        if raw["assigned_render_sha256"] != observation["winner_render_sha256"]:
            raise ExperimentInvalid("assigned creative hash mismatch")
        if raw["observed_render_sha256"] != observation["winner_render_sha256"]:
            raise ExperimentInvalid("changed creative after assignment")
        if parse_timestamp(raw["assignment_at"]) > parse_timestamp(raw["exposure_at"]):
            raise ExperimentInvalid("exposure precedes assignment")
        if raw["source_class"] != observation["source_class"]:
            raise ExperimentInvalid("experiment source class mismatch")
        seed = _nonempty(raw["randomization_seed"], "randomization_seed")
        expected_treatment = (
            "treatment"
            if int(
                hashlib.sha256(
                    f"{seed}|{raw['publish_result_id']}".encode("utf-8")
                ).hexdigest(),
                16,
            )
            % 2
            else "control"
        )
        if raw["treatment"] != expected_treatment:
            raise ExperimentInvalid("assignment does not match deterministic randomization")
        assignment_material = {
            "experiment_id": raw["experiment_id"],
            "assignment_id": assignment_id,
            "publish_result_id": raw["publish_result_id"],
            "randomization_seed": seed,
            "treatment": raw["treatment"],
            "predeclared_window_class": raw["predeclared_window_class"],
            "predeclared_primary_metric": raw["predeclared_primary_metric"],
            "assigned_render_sha256": raw["assigned_render_sha256"],
        }
        if raw["assignment_digest"] != _assignment_digest(assignment_material):
            raise ExperimentInvalid("assignment digest mismatch")
        exposure_material = {
            "assignment_digest": raw["assignment_digest"],
            "exposure_id": exposure_id,
            "exposure_at": raw["exposure_at"],
            "observed_render_sha256": raw["observed_render_sha256"],
        }
        if raw["exposure_digest"] != _assignment_digest(exposure_material):
            raise ExperimentInvalid("exposure digest mismatch")
        outcome = observation["metrics"][primary_metric]
        if not outcome["available"]:
            raise ExperimentInvalid("randomized primary metric unavailable")
        treatments[raw["treatment"]].append(float(outcome["value"]))
        evidence_ids.append(exposure_id)
    if len(experiment_ids) != 1:
        raise ExperimentInvalid("mixed experiment IDs")
    control = treatments["control"]
    treatment = treatments["treatment"]
    minimum = policy["minimum_randomized_exposures_per_arm"]
    gate = len(control) >= minimum and len(treatment) >= minimum
    lift = (
        statistics.mean(treatment) - statistics.mean(control)
        if control and treatment
        else None
    )
    interval = None
    if lift is not None and gate:
        def variance(values: Sequence[float]) -> float:
            return statistics.variance(values) if len(values) > 1 else 0.0
        se = math.sqrt(
            variance(treatment) / len(treatment)
            + variance(control) / len(control)
        )
        margin = 1.96 * se
        interval = [
            round(max(-1.0, lift - margin), 8),
            round(min(1.0, lift + margin), 8),
        ]
    direction = (
        "treatment"
        if lift is not None and lift > 0
        else ("control" if lift is not None and lift < 0 else "none")
    )
    update_allowed = bool(
        gate
        and interval is not None
        and (
            (direction == "treatment" and interval[0] > 0)
            or (direction == "control" and interval[1] < 0)
        )
    )
    return {
        "experiment_id": next(iter(experiment_ids)),
        "control_n": len(control),
        "treatment_n": len(treatment),
        "minimum_sample_gate_met": gate,
        "bounded_lift_estimate": None if lift is None else round(lift, 8),
        "confidence_interval_95": interval,
        "preferred_arm": direction if update_allowed else None,
        "preference_update_allowed": update_allowed,
        "causal_language_allowed": bool(gate),
        "causally_proven": False,
        "evidence_ids": sorted(evidence_ids),
    }


def _baseline_profile(observations: Sequence[Mapping[str, Any]], as_of: str) -> dict[str, Any]:
    return {
        "platform_distribution": _distribution([o["platform"] for o in observations]),
        "topic_distribution": _distribution([o["topic_cluster"] for o in observations]),
        "source_distribution": _distribution([o["source_sha256"] for o in observations]),
        "schema_versions": sorted(
            {f"{o['platform']}:{o['provider_schema_version']}" for o in observations}
        ),
        "as_of": as_of,
    }


def build_learning_result(
    *,
    raw_observations: Sequence[Mapping[str, Any]],
    experiment_records: Sequence[Mapping[str, Any]] | None,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
    as_of: str,
    exploration_seed: str,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    _git_sha(growth_sha, "growth_sha")
    if isinstance(growth_ci_run_id, bool) or not isinstance(growth_ci_run_id, int) or growth_ci_run_id < 1:
        raise R31Error("growth_ci_run_id must be positive integer")
    observations = validate_dataset(
        raw_observations,
        authority=authority,
        policy=policy,
    )
    drift = detect_drift(
        observations,
        baseline=baseline,
        policy=policy,
        as_of=as_of,
    )
    priors = robust_strategy_priors(
        observations,
        policy=policy,
        as_of=as_of,
    )
    exploration = exploration_prior(priors, policy=policy)
    experiment = validate_experiment_records(
        list(experiment_records or []),
        observations=observations,
        policy=policy,
    )
    observational_gate = bool(priors) and any(
        row["minimum_sample_gate_met"] for row in priors
    )
    evidence_state = (
        "EXPERIMENT_EVIDENCE_READY"
        if experiment is not None and experiment["minimum_sample_gate_met"]
        else (
            "LEARNING_SOURCE_READY"
            if observational_gate
            else "INSUFFICIENT_EVIDENCE"
        )
    )
    parent_waiting = (
        authority["growth_r30_parent"]["parent_qa_state"] == "WAITING_PARENT_QA"
        or authority["growth_r30_parent"]["authoritative_integration_allowed"] is False
    )
    status = "WAITING_PARENT_QA" if parent_waiting else evidence_state
    if status not in STATUSES:
        raise R31Error("unexpected R31 status")
    active_strategies = [row["strategy_id"] for row in exploration]
    next_assignment = (
        None
        if not active_strategies
        else deterministic_assignment(
            seed=exploration_seed,
            unit_id=sha256_json(
                [item["publish_result_id"] for item in observations]
            ),
            strategies=active_strategies,
        )
    )
    suggestion = {
        "contract_version": SUGGESTION_VERSION,
        "suggestion_id": "",
        "suggestion_digest": "",
        "status": status,
        "evidence_state": evidence_state,
        "authoritative_integration": not parent_waiting,
        "window_class": observations[0]["window_class"],
        "account_pseudonym": observations[0]["account_pseudonym"],
        "ranked_strategy_priors": exploration,
        "observational_strategy_evidence": priors,
        "randomized_experiment_evidence": experiment,
        "drift": drift,
        "confidence_and_uncertainty": {
            "observational_causal_interpretation": False,
            "stale_confidence_decay_applied": True,
            "randomized_interval_available": bool(
                experiment and experiment["confidence_interval_95"] is not None
            ),
            "censored_observation_count": sum(
                1 for item in observations if item["censored_or_incomplete"]
            ),
        },
        "evidence_ids": sorted(item["observation_id"] for item in observations),
        "forbidden_conclusions": list(FORBIDDEN_CONCLUSIONS),
        "recommended_next_experiment": {
            "randomized": True,
            "predeclared_primary_metric": policy["primary_metric"],
            "predeclared_window_class": observations[0]["window_class"],
            "minimum_exposures_per_arm": policy[
                "minimum_randomized_exposures_per_arm"
            ],
            "exploration_share_minimum": policy["exploration"]["minimum_share"],
            "deterministic_seed": exploration_seed,
            "next_strategy_assignment": next_assignment,
            "automatic_publish": False,
            "automatic_irreversible_mutation": False,
        },
        "growth_r31": {
            "producer_sha": growth_sha,
            "ci_run_id": growth_ci_run_id,
            "authority_digest": authority_digest(authority),
            "policy_digest": policy_digest(policy),
            "parent_r30_sha": PARENT_SHA,
            "parent_qa_state": authority["growth_r30_parent"]["parent_qa_state"],
        },
        "evidence_boundary": {
            "observational_performance_is_causal_effect": False,
            "historical_best_guarantees_future_best": False,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "human_parity_inferred": False,
            "platform_api_call": False,
            "live_publish": False,
            "automatic_irreversible_mutation": False,
        },
    }
    suggestion["suggestion_id"] = "gr31p1:" + sha256_json(
        {
            "observation_ids": suggestion["evidence_ids"],
            "policy_digest": suggestion["growth_r31"]["policy_digest"],
            "authority_digest": suggestion["growth_r31"]["authority_digest"],
            "window_class": suggestion["window_class"],
        }
    )
    material = copy.deepcopy(suggestion)
    material["suggestion_digest"] = ""
    suggestion["suggestion_digest"] = sha256_json(material)
    return _clone(suggestion)


def _fixture_observation(
    *,
    index: int,
    strategy: str,
    completion: float | None,
    views: int,
    platform: str = "instagram_reels",
    account: str = "acct-pseudo-1",
    window_class: str = "early",
    topic: str = "topic-a",
    source_sha: str = "a" * 64,
    source_class: str = "synthetic_fixture",
    render_sha: str | None = None,
    huge_engagement: bool = False,
) -> dict[str, Any]:
    render_sha = render_sha or hashlib.sha256(
        f"render-{index}".encode("utf-8")
    ).hexdigest()
    published = f"2026-09-{1 + index % 20:02d}T10:00:00Z"
    duration = 3600 if window_class == "early" else 604800
    start = published
    end_dt = parse_timestamp(start).timestamp() + duration
    end = datetime.fromtimestamp(end_dt, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    publish = build_publish_result(
        source_class=source_class,
        platform=platform,
        account_id=account,
        post_id=f"post-{index}",
        published_at=published,
        captured_at=published,
        cycle_revision=1,
        creative_artifact_id=f"creative-{index}",
        creative_artifact_digest=hashlib.sha256(
            f"creative-{index}".encode("utf-8")
        ).hexdigest(),
        media_artifact_id=f"media-{index}",
        media_artifact_digest=render_sha,
        media_render_fingerprint=hashlib.sha256(
            f"fingerprint-{index}".encode("utf-8")
        ).hexdigest(),
        media_duration_seconds=30.0,
        provider_receipt_digest=(
            hashlib.sha256(f"receipt-{index}".encode("utf-8")).hexdigest()
            if source_class == "platform_export"
            else None
        ),
        fixture_source_sha256=(
            hashlib.sha256(f"fixture-publish-{index}".encode("utf-8")).hexdigest()
            if source_class == "synthetic_fixture"
            else None
        ),
    )
    available = [
        "views",
        "watch_time_seconds",
        "likes",
        "comments",
        "shares",
        "saves",
    ]
    if completion is not None:
        available.append("completion_rate")
    metrics = {
        "impressions": None,
        "views": views,
        "watch_time_seconds": round(views * 8.0, 8),
        "average_watch_duration_seconds": None,
        "completed_views": None,
        "completion_rate": completion,
        "retention_points": None,
        "retention_denominator_views": None,
        "likes": (views * 5 if huge_engagement else max(1, views // 20)),
        "comments": (views * 2 if huge_engagement else max(0, views // 100)),
        "shares": (views * 3 if huge_engagement else max(0, views // 50)),
        "saves": (views * 2 if huge_engagement else max(0, views // 80)),
        "follows": None,
        "link_clicks": None,
    }
    event = build_platform_metrics_event(
        source_class=source_class,
        platform=platform,
        account_id=account,
        post_id=f"post-{index}",
        cycle_revision=1,
        captured_at=end,
        window_start=start,
        window_end=end,
        complete=True,
        available_metrics=available,
        metrics=metrics,
        export_id=f"export-{index}",
        export_digest=hashlib.sha256(f"export-{index}".encode("utf-8")).hexdigest(),
        fixture_source_sha256=(
            hashlib.sha256(f"fixture-metric-{index}".encode("utf-8")).hexdigest()
            if source_class == "synthetic_fixture"
            else None
        ),
    )
    snapshot = build_metric_snapshot(
        publish_result=publish,
        metrics_events=[event],
    )
    op_digest = hashlib.sha256(f"ops-{strategy}".encode("utf-8")).hexdigest()
    provider_schema = {
        "instagram_reels": "instagram-insights-v26.0",
        "tiktok": "tiktok-video-query-v2",
        "youtube_shorts": "youtube-analytics-v2",
    }[platform]
    return {
        "contract_version": OBSERVATION_VERSION,
        "observation_id": f"obs-{index}",
        "publish_result": publish,
        "metric_snapshot": snapshot,
        "prior_metric_snapshot": None,
        "lineage": {
            "creator_session_id": "creator-session-r31",
            "creator_tournament_id": "tournament-r31",
            "winner_candidate_id": f"candidate-{index}",
            "winner_render_sha256": render_sha,
            "winner_envelope_digest": hashlib.sha256(
                f"envelope-{index}".encode("utf-8")
            ).hexdigest(),
            "operation_graph_digest": op_digest,
            "publish_transaction_id": f"txn-{index}",
            "external_post_id": f"post-{index}",
            "account_pseudonym": account,
        },
        "strategy": {
            "strategy_id": strategy,
            "operation_graph_digest": op_digest,
            "operation_classes": ["trim", "caption"],
        },
        "metric_definition": {
            "provider_schema_version": provider_schema,
            "normalized_schema_version": METRIC_SNAPSHOT_VERSION,
        },
        "topic_source": {
            "topic_cluster": topic,
            "source_sha256": source_sha,
        },
        "window_class": window_class,
        "fixture": source_class == "synthetic_fixture",
    }


def _fixture_experiment(
    observations: Sequence[Mapping[str, Any]],
    *,
    experiment_id: str = "exp-r31-synthetic",
    seed: str = "r31-seed",
) -> list[dict[str, Any]]:
    rows = []
    for index, raw in enumerate(observations):
        publish_id = raw["publish_result"]["publish_result_id"]
        treatment = (
            "treatment"
            if int(
                hashlib.sha256(f"{seed}|{publish_id}".encode("utf-8")).hexdigest(),
                16,
            )
            % 2
            else "control"
        )
        assignment_id = f"assignment-{index}"
        assignment_material = {
            "experiment_id": experiment_id,
            "assignment_id": assignment_id,
            "publish_result_id": publish_id,
            "randomization_seed": seed,
            "treatment": treatment,
            "predeclared_window_class": raw["window_class"],
            "predeclared_primary_metric": "completion_rate",
            "assigned_render_sha256": raw["lineage"]["winner_render_sha256"],
        }
        assignment_digest = sha256_json(assignment_material)
        exposure_id = f"exposure-{index}"
        exposure_at = raw["publish_result"]["published_at"]
        exposure_material = {
            "assignment_digest": assignment_digest,
            "exposure_id": exposure_id,
            "exposure_at": exposure_at,
            "observed_render_sha256": raw["lineage"]["winner_render_sha256"],
        }
        rows.append(
            {
                "contract_version": EXPERIMENT_VERSION,
                "experiment_id": experiment_id,
                "assignment_id": assignment_id,
                "assignment_digest": assignment_digest,
                "exposure_id": exposure_id,
                "exposure_digest": sha256_json(exposure_material),
                "publish_result_id": publish_id,
                "randomization_seed": seed,
                "randomization_method": "deterministic_sha256_bucket",
                "allocation_ratio": 0.5,
                "treatment": treatment,
                "assignment_at": exposure_at,
                "exposure_at": exposure_at,
                "predeclared_window_class": raw["window_class"],
                "predeclared_primary_metric": "completion_rate",
                "assigned_render_sha256": raw["lineage"]["winner_render_sha256"],
                "observed_render_sha256": raw["lineage"]["winner_render_sha256"],
                "treatment_visible_before_assignment": False,
                "creative_changed_after_assignment": False,
                "posthoc_window_selected": False,
                "source_class": raw["publish_result"]["source_class"],
            }
        )
    return rows


def build_rehearsal(
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    observations = []
    index = 1
    for strategy, values in (
        ("fast_hook", [0.62, 0.64, 0.61, 0.63, 0.99]),
        ("steady_pacing", [0.48, 0.50, 0.47, 0.49, 0.51]),
    ):
        for value in values:
            observations.append(
                _fixture_observation(
                    index=index,
                    strategy=strategy,
                    completion=value,
                    views=1000,
                )
            )
            index += 1
    baseline = _baseline_profile(
        [
            normalize_observation(item, authority=authority, policy=policy)
            for item in observations
        ],
        "2026-09-21T00:00:00Z",
    )
    observational = build_learning_result(
        raw_observations=observations,
        experiment_records=[],
        authority=authority,
        policy=policy,
        baseline=baseline,
        as_of="2026-10-04T00:00:00Z",
        exploration_seed="r31-observational-seed",
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    randomized = []
    while len(randomized) < 32:
        i = 100 + len(randomized)
        raw = _fixture_observation(
            index=i,
            strategy="randomized_pair",
            completion=0.5,
            views=1200,
        )
        randomized.append(raw)
    records = _fixture_experiment(randomized)
    for raw, record in zip(randomized, records):
        raw["metric_snapshot"]["normalized_metrics"]["completion_rate"] = (
            0.78 if record["treatment"] == "treatment" else 0.30
        )
        raw["metric_snapshot"]["raw_metrics"]["completion_rate"] = (
            0.78 if record["treatment"] == "treatment" else 0.30
        )
        material = dict(raw["metric_snapshot"])
        material.pop("snapshot_digest")
        raw["metric_snapshot"]["snapshot_digest"] = sha256_json(material)
    randomized_baseline = _baseline_profile(
        [
            normalize_observation(item, authority=authority, policy=policy)
            for item in randomized
        ],
        "2026-09-21T00:00:00Z",
    )
    experimental = build_learning_result(
        raw_observations=randomized,
        experiment_records=records,
        authority=authority,
        policy=policy,
        baseline=randomized_baseline,
        as_of="2026-10-04T00:00:00Z",
        exploration_seed="r31-randomized-seed",
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    rejected: dict[str, dict[str, str]] = {}
    def reject(name: str, fn) -> None:
        try:
            fn()
        except Exception as exc:
            rejected[name] = {
                "state": "REJECTED",
                "reason": type(exc).__name__,
                "detail": str(exc),
            }
        else:
            raise AssertionError(f"adversarial case did not reject: {name}")

    duplicate = copy.deepcopy(observations[:3])
    duplicate[1]["publish_result"]["post_id"] = duplicate[0]["publish_result"]["post_id"]
    # Rebuilding IDs would make this a different receipt; direct duplicate evidence is clearer.
    duplicate[1] = copy.deepcopy(duplicate[0])
    duplicate[1]["observation_id"] = "duplicate-post-new-id"
    reject(
        "duplicate_provider_post",
        lambda: validate_dataset(duplicate, authority=authority, policy=policy),
    )
    replay = copy.deepcopy(observations[:3])
    replay[1]["metric_snapshot"] = copy.deepcopy(replay[0]["metric_snapshot"])
    replay[1]["publish_result"] = copy.deepcopy(replay[0]["publish_result"])
    replay[1]["lineage"]["external_post_id"] = replay[0]["lineage"]["external_post_id"]
    replay[1]["lineage"]["account_pseudonym"] = replay[0]["lineage"]["account_pseudonym"]
    replay[1]["lineage"]["winner_render_sha256"] = replay[0]["lineage"]["winner_render_sha256"]
    reject(
        "snapshot_replay_new_id",
        lambda: validate_dataset(replay, authority=authority, policy=policy),
    )
    changed = copy.deepcopy(observations[0])
    changed["lineage"]["winner_render_sha256"] = "0" * 64
    reject(
        "changed_winner_hash",
        lambda: normalize_observation(changed, authority=authority, policy=policy),
    )
    mixed = copy.deepcopy(observations[:3])
    mixed[2] = _fixture_observation(
        index=999,
        strategy="fast_hook",
        completion=0.6,
        views=1000,
        window_class="mature",
    )
    reject(
        "mixed_1h_7d_windows",
        lambda: validate_dataset(mixed, authority=authority, policy=policy),
    )
    stale_schema = copy.deepcopy(observations[0])
    stale_schema["metric_definition"]["provider_schema_version"] = "instagram-insights-v25.0"
    reject(
        "stale_platform_definition",
        lambda: normalize_observation(stale_schema, authority=authority, policy=policy),
    )
    contaminated = copy.deepcopy(observations[:3])
    contaminated[2] = _fixture_observation(
        index=998,
        strategy="fast_hook",
        completion=0.6,
        views=1000,
        account="other-account",
    )
    reject(
        "cross_account_contamination",
        lambda: validate_dataset(contaminated, authority=authority, policy=policy),
    )
    bad_experiment = copy.deepcopy(records)
    bad_experiment[0]["randomization_method"] = "historical_best"
    reject(
        "historical_comparison_mislabeled_random",
        lambda: validate_experiment_records(
            bad_experiment,
            observations=validate_dataset(randomized, authority=authority, policy=policy),
            policy=policy,
        ),
    )
    leaked = copy.deepcopy(records)
    leaked[0]["treatment_visible_before_assignment"] = True
    reject(
        "treatment_leakage",
        lambda: validate_experiment_records(
            leaked,
            observations=validate_dataset(randomized, authority=authority, policy=policy),
            policy=policy,
        ),
    )
    posthoc = copy.deepcopy(records)
    posthoc[0]["posthoc_window_selected"] = True
    reject(
        "posthoc_window_selection",
        lambda: validate_experiment_records(
            posthoc,
            observations=validate_dataset(randomized, authority=authority, policy=policy),
            policy=policy,
        ),
    )
    report = {
        "report_version": REPORT_VERSION,
        "status": "WAITING_PARENT_QA",
        "parent_qa_state": "WAITING_PARENT_QA",
        "authoritative_integration": False,
        "observational_case": {
            "evidence_state": observational["evidence_state"],
            "status": observational["status"],
            "suggestion_digest": observational["suggestion_digest"],
            "top_strategy": (
                observational["ranked_strategy_priors"][0]["strategy_id"]
                if observational["ranked_strategy_priors"]
                else None
            ),
            "outlier_count": sum(
                row["outlier_count"]
                for row in observational["observational_strategy_evidence"]
            ),
            "observational_is_causal": False,
        },
        "randomized_case": {
            "evidence_state": experimental["evidence_state"],
            "status": experimental["status"],
            "suggestion_digest": experimental["suggestion_digest"],
            "experiment": experimental["randomized_experiment_evidence"],
            "causally_proven": False,
        },
        "rejected_cases": rejected,
        "forbidden_conclusions": list(FORBIDDEN_CONCLUSIONS),
        "no_network": True,
        "no_live_publish": True,
        "human_ground_truth": False,
    }
    report["report_digest"] = sha256_json(report)
    return _clone(report)


def _load_observation_dir(path: Path) -> list[dict[str, Any]]:
    root = Path(path)
    files = sorted(root.glob("observation-*.json"))
    if not files:
        raise R31Error("observation directory has no observation-*.json files")
    return [_load(file) for file in files]


def _load_experiment_dir(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    files = sorted(Path(path).glob("exposure-*.json"))
    return [_load(file) for file in files]


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-postpublish-learning-r31")
    sub = parser.add_subparsers(dest="command", required=True)
    learn = sub.add_parser("learn")
    learn.add_argument("--observations-dir", required=True)
    learn.add_argument("--experiment-dir")
    learn.add_argument("--baseline")
    learn.add_argument("--authority", required=True)
    learn.add_argument("--policy", required=True)
    learn.add_argument("--out-dir", required=True)
    learn.add_argument("--as-of", required=True)
    learn.add_argument("--exploration-seed", required=True)
    learn.add_argument("--growth-sha", required=True)
    learn.add_argument("--growth-ci-run-id", type=int, required=True)
    rehearsal = sub.add_parser("rehearse-fixtures")
    rehearsal.add_argument("--authority", required=True)
    rehearsal.add_argument("--policy", required=True)
    rehearsal.add_argument("--out-dir", required=True)
    rehearsal.add_argument("--growth-sha", required=True)
    rehearsal.add_argument("--growth-ci-run-id", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    authority = _load(Path(args.authority))
    policy = _load(Path(args.policy))
    out = Path(args.out_dir)
    try:
        if args.command == "learn":
            result = build_learning_result(
                raw_observations=_load_observation_dir(Path(args.observations_dir)),
                experiment_records=_load_experiment_dir(
                    None if args.experiment_dir is None else Path(args.experiment_dir)
                ),
                authority=authority,
                policy=policy,
                baseline=(
                    None if args.baseline is None else _load(Path(args.baseline))
                ),
                as_of=args.as_of,
                exploration_seed=args.exploration_seed,
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            _write(out / "policy-suggestion.json", result)
            report = {
                "report_version": REPORT_VERSION,
                "status": result["status"],
                "evidence_state": result["evidence_state"],
                "suggestion_digest": result["suggestion_digest"],
                "parent_qa_state": result["growth_r31"]["parent_qa_state"],
                "authoritative_integration": result["authoritative_integration"],
                "observational_is_causal": False,
                "causally_proven": False,
                "human_ground_truth": False,
                "platform_api_call": False,
                "live_publish": False,
            }
            report["report_digest"] = sha256_json(report)
            _write(out / "readiness.json", report)
        else:
            report = build_rehearsal(
                authority=authority,
                policy=policy,
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            _write(out / "rehearsal.json", report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as exc:
        blocked = {
            "report_version": REPORT_VERSION,
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": type(exc).__name__,
            "detail": str(exc),
            "parent_qa_state": "WAITING_PARENT_QA",
            "authoritative_integration": False,
            "causally_proven": False,
            "human_ground_truth": False,
            "platform_api_call": False,
            "live_publish": False,
        }
        blocked["report_digest"] = sha256_json(blocked)
        _write(out / "readiness.json", blocked)
        print(json.dumps(blocked, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
