from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .event_stream import parse_timestamp

CONTRACT_VERSION = "growth.sequential_experiment_policy.r32.v1"
AUTHORITY_VERSION = "growth.sequential_experiment_authority.r32.v1"
POLICY_VERSION = "growth.sequential_experiment_policy_config.r32.v1"
CAMPAIGN_VERSION = "growth.sequential_experiment_campaign.r32.v1"
EVENT_VERSION = "growth.sequential_experiment_event.r32.v1"
DECISION_VERSION = "growth.sequential_experiment_decision.r32.v1"
REPORT_VERSION = "growth.sequential_experiment_policy.r32.rehearsal.v1"

PARENT_SHA = "311606b677d6f0d97669c905265f1eb64b7ff9a4"
PARENT_CI = 37204153689
PARENT_ARTIFACT_ID = 11304006807
PARENT_ARTIFACT_DIGEST = (
    "sha256:fab2ab2df9a4c8106352c5e6c17da3b3281f15a2b0e2015ec285304b2070334d"
)
PARENT_CONTRACT = "growth.postpublish_learning.r31.v1"

MODES = {"randomized_controlled", "observational_monitoring"}
RECOMMENDATIONS = {
    "KEEP",
    "TEST_MORE",
    "ROLLBACK_RECOMMENDED",
    "PROMOTE_CANDIDATE",
    "HUMAN_REVIEW_REQUIRED",
}
STATUS = "SOURCE_READY_WAITING_PARENT_QA"
POLICY_STALE = "STALE_POLICY_REVIEW_REQUIRED"
POLICY_CURRENT = "POLICY_CURRENT"


class R32Error(ValueError):
    pass


class AuthorityDrift(R32Error):
    pass


class PolicyConflict(R32Error):
    pass


class EvidenceConflict(R32Error):
    pass


class SequentialBoundaryViolation(R32Error):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise R32Error(f"{field} must be non-empty string")
    return value


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R32Error(f"{field} must be lowercase SHA-256")
    return value


def _git_sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R32Error(f"{field} must be exact Git SHA")
    return value


def _number(value: Any, field: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R32Error(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise R32Error(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise R32Error(f"{field} below minimum")
    if maximum is not None and result > maximum:
        raise R32Error(f"{field} above maximum")
    return result


def default_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.sequential_experiment_policy.r32.v1"
        / "authority.json"
    )


def default_policy() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.sequential_experiment_policy.r32.v1"
        / "policy.json"
    )


def validate_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "growth_r31_parent",
        "metric_contracts",
        "evidence_boundary",
    }:
        raise AuthorityDrift("R32 authority fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R32 authority version mismatch")
    if value["growth_r31_parent"] != {
        "repository": "foto6/video3",
        "producer_sha": PARENT_SHA,
        "ci_run_id": PARENT_CI,
        "artifact_id": PARENT_ARTIFACT_ID,
        "artifact_name": "growth-r31-postpublish-learning-policy",
        "artifact_digest": PARENT_ARTIFACT_DIGEST,
        "contract": PARENT_CONTRACT,
        "qa_state": "WAITING_QA_R3",
        "authoritative_integration_allowed": False,
    }:
        raise AuthorityDrift("exact R31 parent authority drift")
    if value["metric_contracts"] != {
        "publish_result": "growth.shortform_publish_result.v1",
        "metric_snapshot": "growth.shortform_metric_snapshot.v1",
        "r31_observation": "growth.postpublish_observation.r31.v1",
    }:
        raise AuthorityDrift("metric contract authority drift")
    if value["evidence_boundary"] != {
        "randomized_and_observational_combined_into_one_causal_estimate": False,
        "observational_is_causal": False,
        "model_review_is_human_ground_truth": False,
        "human_ground_truth": False,
        "automatic_creator_mutation": False,
        "browser_or_provider_mutation": False,
        "publish": False,
        "merge": False,
    }:
        raise AuthorityDrift("R32 evidence boundary drift")
    return _clone(value)


def validate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("contract_version") != POLICY_VERSION:
        raise PolicyConflict("R32 policy contract mismatch")
    expected_keys = {
        "contract_version",
        "policy_version",
        "primary_metric",
        "guardrail_metrics",
        "minimum_exposures_per_arm",
        "maximum_exposures_per_arm",
        "maximum_horizon_exposures",
        "look_fractions",
        "alpha_total",
        "alpha_spending_cumulative",
        "multiple_comparison_method",
        "minimum_effect_size",
        "rollback_effect_size",
        "guardrail_max_relative_degradation",
        "sample_ratio_mismatch_z_threshold",
        "severe_imbalance_min_arm_share",
        "late_metric_tolerance_seconds",
        "drift",
        "observational",
        "stopping",
    }
    if set(value) != expected_keys:
        raise PolicyConflict("R32 policy fields invalid")
    if value["policy_version"] != 1:
        raise PolicyConflict("policy version drift")
    if value["primary_metric"] != "completion_rate":
        raise PolicyConflict("primary metric drift")
    if value["guardrail_metrics"] != ["share_rate", "comment_rate"]:
        raise PolicyConflict("guardrail metric drift")
    if value["look_fractions"] != [0.25, 0.5, 0.75, 1.0]:
        raise PolicyConflict("look fractions drift")
    if value["alpha_spending_cumulative"] != [0.005, 0.0125, 0.025, 0.05]:
        raise PolicyConflict("alpha spending drift")
    if value["alpha_total"] != 0.05:
        raise PolicyConflict("alpha budget drift")
    if value["multiple_comparison_method"] != "bonferroni_candidates_x_tested_metrics":
        raise PolicyConflict("multiple comparison method drift")
    if value["minimum_exposures_per_arm"] != 20:
        raise PolicyConflict("minimum exposure gate drift")
    if value["maximum_exposures_per_arm"] != 80:
        raise PolicyConflict("maximum per-arm horizon drift")
    if value["maximum_horizon_exposures"] != 320:
        raise PolicyConflict("maximum horizon drift")
    if value["stopping"] != {
        "promote_requires_adjusted_p_below_boundary": True,
        "promote_requires_effect_at_least_minimum": True,
        "rollback_requires_adjusted_p_below_boundary": True,
        "maximum_one_decision_per_declared_look": True,
        "undeclared_look_is_conflict": True,
    }:
        raise PolicyConflict("stopping boundary policy drift")
    return _clone(value)


def authority_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority(value))


def policy_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_policy(value))


def build_campaign(
    *,
    campaign_id: str,
    mode: str,
    session_id: str,
    policy: Mapping[str, Any],
    declared_candidates: Sequence[str],
    control_candidate_id: str,
    metric_schema_hash: str,
    metric_definition_hash: str,
    metric_window_seconds: int,
    training_distribution: Mapping[str, Any],
) -> dict[str, Any]:
    policy = validate_policy(policy)
    if mode not in MODES:
        raise PolicyConflict("unsupported evidence mode")
    campaign_id = _nonempty(campaign_id, "campaign_id")
    session_id = _nonempty(session_id, "session_id")
    candidates = sorted(set(declared_candidates))
    if len(candidates) != len(declared_candidates) or len(candidates) < 2:
        raise PolicyConflict("declared candidates must be unique and >=2")
    if control_candidate_id not in candidates:
        raise PolicyConflict("control candidate missing")
    if len(candidates) > 3:
        raise PolicyConflict("R32 bounded candidate count is <=3")
    _sha(metric_schema_hash, "metric_schema_hash")
    _sha(metric_definition_hash, "metric_definition_hash")
    if (
        isinstance(metric_window_seconds, bool)
        or not isinstance(metric_window_seconds, int)
        or metric_window_seconds < 1
    ):
        raise PolicyConflict("metric_window_seconds invalid")
    if not isinstance(training_distribution, Mapping) or set(training_distribution) != {
        "platform_distribution",
        "topic_distribution",
        "metric_schema_hash",
        "metric_definition_hash",
    }:
        raise PolicyConflict("training distribution invalid")
    if training_distribution["metric_schema_hash"] != metric_schema_hash:
        raise PolicyConflict("training metric schema hash mismatch")
    if training_distribution["metric_definition_hash"] != metric_definition_hash:
        raise PolicyConflict("training metric definition hash mismatch")

    material = {
        "contract_version": CAMPAIGN_VERSION,
        "campaign_id": campaign_id,
        "campaign_digest": "",
        "mode": mode,
        "session_id": session_id,
        "policy_version": policy["policy_version"],
        "policy_digest": policy_digest(policy),
        "primary_metric": policy["primary_metric"],
        "guardrail_metrics": list(policy["guardrail_metrics"]),
        "declared_candidates": candidates,
        "control_candidate_id": control_candidate_id,
        "declared_look_fractions": list(policy["look_fractions"]),
        "maximum_horizon_exposures": policy["maximum_horizon_exposures"],
        "metric_schema_hash": metric_schema_hash,
        "metric_definition_hash": metric_definition_hash,
        "metric_window_seconds": metric_window_seconds,
        "training_distribution": _clone(training_distribution),
        "parent_r31_sha": PARENT_SHA,
    }
    digest_material = copy.deepcopy(material)
    digest_material["campaign_digest"] = ""
    material["campaign_digest"] = sha256_json(digest_material)
    return _clone(material)


def parse_campaign(value: Mapping[str, Any], *, policy: Mapping[str, Any]) -> dict[str, Any]:
    policy = validate_policy(policy)
    expected = {
        "contract_version",
        "campaign_id",
        "campaign_digest",
        "mode",
        "session_id",
        "policy_version",
        "policy_digest",
        "primary_metric",
        "guardrail_metrics",
        "declared_candidates",
        "control_candidate_id",
        "declared_look_fractions",
        "maximum_horizon_exposures",
        "metric_schema_hash",
        "metric_definition_hash",
        "metric_window_seconds",
        "training_distribution",
        "parent_r31_sha",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PolicyConflict("campaign fields invalid")
    rebuilt = build_campaign(
        campaign_id=value["campaign_id"],
        mode=value["mode"],
        session_id=value["session_id"],
        policy=policy,
        declared_candidates=value["declared_candidates"],
        control_candidate_id=value["control_candidate_id"],
        metric_schema_hash=value["metric_schema_hash"],
        metric_definition_hash=value["metric_definition_hash"],
        metric_window_seconds=value["metric_window_seconds"],
        training_distribution=value["training_distribution"],
    )
    if rebuilt != value:
        raise PolicyConflict("campaign digest/policy/version binding mismatch")
    return rebuilt


def _event_digest(material: Mapping[str, Any]) -> str:
    copy_value = copy.deepcopy(material)
    copy_value["event_digest"] = ""
    return sha256_json(copy_value)


def build_event(
    *,
    campaign: Mapping[str, Any],
    policy: Mapping[str, Any],
    event_id: str,
    candidate_id: str,
    publish_transaction_id: str,
    creator_session_id: str,
    published_candidate_id: str,
    published_render_sha256: str,
    exposure_id: str,
    exposure_at: str,
    metric_captured_at: str,
    platform: str,
    account_pseudonym: str,
    topic_cluster: str,
    source_sha256: str,
    primary_value: float | None,
    guardrails: Mapping[str, float | None],
    metric_schema_hash: str,
    metric_definition_hash: str,
    randomized: Mapping[str, Any] | None,
    fixture: bool,
) -> dict[str, Any]:
    campaign = parse_campaign(campaign, policy=policy)
    if candidate_id not in campaign["declared_candidates"]:
        raise EvidenceConflict("candidate not declared")
    for value, field in (
        (event_id, "event_id"),
        (publish_transaction_id, "publish_transaction_id"),
        (creator_session_id, "creator_session_id"),
        (published_candidate_id, "published_candidate_id"),
        (exposure_id, "exposure_id"),
        (platform, "platform"),
        (account_pseudonym, "account_pseudonym"),
        (topic_cluster, "topic_cluster"),
    ):
        _nonempty(value, field)
    _sha(published_render_sha256, "published_render_sha256")
    _sha(source_sha256, "source_sha256")
    _sha(metric_schema_hash, "metric_schema_hash")
    _sha(metric_definition_hash, "metric_definition_hash")
    parse_timestamp(exposure_at)
    parse_timestamp(metric_captured_at)
    if primary_value is not None:
        primary_value = round(_number(primary_value, "primary_value", 0.0, 1.0), 8)
    if not isinstance(guardrails, Mapping) or set(guardrails) != set(
        campaign["guardrail_metrics"]
    ):
        raise EvidenceConflict("guardrail metric set changed")
    normalized_guardrails = {}
    missing = []
    for name in campaign["guardrail_metrics"]:
        value = guardrails[name]
        if value is None:
            normalized_guardrails[name] = None
            missing.append(name)
        else:
            normalized_guardrails[name] = round(
                _number(value, f"guardrail.{name}", 0.0, 1.0),
                8,
            )
    if primary_value is None:
        missing.append(campaign["primary_metric"])

    if campaign["mode"] == "randomized_controlled":
        required = {
            "assignment_id",
            "assignment_digest",
            "randomization_seed",
            "assigned_candidate_id",
            "treatment_visible_before_assignment",
            "exposure_leakage",
        }
        if not isinstance(randomized, Mapping) or set(randomized) != required:
            raise EvidenceConflict("randomized evidence fields invalid")
        if randomized["assigned_candidate_id"] != candidate_id:
            raise EvidenceConflict("assignment candidate mismatch")
        if randomized["treatment_visible_before_assignment"] is not False:
            raise EvidenceConflict("treatment leakage")
        if randomized["exposure_leakage"] is not False:
            raise EvidenceConflict("exposure leakage")
        assignment_material = {
            "campaign_id": campaign["campaign_id"],
            "assignment_id": randomized["assignment_id"],
            "exposure_id": exposure_id,
            "seed": randomized["randomization_seed"],
            "candidate_id": candidate_id,
        }
        if randomized["assignment_digest"] != sha256_json(assignment_material):
            raise EvidenceConflict("assignment digest mismatch")
        randomized_out: dict[str, Any] | None = _clone(randomized)
    else:
        if randomized is not None:
            raise EvidenceConflict("observational event cannot carry random assignment")
        randomized_out = None

    material = {
        "contract_version": EVENT_VERSION,
        "event_id": event_id,
        "event_digest": "",
        "campaign_id": campaign["campaign_id"],
        "campaign_digest": campaign["campaign_digest"],
        "mode": campaign["mode"],
        "candidate_id": candidate_id,
        "publish_transaction_id": publish_transaction_id,
        "creator_session_id": creator_session_id,
        "published_candidate_id": published_candidate_id,
        "published_render_sha256": published_render_sha256,
        "exposure_id": exposure_id,
        "exposure_at": exposure_at,
        "metric_captured_at": metric_captured_at,
        "platform": platform,
        "account_pseudonym": account_pseudonym,
        "topic_cluster": topic_cluster,
        "source_sha256": source_sha256,
        "policy_version": campaign["policy_version"],
        "policy_digest": campaign["policy_digest"],
        "metric_schema_hash": metric_schema_hash,
        "metric_definition_hash": metric_definition_hash,
        "primary_metric": campaign["primary_metric"],
        "primary_value": primary_value,
        "guardrails": normalized_guardrails,
        "missing_metrics": sorted(missing),
        "randomized": randomized_out,
        "fixture": bool(fixture),
    }
    material["event_digest"] = _event_digest(material)
    return _clone(material)


def parse_event(
    value: Mapping[str, Any],
    *,
    campaign: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    campaign = parse_campaign(campaign, policy=policy)
    expected = {
        "contract_version",
        "event_id",
        "event_digest",
        "campaign_id",
        "campaign_digest",
        "mode",
        "candidate_id",
        "publish_transaction_id",
        "creator_session_id",
        "published_candidate_id",
        "published_render_sha256",
        "exposure_id",
        "exposure_at",
        "metric_captured_at",
        "platform",
        "account_pseudonym",
        "topic_cluster",
        "source_sha256",
        "policy_version",
        "policy_digest",
        "metric_schema_hash",
        "metric_definition_hash",
        "primary_metric",
        "primary_value",
        "guardrails",
        "missing_metrics",
        "randomized",
        "fixture",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise EvidenceConflict("event fields invalid")
    if value["contract_version"] != EVENT_VERSION:
        raise EvidenceConflict("event contract mismatch")
    if value["event_digest"] != _event_digest(value):
        raise EvidenceConflict("event digest mismatch")
    if (
        value["campaign_id"] != campaign["campaign_id"]
        or value["campaign_digest"] != campaign["campaign_digest"]
        or value["mode"] != campaign["mode"]
        or value["policy_version"] != campaign["policy_version"]
        or value["policy_digest"] != campaign["policy_digest"]
        or value["primary_metric"] != campaign["primary_metric"]
    ):
        raise EvidenceConflict("event campaign/policy binding mismatch")
    if value["metric_schema_hash"] != campaign["metric_schema_hash"]:
        raise EvidenceConflict("metric schema drift")
    if value["metric_definition_hash"] != campaign["metric_definition_hash"]:
        raise EvidenceConflict("metric-definition change")
    return build_event(
        campaign=campaign,
        policy=policy,
        event_id=value["event_id"],
        candidate_id=value["candidate_id"],
        publish_transaction_id=value["publish_transaction_id"],
        creator_session_id=value["creator_session_id"],
        published_candidate_id=value["published_candidate_id"],
        published_render_sha256=value["published_render_sha256"],
        exposure_id=value["exposure_id"],
        exposure_at=value["exposure_at"],
        metric_captured_at=value["metric_captured_at"],
        platform=value["platform"],
        account_pseudonym=value["account_pseudonym"],
        topic_cluster=value["topic_cluster"],
        source_sha256=value["source_sha256"],
        primary_value=value["primary_value"],
        guardrails=value["guardrails"],
        metric_schema_hash=value["metric_schema_hash"],
        metric_definition_hash=value["metric_definition_hash"],
        randomized=value["randomized"],
        fixture=value["fixture"],
    )


def _distribution(values: Sequence[str]) -> dict[str, float]:
    counts = Counter(values)
    total = len(values)
    if total == 0:
        return {}
    return {key: round(count / total, 8) for key, count in sorted(counts.items())}


def _tv(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = set(a) | set(b)
    return round(
        0.5 * sum(abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) for k in keys),
        8,
    )


def _normal_two_sided_p(z: float) -> float:
    return max(0.0, min(1.0, math.erfc(abs(z) / math.sqrt(2.0))))


def _welch_result(a: Sequence[float], b: Sequence[float]) -> dict[str, float | None]:
    if not a or not b:
        return {"difference": None, "z": None, "p_value": None}
    ma = statistics.mean(a)
    mb = statistics.mean(b)
    va = statistics.variance(a) if len(a) > 1 else 0.0
    vb = statistics.variance(b) if len(b) > 1 else 0.0
    se = math.sqrt(va / len(a) + vb / len(b))
    diff = ma - mb
    if se == 0:
        z = 0.0 if diff == 0 else (999.0 if diff > 0 else -999.0)
    else:
        z = diff / se
    return {
        "difference": round(diff, 8),
        "z": round(z, 8),
        "p_value": round(_normal_two_sided_p(z), 12),
    }


def _look_index(policy: Mapping[str, Any], look_fraction: float) -> int:
    looks = [float(v) for v in policy["look_fractions"]]
    try:
        return looks.index(float(look_fraction))
    except ValueError as exc:
        raise SequentialBoundaryViolation("additional look outside predeclared policy") from exc


def _validate_look_history(
    *,
    policy: Mapping[str, Any],
    look_fraction: float,
    prior_look_fractions: Sequence[float],
) -> int:
    idx = _look_index(policy, look_fraction)
    expected = policy["look_fractions"][:idx]
    if list(prior_look_fractions) != expected:
        raise SequentialBoundaryViolation(
            "repeated peeking or skipped/reordered look not covered by sequential boundary"
        )
    return idx


def _event_quality(
    events: Sequence[Mapping[str, Any]],
    *,
    campaign: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    ids: dict[str, str] = {}
    exposures: set[str] = set()
    publish_txns: set[str] = set()
    session_ids: set[str] = set()
    account_ids: set[str] = set()
    late = []
    missing = []
    for event in events:
        prior = ids.get(event["event_id"])
        if prior is not None:
            if prior != event["event_digest"]:
                raise EvidenceConflict("event identity changed bytes")
            raise EvidenceConflict("duplicate event identity")
        ids[event["event_id"]] = event["event_digest"]
        if event["exposure_id"] in exposures:
            raise EvidenceConflict("duplicate exposure identity")
        exposures.add(event["exposure_id"])
        if event["publish_transaction_id"] in publish_txns:
            raise EvidenceConflict("duplicate publish transaction/campaign identity")
        publish_txns.add(event["publish_transaction_id"])
        session_ids.add(event["creator_session_id"])
        account_ids.add(event["account_pseudonym"])
        if event["missing_metrics"]:
            missing.append(event["event_id"])
        deadline = (
            parse_timestamp(event["exposure_at"])
            + timedelta(
                seconds=campaign["metric_window_seconds"]
                + policy["late_metric_tolerance_seconds"]
            )
        )
        if parse_timestamp(event["metric_captured_at"]) > deadline:
            late.append(event["event_id"])
    if len(session_ids) != 1 or next(iter(session_ids)) != campaign["session_id"]:
        reasons.append("WRONG_OR_MIXED_SESSION")
    if len(account_ids) != 1:
        reasons.append("CROSS_ACCOUNT_CONTAMINATION")
    if missing:
        reasons.append("MISSING_METRICS")
    if late:
        reasons.append("LATE_METRICS")
    return reasons, {
        "missing_event_ids": sorted(missing),
        "late_event_ids": sorted(late),
    }


def _drift(
    events: Sequence[Mapping[str, Any]],
    *,
    campaign: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    ordered = sorted(events, key=lambda row: (parse_timestamp(row["exposure_at"]), row["event_id"]))
    split = max(1, len(ordered) // 2)
    first, second = ordered[:split], ordered[split:]
    primary_shift = 0.0
    if second:
        first_values = [e["primary_value"] for e in first if e["primary_value"] is not None]
        second_values = [e["primary_value"] for e in second if e["primary_value"] is not None]
        if first_values and second_values:
            primary_shift = abs(statistics.mean(first_values) - statistics.mean(second_values))
    platform_current = _distribution([e["platform"] for e in ordered])
    topic_current = _distribution([e["topic_cluster"] for e in ordered])
    training = campaign["training_distribution"]
    platform_tv = _tv(platform_current, training["platform_distribution"])
    topic_tv = _tv(topic_current, training["topic_distribution"])
    reasons = []
    if primary_shift > policy["drift"]["half_horizon_primary_mean_shift"]:
        reasons.append("NONSTATIONARITY_PRIMARY_SHIFT")
    if platform_tv > policy["drift"]["policy_stale_distribution_total_variation"]:
        reasons.append("PLATFORM_DISTRIBUTION_DRIFT")
    if topic_tv > policy["drift"]["policy_stale_distribution_total_variation"]:
        reasons.append("TOPIC_DISTRIBUTION_DRIFT")
    return {
        "policy_state": POLICY_STALE if reasons else POLICY_CURRENT,
        "reason_codes": sorted(reasons),
        "primary_half_shift": round(primary_shift, 8),
        "platform_total_variation": platform_tv,
        "topic_total_variation": topic_tv,
        "training_metric_schema_hash": training["metric_schema_hash"],
        "training_metric_definition_hash": training["metric_definition_hash"],
    }


def _srm(
    events: Sequence[Mapping[str, Any]],
    *,
    candidates: Sequence[str],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    counts = Counter(e["candidate_id"] for e in events)
    total = len(events)
    k = len(candidates)
    expected_share = 1.0 / k
    z_scores = {}
    severe = False
    for candidate in candidates:
        count = counts[candidate]
        share = count / total if total else 0.0
        sd = math.sqrt(max(1e-12, total * expected_share * (1.0 - expected_share)))
        z = (count - total * expected_share) / sd if total else 0.0
        z_scores[candidate] = round(z, 8)
        if share < policy["severe_imbalance_min_arm_share"]:
            severe = True
    mismatch = any(
        abs(z) > policy["sample_ratio_mismatch_z_threshold"]
        for z in z_scores.values()
    )
    return {
        "counts": {candidate: counts[candidate] for candidate in sorted(candidates)},
        "expected_share": round(expected_share, 8),
        "z_scores": z_scores,
        "sample_ratio_mismatch": mismatch,
        "severe_imbalance": severe,
    }


def _guardrail_result(
    *,
    candidate_events: Sequence[Mapping[str, Any]],
    control_events: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    max_relative_degradation: float,
) -> dict[str, Any]:
    rows = {}
    violation = False
    for name in names:
        ca = [e["guardrails"][name] for e in candidate_events if e["guardrails"][name] is not None]
        co = [e["guardrails"][name] for e in control_events if e["guardrails"][name] is not None]
        if len(ca) != len(candidate_events) or len(co) != len(control_events):
            rows[name] = {"state": "missing", "relative_change": None}
            violation = True
            continue
        mc = statistics.mean(ca)
        mb = statistics.mean(co)
        relative = 0.0 if mb == 0 and mc == 0 else (
            float("inf") if mb == 0 else (mc - mb) / mb
        )
        if relative < -max_relative_degradation:
            violation = True
        rows[name] = {
            "state": "available",
            "candidate_mean": round(mc, 8),
            "control_mean": round(mb, 8),
            "relative_change": (
                "inf" if not math.isfinite(relative) else round(relative, 8)
            ),
        }
    return {"metrics": rows, "violation": violation}


def evaluate(
    *,
    campaign: Mapping[str, Any],
    raw_events: Sequence[Mapping[str, Any]],
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    look_fraction: float,
    prior_look_fractions: Sequence[float],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    campaign = parse_campaign(campaign, policy=policy)
    _git_sha(growth_sha, "growth_sha")
    if isinstance(growth_ci_run_id, bool) or not isinstance(growth_ci_run_id, int) or growth_ci_run_id < 1:
        raise R32Error("growth_ci_run_id invalid")
    look_idx = _validate_look_history(
        policy=policy,
        look_fraction=look_fraction,
        prior_look_fractions=prior_look_fractions,
    )
    events = [
        parse_event(event, campaign=campaign, policy=policy)
        for event in raw_events
    ]
    events = sorted(
        events,
        key=lambda row: (
            row["candidate_id"],
            parse_timestamp(row["exposure_at"]),
            row["event_id"],
        ),
    )
    if not events:
        raise EvidenceConflict("no experiment events")

    quality_reasons, quality = _event_quality(
        events,
        campaign=campaign,
        policy=policy,
    )
    drift = _drift(events, campaign=campaign, policy=policy)
    reason_codes = list(quality_reasons)
    if drift["policy_state"] == POLICY_STALE:
        reason_codes.extend(drift["reason_codes"])

    total = len(events)
    expected_horizon = min(
        campaign["maximum_horizon_exposures"],
        policy["maximum_exposures_per_arm"] * len(campaign["declared_candidates"]),
    )
    required_at_look = max(
        len(campaign["declared_candidates"]) * policy["minimum_exposures_per_arm"],
        math.ceil(expected_horizon * float(look_fraction)),
    )
    if total < required_at_look:
        reason_codes.append("TINY_SAMPLE_OR_LOOK_NOT_REACHED")
    if total > campaign["maximum_horizon_exposures"]:
        reason_codes.append("MAX_HORIZON_EXCEEDED")

    result = {
        "contract_version": DECISION_VERSION,
        "decision_id": "",
        "decision_digest": "",
        "status": STATUS,
        "parent_qa_state": authority["growth_r31_parent"]["qa_state"],
        "authoritative_integration": False,
        "campaign_id": campaign["campaign_id"],
        "campaign_digest": campaign["campaign_digest"],
        "mode": campaign["mode"],
        "look_fraction": float(look_fraction),
        "look_index": look_idx,
        "prior_look_fractions": list(prior_look_fractions),
        "recommendation": "HUMAN_REVIEW_REQUIRED",
        "recommended_candidate_id": None,
        "confidence_summary": {},
        "evidence_summary": {
            "event_count": total,
            "event_digests": sorted(e["event_digest"] for e in events),
            "fixture_only": all(e["fixture"] for e in events),
            "quality": quality,
        },
        "reason_codes": [],
        "drift": drift,
        "randomized_result": None,
        "observational_result": None,
        "policy_state": drift["policy_state"],
        "causal_claim_allowed": False,
        "human_ground_truth": False,
        "model_review_is_human_ground_truth": False,
        "creator_mutation": False,
        "provider_publish": False,
    }

    if campaign["mode"] == "observational_monitoring":
        by_candidate: dict[str, list[float]] = defaultdict(list)
        for event in events:
            if event["primary_value"] is not None:
                by_candidate[event["candidate_id"]].append(float(event["primary_value"]))
        associations = []
        for candidate in campaign["declared_candidates"]:
            values = by_candidate[candidate]
            associations.append(
                {
                    "candidate_id": candidate,
                    "n": len(values),
                    "mean": None if not values else round(statistics.mean(values), 8),
                    "association_only": True,
                    "causal_winner": False,
                }
            )
        enough = all(
            row["n"] >= policy["observational"]["minimum_independent_posts"]
            for row in associations
        )
        if reason_codes:
            recommendation = "HUMAN_REVIEW_REQUIRED"
        elif enough:
            recommendation = "TEST_MORE"
            reason_codes.append("OBSERVATIONAL_ASSOCIATION_ONLY")
        else:
            recommendation = "TEST_MORE"
            reason_codes.append("INSUFFICIENT_OBSERVATIONAL_SAMPLE")
        result["recommendation"] = recommendation
        result["observational_result"] = {
            "candidate_associations": associations,
            "causal_winner": None,
            "observational_is_causal": False,
        }
        result["confidence_summary"] = {
            "mode": "association_only",
            "sequential_p_value": None,
            "causal_preference": False,
        }
    else:
        srm = _srm(
            events,
            candidates=campaign["declared_candidates"],
            policy=policy,
        )
        if srm["sample_ratio_mismatch"]:
            reason_codes.append("SAMPLE_RATIO_MISMATCH")
        if srm["severe_imbalance"]:
            reason_codes.append("SEVERE_IMBALANCE")

        control_id = campaign["control_candidate_id"]
        control = [e for e in events if e["candidate_id"] == control_id]
        candidates = [
            candidate
            for candidate in campaign["declared_candidates"]
            if candidate != control_id
        ]
        comparisons = len(candidates) * (
            1 + len(campaign["guardrail_metrics"])
        )
        boundary = policy["alpha_spending_cumulative"][look_idx]
        primary_rows = []
        best = None
        rollback = None
        for candidate in candidates:
            arm = [e for e in events if e["candidate_id"] == candidate]
            control_values = [
                float(e["primary_value"])
                for e in control
                if e["primary_value"] is not None
            ]
            arm_values = [
                float(e["primary_value"])
                for e in arm
                if e["primary_value"] is not None
            ]
            test = _welch_result(arm_values, control_values)
            raw_p = test["p_value"]
            adjusted = None if raw_p is None else min(1.0, float(raw_p) * comparisons)
            guards = _guardrail_result(
                candidate_events=arm,
                control_events=control,
                names=campaign["guardrail_metrics"],
                max_relative_degradation=policy["guardrail_max_relative_degradation"],
            )
            row = {
                "candidate_id": candidate,
                "candidate_n": len(arm),
                "control_n": len(control),
                "difference": test["difference"],
                "z": test["z"],
                "raw_p_value": raw_p,
                "adjusted_p_value": None if adjusted is None else round(adjusted, 12),
                "alpha_boundary": boundary,
                "multiple_comparison_count": comparisons,
                "guardrails": guards,
                "passes_boundary": bool(
                    adjusted is not None
                    and adjusted <= boundary
                    and len(arm) >= policy["minimum_exposures_per_arm"]
                    and len(control) >= policy["minimum_exposures_per_arm"]
                ),
            }
            primary_rows.append(row)
            if row["passes_boundary"] and not guards["violation"]:
                if (
                    row["difference"] is not None
                    and row["difference"] >= policy["minimum_effect_size"]
                ):
                    if best is None or row["difference"] > best["difference"]:
                        best = row
                if (
                    row["difference"] is not None
                    and row["difference"] <= policy["rollback_effect_size"]
                ):
                    if rollback is None or row["difference"] < rollback["difference"]:
                        rollback = row

        hard_reasons = set(reason_codes) & {
            "WRONG_OR_MIXED_SESSION",
            "CROSS_ACCOUNT_CONTAMINATION",
            "MISSING_METRICS",
            "LATE_METRICS",
            "TINY_SAMPLE_OR_LOOK_NOT_REACHED",
            "MAX_HORIZON_EXCEEDED",
            "SAMPLE_RATIO_MISMATCH",
            "SEVERE_IMBALANCE",
            "NONSTATIONARITY_PRIMARY_SHIFT",
            "PLATFORM_DISTRIBUTION_DRIFT",
            "TOPIC_DISTRIBUTION_DRIFT",
        }
        if hard_reasons:
            recommendation = "HUMAN_REVIEW_REQUIRED"
        elif rollback is not None:
            recommendation = "ROLLBACK_RECOMMENDED"
            result["recommended_candidate_id"] = rollback["candidate_id"]
            reason_codes.append("SEQUENTIAL_NEGATIVE_BOUNDARY_CROSSED")
            result["causal_claim_allowed"] = True
        elif best is not None:
            recommendation = "PROMOTE_CANDIDATE"
            result["recommended_candidate_id"] = best["candidate_id"]
            reason_codes.append("SEQUENTIAL_POSITIVE_BOUNDARY_CROSSED")
            result["causal_claim_allowed"] = True
        elif float(look_fraction) < 1.0:
            recommendation = "TEST_MORE"
            reason_codes.append("NO_STOPPING_BOUNDARY_CROSSED")
        else:
            recommendation = "KEEP"
            reason_codes.append("MAX_DECLARED_LOOK_WITHOUT_SUPPORTED_CHANGE")

        result["recommendation"] = recommendation
        result["randomized_result"] = {
            "sample_ratio": srm,
            "candidate_tests": primary_rows,
            "alpha_spending_boundary": boundary,
            "multiple_comparison_count": comparisons,
            "causal_preference_supported": recommendation
            in {"PROMOTE_CANDIDATE", "ROLLBACK_RECOMMENDED"},
            "causally_proven": False,
        }
        result["confidence_summary"] = {
            "mode": "randomized_controlled",
            "alpha_total": policy["alpha_total"],
            "alpha_spent_cumulative": boundary,
            "multiple_comparison_method": policy["multiple_comparison_method"],
            "causal_preference": result["randomized_result"][
                "causal_preference_supported"
            ],
            "causally_proven": False,
        }

    result["reason_codes"] = sorted(set(reason_codes))
    result["decision_id"] = "gr32d1:" + sha256_json(
        {
            "campaign_digest": campaign["campaign_digest"],
            "look_fraction": result["look_fraction"],
            "event_digests": result["evidence_summary"]["event_digests"],
            "policy_digest": campaign["policy_digest"],
        }
    )
    material = copy.deepcopy(result)
    material["decision_digest"] = ""
    result["decision_digest"] = sha256_json(material)
    if result["recommendation"] not in RECOMMENDATIONS:
        raise R32Error("invalid recommendation")
    return _clone(result)


class SequentialLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.state = {
            "campaigns": {},
            "events": {},
            "decisions": {},
        }
        if self.path.exists():
            self.state = _load(self.path)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def record_campaign(self, campaign: Mapping[str, Any]) -> bool:
        cid = campaign["campaign_id"]
        digest = campaign["campaign_digest"]
        previous = self.state["campaigns"].get(cid)
        if previous is not None:
            if previous != digest:
                raise EvidenceConflict("campaign identity changed bytes")
            return False
        self.state["campaigns"][cid] = digest
        self._save()
        return True

    def record_events(self, events: Sequence[Mapping[str, Any]]) -> int:
        added = 0
        for event in events:
            eid = event["event_id"]
            digest = event["event_digest"]
            previous = self.state["events"].get(eid)
            if previous is not None:
                if previous != digest:
                    raise EvidenceConflict("event replay changed bytes")
                continue
            self.state["events"][eid] = digest
            added += 1
        self._save()
        return added

    def record_decision(self, decision: Mapping[str, Any]) -> bool:
        key = f"{decision['campaign_id']}@{decision['look_fraction']}"
        digest = decision["decision_digest"]
        previous = self.state["decisions"].get(key)
        if previous is not None:
            if previous != digest:
                raise EvidenceConflict("same campaign/look changed decision bytes")
            return False
        self.state["decisions"][key] = digest
        self._save()
        return True


def _fixture_campaign(
    *,
    mode: str,
    policy: Mapping[str, Any],
    candidates: Sequence[str] = ("control", "candidate"),
) -> dict[str, Any]:
    schema_hash = hashlib.sha256(b"metric-schema-r32").hexdigest()
    definition_hash = hashlib.sha256(b"metric-definition-r32").hexdigest()
    return build_campaign(
        campaign_id=f"campaign-{mode}-{'-'.join(candidates)}",
        mode=mode,
        session_id="creator-session-r32",
        policy=policy,
        declared_candidates=list(candidates),
        control_candidate_id="control",
        metric_schema_hash=schema_hash,
        metric_definition_hash=definition_hash,
        metric_window_seconds=3600,
        training_distribution={
            "platform_distribution": {"instagram_reels": 1.0},
            "topic_distribution": {"topic-a": 1.0},
            "metric_schema_hash": schema_hash,
            "metric_definition_hash": definition_hash,
        },
    )


def _candidate_for_unit(seed: str, unit: str, candidates: Sequence[str]) -> str:
    scores = [
        (
            hashlib.sha256(f"{seed}|{unit}|{candidate}".encode("utf-8")).hexdigest(),
            candidate,
        )
        for candidate in candidates
    ]
    return min(scores)[1]


def _fixture_events(
    *,
    campaign: Mapping[str, Any],
    policy: Mapping[str, Any],
    values: Mapping[str, Sequence[float]],
    seed: str = "r32-seed",
    platform_by_index: Mapping[int, str] | None = None,
    topic_by_index: Mapping[int, str] | None = None,
    missing_index: int | None = None,
    late_index: int | None = None,
    leak_index: int | None = None,
) -> list[dict[str, Any]]:
    rows = []
    counters = {candidate: 0 for candidate in campaign["declared_candidates"]}
    sequences = {candidate: list(seq) for candidate, seq in values.items()}
    target_total = sum(len(seq) for seq in sequences.values())
    index = 0
    # Deterministic balanced assignment for fixtures; randomized digest still binds.
    while len(rows) < target_total:
        for candidate in campaign["declared_candidates"]:
            if counters[candidate] >= len(sequences[candidate]):
                continue
            unit = f"unit-{index}"
            exposure = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
            captured = exposure + timedelta(seconds=campaign["metric_window_seconds"])
            if late_index == index:
                captured += timedelta(seconds=policy["late_metric_tolerance_seconds"] + 1)
            primary = sequences[candidate][counters[candidate]]
            guardrails = {"share_rate": 0.04, "comment_rate": 0.02}
            if missing_index == index:
                guardrails["comment_rate"] = None
            randomized = None
            if campaign["mode"] == "randomized_controlled":
                assignment_id = f"assignment-{index}"
                exposure_id = f"exposure-{index}"
                assignment_material = {
                    "campaign_id": campaign["campaign_id"],
                    "assignment_id": assignment_id,
                    "exposure_id": exposure_id,
                    "seed": seed,
                    "candidate_id": candidate,
                }
                randomized = {
                    "assignment_id": assignment_id,
                    "assignment_digest": sha256_json(assignment_material),
                    "randomization_seed": seed,
                    "assigned_candidate_id": candidate,
                    "treatment_visible_before_assignment": False,
                    "exposure_leakage": leak_index == index,
                }
            else:
                exposure_id = f"exposure-{index}"
            row = build_event(
                campaign=campaign,
                policy=policy,
                event_id=f"event-{index}",
                candidate_id=candidate,
                publish_transaction_id=f"publish-txn-{index}",
                creator_session_id=campaign["session_id"],
                published_candidate_id=f"published-{candidate}-{index}",
                published_render_sha256=hashlib.sha256(
                    f"render-{candidate}-{index}".encode("utf-8")
                ).hexdigest(),
                exposure_id=exposure_id,
                exposure_at=exposure.isoformat().replace("+00:00", "Z"),
                metric_captured_at=captured.isoformat().replace("+00:00", "Z"),
                platform=(
                    platform_by_index.get(index, "instagram_reels")
                    if platform_by_index
                    else "instagram_reels"
                ),
                account_pseudonym="acct-r32",
                topic_cluster=(
                    topic_by_index.get(index, "topic-a")
                    if topic_by_index
                    else "topic-a"
                ),
                source_sha256=hashlib.sha256(
                    f"source-{index}".encode("utf-8")
                ).hexdigest(),
                primary_value=primary,
                guardrails=guardrails,
                metric_schema_hash=campaign["metric_schema_hash"],
                metric_definition_hash=campaign["metric_definition_hash"],
                randomized=randomized,
                fixture=True,
            )
            rows.append(row)
            counters[candidate] += 1
            index += 1
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

    accepted: dict[str, Any] = {}
    rejected: dict[str, Any] = {}

    true_campaign = _fixture_campaign(mode="randomized_controlled", policy=policy)
    true_events = _fixture_events(
        campaign=true_campaign,
        policy=policy,
        values={
            "control": [0.35 + (i % 3) * 0.002 for i in range(80)],
            "candidate": [0.55 + (i % 3) * 0.002 for i in range(80)],
        },
    )
    accepted["true_positive_uplift"] = evaluate(
        campaign=true_campaign,
        raw_events=true_events,
        authority=authority,
        policy=policy,
        look_fraction=1.0,
        prior_look_fractions=[0.25, 0.5, 0.75],
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    null_campaign = _fixture_campaign(mode="randomized_controlled", policy=policy)
    null_events = _fixture_events(
        campaign=null_campaign,
        policy=policy,
        values={
            "control": [0.45 + (i % 4) * 0.001 for i in range(80)],
            "candidate": [0.45 + ((i + 2) % 4) * 0.001 for i in range(80)],
        },
    )
    accepted["null_effect"] = evaluate(
        campaign=null_campaign,
        raw_events=null_events,
        authority=authority,
        policy=policy,
        look_fraction=1.0,
        prior_look_fractions=[0.25, 0.5, 0.75],
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    reverse_campaign = _fixture_campaign(mode="randomized_controlled", policy=policy)
    reverse_early = _fixture_events(
        campaign=reverse_campaign,
        policy=policy,
        values={
            "control": [0.45 + (i % 3) * 0.01 for i in range(20)],
            "candidate": [0.49 + (i % 3) * 0.01 for i in range(20)],
        },
    )
    accepted["early_noisy_uplift"] = evaluate(
        campaign=reverse_campaign,
        raw_events=reverse_early,
        authority=authority,
        policy=policy,
        look_fraction=0.25,
        prior_look_fractions=[],
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    reverse_full = _fixture_events(
        campaign=reverse_campaign,
        policy=policy,
        values={
            "control": (
                [0.45 + (i % 3) * 0.01 for i in range(20)]
                + [0.60 + (i % 3) * 0.005 for i in range(60)]
            ),
            "candidate": (
                [0.49 + (i % 3) * 0.01 for i in range(20)]
                + [0.43 + (i % 3) * 0.005 for i in range(60)]
            ),
        },
    )
    accepted["early_uplift_reverses"] = evaluate(
        campaign=reverse_campaign,
        raw_events=reverse_full,
        authority=authority,
        policy=policy,
        look_fraction=1.0,
        prior_look_fractions=[0.25, 0.5, 0.75],
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    obs_campaign = _fixture_campaign(mode="observational_monitoring", policy=policy)
    obs_events = _fixture_events(
        campaign=obs_campaign,
        policy=policy,
        values={
            "control": [0.30 + i * 0.001 for i in range(20)],
            "candidate": [0.75 + i * 0.001 for i in range(20)],
        },
    )
    accepted["observational_confounding"] = evaluate(
        campaign=obs_campaign,
        raw_events=obs_events,
        authority=authority,
        policy=policy,
        look_fraction=0.25,
        prior_look_fractions=[],
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    multi_campaign = _fixture_campaign(
        mode="randomized_controlled",
        policy=policy,
        candidates=("control", "candidate-a", "candidate-b"),
    )
    multi_events = _fixture_events(
        campaign=multi_campaign,
        policy=policy,
        values={
            "control": [0.50 + (i % 5) * 0.002 for i in range(80)],
            "candidate-a": [0.515 + (i % 5) * 0.002 for i in range(80)],
            "candidate-b": [0.516 + ((i + 2) % 5) * 0.002 for i in range(80)],
        },
    )
    accepted["multiple_candidates_correction"] = evaluate(
        campaign=multi_campaign,
        raw_events=multi_events,
        authority=authority,
        policy=policy,
        look_fraction=1.0,
        prior_look_fractions=[0.25, 0.5, 0.75],
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    def reject(name: str, fn) -> None:
        try:
            value = fn()
        except Exception as exc:
            rejected[name] = {
                "state": "REJECTED",
                "reason": type(exc).__name__,
                "detail": str(exc),
            }
        else:
            if value["recommendation"] == "HUMAN_REVIEW_REQUIRED":
                rejected[name] = {
                    "state": "REJECTED",
                    "reason": "HUMAN_REVIEW_REQUIRED",
                    "detail": ",".join(value["reason_codes"]),
                    "decision_digest": value["decision_digest"],
                }
            else:
                raise AssertionError(f"adversarial case did not reject: {name}")

    srm_campaign = _fixture_campaign(mode="randomized_controlled", policy=policy)
    srm_events = _fixture_events(
        campaign=srm_campaign,
        policy=policy,
        values={
            "control": [0.45 for _ in range(70)],
            "candidate": [0.60 for _ in range(20)],
        },
    )
    reject(
        "sample_ratio_mismatch",
        lambda: evaluate(
            campaign=srm_campaign,
            raw_events=srm_events,
            authority=authority,
            policy=policy,
            look_fraction=0.5,
            prior_look_fractions=[0.25],
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    missing_campaign = _fixture_campaign(mode="randomized_controlled", policy=policy)
    missing_events = _fixture_events(
        campaign=missing_campaign,
        policy=policy,
        values={"control": [0.45] * 40, "candidate": [0.60] * 40},
        missing_index=3,
    )
    reject(
        "missing_metrics",
        lambda: evaluate(
            campaign=missing_campaign,
            raw_events=missing_events,
            authority=authority,
            policy=policy,
            look_fraction=0.5,
            prior_look_fractions=[0.25],
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    drift_campaign = _fixture_campaign(mode="randomized_controlled", policy=policy)
    drift_events = _fixture_events(
        campaign=drift_campaign,
        policy=policy,
        values={
            "control": [0.30] * 40 + [0.65] * 40,
            "candidate": [0.55] * 40 + [0.56] * 40,
        },
    )
    reject(
        "drift_after_half_horizon",
        lambda: evaluate(
            campaign=drift_campaign,
            raw_events=drift_events,
            authority=authority,
            policy=policy,
            look_fraction=1.0,
            prior_look_fractions=[0.25, 0.5, 0.75],
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    leak_campaign = _fixture_campaign(mode="randomized_controlled", policy=policy)
    try:
        _fixture_events(
            campaign=leak_campaign,
            policy=policy,
            values={"control": [0.4] * 20, "candidate": [0.5] * 20},
            leak_index=0,
        )
    except Exception as exc:
        rejected["exposure_leakage"] = {
            "state": "REJECTED",
            "reason": type(exc).__name__,
            "detail": str(exc),
        }
    else:
        raise AssertionError("leakage fixture did not reject")

    reject(
        "undeclared_extra_look",
        lambda: evaluate(
            campaign=true_campaign,
            raw_events=true_events[:80],
            authority=authority,
            policy=policy,
            look_fraction=0.33,
            prior_look_fractions=[],
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )
    reject(
        "repeated_peeking",
        lambda: evaluate(
            campaign=true_campaign,
            raw_events=true_events[:80],
            authority=authority,
            policy=policy,
            look_fraction=0.5,
            prior_look_fractions=[],
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    schema_event = copy.deepcopy(true_events[0])
    schema_event["metric_schema_hash"] = "0" * 64
    schema_event["event_digest"] = _event_digest(schema_event)
    reject(
        "schema_drift",
        lambda: evaluate(
            campaign=true_campaign,
            raw_events=[schema_event] + true_events[1:],
            authority=authority,
            policy=policy,
            look_fraction=1.0,
            prior_look_fractions=[0.25, 0.5, 0.75],
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    metric_switch = copy.deepcopy(true_events[0])
    metric_switch["metric_definition_hash"] = "0" * 64
    metric_switch["event_digest"] = _event_digest(metric_switch)
    reject(
        "metric_switch_mid_test",
        lambda: evaluate(
            campaign=true_campaign,
            raw_events=[metric_switch] + true_events[1:],
            authority=authority,
            policy=policy,
            look_fraction=1.0,
            prior_look_fractions=[0.25, 0.5, 0.75],
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    tiny_campaign = _fixture_campaign(mode="randomized_controlled", policy=policy)
    tiny_events = _fixture_events(
        campaign=tiny_campaign,
        policy=policy,
        values={"control": [0.4] * 4, "candidate": [0.7] * 4},
    )
    reject(
        "tiny_sample",
        lambda: evaluate(
            campaign=tiny_campaign,
            raw_events=tiny_events,
            authority=authority,
            policy=policy,
            look_fraction=0.25,
            prior_look_fractions=[],
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    duplicate_event = copy.deepcopy(true_events)
    duplicate_event.append(copy.deepcopy(true_events[0]))
    reject(
        "duplicate_event_identity",
        lambda: evaluate(
            campaign=true_campaign,
            raw_events=duplicate_event,
            authority=authority,
            policy=policy,
            look_fraction=1.0,
            prior_look_fractions=[0.25, 0.5, 0.75],
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    reordered = evaluate(
        campaign=true_campaign,
        raw_events=list(reversed(true_events)),
        authority=authority,
        policy=policy,
        look_fraction=1.0,
        prior_look_fractions=[0.25, 0.5, 0.75],
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    reorder_stable = (
        reordered["decision_digest"]
        == accepted["true_positive_uplift"]["decision_digest"]
    )

    report = {
        "report_version": REPORT_VERSION,
        "status": STATUS,
        "parent_qa_state": "WAITING_QA_R3",
        "authoritative_integration": False,
        "accepted_cases": {
            name: {
                "recommendation": value["recommendation"],
                "decision_digest": value["decision_digest"],
                "reason_codes": value["reason_codes"],
                "policy_state": value["policy_state"],
                "causal_claim_allowed": value["causal_claim_allowed"],
            }
            for name, value in sorted(accepted.items())
        },
        "rejected_cases": rejected,
        "anti_p_hacking": {
            "reordered_data_same_decision": reorder_stable,
            "additional_look_outside_policy_rejected": "undeclared_extra_look" in rejected,
            "repeated_peeking_rejected": "repeated_peeking" in rejected,
            "metric_switch_mid_test_rejected": "metric_switch_mid_test" in rejected,
        },
        "evidence_boundaries": {
            "observational_is_causal": False,
            "randomized_and_observational_merged": False,
            "model_review_is_human_ground_truth": False,
            "human_ground_truth": False,
            "creator_mutation": False,
            "provider_publish": False,
        },
    }
    report["report_digest"] = sha256_json(report)
    return _clone(report)


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-r32-sequential-experiment-policy")
    sub = parser.add_subparsers(dest="command", required=True)
    rehearse = sub.add_parser("rehearse-fixtures")
    rehearse.add_argument("--authority", required=True)
    rehearse.add_argument("--policy", required=True)
    rehearse.add_argument("--out-dir", required=True)
    rehearse.add_argument("--growth-sha", required=True)
    rehearse.add_argument("--growth-ci-run-id", required=True, type=int)
    evaluate_cmd = sub.add_parser("evaluate")
    evaluate_cmd.add_argument("--campaign", required=True)
    evaluate_cmd.add_argument("--events", required=True)
    evaluate_cmd.add_argument("--authority", required=True)
    evaluate_cmd.add_argument("--policy", required=True)
    evaluate_cmd.add_argument("--look-fraction", required=True, type=float)
    evaluate_cmd.add_argument("--prior-looks", default="")
    evaluate_cmd.add_argument("--out-dir", required=True)
    evaluate_cmd.add_argument("--growth-sha", required=True)
    evaluate_cmd.add_argument("--growth-ci-run-id", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = Path(args.out_dir)
    authority = _load(Path(args.authority))
    policy = _load(Path(args.policy))
    try:
        if args.command == "rehearse-fixtures":
            report = build_rehearsal(
                authority=authority,
                policy=policy,
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            _write(out / "simulation-report.json", report)
        else:
            campaign = _load(Path(args.campaign))
            raw_events = _load(Path(args.events))
            if not isinstance(raw_events, list):
                raise R32Error("events file must contain array")
            prior = (
                []
                if not args.prior_looks
                else [float(x) for x in args.prior_looks.split(",") if x]
            )
            decision = evaluate(
                campaign=campaign,
                raw_events=raw_events,
                authority=authority,
                policy=policy,
                look_fraction=args.look_fraction,
                prior_look_fractions=prior,
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            _write(out / "decision.json", decision)
            report = {
                "report_version": REPORT_VERSION,
                "status": decision["status"],
                "recommendation": decision["recommendation"],
                "decision_digest": decision["decision_digest"],
                "policy_state": decision["policy_state"],
                "parent_qa_state": decision["parent_qa_state"],
                "authoritative_integration": False,
                "human_ground_truth": False,
                "creator_mutation": False,
                "provider_publish": False,
            }
            report["report_digest"] = sha256_json(report)
            _write(out / "readiness.json", report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as exc:
        blocked = {
            "report_version": REPORT_VERSION,
            "status": STATUS,
            "recommendation": "HUMAN_REVIEW_REQUIRED",
            "reason": type(exc).__name__,
            "detail": str(exc),
            "parent_qa_state": "WAITING_QA_R3",
            "authoritative_integration": False,
            "human_ground_truth": False,
            "creator_mutation": False,
            "provider_publish": False,
        }
        blocked["report_digest"] = sha256_json(blocked)
        _write(out / "readiness.json", blocked)
        print(json.dumps(blocked, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
