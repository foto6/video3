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
from . import sequential_experiment_policy_r32 as r32

CONTRACT_VERSION = "growth.adaptive_portfolio_governor.r33.v1"
AUTHORITY_VERSION = "growth.adaptive_portfolio_governor_authority.r33.v1"
POLICY_VERSION = "growth.adaptive_portfolio_policy.r33.v1"
PORTFOLIO_VERSION = "growth.adaptive_portfolio.r33.v1"
BUNDLE_VERSION = "growth.portfolio_campaign_bundle.r33.v1"
DECISION_VERSION = "growth.adaptive_portfolio_decision.r33.v1"
REPORT_VERSION = "growth.adaptive_portfolio_governor.r33.rehearsal.v1"

R32_SHA = "aacaefca808a0d71adb56fbb5b5ecf6213e874c8"
R32_CI = 37207893319
R32_ARTIFACT_ID = 11304917444
R32_ARTIFACT_DIGEST = (
    "sha256:a697270b382b935271fc088e856720e29f9b2e6ff2c313adc93b2789234032be"
)
R32_AUTHORITY_BLOB = "3b84e62d1b26dbad515300805de1c3e742d1180b"
R32_POLICY_BLOB = "adbae9426bcc5f3c2dce913c24594767f36f0cc9"
R32_CONTRACT_BLOB = "4b1e33300946463c1667027203a02e7e0ca940b5"
R32_IMPLEMENTATION_BLOB = "0053f869c127f4eb8a100f4528ac6a2eb596ecef"

R31_SHA = "311606b677d6f0d97669c905265f1eb64b7ff9a4"
R31_CI = 37204153689
R31_ARTIFACT_ID = 11304006807
R31_ARTIFACT_DIGEST = (
    "sha256:fab2ab2df9a4c8106352c5e6c17da3b3281f15a2b0e2015ec285304b2070334d"
)

STATUS = "SHADOW_SOURCE_READY"
RECOMMENDATIONS = {
    "KEEP",
    "TEST_MORE",
    "SHADOW_PROMOTE",
    "SHADOW_ROLLBACK",
    "HUMAN_REVIEW_REQUIRED",
}


class R33Error(ValueError):
    pass


class AuthorityDrift(R33Error):
    pass


class PortfolioConflict(R33Error):
    pass


class EvidenceConflict(R33Error):
    pass


class ReplayConflict(R33Error):
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
        raise R33Error(f"{field} must be lowercase SHA-256")
    return value


def _git_sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R33Error(f"{field} must be exact Git SHA")
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise R33Error(f"{field} must be non-empty string")
    return value


def _number(
    value: Any,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R33Error(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise R33Error(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise R33Error(f"{field} below minimum")
    if maximum is not None and result > maximum:
        raise R33Error(f"{field} above maximum")
    return result


def default_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.adaptive_portfolio_governor.r33.v1"
        / "authority.json"
    )


def default_policy() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.adaptive_portfolio_governor.r33.v1"
        / "policy.json"
    )


def _local_r32_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.sequential_experiment_policy.r32.v1"
        / "authority.json"
    )


def _local_r32_policy() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.sequential_experiment_policy.r32.v1"
        / "policy.json"
    )


def validate_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "growth_r32",
        "growth_r31_lineage",
        "evidence_boundary",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise AuthorityDrift("R33 authority fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R33 authority version mismatch")
    if value["growth_r32"] != {
        "repository": "foto6/video3",
        "producer_sha": R32_SHA,
        "ci_run_id": R32_CI,
        "artifact_id": R32_ARTIFACT_ID,
        "artifact_name": "growth-r32-sequential-experiment-policy",
        "artifact_digest": R32_ARTIFACT_DIGEST,
        "contract": "growth.sequential_experiment_policy.r32.v1",
        "authority_blob": R32_AUTHORITY_BLOB,
        "policy_blob": R32_POLICY_BLOB,
        "contract_blob": R32_CONTRACT_BLOB,
        "implementation_blob": R32_IMPLEMENTATION_BLOB,
    }:
        raise AuthorityDrift("exact R32 authority drift")
    if value["growth_r31_lineage"] != {
        "repository": "foto6/video3",
        "producer_sha": R31_SHA,
        "ci_run_id": R31_CI,
        "artifact_id": R31_ARTIFACT_ID,
        "artifact_name": "growth-r31-postpublish-learning-policy",
        "artifact_digest": R31_ARTIFACT_DIGEST,
        "contract": "growth.postpublish_learning.r31.v1",
    }:
        raise AuthorityDrift("exact R31 lineage authority drift")
    if value["evidence_boundary"] != {
        "shadow_only": True,
        "creator_mutation": False,
        "provider_mutation": False,
        "traffic_routing": False,
        "budget_allocation": False,
        "live_publish": False,
        "human_ground_truth": False,
        "observational_is_causal": False,
        "model_review_is_human_ground_truth": False,
        "merge": False,
    }:
        raise AuthorityDrift("R33 evidence boundary drift")
    # The local frozen R32 parser/policy must still be the requested starting authority.
    r32.validate_authority(_local_r32_authority())
    r32.validate_policy(_local_r32_policy())
    return _clone(value)


def validate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "policy_version",
        "family_error_accounting",
        "maturity_windows_seconds",
        "exploration",
        "holdback",
        "interference",
        "strata",
        "novelty",
        "drift",
        "recommendations",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PortfolioConflict("R33 policy fields invalid")
    if value["contract_version"] != POLICY_VERSION or value["policy_version"] != 1:
        raise PortfolioConflict("R33 policy version drift")
    if value["family_error_accounting"] != {
        "alpha_total": 0.05,
        "method": "prospective_slot_reservation",
        "max_campaigns": 4,
        "slot_alpha": [0.02, 0.0125, 0.01, 0.0075],
        "previous_slots_rewritten": False,
    }:
        raise PortfolioConflict("R33 family error policy drift")
    if value["maturity_windows_seconds"] != {"early": 3600, "mature": 604800}:
        raise PortfolioConflict("R33 maturity windows drift")
    if value["exploration"] != {
        "minimum_exploration_floor": 0.2,
        "maximum_concentration_recommendation": 0.7,
        "minimum_mature_exposures_per_arm": 20,
        "winner_take_all_allowed": False,
    }:
        raise PortfolioConflict("R33 exploration policy drift")
    if value["holdback"] != {"minimum_share": 0.1}:
        raise PortfolioConflict("R33 holdback policy drift")
    if value["interference"] != {
        "source_overlap_blocks": True,
        "candidate_bytes_overlap_blocks": True,
        "cross_platform_candidate_reuse_blocks": True,
        "exposure_identity_overlap_blocks": True,
        "external_change_blocks": True,
    }:
        raise PortfolioConflict("R33 interference policy drift")
    if value["strata"] != {
        "dimensions": ["platform", "account_pseudonym", "topic_cluster"],
        "critical_reversal_effect": -0.05,
        "overall_positive_effect": 0.05,
        "aggregation_policy": "equal_event_weight_with_critical_reversal_veto",
    }:
        raise PortfolioConflict("R33 strata policy drift")
    if value["novelty"] != {
        "early_spike_effect": 0.08,
        "mature_persistence_effect": 0.02,
        "mature_evidence_required_for_shadow_promote": True,
    }:
        raise PortfolioConflict("R33 novelty policy drift")
    if value["drift"] != {
        "stale_r32_policy_blocks": True,
        "platform_total_variation": 0.35,
        "topic_total_variation": 0.4,
    }:
        raise PortfolioConflict("R33 drift policy drift")
    if value["recommendations"] != [
        "KEEP",
        "TEST_MORE",
        "SHADOW_PROMOTE",
        "SHADOW_ROLLBACK",
        "HUMAN_REVIEW_REQUIRED",
    ]:
        raise PortfolioConflict("R33 recommendations drift")
    return _clone(value)


def authority_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority(value))


def policy_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_policy(value))


def r32_authority_tuple() -> dict[str, Any]:
    return {
        "producer_sha": R32_SHA,
        "ci_run_id": R32_CI,
        "artifact_id": R32_ARTIFACT_ID,
        "artifact_digest": R32_ARTIFACT_DIGEST,
        "contract": "growth.sequential_experiment_policy.r32.v1",
    }


def r31_authority_tuple() -> dict[str, Any]:
    return {
        "producer_sha": R31_SHA,
        "ci_run_id": R31_CI,
        "artifact_id": R31_ARTIFACT_ID,
        "artifact_digest": R31_ARTIFACT_DIGEST,
        "contract": "growth.postpublish_learning.r31.v1",
    }


def build_portfolio(
    *,
    portfolio_id: str,
    created_at: str,
    registry_frozen_at: str,
    registry: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    policy = validate_policy(policy)
    _nonempty(portfolio_id, "portfolio_id")
    created = parse_timestamp(created_at)
    frozen = parse_timestamp(registry_frozen_at)
    if frozen < created:
        raise PortfolioConflict("registry frozen before portfolio creation")
    max_campaigns = policy["family_error_accounting"]["max_campaigns"]
    if not isinstance(registry, Sequence) or isinstance(registry, (str, bytes)):
        raise PortfolioConflict("registry must be array")
    if not registry or len(registry) > max_campaigns:
        raise PortfolioConflict("registry campaign count outside policy")
    slots = set()
    keys = set()
    total_traffic = 0.0
    parsed = []
    for raw in registry:
        required = {
            "campaign_key",
            "family_slot",
            "priority",
            "traffic_budget_share",
            "holdback_share",
            "registered_at",
            "platforms",
            "accounts",
            "topics",
        }
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise PortfolioConflict("registry entry fields invalid")
        key = _nonempty(raw["campaign_key"], "campaign_key")
        slot = raw["family_slot"]
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < max_campaigns:
            raise PortfolioConflict("family slot invalid")
        if key in keys:
            raise PortfolioConflict("duplicate campaign identity")
        if slot in slots:
            raise PortfolioConflict("duplicate family alpha slot")
        keys.add(key)
        slots.add(slot)
        priority = raw["priority"]
        if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
            raise PortfolioConflict("campaign priority invalid")
        traffic = _number(raw["traffic_budget_share"], "traffic_budget_share", 0.0, 1.0)
        holdback = _number(raw["holdback_share"], "holdback_share", 0.0, 1.0)
        registered = parse_timestamp(raw["registered_at"])
        if registered < created or registered > frozen:
            raise PortfolioConflict("campaign registration outside frozen registry window")
        if holdback < policy["holdback"]["minimum_share"]:
            # Keep it parseable; evaluation will surface a human-review blocker.
            pass
        for field in ("platforms", "accounts", "topics"):
            values = raw[field]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(x, str) or not x for x in values)
                or len(values) != len(set(values))
            ):
                raise PortfolioConflict(f"registry {field} invalid")
        total_traffic += traffic
        parsed.append(
            {
                "campaign_key": key,
                "family_slot": slot,
                "priority": priority,
                "traffic_budget_share": round(traffic, 8),
                "holdback_share": round(holdback, 8),
                "registered_at": raw["registered_at"],
                "platforms": sorted(raw["platforms"]),
                "accounts": sorted(raw["accounts"]),
                "topics": sorted(raw["topics"]),
                "reserved_alpha": policy["family_error_accounting"]["slot_alpha"][slot],
            }
        )
    if total_traffic > 1.0 + 1e-12:
        raise PortfolioConflict("declared traffic budget exceeds portfolio")
    parsed.sort(key=lambda x: (x["family_slot"], x["campaign_key"]))
    material = {
        "contract_version": PORTFOLIO_VERSION,
        "portfolio_id": portfolio_id,
        "portfolio_digest": "",
        "created_at": created_at,
        "registry_frozen_at": registry_frozen_at,
        "policy_version": policy["policy_version"],
        "policy_digest": policy_digest(policy),
        "registry": parsed,
        "family_error_accounting": {
            "alpha_total": policy["family_error_accounting"]["alpha_total"],
            "reserved_alpha_total": round(sum(x["reserved_alpha"] for x in parsed), 8),
            "method": policy["family_error_accounting"]["method"],
            "prospective_only": True,
            "previous_slots_rewritten": False,
        },
    }
    if material["family_error_accounting"]["reserved_alpha_total"] > (
        policy["family_error_accounting"]["alpha_total"] + 1e-12
    ):
        raise PortfolioConflict("alpha/error-budget overspend")
    digest_material = copy.deepcopy(material)
    digest_material["portfolio_digest"] = ""
    material["portfolio_digest"] = sha256_json(digest_material)
    return _clone(material)


def parse_portfolio(value: Mapping[str, Any], *, policy: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "portfolio_id",
        "portfolio_digest",
        "created_at",
        "registry_frozen_at",
        "policy_version",
        "policy_digest",
        "registry",
        "family_error_accounting",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PortfolioConflict("portfolio fields invalid")
    raw_registry = []
    for item in value["registry"]:
        raw_registry.append(
            {
                "campaign_key": item["campaign_key"],
                "family_slot": item["family_slot"],
                "priority": item["priority"],
                "traffic_budget_share": item["traffic_budget_share"],
                "holdback_share": item["holdback_share"],
                "registered_at": item["registered_at"],
                "platforms": item["platforms"],
                "accounts": item["accounts"],
                "topics": item["topics"],
            }
        )
    rebuilt = build_portfolio(
        portfolio_id=value["portfolio_id"],
        created_at=value["created_at"],
        registry_frozen_at=value["registry_frozen_at"],
        registry=raw_registry,
        policy=policy,
    )
    if rebuilt != value:
        raise PortfolioConflict("portfolio digest/policy binding mismatch")
    return rebuilt


def _validate_r31_lineage(
    lineage: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(lineage, Sequence) or isinstance(lineage, (str, bytes)):
        raise EvidenceConflict("R31 lineage must be array")
    if len(lineage) != len(events):
        raise EvidenceConflict("R31 lineage/event cardinality mismatch")
    event_by_id = {event["event_id"]: event for event in events}
    parsed = []
    seen = set()
    required = {
        "event_id",
        "publish_transaction_id",
        "creator_session_id",
        "published_candidate_id",
        "published_render_sha256",
        "source_sha256",
        "r31_observation_digest",
        "r31_policy_suggestion_digest",
        "lineage_digest",
    }
    for raw in lineage:
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise EvidenceConflict("R31 lineage fields invalid")
        event_id = _nonempty(raw["event_id"], "lineage.event_id")
        if event_id in seen:
            raise EvidenceConflict("duplicate R31 lineage event")
        seen.add(event_id)
        event = event_by_id.get(event_id)
        if event is None:
            raise EvidenceConflict("R31 lineage references unknown R32 event")
        for field in (
            "publish_transaction_id",
            "creator_session_id",
            "published_candidate_id",
            "published_render_sha256",
            "source_sha256",
        ):
            if raw[field] != event[field]:
                raise EvidenceConflict(f"R31/R32 lineage mismatch: {field}")
        _sha(raw["r31_observation_digest"], "r31_observation_digest")
        _sha(raw["r31_policy_suggestion_digest"], "r31_policy_suggestion_digest")
        material = {k: raw[k] for k in raw if k != "lineage_digest"}
        if raw["lineage_digest"] != sha256_json(material):
            raise EvidenceConflict("R31 lineage digest mismatch")
        parsed.append(_clone(raw))
    return sorted(parsed, key=lambda x: x["event_id"])


def _validate_r32_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_window: int,
) -> dict[str, Any]:
    required = {
        "campaign",
        "decision",
        "events",
        "r31_lineage",
        "r32_authority",
        "r31_authority",
    }
    if not isinstance(evidence, Mapping) or set(evidence) != required:
        raise EvidenceConflict("R32 evidence bundle fields invalid")
    if evidence["r32_authority"] != r32_authority_tuple():
        raise AuthorityDrift("conflicting R32 parent authority")
    if evidence["r31_authority"] != r31_authority_tuple():
        raise AuthorityDrift("conflicting R31 lineage authority")
    r32_policy = _local_r32_policy()
    campaign = r32.parse_campaign(evidence["campaign"], policy=r32_policy)
    if campaign["metric_window_seconds"] != expected_window:
        raise EvidenceConflict("metric maturity window mismatch")
    events = [
        r32.parse_event(event, campaign=campaign, policy=r32_policy)
        for event in evidence["events"]
    ]
    decision = evidence["decision"]
    if not isinstance(decision, Mapping):
        raise EvidenceConflict("R32 decision missing")
    recomputed = r32.evaluate(
        campaign=campaign,
        raw_events=events,
        authority=_local_r32_authority(),
        policy=r32_policy,
        look_fraction=decision["look_fraction"],
        prior_look_fractions=decision["prior_look_fractions"],
        growth_sha=R32_SHA,
        growth_ci_run_id=R32_CI,
    )
    if recomputed != decision:
        raise EvidenceConflict("R32 decision is not exact verified recomputation")
    lineage = _validate_r31_lineage(evidence["r31_lineage"], events)
    return {
        "campaign": campaign,
        "decision": _clone(decision),
        "events": sorted(events, key=lambda x: x["event_id"]),
        "r31_lineage": lineage,
        "r32_authority": r32_authority_tuple(),
        "r31_authority": r31_authority_tuple(),
        "evidence_digest": sha256_json(
            {
                "campaign_digest": campaign["campaign_digest"],
                "decision_digest": decision["decision_digest"],
                "event_digests": sorted(event["event_digest"] for event in events),
                "lineage_digests": sorted(item["lineage_digest"] for item in lineage),
                "r32_authority": r32_authority_tuple(),
                "r31_authority": r31_authority_tuple(),
            }
        ),
    }


def validate_bundle(
    raw: Mapping[str, Any],
    *,
    registry_entry: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "contract_version",
        "campaign_key",
        "family_slot",
        "priority",
        "traffic_budget_share",
        "holdback_share",
        "external_change_during_measurement",
        "early",
        "mature",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise EvidenceConflict("portfolio campaign bundle fields invalid")
    if raw["contract_version"] != BUNDLE_VERSION:
        raise EvidenceConflict("campaign bundle contract mismatch")
    for field in (
        "campaign_key",
        "family_slot",
        "priority",
        "traffic_budget_share",
        "holdback_share",
    ):
        if raw[field] != registry_entry[field]:
            raise EvidenceConflict(f"bundle/registry mismatch: {field}")
    early = _validate_r32_evidence(
        raw["early"],
        expected_window=policy["maturity_windows_seconds"]["early"],
    )
    mature = None
    if raw["mature"] is not None:
        mature = _validate_r32_evidence(
            raw["mature"],
            expected_window=policy["maturity_windows_seconds"]["mature"],
        )
        for field in ("session_id", "declared_candidates", "control_candidate_id"):
            if mature["campaign"][field] != early["campaign"][field]:
                raise EvidenceConflict(f"early/mature campaign lineage mismatch: {field}")
        early_cohorts = sorted(
            (e["publish_transaction_id"], e["published_render_sha256"])
            for e in early["events"]
        )
        mature_cohorts = sorted(
            (e["publish_transaction_id"], e["published_render_sha256"])
            for e in mature["events"]
        )
        if early_cohorts != mature_cohorts:
            raise EvidenceConflict("mature cohort does not match declared early cohort")
        if mature["decision"]["look_fraction"] not in early["campaign"]["declared_look_fractions"]:
            raise EvidenceConflict("late metrics update undeclared look")
    for evidence in (early, mature):
        if evidence is None:
            continue
        for event in evidence["events"]:
            if event["platform"] not in registry_entry["platforms"]:
                raise EvidenceConflict("event platform outside declared stratum")
            if event["account_pseudonym"] not in registry_entry["accounts"]:
                raise EvidenceConflict("cross-account mixing outside declared stratum")
            if event["topic_cluster"] not in registry_entry["topics"]:
                raise EvidenceConflict("event topic outside declared stratum")
    return {
        "contract_version": BUNDLE_VERSION,
        "campaign_key": raw["campaign_key"],
        "family_slot": raw["family_slot"],
        "priority": raw["priority"],
        "traffic_budget_share": raw["traffic_budget_share"],
        "holdback_share": raw["holdback_share"],
        "reserved_alpha": registry_entry["reserved_alpha"],
        "external_change_during_measurement": bool(
            raw["external_change_during_measurement"]
        ),
        "early": early,
        "mature": mature,
        "bundle_digest": sha256_json(
            {
                "campaign_key": raw["campaign_key"],
                "family_slot": raw["family_slot"],
                "early": early["evidence_digest"],
                "mature": None if mature is None else mature["evidence_digest"],
                "external_change": bool(raw["external_change_during_measurement"]),
            }
        ),
    }


def _effect_from_decision(decision: Mapping[str, Any]) -> float | None:
    randomized = decision.get("randomized_result")
    if not isinstance(randomized, Mapping):
        return None
    tests = randomized.get("candidate_tests") or []
    if not tests:
        return None
    rows = [
        row
        for row in tests
        if row.get("difference") is not None
    ]
    if not rows:
        return None
    return float(max(rows, key=lambda x: abs(float(x["difference"])))["difference"])


def _candidate_from_decision(decision: Mapping[str, Any]) -> str | None:
    if decision.get("recommended_candidate_id"):
        return decision["recommended_candidate_id"]
    randomized = decision.get("randomized_result")
    if not isinstance(randomized, Mapping):
        return None
    rows = randomized.get("candidate_tests") or []
    if not rows:
        return None
    return max(
        rows,
        key=lambda x: (
            float("-inf") if x.get("difference") is None else float(x["difference"]),
            x["candidate_id"],
        ),
    )["candidate_id"]


def _family_gate(
    decision: Mapping[str, Any],
    *,
    reserved_alpha: float,
) -> dict[str, Any]:
    randomized = decision.get("randomized_result")
    if not isinstance(randomized, Mapping):
        return {
            "applicable": False,
            "passes": False,
            "minimum_adjusted_p": None,
            "reserved_alpha": reserved_alpha,
        }
    values = [
        float(row["adjusted_p_value"])
        for row in randomized.get("candidate_tests", [])
        if row.get("adjusted_p_value") is not None
    ]
    minimum = None if not values else min(values)
    return {
        "applicable": True,
        "passes": bool(minimum is not None and minimum <= reserved_alpha),
        "minimum_adjusted_p": minimum,
        "reserved_alpha": reserved_alpha,
    }


def _stratum_summaries(
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    campaign = evidence["campaign"]
    control = campaign["control_candidate_id"]
    candidate = _candidate_from_decision(evidence["decision"])
    result = {}
    for dimension in ("platform", "account_pseudonym", "topic_cluster"):
        groups: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: {"control": [], "candidate": []}
        )
        for event in evidence["events"]:
            if event["primary_value"] is None:
                continue
            if event["candidate_id"] == control:
                arm = "control"
            elif candidate is not None and event["candidate_id"] == candidate:
                arm = "candidate"
            else:
                continue
            groups[event[dimension]][arm].append(float(event["primary_value"]))
        rows = []
        for name in sorted(groups):
            control_values = groups[name]["control"]
            candidate_values = groups[name]["candidate"]
            effect = None
            if control_values and candidate_values:
                effect = round(
                    statistics.mean(candidate_values)
                    - statistics.mean(control_values),
                    8,
                )
            rows.append(
                {
                    "stratum": name,
                    "control_n": len(control_values),
                    "candidate_n": len(candidate_values),
                    "effect": effect,
                }
            )
        result[dimension] = rows
    return result


def _simpson_guard(
    mature: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    overall = _effect_from_decision(mature["decision"])
    summaries = _stratum_summaries(mature)
    reversals = []
    if overall is not None and overall >= policy["strata"]["overall_positive_effect"]:
        for dimension, rows in summaries.items():
            for row in rows:
                if (
                    row["effect"] is not None
                    and row["effect"] <= policy["strata"]["critical_reversal_effect"]
                ):
                    reversals.append(
                        {
                            "dimension": dimension,
                            "stratum": row["stratum"],
                            "effect": row["effect"],
                        }
                    )
    return {
        "overall_effect": overall,
        "summaries": summaries,
        "critical_reversals": reversals,
        "veto": bool(reversals),
    }


def _maturity(bundle: Mapping[str, Any], *, policy: Mapping[str, Any]) -> dict[str, Any]:
    early = bundle["early"]
    mature = bundle["mature"]
    early_complete = bool(
        early["decision"]["recommendation"]
        not in {"HUMAN_REVIEW_REQUIRED"}
        and not early["decision"]["evidence_summary"]["quality"]["missing_event_ids"]
    )
    mature_complete = False
    if mature is not None:
        min_per_arm = policy["exploration"]["minimum_mature_exposures_per_arm"]
        counts = Counter(event["candidate_id"] for event in mature["events"])
        mature_complete = (
            all(
                counts[candidate] >= min_per_arm
                for candidate in mature["campaign"]["declared_candidates"]
            )
            and not mature["decision"]["evidence_summary"]["quality"]["missing_event_ids"]
            and mature["decision"]["policy_state"] == r32.POLICY_CURRENT
        )
    return {
        "early_complete": early_complete,
        "mature_present": mature is not None,
        "mature_complete": mature_complete,
        "state": (
            "MATURE"
            if mature_complete
            else ("CENSORED_OR_INCOMPLETE" if mature is not None else "EARLY_ONLY")
        ),
    }


def _novelty(bundle: Mapping[str, Any], *, policy: Mapping[str, Any]) -> dict[str, Any]:
    early_effect = _effect_from_decision(bundle["early"]["decision"])
    mature_effect = (
        None
        if bundle["mature"] is None
        else _effect_from_decision(bundle["mature"]["decision"])
    )
    early_spike = bool(
        early_effect is not None
        and early_effect >= policy["novelty"]["early_spike_effect"]
    )
    decayed = bool(
        early_spike
        and mature_effect is not None
        and mature_effect < policy["novelty"]["mature_persistence_effect"]
    )
    return {
        "early_effect": early_effect,
        "mature_effect": mature_effect,
        "early_spike": early_spike,
        "novelty_decay": decayed,
    }


def _interference(bundles: Sequence[Mapping[str, Any]]) -> list[str]:
    blockers = set()
    exposures: dict[str, str] = {}
    sources: dict[str, str] = {}
    renders: dict[str, tuple[str, str]] = {}
    for bundle in bundles:
        key = bundle["campaign_key"]
        evidence = bundle["early"]
        for event in evidence["events"]:
            exposure = event["exposure_id"]
            if exposure in exposures and exposures[exposure] != key:
                blockers.add("OVERLAPPING_EXPOSURE_IDENTITIES")
            else:
                exposures[exposure] = key
            source = event["source_sha256"]
            if source in sources and sources[source] != key:
                blockers.add("OVERLAPPING_SOURCE_IDENTITY")
            else:
                sources[source] = key
            render = event["published_render_sha256"]
            previous = renders.get(render)
            if previous is not None and previous[0] != key:
                blockers.add("OVERLAPPING_CANDIDATE_BYTES")
                if previous[1] != event["platform"]:
                    blockers.add("CROSS_PLATFORM_CANDIDATE_REUSE")
            else:
                renders[render] = (key, event["platform"])
        if bundle["external_change_during_measurement"]:
            blockers.add("EXTERNAL_CAMPAIGN_CHANGE_DURING_MEASUREMENT")
    return sorted(blockers)


def _posthoc_registration(
    portfolio: Mapping[str, Any],
    bundles: Sequence[Mapping[str, Any]],
) -> bool:
    earliest_result = None
    for bundle in bundles:
        for event in bundle["early"]["events"]:
            captured = parse_timestamp(event["metric_captured_at"])
            if earliest_result is None or captured < earliest_result:
                earliest_result = captured
    if earliest_result is None:
        return False
    return any(
        parse_timestamp(entry["registered_at"]) > earliest_result
        for entry in portfolio["registry"]
    )


def _portfolio_stratum_distribution(
    bundles: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, float]:
    values = []
    for bundle in bundles:
        values.extend(event[field] for event in bundle["early"]["events"])
    counts = Counter(values)
    total = len(values)
    if total == 0:
        return {}
    return {key: round(value / total, 8) for key, value in sorted(counts.items())}


def _tv(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = set(a) | set(b)
    return round(
        0.5 * sum(abs(float(a.get(key, 0.0)) - float(b.get(key, 0.0)) for key in keys),
        8,
    )


def _campaign_state(
    bundle: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    maturity = _maturity(bundle, policy=policy)
    early_decision = bundle["early"]["decision"]
    mature_decision = None if bundle["mature"] is None else bundle["mature"]["decision"]
    novelty = _novelty(bundle, policy=policy)
    family = _family_gate(
        mature_decision if mature_decision is not None else early_decision,
        reserved_alpha=float(bundle["reserved_alpha"]),
    )
    simpson = (
        None
        if bundle["mature"] is None
        else _simpson_guard(bundle["mature"], policy=policy)
    )
    blockers = []
    if bundle["holdback_share"] < policy["holdback"]["minimum_share"]:
        blockers.append("HOLDBACK_BELOW_MINIMUM")
    if (
        bundle["traffic_budget_share"]
        > policy["exploration"]["maximum_concentration_recommendation"]
    ):
        blockers.append("TRAFFIC_CONCENTRATION_ABOVE_SAFETY_CAP")
    for decision in (early_decision, mature_decision):
        if decision is None:
            continue
        if decision["policy_state"] == r32.POLICY_STALE:
            blockers.append("STALE_R32_POLICY")
        if decision["mode"] == "observational_monitoring" and decision.get(
            "causal_claim_allowed"
        ):
            blockers.append("OBSERVATIONAL_CAUSAL_OVERCLAIM")
    if simpson is not None and simpson["veto"]:
        blockers.append("CRITICAL_STRATUM_REVERSAL")
    if bundle["external_change_during_measurement"]:
        blockers.append("EXTERNAL_CAMPAIGN_CHANGE_DURING_MEASUREMENT")

    recommendation = "TEST_MORE"
    reason_codes = list(blockers)
    source_decision = mature_decision if mature_decision is not None else early_decision
    if blockers:
        recommendation = "HUMAN_REVIEW_REQUIRED"
    elif source_decision["mode"] == "observational_monitoring":
        recommendation = "TEST_MORE"
        reason_codes.append("OBSERVATIONAL_ASSOCIATION_ONLY")
    elif not maturity["mature_complete"]:
        recommendation = "TEST_MORE"
        reason_codes.append("MATURE_EVIDENCE_REQUIRED")
    elif novelty["novelty_decay"]:
        recommendation = "SHADOW_ROLLBACK"
        reason_codes.append("NOVELTY_SPIKE_DECAYED")
    elif source_decision["recommendation"] == "ROLLBACK_RECOMMENDED":
        recommendation = "SHADOW_ROLLBACK"
        reason_codes.append("R32_RANDOMIZED_ROLLBACK")
    elif source_decision["recommendation"] == "PROMOTE_CANDIDATE":
        if family["passes"]:
            recommendation = "SHADOW_PROMOTE"
            reason_codes.append("R32_RANDOMIZED_PREFERENCE_FAMILY_GATE_PASSED")
        else:
            recommendation = "TEST_MORE"
            reason_codes.append("PORTFOLIO_MULTIPLICITY_GATE_NOT_PASSED")
    elif source_decision["recommendation"] == "KEEP":
        recommendation = "KEEP"
        reason_codes.append("R32_KEEP")
    elif source_decision["recommendation"] == "TEST_MORE":
        recommendation = "TEST_MORE"
        reason_codes.append("R32_TEST_MORE")
    else:
        recommendation = "HUMAN_REVIEW_REQUIRED"
        reason_codes.append("R32_HUMAN_REVIEW_REQUIRED")

    candidate_id = _candidate_from_decision(source_decision)
    render_hashes = sorted(
        {
            event["published_render_sha256"]
            for event in (
                bundle["mature"]["events"]
                if bundle["mature"] is not None
                else bundle["early"]["events"]
            )
            if candidate_id is not None and event["candidate_id"] == candidate_id
        }
    )
    identity = None
    if candidate_id is not None:
        identity = sha256_json(
            {
                "campaign_key": bundle["campaign_key"],
                "candidate_id": candidate_id,
                "render_hashes": render_hashes,
                "policy_digest": source_decision["campaign_digest"],
            }
        )
    return {
        "campaign_key": bundle["campaign_key"],
        "priority": bundle["priority"],
        "family_slot": bundle["family_slot"],
        "reserved_alpha": bundle["reserved_alpha"],
        "traffic_budget_share": bundle["traffic_budget_share"],
        "holdback_share": bundle["holdback_share"],
        "maturity": maturity,
        "novelty": novelty,
        "family_gate": family,
        "strata": simpson,
        "r32_recommendation": source_decision["recommendation"],
        "r32_decision_digest": source_decision["decision_digest"],
        "evidence_mode": source_decision["mode"],
        "recommendation": recommendation,
        "reason_codes": sorted(set(reason_codes)),
        "candidate_id": candidate_id,
        "candidate_identity_digest": identity,
        "shadow_only": True,
        "creator_mutation": False,
        "provider_mutation": False,
        "traffic_routing": False,
        "budget_allocation": False,
        "live_publish": False,
    }


def evaluate_portfolio(
    *,
    portfolio: Mapping[str, Any],
    raw_bundles: Sequence[Mapping[str, Any]],
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    portfolio_look_id: str,
    baseline_distribution: Mapping[str, Any] | None,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    portfolio = parse_portfolio(portfolio, policy=policy)
    _nonempty(portfolio_look_id, "portfolio_look_id")
    _git_sha(growth_sha, "growth_sha")
    if (
        isinstance(growth_ci_run_id, bool)
        or not isinstance(growth_ci_run_id, int)
        or growth_ci_run_id < 1
    ):
        raise R33Error("growth_ci_run_id invalid")
    if (
        not isinstance(raw_bundles, Sequence)
        or isinstance(raw_bundles, (str, bytes))
        or len(raw_bundles) != len(portfolio["registry"])
    ):
        raise PortfolioConflict("bundle count must match frozen registry")
    registry = {entry["campaign_key"]: entry for entry in portfolio["registry"]}
    raw_keys = [bundle.get("campaign_key") for bundle in raw_bundles]
    if len(raw_keys) != len(set(raw_keys)):
        raise PortfolioConflict("duplicate campaign bundle")
    if set(raw_keys) != set(registry):
        raise PortfolioConflict("campaign set changed under frozen portfolio identity")
    bundles = [
        validate_bundle(
            bundle,
            registry_entry=registry[bundle["campaign_key"]],
            policy=policy,
        )
        for bundle in raw_bundles
    ]
    bundles.sort(key=lambda x: (x["family_slot"], x["campaign_key"]))

    global_blockers = _interference(bundles)
    if _posthoc_registration(portfolio, bundles):
        global_blockers.append("CAMPAIGN_ADDED_AFTER_RESULTS")
    if portfolio["family_error_accounting"]["reserved_alpha_total"] > policy[
        "family_error_accounting"
    ]["alpha_total"] + 1e-12:
        global_blockers.append("ALPHA_ERROR_BUDGET_OVERSPEND")

    platform_dist = _portfolio_stratum_distribution(bundles, "platform")
    topic_dist = _portfolio_stratum_distribution(bundles, "topic_cluster")
    drift = {
        "platform_distribution": platform_dist,
        "topic_distribution": topic_dist,
        "platform_total_variation": 0.0,
        "topic_total_variation": 0.0,
        "stale": False,
    }
    if baseline_distribution is not None:
        required = {"platform_distribution", "topic_distribution"}
        if not isinstance(baseline_distribution, Mapping) or set(
            baseline_distribution
        ) != required:
            raise PortfolioConflict("portfolio baseline distribution invalid")
        drift["platform_total_variation"] = _tv(
            platform_dist,
            baseline_distribution["platform_distribution"],
        )
        drift["topic_total_variation"] = _tv(
            topic_dist,
            baseline_distribution["topic_distribution"],
        )
        if drift["platform_total_variation"] > policy["drift"][
            "platform_total_variation"
        ]:
            global_blockers.append("PORTFOLIO_PLATFORM_SHIFT")
        if drift["topic_total_variation"] > policy["drift"][
            "topic_total_variation"
        ]:
            global_blockers.append("PORTFOLIO_TOPIC_SHIFT")
        drift["stale"] = bool(
            "PORTFOLIO_PLATFORM_SHIFT" in global_blockers
            or "PORTFOLIO_TOPIC_SHIFT" in global_blockers
        )

    states = [_campaign_state(bundle, policy=policy) for bundle in bundles]
    if global_blockers:
        for state in states:
            state["recommendation"] = "HUMAN_REVIEW_REQUIRED"
            state["reason_codes"] = sorted(
                set(state["reason_codes"]) | set(global_blockers)
            )

    recommendation = "KEEP"
    if global_blockers or any(
        state["recommendation"] == "HUMAN_REVIEW_REQUIRED" for state in states
    ):
        recommendation = "HUMAN_REVIEW_REQUIRED"
    elif any(state["recommendation"] == "SHADOW_ROLLBACK" for state in states):
        recommendation = "SHADOW_ROLLBACK"
    elif any(state["recommendation"] == "SHADOW_PROMOTE" for state in states):
        recommendation = "SHADOW_PROMOTE"
    elif any(state["recommendation"] == "TEST_MORE" for state in states):
        recommendation = "TEST_MORE"

    ranked = sorted(
        states,
        key=lambda x: (
            0 if x["recommendation"] == "SHADOW_ROLLBACK" else
            1 if x["recommendation"] == "HUMAN_REVIEW_REQUIRED" else
            2 if x["recommendation"] == "SHADOW_PROMOTE" else
            3 if x["recommendation"] == "TEST_MORE" else 4,
            -x["priority"],
            x["campaign_key"],
        ),
    )
    concentration = {
        "minimum_exploration_floor": policy["exploration"][
            "minimum_exploration_floor"
        ],
        "maximum_concentration_recommendation": policy["exploration"][
            "maximum_concentration_recommendation"
        ],
        "winner_take_all_allowed": False,
        "live_traffic_routing": False,
        "advisory_only": True,
    }

    result = {
        "contract_version": DECISION_VERSION,
        "decision_id": "",
        "decision_digest": "",
        "status": STATUS,
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_digest": portfolio["portfolio_digest"],
        "portfolio_look_id": portfolio_look_id,
        "policy_version": policy["policy_version"],
        "policy_digest": policy_digest(policy),
        "authority": {
            "growth_r33": {
                "producer_sha": growth_sha,
                "ci_run_id": growth_ci_run_id,
            },
            "growth_r32": r32_authority_tuple(),
            "growth_r31_lineage": r31_authority_tuple(),
            "authority_digest": authority_digest(authority),
        },
        "family_error_accounting": _clone(portfolio["family_error_accounting"]),
        "campaign_states": ranked,
        "global_blockers": sorted(set(global_blockers)),
        "portfolio_drift": drift,
        "shadow_recommendation": recommendation,
        "evidence_maturity": {
            state["campaign_key"]: state["maturity"]["state"]
            for state in sorted(states, key=lambda x: x["campaign_key"])
        },
        "concentration_guard": concentration,
        "evidence_boundary": {
            "shadow_only": True,
            "creator_mutation": False,
            "provider_mutation": False,
            "traffic_routing": False,
            "budget_allocation": False,
            "live_publish": False,
            "human_ground_truth": False,
            "observational_is_causal": False,
            "model_review_is_human_ground_truth": False,
        },
    }
    result["decision_id"] = "gr33p1:" + sha256_json(
        {
            "portfolio_digest": result["portfolio_digest"],
            "portfolio_look_id": portfolio_look_id,
            "bundle_digests": [bundle["bundle_digest"] for bundle in bundles],
            "policy_digest": result["policy_digest"],
        }
    )
    digest_material = copy.deepcopy(result)
    digest_material["decision_digest"] = ""
    result["decision_digest"] = sha256_json(digest_material)
    if recommendation not in RECOMMENDATIONS:
        raise R33Error("invalid portfolio recommendation")
    return _clone(result)


class PortfolioLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.state = {
            "looks": {},
            "rollback_memory": {},
        }
        if self.path.exists():
            self.state = _load(self.path)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def record(self, decision: Mapping[str, Any]) -> bool:
        key = f"{decision['portfolio_id']}@{decision['portfolio_look_id']}"
        digest = decision["decision_digest"]
        prior = self.state["looks"].get(key)
        if prior is not None:
            if prior != digest:
                raise ReplayConflict(
                    "same portfolio/look identity changed campaign set or metric bytes"
                )
            return False
        for state in decision["campaign_states"]:
            identity = state["candidate_identity_digest"]
            if identity is None:
                continue
            memory = self.state["rollback_memory"].get(identity)
            if (
                memory is not None
                and state["recommendation"] == "SHADOW_PROMOTE"
            ):
                raise ReplayConflict(
                    "same candidate/policy identity cannot erase prior rollback"
                )
        self.state["looks"][key] = digest
        for state in decision["campaign_states"]:
            if (
                state["recommendation"] == "SHADOW_ROLLBACK"
                and state["candidate_identity_digest"] is not None
            ):
                self.state["rollback_memory"][
                    state["candidate_identity_digest"]
                ] = {
                    "portfolio_id": decision["portfolio_id"],
                    "portfolio_look_id": decision["portfolio_look_id"],
                    "decision_digest": digest,
                    "campaign_key": state["campaign_key"],
                    "reason_codes": state["reason_codes"],
                }
        self._save()
        return True


def _lineage_for_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        material = {
            "event_id": event["event_id"],
            "publish_transaction_id": event["publish_transaction_id"],
            "creator_session_id": event["creator_session_id"],
            "published_candidate_id": event["published_candidate_id"],
            "published_render_sha256": event["published_render_sha256"],
            "source_sha256": event["source_sha256"],
            "r31_observation_digest": hashlib.sha256(
                f"r31-observation:{event['event_id']}".encode("utf-8")
            ).hexdigest(),
            "r31_policy_suggestion_digest": hashlib.sha256(
                f"r31-policy:{event['event_id']}".encode("utf-8")
            ).hexdigest(),
        }
        rows.append(
            {
                **material,
                "lineage_digest": sha256_json(material),
            }
        )
    return rows


def _make_r32_evidence(
    *,
    label: str,
    mode: str,
    window_seconds: int,
    control_values: Sequence[float],
    candidate_values: Sequence[float],
    platform: str = "instagram_reels",
    account: str = "acct-r33",
    topic: str = "topic-r33",
    look_fraction: float = 1.0,
    platform_by_index: Mapping[int, str] | None = None,
    topic_by_index: Mapping[int, str] | None = None,
    campaign_policy_state_stale: bool = False,
) -> dict[str, Any]:
    r32_policy = _local_r32_policy()
    schema_hash = hashlib.sha256(b"r33-metric-schema").hexdigest()
    definition_hash = hashlib.sha256(b"r33-metric-definition").hexdigest()
    all_platforms = (
        sorted(set(platform_by_index.values()) | {platform})
        if platform_by_index
        else [platform]
    )
    all_topics = (
        sorted(set(topic_by_index.values()) | {topic})
        if topic_by_index
        else [topic]
    )
    total = len(control_values) + len(candidate_values)
    platform_distribution = {platform: 1.0}
    topic_distribution = {topic: 1.0}
    if platform_by_index:
        values = [
            platform_by_index.get(i, platform)
            for i in range(total)
        ]
        counts = Counter(values)
        platform_distribution = {
            key: round(value / total, 8)
            for key, value in sorted(counts.items())
        }
    if topic_by_index:
        values = [
            topic_by_index.get(i, topic)
            for i in range(total)
        ]
        counts = Counter(values)
        topic_distribution = {
            key: round(value / total, 8)
            for key, value in sorted(counts.items())
        }
    window_name = "early" if window_seconds == 3600 else "mature"
    campaign = r32.build_campaign(
        campaign_id=f"r33-{label}-{window_name}",
        mode=mode,
        session_id=f"session-{label}",
        policy=r32_policy,
        declared_candidates=["control", "candidate"],
        control_candidate_id="control",
        metric_schema_hash=schema_hash,
        metric_definition_hash=definition_hash,
        metric_window_seconds=window_seconds,
        training_distribution={
            "platform_distribution": platform_distribution,
            "topic_distribution": topic_distribution,
            "metric_schema_hash": schema_hash,
            "metric_definition_hash": definition_hash,
        },
    )
    rows = []
    values_by_arm = {"control": list(control_values), "candidate": list(candidate_values)}
    index = 0
    for candidate_id in ("control", "candidate"):
        for arm_index, primary in enumerate(values_by_arm[candidate_id]):
            exposure = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(
                minutes=index
            )
            captured = exposure + timedelta(seconds=window_seconds)
            exposure_id = f"exp-{label}-{index}"
            assignment_id = f"assign-{label}-{window_name}-{index}"
            randomized = None
            if mode == "randomized_controlled":
                assignment_material = {
                    "campaign_id": campaign["campaign_id"],
                    "assignment_id": assignment_id,
                    "exposure_id": exposure_id,
                    "seed": f"seed-{label}",
                    "candidate_id": candidate_id,
                }
                randomized = {
                    "assignment_id": assignment_id,
                    "assignment_digest": sha256_json(assignment_material),
                    "randomization_seed": f"seed-{label}",
                    "assigned_candidate_id": candidate_id,
                    "treatment_visible_before_assignment": False,
                    "exposure_leakage": False,
                }
            row = r32.build_event(
                campaign=campaign,
                policy=r32_policy,
                event_id=f"evt-{label}-{window_name}-{index}",
                candidate_id=candidate_id,
                publish_transaction_id=f"txn-{label}-{index}",
                creator_session_id=campaign["session_id"],
                published_candidate_id=f"pub-{label}-{candidate_id}-{arm_index}",
                published_render_sha256=hashlib.sha256(
                    f"render-{label}-{candidate_id}-{arm_index}".encode("utf-8")
                ).hexdigest(),
                exposure_id=exposure_id,
                exposure_at=exposure.isoformat().replace("+00:00", "Z"),
                metric_captured_at=captured.isoformat().replace("+00:00", "Z"),
                platform=(
                    platform_by_index.get(index, platform)
                    if platform_by_index
                    else platform
                ),
                account_pseudonym=account,
                topic_cluster=(
                    topic_by_index.get(index, topic)
                    if topic_by_index
                    else topic
                ),
                source_sha256=hashlib.sha256(
                    f"source-{label}-{index}".encode("utf-8")
                ).hexdigest(),
                primary_value=primary,
                guardrails={"share_rate": 0.04, "comment_rate": 0.02},
                metric_schema_hash=schema_hash,
                metric_definition_hash=definition_hash,
                randomized=randomized,
                fixture=True,
            )
            rows.append(row)
            index += 1
    prior = []
    if look_fraction == 0.5:
        prior = [0.25]
    elif look_fraction == 0.75:
        prior = [0.25, 0.5]
    elif look_fraction == 1.0:
        prior = [0.25, 0.5, 0.75]
    decision = r32.evaluate(
        campaign=campaign,
        raw_events=rows,
        authority=_local_r32_authority(),
        policy=r32_policy,
        look_fraction=look_fraction,
        prior_look_fractions=prior,
        growth_sha=R32_SHA,
        growth_ci_run_id=R32_CI,
    )
    if campaign_policy_state_stale:
        decision = copy.deepcopy(decision)
        decision["policy_state"] = r32.POLICY_STALE
        decision["reason_codes"] = sorted(
            set(decision["reason_codes"]) | {"SIMULATED_STALE_POLICY"}
        )
        material = copy.deepcopy(decision)
        material["decision_digest"] = ""
        decision["decision_digest"] = sha256_json(material)
        # This intentionally ceases to be exact R32 recomputation; use only in
        # adversarial tests that expect validation failure.
    return {
        "campaign": campaign,
        "decision": decision,
        "events": rows,
        "r31_lineage": _lineage_for_events(rows),
        "r32_authority": r32_authority_tuple(),
        "r31_authority": r31_authority_tuple(),
        "_platforms": all_platforms,
        "_topics": all_topics,
        "_account": account,
    }


def _strip_fixture_meta(evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _clone(value)
        for key, value in evidence.items()
        if not key.startswith("_")
    }


def _make_bundle(
    *,
    label: str,
    slot: int,
    priority: int,
    traffic: float,
    holdback: float,
    early: Mapping[str, Any],
    mature: Mapping[str, Any] | None,
    external_change: bool = False,
) -> dict[str, Any]:
    return {
        "contract_version": BUNDLE_VERSION,
        "campaign_key": label,
        "family_slot": slot,
        "priority": priority,
        "traffic_budget_share": traffic,
        "holdback_share": holdback,
        "external_change_during_measurement": external_change,
        "early": _strip_fixture_meta(early),
        "mature": None if mature is None else _strip_fixture_meta(mature),
    }


def _registry_entry(
    *,
    label: str,
    slot: int,
    priority: int,
    traffic: float,
    holdback: float,
    evidence: Mapping[str, Any],
    registered_at: str = "2026-08-31T00:00:00Z",
) -> dict[str, Any]:
    return {
        "campaign_key": label,
        "family_slot": slot,
        "priority": priority,
        "traffic_budget_share": traffic,
        "holdback_share": holdback,
        "registered_at": registered_at,
        "platforms": sorted(evidence["_platforms"]),
        "accounts": [evidence["_account"]],
        "topics": sorted(evidence["_topics"]),
    }


def _base_values(effect: float, n: int = 80) -> tuple[list[float], list[float]]:
    control = [0.40 + (i % 5) * 0.002 for i in range(n)]
    candidate = [min(0.99, value + effect) for value in control]
    return control, candidate


def _case_result(name: str, fn) -> dict[str, Any]:
    try:
        decision = fn()
    except Exception as exc:
        return {
            "name": name,
            "state": "REJECTED",
            "reason": type(exc).__name__,
            "detail": str(exc),
        }
    return {
        "name": name,
        "state": "ACCEPTED",
        "shadow_recommendation": decision["shadow_recommendation"],
        "decision_digest": decision["decision_digest"],
        "global_blockers": decision["global_blockers"],
        "campaign_states": [
            {
                "campaign_key": row["campaign_key"],
                "recommendation": row["recommendation"],
                "reason_codes": row["reason_codes"],
                "maturity": row["maturity"]["state"],
            }
            for row in decision["campaign_states"]
        ],
        "shadow_only": decision["evidence_boundary"]["shadow_only"],
        "creator_mutation": decision["evidence_boundary"]["creator_mutation"],
        "provider_mutation": decision["evidence_boundary"]["provider_mutation"],
        "traffic_routing": decision["evidence_boundary"]["traffic_routing"],
        "live_publish": decision["evidence_boundary"]["live_publish"],
    }


def _build_fixture_portfolio(
    specs: Sequence[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
    portfolio_id: str,
    frozen_at: str = "2026-08-31T23:00:00Z",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry = [
        _registry_entry(
            label=spec["label"],
            slot=spec["slot"],
            priority=spec.get("priority", 3),
            traffic=spec.get("traffic", 0.3),
            holdback=spec.get("holdback", 0.2),
            evidence=spec["early"],
            registered_at=spec.get("registered_at", "2026-08-31T00:00:00Z"),
        )
        for spec in specs
    ]
    portfolio = build_portfolio(
        portfolio_id=portfolio_id,
        created_at="2026-08-30T00:00:00Z",
        registry_frozen_at=frozen_at,
        registry=registry,
        policy=policy,
    )
    bundles = [
        _make_bundle(
            label=spec["label"],
            slot=spec["slot"],
            priority=spec.get("priority", 3),
            traffic=spec.get("traffic", 0.3),
            holdback=spec.get("holdback", 0.2),
            early=spec["early"],
            mature=spec.get("mature"),
            external_change=spec.get("external_change", False),
        )
        for spec in specs
    ]
    return portfolio, bundles


def _evaluate_fixture(
    *,
    portfolio: Mapping[str, Any],
    bundles: Sequence[Mapping[str, Any]],
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    look_id: str,
    baseline: Mapping[str, Any] | None = None,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    return evaluate_portfolio(
        portfolio=portfolio,
        raw_bundles=bundles,
        authority=authority,
        policy=policy,
        portfolio_look_id=look_id,
        baseline_distribution=baseline,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )


def build_rehearsal(
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    _git_sha(growth_sha, "growth_sha")
    cases: dict[str, Any] = {}

    def make_pair(label: str, early_effect: float, mature_effect: float, mode: str = "randomized_controlled"):
        ec, ea = _base_values(early_effect)
        mc, ma = _base_values(mature_effect)
        early = _make_r32_evidence(
            label=label,
            mode=mode,
            window_seconds=3600,
            control_values=ec,
            candidate_values=ea,
        )
        mature = _make_r32_evidence(
            label=label,
            mode=mode,
            window_seconds=604800,
            control_values=mc,
            candidate_values=ma,
        )
        return early, mature

    # 1 clean persistent winner.
    early, mature = make_pair("persistent-winner", 0.20, 0.18)
    p, b = _build_fixture_portfolio(
        [{"label": "persistent-winner", "slot": 0, "early": early, "mature": mature}],
        policy=policy,
        portfolio_id="p-clean",
    )
    cases["01_persistent_winner_shadow_promote"] = _case_result(
        "01_persistent_winner_shadow_promote",
        lambda: _evaluate_fixture(
            portfolio=p,bundles=b,authority=authority,policy=policy,
            look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,
        ),
    )

    # 2 null.
    e2, m2 = make_pair("null", 0.0, 0.0)
    p2, b2 = _build_fixture_portfolio(
        [{"label": "null", "slot": 0, "early": e2, "mature": m2}],
        policy=policy,portfolio_id="p-null",
    )
    cases["02_null_keep"] = _case_result(
        "02_null_keep",
        lambda: _evaluate_fixture(
            portfolio=p2,bundles=b2,authority=authority,policy=policy,
            look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,
        ),
    )

    # 3 delayed winner illusion: early winner, mature absent.
    e3, _ = make_pair("delayed-winner", 0.20, 0.18)
    p3, b3 = _build_fixture_portfolio(
        [{"label":"delayed-winner","slot":0,"early":e3,"mature":None}],
        policy=policy,portfolio_id="p-delayed",
    )
    cases["03_delayed_winner_illusion"] = _case_result(
        "03_delayed_winner_illusion",
        lambda: _evaluate_fixture(
            portfolio=p3,bundles=b3,authority=authority,policy=policy,
            look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,
        ),
    )

    # 4 novelty spike then decay.
    e4, m4 = make_pair("novelty", 0.20, 0.0)
    p4, b4 = _build_fixture_portfolio(
        [{"label":"novelty","slot":0,"early":e4,"mature":m4}],
        policy=policy,portfolio_id="p-novelty",
    )
    cases["04_novelty_spike_then_decay"] = _case_result(
        "04_novelty_spike_then_decay",
        lambda: _evaluate_fixture(
            portfolio=p4,bundles=b4,authority=authority,policy=policy,
            look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,
        ),
    )

    # 5 mature rollback.
    e5, m5 = make_pair("rollback", 0.01, -0.20)
    p5, b5 = _build_fixture_portfolio(
        [{"label":"rollback","slot":0,"early":e5,"mature":m5}],
        policy=policy,portfolio_id="p-rollback",
    )
    cases["05_mature_rollback"] = _case_result(
        "05_mature_rollback",
        lambda: _evaluate_fixture(
            portfolio=p5,bundles=b5,authority=authority,policy=policy,
            look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,
        ),
    )

    # 6 observational-only.
    eo, mo = make_pair("observational", 0.30, 0.30, mode="observational_monitoring")
    po, bo = _build_fixture_portfolio(
        [{"label":"observational","slot":0,"early":eo,"mature":mo}],
        policy=policy,portfolio_id="p-observational",
    )
    cases["06_observational_association_only"] = _case_result(
        "06_observational_association_only",
        lambda: _evaluate_fixture(
            portfolio=po,bundles=bo,authority=authority,policy=policy,
            look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,
        ),
    )

    # 7 censored loser: mature evidence incomplete (10/arm).
    ec, ea = _base_values(0.15)
    mc, ma = _base_values(-0.15, n=10)
    e7 = _make_r32_evidence(
        label="censored",mode="randomized_controlled",window_seconds=3600,
        control_values=ec,candidate_values=ea,
    )
    m7 = _make_r32_evidence(
        label="censored",mode="randomized_controlled",window_seconds=604800,
        control_values=mc,candidate_values=ma,look_fraction=0.25,
    )
    # Mature R32 itself will be human review because look size is insufficient.
    p7,b7=_build_fixture_portfolio(
        [{"label":"censored","slot":0,"early":e7,"mature":m7}],
        policy=policy,portfolio_id="p-censored",
    )
    cases["07_censored_loser"]=_case_result(
        "07_censored_loser",
        lambda:_evaluate_fixture(portfolio=p7,bundles=b7,authority=authority,policy=policy,
        look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 8 Simpson reversal. Equal total arm size, one critical platform reverses.
    n=80
    control=[0.50]*40+[0.20]*40
    candidate=[0.40]*40+[0.60]*40
    platforms={i:("instagram_reels" if (i % 80)<40 else "tiktok") for i in range(160)}
    e8=_make_r32_evidence(
        label="simpson",mode="randomized_controlled",window_seconds=3600,
        control_values=control,candidate_values=candidate,platform_by_index=platforms,
    )
    m8=_make_r32_evidence(
        label="simpson",mode="randomized_controlled",window_seconds=604800,
        control_values=control,candidate_values=candidate,platform_by_index=platforms,
    )
    p8,b8=_build_fixture_portfolio(
        [{"label":"simpson","slot":0,"early":e8,"mature":m8}],
        policy=policy,portfolio_id="p-simpson",
    )
    cases["08_simpson_reversal"]=_case_result(
        "08_simpson_reversal",
        lambda:_evaluate_fixture(portfolio=p8,bundles=b8,authority=authority,policy=policy,
        look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 9/10 valid multiple accounts/platforms across separate campaigns.
    ea1,ma1=make_pair("acct-a",0.18,0.16)
    ec2,mc2=make_pair("acct-b",0.0,0.0)
    # Rebuild second account to prove cross-account portfolio support.
    c0,c1=_base_values(0.0)
    eb=_make_r32_evidence(label="acct-b",mode="randomized_controlled",window_seconds=3600,
        control_values=c0,candidate_values=c1,account="acct-b",platform="tiktok",topic="topic-b")
    mb=_make_r32_evidence(label="acct-b",mode="randomized_controlled",window_seconds=604800,
        control_values=c0,candidate_values=c1,account="acct-b",platform="tiktok",topic="topic-b")
    p9,b9=_build_fixture_portfolio(
        [
            {"label":"acct-a","slot":0,"early":ea1,"mature":ma1,"traffic":0.4},
            {"label":"acct-b","slot":1,"early":eb,"mature":mb,"traffic":0.4},
        ],policy=policy,portfolio_id="p-multi-account",
    )
    cases["09_multi_account_valid"]=_case_result(
        "09_multi_account_valid",
        lambda:_evaluate_fixture(portfolio=p9,bundles=b9,authority=authority,policy=policy,
        look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )
    cases["10_multi_platform_valid"]=_clone(cases["09_multi_account_valid"])
    cases["10_multi_platform_valid"]["name"]="10_multi_platform_valid"

    # 11 duplicate exposures between campaigns.
    bad_b=copy.deepcopy(b9)
    bad_b[1]["early"]["events"][0]["exposure_id"]=bad_b[0]["early"]["events"][0]["exposure_id"]
    bad_b[1]["early"]["events"][0]["event_digest"]=r32._event_digest(bad_b[1]["early"]["events"][0])
    # decision recomputation must match changed event; update exact decision and lineage.
    ev=bad_b[1]["early"]
    ev["decision"]=r32.evaluate(campaign=ev["campaign"],raw_events=ev["events"],
        authority=_local_r32_authority(),policy=_local_r32_policy(),
        look_fraction=ev["decision"]["look_fraction"],prior_look_fractions=ev["decision"]["prior_look_fractions"],
        growth_sha=R32_SHA,growth_ci_run_id=R32_CI)
    ev["r31_lineage"]=_lineage_for_events(ev["events"])
    cases["11_duplicate_exposures_between_campaigns"]=_case_result(
        "11_duplicate_exposures_between_campaigns",
        lambda:_evaluate_fixture(portfolio=p9,bundles=bad_b,authority=authority,policy=policy,
        look_id="look-dup-exp",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 12 source identity reuse.
    bad_source=copy.deepcopy(b9)
    bad_source[1]["early"]["events"][0]["source_sha256"]=bad_source[0]["early"]["events"][0]["source_sha256"]
    bad_source[1]["early"]["events"][0]["event_digest"]=r32._event_digest(bad_source[1]["early"]["events"][0])
    ev=bad_source[1]["early"]
    ev["decision"]=r32.evaluate(campaign=ev["campaign"],raw_events=ev["events"],
        authority=_local_r32_authority(),policy=_local_r32_policy(),
        look_fraction=ev["decision"]["look_fraction"],prior_look_fractions=ev["decision"]["prior_look_fractions"],
        growth_sha=R32_SHA,growth_ci_run_id=R32_CI)
    ev["r31_lineage"]=_lineage_for_events(ev["events"])
    cases["12_source_identity_reuse"]=_case_result(
        "12_source_identity_reuse",
        lambda:_evaluate_fixture(portfolio=p9,bundles=bad_source,authority=authority,policy=policy,
        look_id="look-source",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 13 candidate bytes reuse.
    bad_render=copy.deepcopy(b9)
    bad_render[1]["early"]["events"][0]["published_render_sha256"]=bad_render[0]["early"]["events"][0]["published_render_sha256"]
    bad_render[1]["early"]["events"][0]["event_digest"]=r32._event_digest(bad_render[1]["early"]["events"][0])
    ev=bad_render[1]["early"]
    ev["decision"]=r32.evaluate(campaign=ev["campaign"],raw_events=ev["events"],
        authority=_local_r32_authority(),policy=_local_r32_policy(),
        look_fraction=ev["decision"]["look_fraction"],prior_look_fractions=ev["decision"]["prior_look_fractions"],
        growth_sha=R32_SHA,growth_ci_run_id=R32_CI)
    ev["r31_lineage"]=_lineage_for_events(ev["events"])
    cases["13_candidate_identity_reuse"]=_case_result(
        "13_candidate_identity_reuse",
        lambda:_evaluate_fixture(portfolio=p9,bundles=bad_render,authority=authority,policy=policy,
        look_id="look-render",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 14 cross-platform same candidate bytes.
    bad_cross=copy.deepcopy(bad_render)
    cases["14_cross_platform_candidate_reuse"]=_case_result(
        "14_cross_platform_candidate_reuse",
        lambda:_evaluate_fixture(portfolio=p9,bundles=bad_cross,authority=authority,policy=policy,
        look_id="look-cross-platform",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 15 cross-account mixing inside a campaign.
    bad_account=copy.deepcopy(b)
    bad_account[0]["early"]["events"][0]["account_pseudonym"]="other-account"
    bad_account[0]["early"]["events"][0]["event_digest"]=r32._event_digest(bad_account[0]["early"]["events"][0])
    ev=bad_account[0]["early"]
    ev["decision"]=r32.evaluate(campaign=ev["campaign"],raw_events=ev["events"],
        authority=_local_r32_authority(),policy=_local_r32_policy(),
        look_fraction=ev["decision"]["look_fraction"],prior_look_fractions=ev["decision"]["prior_look_fractions"],
        growth_sha=R32_SHA,growth_ci_run_id=R32_CI)
    ev["r31_lineage"]=_lineage_for_events(ev["events"])
    cases["15_cross_account_mixing"]=_case_result(
        "15_cross_account_mixing",
        lambda:_evaluate_fixture(portfolio=p,bundles=bad_account,authority=authority,policy=policy,
        look_id="look-account",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 16 external campaign change.
    p16,b16=_build_fixture_portfolio(
        [{"label":"persistent-winner","slot":0,"early":early,"mature":mature,"external_change":True}],
        policy=policy,portfolio_id="p-external-change",
    )
    cases["16_external_campaign_change"]=_case_result(
        "16_external_campaign_change",
        lambda:_evaluate_fixture(portfolio=p16,bundles=b16,authority=authority,policy=policy,
        look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 17 campaign registered after results.
    p17,b17=_build_fixture_portfolio(
        [{"label":"persistent-winner","slot":0,"early":early,"mature":mature,
          "registered_at":"2026-09-01T02:00:00Z"}],
        policy=policy,portfolio_id="p-posthoc",frozen_at="2026-09-01T03:00:00Z",
    )
    cases["17_campaign_added_after_results"]=_case_result(
        "17_campaign_added_after_results",
        lambda:_evaluate_fixture(portfolio=p17,bundles=b17,authority=authority,policy=policy,
        look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 18 traffic concentration.
    p18,b18=_build_fixture_portfolio(
        [{"label":"persistent-winner","slot":0,"early":early,"mature":mature,"traffic":0.8}],
        policy=policy,portfolio_id="p-concentration",
    )
    cases["18_traffic_concentration_above_cap"]=_case_result(
        "18_traffic_concentration_above_cap",
        lambda:_evaluate_fixture(portfolio=p18,bundles=b18,authority=authority,policy=policy,
        look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 19 holdback below minimum.
    p19,b19=_build_fixture_portfolio(
        [{"label":"persistent-winner","slot":0,"early":early,"mature":mature,"holdback":0.05}],
        policy=policy,portfolio_id="p-holdback",
    )
    cases["19_holdback_below_minimum"]=_case_result(
        "19_holdback_below_minimum",
        lambda:_evaluate_fixture(portfolio=p19,bundles=b19,authority=authority,policy=policy,
        look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 20 conflicting R32 authority.
    bad_auth=copy.deepcopy(b)
    bad_auth[0]["early"]["r32_authority"]["producer_sha"]="0"*40
    cases["20_conflicting_r32_parent_authority"]=_case_result(
        "20_conflicting_r32_parent_authority",
        lambda:_evaluate_fixture(portfolio=p,bundles=bad_auth,authority=authority,policy=policy,
        look_id="look-auth",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 21 conflicting R31 authority.
    bad_r31=copy.deepcopy(b)
    bad_r31[0]["early"]["r31_authority"]["artifact_id"]+=1
    cases["21_conflicting_r31_lineage_authority"]=_case_result(
        "21_conflicting_r31_lineage_authority",
        lambda:_evaluate_fixture(portfolio=p,bundles=bad_r31,authority=authority,policy=policy,
        look_id="look-r31-auth",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 22 schema switch.
    bad_schema=copy.deepcopy(b)
    bad_schema[0]["early"]["events"][0]["metric_schema_hash"]="0"*64
    bad_schema[0]["early"]["events"][0]["event_digest"]=r32._event_digest(bad_schema[0]["early"]["events"][0])
    cases["22_metric_schema_switch"]=_case_result(
        "22_metric_schema_switch",
        lambda:_evaluate_fixture(portfolio=p,bundles=bad_schema,authority=authority,policy=policy,
        look_id="look-schema",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 23 definition switch.
    bad_def=copy.deepcopy(b)
    bad_def[0]["early"]["events"][0]["metric_definition_hash"]="0"*64
    bad_def[0]["early"]["events"][0]["event_digest"]=r32._event_digest(bad_def[0]["early"]["events"][0])
    cases["23_metric_definition_switch"]=_case_result(
        "23_metric_definition_switch",
        lambda:_evaluate_fixture(portfolio=p,bundles=bad_def,authority=authority,policy=policy,
        look_id="look-def",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 24 stale policy evidence: exact decision is tampered, must fail exact R32 verification.
    stale=copy.deepcopy(b)
    stale[0]["mature"]["decision"]["policy_state"]=r32.POLICY_STALE
    material=copy.deepcopy(stale[0]["mature"]["decision"]);material["decision_digest"]=""
    stale[0]["mature"]["decision"]["decision_digest"]=sha256_json(material)
    cases["24_stale_policy_tamper"]=_case_result(
        "24_stale_policy_tamper",
        lambda:_evaluate_fixture(portfolio=p,bundles=stale,authority=authority,policy=policy,
        look_id="look-stale",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 25 platform distribution shift versus frozen portfolio baseline.
    baseline={"platform_distribution":{"tiktok":1.0},"topic_distribution":{"topic-r33":1.0}}
    cases["25_platform_shift"]=_case_result(
        "25_platform_shift",
        lambda:_evaluate_fixture(portfolio=p,bundles=b,authority=authority,policy=policy,
        look_id="look-shift",baseline=baseline,growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 26 topic shift.
    baseline_topic={"platform_distribution":{"instagram_reels":1.0},"topic_distribution":{"other-topic":1.0}}
    cases["26_topic_shift"]=_case_result(
        "26_topic_shift",
        lambda:_evaluate_fixture(portfolio=p,bundles=b,authority=authority,policy=policy,
        look_id="look-topic-shift",baseline=baseline_topic,growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 27 duplicate campaign bundle.
    cases["27_duplicate_campaign_bundle"]=_case_result(
        "27_duplicate_campaign_bundle",
        lambda:_evaluate_fixture(portfolio=p,bundles=[b[0],copy.deepcopy(b[0])],authority=authority,policy=policy,
        look_id="look-dup-bundle",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 28 campaign set changed (missing frozen campaign).
    cases["28_campaign_set_changed"]=_case_result(
        "28_campaign_set_changed",
        lambda:_evaluate_fixture(portfolio=p9,bundles=[b9[0]],authority=authority,policy=policy,
        look_id="look-set",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 29 alpha slot duplication rejected at portfolio construction.
    def alpha_duplicate():
        reg=[
            _registry_entry(label="a",slot=0,priority=3,traffic=0.3,holdback=0.2,evidence=early),
            _registry_entry(label="b",slot=0,priority=3,traffic=0.3,holdback=0.2,evidence=eb),
        ]
        return build_portfolio(portfolio_id="p-alpha-dup",created_at="2026-08-30T00:00:00Z",
            registry_frozen_at="2026-08-31T23:00:00Z",registry=reg,policy=policy)
    cases["29_alpha_slot_overspend_or_duplicate"]=_case_result(
        "29_alpha_slot_overspend_or_duplicate",
        lambda: alpha_duplicate(),
    )

    # 30 R31 lineage bytes changed.
    bad_lineage=copy.deepcopy(b)
    bad_lineage[0]["early"]["r31_lineage"][0]["published_render_sha256"]="0"*64
    material={k:v for k,v in bad_lineage[0]["early"]["r31_lineage"][0].items() if k!="lineage_digest"}
    bad_lineage[0]["early"]["r31_lineage"][0]["lineage_digest"]=sha256_json(material)
    cases["30_r31_lineage_mismatch"]=_case_result(
        "30_r31_lineage_mismatch",
        lambda:_evaluate_fixture(portfolio=p,bundles=bad_lineage,authority=authority,policy=policy,
        look_id="look-lineage",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # 31 ordering stability.
    cases["31_input_order_stability"]=_case_result(
        "31_input_order_stability",
        lambda:_evaluate_fixture(portfolio=p9,bundles=list(reversed(b9)),authority=authority,policy=policy,
        look_id="look-order",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )
    ordered=_evaluate_fixture(portfolio=p9,bundles=b9,authority=authority,policy=policy,
        look_id="look-order",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id)
    cases["31_input_order_stability"]["same_as_ordered"]=(
        cases["31_input_order_stability"].get("decision_digest")==ordered["decision_digest"]
    )

    # 32 replay idempotent and 33 changed bytes conflict.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ledger=PortfolioLedger(Path(tmp)/"ledger.json")
        d1=_evaluate_fixture(portfolio=p,bundles=b,authority=authority,policy=policy,
            look_id="look-ledger",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id)
        first=ledger.record(d1)
        second=ledger.record(d1)
        cases["32_exact_replay_idempotent"]={
            "name":"32_exact_replay_idempotent","state":"ACCEPTED",
            "first_recorded":first,"second_recorded":second,
            "decision_digest":d1["decision_digest"],
        }
        changed=copy.deepcopy(d1)
        changed["decision_digest"]="0"*64
        try:
            ledger.record(changed)
        except Exception as exc:
            cases["33_changed_portfolio_look_conflict"]={
                "name":"33_changed_portfolio_look_conflict","state":"REJECTED",
                "reason":type(exc).__name__,"detail":str(exc),
            }
        else:
            raise AssertionError("changed portfolio/look replay did not conflict")

    # 34 rollback memory persists for same identity.
    with tempfile.TemporaryDirectory() as tmp:
        ledger=PortfolioLedger(Path(tmp)/"ledger.json")
        rollback_decision=_evaluate_fixture(portfolio=p5,bundles=b5,authority=authority,policy=policy,
            look_id="look-rb",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id)
        ledger.record(rollback_decision)
        fake_promote=copy.deepcopy(rollback_decision)
        fake_promote["portfolio_look_id"]="look-rb-2"
        fake_promote["shadow_recommendation"]="SHADOW_PROMOTE"
        for state in fake_promote["campaign_states"]:
            state["recommendation"]="SHADOW_PROMOTE"
        mat=copy.deepcopy(fake_promote);mat["decision_digest"]=""
        fake_promote["decision_digest"]=sha256_json(mat)
        try:
            ledger.record(fake_promote)
        except Exception as exc:
            cases["34_same_identity_cannot_erase_rollback"]={
                "name":"34_same_identity_cannot_erase_rollback","state":"REJECTED",
                "reason":type(exc).__name__,"detail":str(exc),
            }
        else:
            raise AssertionError("rollback memory was erased")

    # 35 genuinely new candidate/policy identity may proceed independently.
    e35,m35=make_pair("new-after-rollback",0.20,0.18)
    p35,b35=_build_fixture_portfolio(
        [{"label":"new-after-rollback","slot":0,"early":e35,"mature":m35}],
        policy=policy,portfolio_id="p-new-after-rollback",
    )
    cases["35_new_identity_after_rollback_allowed"]=_case_result(
        "35_new_identity_after_rollback_allowed",
        lambda:_evaluate_fixture(portfolio=p35,bundles=b35,authority=authority,policy=policy,
        look_id="look-1",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id),
    )

    # Explicit portfolio-wide shadow safety proof.
    shadow_violations = []
    for name, case in cases.items():
        if case.get("state") != "ACCEPTED":
            continue
        if case.get("creator_mutation") is True:
            shadow_violations.append((name,"creator_mutation"))
        if case.get("provider_mutation") is True:
            shadow_violations.append((name,"provider_mutation"))
        if case.get("traffic_routing") is True:
            shadow_violations.append((name,"traffic_routing"))
        if case.get("live_publish") is True:
            shadow_violations.append((name,"live_publish"))

    report = {
        "report_version": REPORT_VERSION,
        "status": STATUS,
        "case_count": len(cases),
        "accepted_case_count": sum(1 for c in cases.values() if c["state"]=="ACCEPTED"),
        "rejected_case_count": sum(1 for c in cases.values() if c["state"]=="REJECTED"),
        "cases": {key:cases[key] for key in sorted(cases)},
        "shadow_safety": {
            "shadow_promote_is_advisory": True,
            "creator_mutation": False,
            "provider_mutation": False,
            "traffic_routing": False,
            "budget_allocation": False,
            "live_publish": False,
            "violations": shadow_violations,
        },
        "authority": {
            "growth_r32": r32_authority_tuple(),
            "growth_r31_lineage": r31_authority_tuple(),
            "authority_digest": authority_digest(authority),
        },
        "evidence_boundaries": {
            "human_ground_truth": False,
            "observational_is_causal": False,
            "model_review_is_human_ground_truth": False,
        },
    }
    report["report_digest"]=sha256_json(report)
    return _clone(report)


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(prog="growth-r33-adaptive-portfolio-governor")
    sub=parser.add_subparsers(dest="command",required=True)
    status=sub.add_parser("status")
    status.add_argument("--portfolio",required=True)
    status.add_argument("--bundles",required=True)
    status.add_argument("--authority",required=True)
    status.add_argument("--policy",required=True)
    status.add_argument("--portfolio-look-id",required=True)
    status.add_argument("--baseline")
    status.add_argument("--ledger",required=True)
    status.add_argument("--out-dir",required=True)
    status.add_argument("--growth-sha",required=True)
    status.add_argument("--growth-ci-run-id",required=True,type=int)
    rehearsal=sub.add_parser("rehearse-fixtures")
    rehearsal.add_argument("--authority",required=True)
    rehearsal.add_argument("--policy",required=True)
    rehearsal.add_argument("--out-dir",required=True)
    rehearsal.add_argument("--growth-sha",required=True)
    rehearsal.add_argument("--growth-ci-run-id",required=True,type=int)
    return parser


def main(argv: Sequence[str] | None=None) -> int:
    args=_parser().parse_args(argv)
    out=Path(args.out_dir)
    authority=_load(Path(args.authority))
    policy=_load(Path(args.policy))
    try:
        if args.command=="rehearse-fixtures":
            report=build_rehearsal(
                authority=authority,policy=policy,growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            _write(out/"simulation-report.json",report)
        else:
            portfolio=_load(Path(args.portfolio))
            bundles=_load(Path(args.bundles))
            baseline=None if args.baseline is None else _load(Path(args.baseline))
            decision=evaluate_portfolio(
                portfolio=portfolio,raw_bundles=bundles,authority=authority,policy=policy,
                portfolio_look_id=args.portfolio_look_id,baseline_distribution=baseline,
                growth_sha=args.growth_sha,growth_ci_run_id=args.growth_ci_run_id,
            )
            ledger=PortfolioLedger(Path(args.ledger))
            newly_recorded=ledger.record(decision)
            _write(out/"portfolio-status.json",decision)
            report={
                "report_version":REPORT_VERSION,
                "status":decision["status"],
                "portfolio_id":decision["portfolio_id"],
                "portfolio_look_id":decision["portfolio_look_id"],
                "decision_digest":decision["decision_digest"],
                "shadow_recommendation":decision["shadow_recommendation"],
                "campaign_states":decision["campaign_states"],
                "blockers":decision["global_blockers"],
                "evidence_maturity":decision["evidence_maturity"],
                "authority":decision["authority"],
                "newly_recorded":newly_recorded,
                "shadow_only":True,
                "creator_mutation":False,
                "provider_mutation":False,
                "traffic_routing":False,
                "budget_allocation":False,
                "live_publish":False,
                "human_ground_truth":False,
            }
            report["report_digest"]=sha256_json(report)
            _write(out/"readiness.json",report)
        print(json.dumps(report,sort_keys=True))
        return 0
    except Exception as exc:
        blocked={
            "report_version":REPORT_VERSION,
            "status":STATUS,
            "shadow_recommendation":"HUMAN_REVIEW_REQUIRED",
            "reason":type(exc).__name__,
            "detail":str(exc),
            "shadow_only":True,
            "creator_mutation":False,
            "provider_mutation":False,
            "traffic_routing":False,
            "budget_allocation":False,
            "live_publish":False,
            "human_ground_truth":False,
        }
        blocked["report_digest"]=sha256_json(blocked)
        _write(out/"readiness.json",blocked)
        print(json.dumps(blocked,sort_keys=True))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
