from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .event_stream import parse_timestamp
from . import counterfactual_policy_promotion_r34 as r34

CONTRACT_VERSION = "growth.canary_evidence_registry.r35.v1"
AUTHORITY_VERSION = "growth.canary_evidence_registry_authority.r35.v1"
POLICY_VERSION = "growth.canary_evidence_registry_policy.r35.v1"
ENTRY_VERSION = "growth.canary_evidence_entry.r35.v1"
SNAPSHOT_VERSION = "growth.canary_evidence_registry_snapshot.r35.v1"
CERTIFICATE_VERSION = "growth.shadow_canary_readiness_certificate.r35.v1"
ADVISORY_VERSION = "growth.shadow_canary_registry_advisory.r35.v1"
REPORT_VERSION = "growth.canary_evidence_registry.r35.rehearsal.v1"
MANIFEST_VERSION = "growth.canary_evidence_registry.r35.conformance_manifest.v1"

STATUS = "ADVISORY_ONLY_SOURCE_READY"
READINESS = {
    "NOT_READY",
    "TEST_MORE",
    "SHADOW_CANARY_CANDIDATE",
    "HUMAN_REVIEW_REQUIRED",
    "SHADOW_ROLLBACK",
}
SOURCE_CLASSES = {
    "RANDOMIZED",
    "OFF_POLICY_REPLAY",
    "OBSERVATIONAL",
    "SYNTHETIC_TEST",
}

R34_SHA = "9ff243bc5ec6977bc5f0eb8f16cd5e51aa0dcdfc"
R34_CI = 37244660304
R34_ARTIFACT_ID = 11318054384
R34_ARTIFACT_DIGEST = (
    "sha256:6fc329964c14ce7c11f27fd2dd47235912470a377c6a1f5a6f3e66f4481f5ac9"
)
R34_AUTHORITY_BLOB = "ef6d2d636a651a927702e106609084e405124d22"
R34_POLICY_BLOB = "c9ef96296d0883852df32b2a90ee6271474e033f"
R34_CONTRACT_BLOB = "d65858f6644d8445d6ba1665e793b371f5d93d7b"
R34_IMPLEMENTATION_BLOB = "74c847fe925375561a045b86f076498b85aafe8c"
R34_SIMULATION_BLOB = "e3557cc41c67fa5c4e5bcc4c91c9f49e015dde12"

R33_SHA = "8bedb5ad79023006b87b17933863ff915ab5e046"
R33_CI = 37210963972
R33_ARTIFACT_ID = 11306727215
R33_ARTIFACT_DIGEST = (
    "sha256:132845c0aaa6d4fec5aaf60e1ade60d779183f2a637a9513e4176bd2ee569660"
)
R33_AUTHORITY_BLOB = "35426e8f05f708d0a6db4356c4211458bf033c16"

R32_SHA = "aacaefca808a0d71adb56fbb5b5ecf6213e874c8"
R32_CI = 37207893319
R32_ARTIFACT_ID = 11304917444
R32_ARTIFACT_DIGEST = (
    "sha256:a697270b382b935271fc088e856720e29f9b2e6ff2c313adc93b2789234032be"
)

QA_R6_SHA = "233a0e3d2237300a9b85b99965e27a190ebb362f"
QA_R6_CI = 37245109258
QA_R6_ARTIFACT_ID = 11319180558
QA_R6_ARTIFACT_DIGEST = (
    "sha256:402f6440b1ec136de6b3ff4f0547970f6f75274f3f3f482857d54528921d3ac8"
)
QA_R6_MATRIX_BLOB = "249574798f19bdd1593301aee09918530c391c71"


class R35Error(ValueError):
    pass


class AuthorityDrift(R35Error):
    pass


class EntryRejected(R35Error):
    pass


class RegistryConflict(R35Error):
    pass


class ReplayConflict(R35Error):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise R35Error(f"{field} must be non-empty string")
    return value


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R35Error(f"{field} must be lowercase SHA-256")
    return value


def _git_sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R35Error(f"{field} must be exact Git SHA")
    return value


def _number(
    value: Any,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R35Error(f"{field} must be numeric")
    result = float(value)
    if not (result == result and abs(result) != float("inf")):
        raise R35Error(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise R35Error(f"{field} below minimum")
    if maximum is not None and result > maximum:
        raise R35Error(f"{field} above maximum")
    return result


def _dt(value: str, field: str) -> datetime:
    try:
        return parse_timestamp(value)
    except Exception as exc:
        raise R35Error(f"{field} must be valid timestamp") from exc


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def default_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.canary_evidence_registry.r35.v1"
        / "authority.json"
    )


def default_policy() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.canary_evidence_registry.r35.v1"
        / "policy.json"
    )


def _local_r34_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.counterfactual_policy_promotion.r34.v1"
        / "authority.json"
    )


def r34_tuple() -> dict[str, Any]:
    return {
        "producer_sha": R34_SHA,
        "ci_run_id": R34_CI,
        "artifact_id": R34_ARTIFACT_ID,
        "artifact_digest": R34_ARTIFACT_DIGEST,
        "contract": "growth.counterfactual_policy_promotion.r34.v1",
    }


def r33_tuple() -> dict[str, Any]:
    return {
        "producer_sha": R33_SHA,
        "ci_run_id": R33_CI,
        "artifact_id": R33_ARTIFACT_ID,
        "artifact_digest": R33_ARTIFACT_DIGEST,
        "contract": "growth.adaptive_portfolio_governor.r33.v1",
    }


def r32_tuple() -> dict[str, Any]:
    return {
        "producer_sha": R32_SHA,
        "ci_run_id": R32_CI,
        "artifact_id": R32_ARTIFACT_ID,
        "artifact_digest": R32_ARTIFACT_DIGEST,
        "contract": "growth.sequential_experiment_policy.r32.v1",
    }


def qa_r6_tuple() -> dict[str, Any]:
    return {
        "producer_sha": QA_R6_SHA,
        "ci_run_id": QA_R6_CI,
        "artifact_id": QA_R6_ARTIFACT_ID,
        "artifact_digest": QA_R6_ARTIFACT_DIGEST,
        "matrix_blob": QA_R6_MATRIX_BLOB,
        "disposition": "ACCEPTED",
    }


def validate_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "growth_r34_parent",
        "growth_r33_ancestry",
        "growth_r32_ancestry",
        "hard_wave_qa_r6",
        "evidence_boundary",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise AuthorityDrift("R35 authority fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R35 authority version mismatch")
    if value["growth_r34_parent"] != {
        "repository": "foto6/video3",
        "producer_sha": R34_SHA,
        "ci_run_id": R34_CI,
        "artifact_id": R34_ARTIFACT_ID,
        "artifact_name": "growth-r34-counterfactual-policy-promotion",
        "artifact_digest": R34_ARTIFACT_DIGEST,
        "contract": "growth.counterfactual_policy_promotion.r34.v1",
        "authority_blob": R34_AUTHORITY_BLOB,
        "policy_blob": R34_POLICY_BLOB,
        "contract_blob": R34_CONTRACT_BLOB,
        "implementation_blob": R34_IMPLEMENTATION_BLOB,
        "simulation_blob": R34_SIMULATION_BLOB,
    }:
        raise AuthorityDrift("exact R34 parent authority drift")
    if value["growth_r33_ancestry"] != {
        "producer_sha": R33_SHA,
        "ci_run_id": R33_CI,
        "artifact_id": R33_ARTIFACT_ID,
        "artifact_digest": R33_ARTIFACT_DIGEST,
        "contract": "growth.adaptive_portfolio_governor.r33.v1",
        "authority_blob": R33_AUTHORITY_BLOB,
    }:
        raise AuthorityDrift("exact R33 ancestry drift")
    if value["growth_r32_ancestry"] != {
        "producer_sha": R32_SHA,
        "ci_run_id": R32_CI,
        "artifact_id": R32_ARTIFACT_ID,
        "artifact_digest": R32_ARTIFACT_DIGEST,
        "contract": "growth.sequential_experiment_policy.r32.v1",
    }:
        raise AuthorityDrift("exact R32 ancestry drift")
    if value["hard_wave_qa_r6"] != {
        "repository": "foto6/boss",
        "producer_sha": QA_R6_SHA,
        "ci_run_id": QA_R6_CI,
        "artifact_id": QA_R6_ARTIFACT_ID,
        "artifact_name": "hard-wave-acceptance-r6-233a0e3d2237300a9b85b99965e27a190ebb362f",
        "artifact_digest": QA_R6_ARTIFACT_DIGEST,
        "matrix_path": "hardwave_qa/reports/COMPATIBILITY_MATRIX_R6.json",
        "matrix_blob": QA_R6_MATRIX_BLOB,
        "matrix_schema": "boss.hard_wave_acceptance_r6_matrix.v1",
        "growth_r34_disposition": "ACCEPTED",
    }:
        raise AuthorityDrift("exact Hard Wave QA-R6 authority drift")
    if value["evidence_boundary"] != {
        "advisory_only": True,
        "shadow_only": True,
        "live_authorization": False,
        "creator_mutation": False,
        "provider_mutation": False,
        "browser_mutation": False,
        "traffic_routing": False,
        "budget_allocation": False,
        "live_publish": False,
        "credential_access": False,
        "human_ground_truth": False,
        "merge": False,
    }:
        raise AuthorityDrift("R35 evidence boundary drift")
    r34.validate_authority(_local_r34_authority())
    return _clone(value)


def validate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "policy_version",
        "validity_window_seconds",
        "source_classes",
        "readiness",
        "conflicts",
        "supersession",
        "aggregation",
        "recommendations",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise RegistryConflict("R35 policy fields invalid")
    if value["contract_version"] != POLICY_VERSION or value["policy_version"] != 1:
        raise RegistryConflict("R35 policy version drift")
    if value["validity_window_seconds"] != 2592000:
        raise RegistryConflict("R35 validity window drift")
    if value["source_classes"] != {
        "RANDOMIZED": {
            "priority": 4,
            "weight": 4.0,
            "readiness_eligible": True,
            "causal_capable": True,
        },
        "OFF_POLICY_REPLAY": {
            "priority": 3,
            "weight": 3.0,
            "readiness_eligible": True,
            "causal_capable": False,
        },
        "OBSERVATIONAL": {
            "priority": 2,
            "weight": 1.0,
            "readiness_eligible": False,
            "causal_capable": False,
        },
        "SYNTHETIC_TEST": {
            "priority": 1,
            "weight": 0.0,
            "readiness_eligible": False,
            "causal_capable": False,
        },
    }:
        raise RegistryConflict("R35 source hierarchy drift")
    if value["readiness"] != {
        "minimum_independent_eligible_entries": 2,
        "minimum_distinct_underlying_corpora": 2,
        "minimum_effective_sample_size": 80.0,
        "minimum_behavior_propensity": 0.05,
        "maximum_drift_total_variation": 0.25,
        "candidate_recommendation": "SHADOW_CANARY_CANDIDATE",
        "negative_recommendations": [
            "SHADOW_ROLLBACK",
            "HUMAN_REVIEW_REQUIRED",
        ],
        "positive_confidence_lower_bound_min": 0.02,
    }:
        raise RegistryConflict("R35 readiness policy drift")
    if value["conflicts"] != {
        "contradictory_mature_evidence_requires_human_review": True,
        "critical_guardrail_veto_persists": True,
        "critical_stratum_reversal_veto_persists": True,
        "duplicate_exposure_identity_is_conflict": True,
        "same_policy_corpus_changed_bytes_is_conflict": True,
    }:
        raise RegistryConflict("R35 conflict policy drift")
    if value["supersession"] != {
        "explicit_parent_required": True,
        "strictly_stronger_source_class_required": True,
        "negative_history_retained": True,
        "cycle_rejected": True,
        "conflict_resolution_must_be_explicit": True,
    }:
        raise RegistryConflict("R35 supersession policy drift")
    if value["aggregation"] != {
        "method": "all_fresh_independent_evidence_with_vetoes",
        "no_best_corpus_selection": True,
        "no_negative_guardrail_averaging": True,
        "source_weighted_summary_is_diagnostic_only": True,
    }:
        raise RegistryConflict("R35 aggregation policy drift")
    if value["recommendations"] != [
        "NOT_READY",
        "TEST_MORE",
        "SHADOW_CANARY_CANDIDATE",
        "HUMAN_REVIEW_REQUIRED",
        "SHADOW_ROLLBACK",
    ]:
        raise RegistryConflict("R35 recommendations drift")
    return _clone(value)


def authority_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority(value))


def policy_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_policy(value))


def _derive_source_class(provenance: Mapping[str, Any]) -> str:
    fixture = provenance["fixture"]
    evidence_mode = provenance["evidence_mode"]
    derivation = provenance["derivation"]
    if fixture:
        return "SYNTHETIC_TEST"
    if evidence_mode == "observational_monitoring":
        return "OBSERVATIONAL"
    if evidence_mode != "randomized_controlled":
        raise EntryRejected("unsupported evidence_mode")
    if derivation == "direct_randomized":
        return "RANDOMIZED"
    if derivation == "off_policy_replay":
        return "OFF_POLICY_REPLAY"
    raise EntryRejected("unsupported evidence derivation")


def _validate_recommendation(value: str) -> str:
    if value not in {
        "KEEP_BASELINE",
        "TEST_MORE",
        "SHADOW_CANARY_CANDIDATE",
        "SHADOW_ROLLBACK",
        "HUMAN_REVIEW_REQUIRED",
    }:
        raise EntryRejected("R34 recommendation invalid")
    return value


def build_entry(
    *,
    policy: Mapping[str, Any],
    policy_id: str,
    policy_digest_value: str,
    corpus_id: str,
    corpus_digest: str,
    underlying_event_corpus_digest: str,
    exposure_identity_digest: str,
    estimator_config_digest: str,
    strata_guardrail_digest: str,
    decision_digest: str,
    decision_recommendation: str,
    confidence_interval: Mapping[str, Any],
    effective_sample_size: float,
    minimum_behavior_propensity: float,
    maximum_importance_weight: float,
    clipped_weight_sensitivity: float,
    unsupported_action_regions: int,
    drift_diagnostics: Mapping[str, Any],
    critical_guardrail_failures: Sequence[str],
    critical_stratum_reversals: Sequence[str],
    provenance: Mapping[str, Any],
    created_at: str,
    evidence_mature_at: str,
    maturity_window_seconds: int,
    maturity_state: str,
    supersedes_entry_ids: Sequence[str] = (),
    resolves_conflict_entry_ids: Sequence[str] = (),
) -> dict[str, Any]:
    policy_config = validate_policy(policy)
    _nonempty(policy_id, "policy_id")
    _sha(policy_digest_value, "policy_digest")
    _nonempty(corpus_id, "corpus_id")
    _sha(corpus_digest, "corpus_digest")
    _sha(underlying_event_corpus_digest, "underlying_event_corpus_digest")
    _sha(exposure_identity_digest, "exposure_identity_digest")
    _sha(estimator_config_digest, "estimator_config_digest")
    _sha(strata_guardrail_digest, "strata_guardrail_digest")
    _sha(decision_digest, "decision_digest")
    recommendation = _validate_recommendation(decision_recommendation)

    if not isinstance(confidence_interval, Mapping) or set(confidence_interval) != {
        "lower_95",
        "point_estimate",
        "upper_95",
    }:
        raise EntryRejected("confidence interval fields invalid")
    lower = _number(confidence_interval["lower_95"], "confidence.lower_95")
    point = _number(confidence_interval["point_estimate"], "confidence.point_estimate")
    upper = _number(confidence_interval["upper_95"], "confidence.upper_95")
    if not lower <= point <= upper:
        raise EntryRejected("confidence interval ordering invalid")

    ess = _number(effective_sample_size, "effective_sample_size", 0.0)
    min_prop = _number(
        minimum_behavior_propensity,
        "minimum_behavior_propensity",
        0.0,
        1.0,
    )
    max_weight = _number(maximum_importance_weight, "maximum_importance_weight", 0.0)
    clip_sensitivity = _number(
        clipped_weight_sensitivity,
        "clipped_weight_sensitivity",
        0.0,
    )
    if (
        isinstance(unsupported_action_regions, bool)
        or not isinstance(unsupported_action_regions, int)
        or unsupported_action_regions < 0
    ):
        raise EntryRejected("unsupported_action_regions invalid")

    if not isinstance(drift_diagnostics, Mapping) or set(drift_diagnostics) != {
        "platform_total_variation",
        "account_total_variation",
        "topic_total_variation",
        "stale_or_shifted",
    }:
        raise EntryRejected("drift diagnostics fields invalid")
    drift = {
        "platform_total_variation": _number(
            drift_diagnostics["platform_total_variation"],
            "platform_total_variation",
            0.0,
            1.0,
        ),
        "account_total_variation": _number(
            drift_diagnostics["account_total_variation"],
            "account_total_variation",
            0.0,
            1.0,
        ),
        "topic_total_variation": _number(
            drift_diagnostics["topic_total_variation"],
            "topic_total_variation",
            0.0,
            1.0,
        ),
        "stale_or_shifted": bool(drift_diagnostics["stale_or_shifted"]),
    }

    guardrail_failures = sorted(set(critical_guardrail_failures))
    stratum_reversals = sorted(set(critical_stratum_reversals))
    if any(not isinstance(x, str) or not x for x in guardrail_failures):
        raise EntryRejected("critical guardrail failure values invalid")
    if any(not isinstance(x, str) or not x for x in stratum_reversals):
        raise EntryRejected("critical stratum reversal values invalid")

    required_provenance = {
        "source_class",
        "evidence_mode",
        "derivation",
        "fixture",
        "causal_claim_allowed",
        "source_run_id",
        "source_artifact_id",
        "source_artifact_digest",
        "source_decision_digest",
        "trained_on_evaluation_corpus",
        "post_outcome_feature_leakage",
        "future_feature_leakage",
    }
    if not isinstance(provenance, Mapping) or set(provenance) != required_provenance:
        raise EntryRejected("provenance fields invalid")
    source_class = provenance["source_class"]
    if source_class not in SOURCE_CLASSES:
        raise EntryRejected("source class invalid")
    expected_class = _derive_source_class(provenance)
    if source_class != expected_class:
        raise EntryRejected(
            "metadata cannot upgrade evidence provenance class"
        )
    if not isinstance(provenance["source_run_id"], int) or isinstance(
        provenance["source_run_id"], bool
    ) or provenance["source_run_id"] < 1:
        raise EntryRejected("source_run_id invalid")
    if not isinstance(provenance["source_artifact_id"], int) or isinstance(
        provenance["source_artifact_id"], bool
    ) or provenance["source_artifact_id"] < 1:
        raise EntryRejected("source_artifact_id invalid")
    artifact_digest = provenance["source_artifact_digest"]
    if (
        not isinstance(artifact_digest, str)
        or not artifact_digest.startswith("sha256:")
        or len(artifact_digest) != 71
    ):
        raise EntryRejected("source_artifact_digest invalid")
    _sha(artifact_digest.removeprefix("sha256:"), "source_artifact_digest")
    _sha(provenance["source_decision_digest"], "source_decision_digest")
    if provenance["post_outcome_feature_leakage"] is not False:
        raise EntryRejected("post-outcome feature leakage hard rejection")
    if provenance["future_feature_leakage"] is not False:
        raise EntryRejected("future-feature leakage hard rejection")
    if provenance["causal_claim_allowed"] and source_class != "RANDOMIZED":
        raise EntryRejected("non-randomized evidence cannot claim causal status")
    if provenance["trained_on_evaluation_corpus"]:
        if source_class not in {"OBSERVATIONAL", "SYNTHETIC_TEST"}:
            raise EntryRejected(
                "evaluation-corpus training disqualifies readiness-eligible evidence"
            )

    created = _dt(created_at, "created_at")
    mature = _dt(evidence_mature_at, "evidence_mature_at")
    if mature < created:
        raise EntryRejected("evidence maturity precedes creation")
    if (
        isinstance(maturity_window_seconds, bool)
        or not isinstance(maturity_window_seconds, int)
        or maturity_window_seconds < 1
    ):
        raise EntryRejected("maturity_window_seconds invalid")
    if maturity_state not in {"MATURE", "CENSORED", "DELAYED"}:
        raise EntryRejected("maturity_state invalid")
    expires = created + timedelta(seconds=policy_config["validity_window_seconds"])

    supersedes = sorted(set(supersedes_entry_ids))
    resolves = sorted(set(resolves_conflict_entry_ids))
    if any(not isinstance(x, str) or not x for x in supersedes + resolves):
        raise EntryRejected("supersession references invalid")
    if any(not x.startswith("gr35e1:") for x in supersedes + resolves):
        raise EntryRejected("supersession references must be R35 entry IDs")

    # This digest intentionally excludes run/artifact metadata and source-class labels.
    # It is the core evidence identity used to catch metadata-only provenance upgrades.
    evidence_payload_material = {
        "policy_id": policy_id,
        "policy_digest": policy_digest_value,
        "corpus_id": corpus_id,
        "corpus_digest": corpus_digest,
        "underlying_event_corpus_digest": underlying_event_corpus_digest,
        "exposure_identity_digest": exposure_identity_digest,
        "estimator_config_digest": estimator_config_digest,
        "strata_guardrail_digest": strata_guardrail_digest,
        "decision_digest": decision_digest,
        "decision_recommendation": recommendation,
        "confidence_interval": {
            "lower_95": lower,
            "point_estimate": point,
            "upper_95": upper,
        },
        "diagnostics": {
            "effective_sample_size": ess,
            "minimum_behavior_propensity": min_prop,
            "maximum_importance_weight": max_weight,
            "clipped_weight_sensitivity": clip_sensitivity,
            "unsupported_action_regions": unsupported_action_regions,
        },
        "drift_diagnostics": drift,
        "critical_guardrail_failures": guardrail_failures,
        "critical_stratum_reversals": stratum_reversals,
        "trained_on_evaluation_corpus": bool(
            provenance["trained_on_evaluation_corpus"]
        ),
        "post_outcome_feature_leakage": False,
        "future_feature_leakage": False,
        "created_at": created_at,
        "evidence_mature_at": evidence_mature_at,
        "maturity_window_seconds": maturity_window_seconds,
        "maturity_state": maturity_state,
        "r34_authority": r34_tuple(),
        "r33_ancestry": r33_tuple(),
        "r32_ancestry": r32_tuple(),
    }
    evidence_payload_digest = sha256_json(evidence_payload_material)

    material = {
        "contract_version": ENTRY_VERSION,
        "registry_entry_id": "",
        "entry_digest": "",
        "evidence_payload_digest": evidence_payload_digest,
        "policy_id": policy_id,
        "policy_digest": policy_digest_value,
        "corpus_id": corpus_id,
        "corpus_digest": corpus_digest,
        "underlying_event_corpus_digest": underlying_event_corpus_digest,
        "exposure_identity_digest": exposure_identity_digest,
        "estimator_config_digest": estimator_config_digest,
        "strata_guardrail_digest": strata_guardrail_digest,
        "decision_digest": decision_digest,
        "decision_recommendation": recommendation,
        "confidence_interval": {
            "lower_95": lower,
            "point_estimate": point,
            "upper_95": upper,
        },
        "diagnostics": {
            "effective_sample_size": ess,
            "minimum_behavior_propensity": min_prop,
            "maximum_importance_weight": max_weight,
            "clipped_weight_sensitivity": clip_sensitivity,
            "unsupported_action_regions": unsupported_action_regions,
        },
        "drift_diagnostics": drift,
        "critical_guardrail_failures": guardrail_failures,
        "critical_stratum_reversals": stratum_reversals,
        "provenance": {
            "source_class": source_class,
            "evidence_mode": provenance["evidence_mode"],
            "derivation": provenance["derivation"],
            "fixture": bool(provenance["fixture"]),
            "causal_claim_allowed": bool(provenance["causal_claim_allowed"]),
            "source_run_id": provenance["source_run_id"],
            "source_artifact_id": provenance["source_artifact_id"],
            "source_artifact_digest": provenance["source_artifact_digest"],
            "source_decision_digest": provenance["source_decision_digest"],
            "trained_on_evaluation_corpus": bool(
                provenance["trained_on_evaluation_corpus"]
            ),
            "post_outcome_feature_leakage": False,
            "future_feature_leakage": False,
            "readiness_disqualified": bool(
                provenance["trained_on_evaluation_corpus"]
                and source_class in {"OBSERVATIONAL", "SYNTHETIC_TEST"}
            ),
        },
        "created_at": created_at,
        "maturity": {
            "state": maturity_state,
            "evidence_mature_at": evidence_mature_at,
            "maturity_window_seconds": maturity_window_seconds,
            "expires_at": _iso(expires),
        },
        "authority": {
            "growth_r34": r34_tuple(),
            "growth_r33": r33_tuple(),
            "growth_r32": r32_tuple(),
            "qa_r6": qa_r6_tuple(),
        },
        "supersedes_entry_ids": supersedes,
        "resolves_conflict_entry_ids": resolves,
        "advisory_boundary": {
            "advisory_only": True,
            "live_authorization": False,
            "creator_mutation_allowed": False,
            "provider_mutation_allowed": False,
            "browser_mutation_allowed": False,
            "traffic_allocation_allowed": False,
            "publish_allowed": False,
            "credential_access_allowed": False,
            "human_ground_truth": False,
        },
    }
    id_material = {
        "evidence_payload_digest": evidence_payload_digest,
        "source_class": source_class,
        "source_run_id": provenance["source_run_id"],
        "source_artifact_id": provenance["source_artifact_id"],
        "source_artifact_digest": provenance["source_artifact_digest"],
        "supersedes_entry_ids": supersedes,
        "resolves_conflict_entry_ids": resolves,
    }
    material["registry_entry_id"] = "gr35e1:" + sha256_json(id_material)
    digest_material = copy.deepcopy(material)
    digest_material["entry_digest"] = ""
    material["entry_digest"] = sha256_json(digest_material)
    return _clone(material)


def parse_entry(value: Mapping[str, Any], *, policy: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "registry_entry_id",
        "entry_digest",
        "evidence_payload_digest",
        "policy_id",
        "policy_digest",
        "corpus_id",
        "corpus_digest",
        "underlying_event_corpus_digest",
        "exposure_identity_digest",
        "estimator_config_digest",
        "strata_guardrail_digest",
        "decision_digest",
        "decision_recommendation",
        "confidence_interval",
        "diagnostics",
        "drift_diagnostics",
        "critical_guardrail_failures",
        "critical_stratum_reversals",
        "provenance",
        "created_at",
        "maturity",
        "authority",
        "supersedes_entry_ids",
        "resolves_conflict_entry_ids",
        "advisory_boundary",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise EntryRejected("registry entry fields invalid")
    if value["contract_version"] != ENTRY_VERSION:
        raise EntryRejected("registry entry contract mismatch")
    if value["authority"] != {
        "growth_r34": r34_tuple(),
        "growth_r33": r33_tuple(),
        "growth_r32": r32_tuple(),
        "qa_r6": qa_r6_tuple(),
    }:
        raise AuthorityDrift("registry entry authority tuple drift")
    p = value["provenance"]
    rebuilt = build_entry(
        policy=policy,
        policy_id=value["policy_id"],
        policy_digest_value=value["policy_digest"],
        corpus_id=value["corpus_id"],
        corpus_digest=value["corpus_digest"],
        underlying_event_corpus_digest=value["underlying_event_corpus_digest"],
        exposure_identity_digest=value["exposure_identity_digest"],
        estimator_config_digest=value["estimator_config_digest"],
        strata_guardrail_digest=value["strata_guardrail_digest"],
        decision_digest=value["decision_digest"],
        decision_recommendation=value["decision_recommendation"],
        confidence_interval=value["confidence_interval"],
        effective_sample_size=value["diagnostics"]["effective_sample_size"],
        minimum_behavior_propensity=value["diagnostics"][
            "minimum_behavior_propensity"
        ],
        maximum_importance_weight=value["diagnostics"]["maximum_importance_weight"],
        clipped_weight_sensitivity=value["diagnostics"][
            "clipped_weight_sensitivity"
        ],
        unsupported_action_regions=value["diagnostics"]["unsupported_action_regions"],
        drift_diagnostics=value["drift_diagnostics"],
        critical_guardrail_failures=value["critical_guardrail_failures"],
        critical_stratum_reversals=value["critical_stratum_reversals"],
        provenance={
            "source_class": p["source_class"],
            "evidence_mode": p["evidence_mode"],
            "derivation": p["derivation"],
            "fixture": p["fixture"],
            "causal_claim_allowed": p["causal_claim_allowed"],
            "source_run_id": p["source_run_id"],
            "source_artifact_id": p["source_artifact_id"],
            "source_artifact_digest": p["source_artifact_digest"],
            "source_decision_digest": p["source_decision_digest"],
            "trained_on_evaluation_corpus": p["trained_on_evaluation_corpus"],
            "post_outcome_feature_leakage": p["post_outcome_feature_leakage"],
            "future_feature_leakage": p["future_feature_leakage"],
        },
        created_at=value["created_at"],
        evidence_mature_at=value["maturity"]["evidence_mature_at"],
        maturity_window_seconds=value["maturity"]["maturity_window_seconds"],
        maturity_state=value["maturity"]["state"],
        supersedes_entry_ids=value["supersedes_entry_ids"],
        resolves_conflict_entry_ids=value["resolves_conflict_entry_ids"],
    )
    if rebuilt != value:
        raise EntryRejected("registry entry digest or canonical bytes mismatch")
    return rebuilt


def _conflict(
    code: str,
    entry_ids: Sequence[str],
    detail: str,
) -> dict[str, Any]:
    ids = sorted(set(entry_ids))
    return {
        "conflict_id": "gr35c1:" + sha256_json(
            {"code": code, "entry_ids": ids, "detail": detail}
        ),
        "code": code,
        "entry_ids": ids,
        "detail": detail,
    }


def _supersession_graph(
    entries: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> tuple[dict[str, list[str]], list[dict[str, Any]], set[str]]:
    by_id = {entry["registry_entry_id"]: entry for entry in entries}
    graph: dict[str, list[str]] = {
        entry_id: [] for entry_id in by_id
    }
    conflicts: list[dict[str, Any]] = []
    valid_superseded: set[str] = set()
    priorities = {
        source: meta["priority"]
        for source, meta in policy["source_classes"].items()
    }
    for entry in entries:
        child_id = entry["registry_entry_id"]
        child_priority = priorities[entry["provenance"]["source_class"]]
        for parent_id in entry["supersedes_entry_ids"]:
            if parent_id not in by_id:
                conflicts.append(
                    _conflict(
                        "SUPERSESSION_PARENT_MISSING",
                        [child_id, parent_id],
                        "supersession references absent registry entry",
                    )
                )
                continue
            parent = by_id[parent_id]
            parent_priority = priorities[parent["provenance"]["source_class"]]
            graph[child_id].append(parent_id)
            if child_priority <= parent_priority:
                conflicts.append(
                    _conflict(
                        "SUPERSESSION_TO_WEAKER_OR_EQUAL_EVIDENCE",
                        [child_id, parent_id],
                        "successor source class must be strictly stronger",
                    )
                )
            else:
                valid_superseded.add(parent_id)

    # Cycle detection is deterministic over sorted IDs.
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        status = state.get(node, 0)
        if status == 2:
            return
        if status == 1:
            if node in stack:
                cycle = stack[stack.index(node):] + [node]
            else:
                cycle = [node]
            conflicts.append(
                _conflict(
                    "SUPERSESSION_CYCLE",
                    cycle,
                    "supersession graph contains a cycle",
                )
            )
            return
        state[node] = 1
        stack.append(node)
        for parent in sorted(graph[node]):
            if parent in graph:
                visit(parent)
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        visit(node)

    cycle_ids = {
        entry_id
        for conflict in conflicts
        if conflict["code"] == "SUPERSESSION_CYCLE"
        for entry_id in conflict["entry_ids"]
    }
    valid_superseded -= cycle_ids
    return (
        {key: sorted(value) for key, value in sorted(graph.items())},
        sorted(conflicts, key=lambda x: x["conflict_id"]),
        valid_superseded,
    )


def analyze_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    policy_config = validate_policy(policy)
    parsed = [parse_entry(entry, policy=policy_config) for entry in entries]
    parsed.sort(key=lambda x: x["registry_entry_id"])
    conflicts: list[dict[str, Any]] = []
    duplicate_groups: list[dict[str, Any]] = []

    by_policy_corpus: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_payload: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_exposure: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in parsed:
        by_policy_corpus[(entry["policy_digest"], entry["corpus_digest"])].append(
            entry
        )
        by_payload[entry["evidence_payload_digest"]].append(entry)
        by_exposure[entry["exposure_identity_digest"]].append(entry)

    for (policy_digest_value, corpus_digest), group in sorted(by_policy_corpus.items()):
        payloads = sorted({entry["evidence_payload_digest"] for entry in group})
        if len(payloads) > 1:
            conflicts.append(
                _conflict(
                    "SAME_POLICY_CORPUS_CHANGED_BYTES",
                    [entry["registry_entry_id"] for entry in group],
                    f"policy {policy_digest_value} corpus {corpus_digest} has changed evidence payload bytes",
                )
            )

    for payload_digest, group in sorted(by_payload.items()):
        classes = sorted({entry["provenance"]["source_class"] for entry in group})
        if len(classes) > 1:
            conflicts.append(
                _conflict(
                    "PROVENANCE_CLASS_UPGRADE_ATTEMPT",
                    [entry["registry_entry_id"] for entry in group],
                    f"same evidence payload {payload_digest} appears under multiple provenance classes {classes}",
                )
            )
        if len(group) > 1:
            canonical = min(entry["registry_entry_id"] for entry in group)
            duplicates = sorted(
                entry["registry_entry_id"]
                for entry in group
                if entry["registry_entry_id"] != canonical
            )
            duplicate_groups.append(
                {
                    "evidence_payload_digest": payload_digest,
                    "underlying_event_corpus_digests": sorted(
                        {
                            entry["underlying_event_corpus_digest"]
                            for entry in group
                        }
                    ),
                    "canonical_entry_id": canonical,
                    "duplicate_entry_ids": duplicates,
                    "detected_cross_run_copy": bool(
                        len({entry["provenance"]["source_run_id"] for entry in group})
                        > 1
                    ),
                    "counted_once": True,
                }
            )

    for exposure_digest, group in sorted(by_exposure.items()):
        corpora = {
            entry["underlying_event_corpus_digest"] for entry in group
        }
        if len(corpora) > 1:
            conflicts.append(
                _conflict(
                    "DUPLICATE_EXPOSURE_IDENTITY_ACROSS_CORPORA",
                    [entry["registry_entry_id"] for entry in group],
                    f"exposure identity {exposure_digest} appears in multiple underlying corpora",
                )
            )

    graph, supersession_conflicts, valid_superseded = _supersession_graph(
        parsed, policy_config
    )
    conflicts.extend(supersession_conflicts)

    # Explicit conflict resolution is valid only when a strictly stronger child
    # actually supersedes the referenced parent.
    priorities = {
        source: meta["priority"]
        for source, meta in policy_config["source_classes"].items()
    }
    by_id = {entry["registry_entry_id"]: entry for entry in parsed}
    resolved_ids: set[str] = set()
    for entry in parsed:
        child_priority = priorities[entry["provenance"]["source_class"]]
        for target_id in entry["resolves_conflict_entry_ids"]:
            target = by_id.get(target_id)
            if target is None:
                conflicts.append(
                    _conflict(
                        "CONFLICT_RESOLUTION_TARGET_MISSING",
                        [entry["registry_entry_id"], target_id],
                        "resolution target is absent",
                    )
                )
                continue
            if target_id not in entry["supersedes_entry_ids"]:
                conflicts.append(
                    _conflict(
                        "CONFLICT_RESOLUTION_WITHOUT_SUPERSESSION",
                        [entry["registry_entry_id"], target_id],
                        "conflict resolution requires explicit supersession parent",
                    )
                )
                continue
            target_priority = priorities[target["provenance"]["source_class"]]
            if child_priority <= target_priority:
                conflicts.append(
                    _conflict(
                        "CONFLICT_RESOLUTION_NOT_STRONGER",
                        [entry["registry_entry_id"], target_id],
                        "conflict resolution requires strictly stronger evidence",
                    )
                )
                continue
            if entry["decision_recommendation"] in {
                "HUMAN_REVIEW_REQUIRED",
                "SHADOW_ROLLBACK",
            }:
                conflicts.append(
                    _conflict(
                        "CONFLICT_RESOLUTION_SUCCESSOR_NEGATIVE",
                        [entry["registry_entry_id"], target_id],
                        "negative successor cannot resolve prior conflict",
                    )
                )
                continue
            resolved_ids.add(target_id)

    unique_conflicts = {
        conflict["conflict_id"]: conflict for conflict in conflicts
    }
    return {
        "entries": parsed,
        "conflicts": [
            unique_conflicts[key] for key in sorted(unique_conflicts)
        ],
        "duplicate_groups": sorted(
            duplicate_groups,
            key=lambda x: (
                x["canonical_entry_id"],
                x["evidence_payload_digest"],
            ),
        ),
        "supersession_graph": graph,
        "valid_superseded_entry_ids": sorted(valid_superseded),
        "explicitly_resolved_entry_ids": sorted(resolved_ids),
    }


class EvidenceRegistry:
    def __init__(
        self,
        *,
        registry_id: str,
        path: Path | None,
        authority: Mapping[str, Any],
        policy: Mapping[str, Any],
    ) -> None:
        self.registry_id = _nonempty(registry_id, "registry_id")
        self.path = None if path is None else Path(path)
        self.authority = validate_authority(authority)
        self.policy = validate_policy(policy)
        self.entries: dict[str, dict[str, Any]] = {}
        if self.path is not None and self.path.exists():
            state = _load(self.path)
            if state.get("registry_id") != self.registry_id:
                raise RegistryConflict("registry_id mismatch with durable ledger")
            for raw in state.get("entries", []):
                entry = parse_entry(raw, policy=self.policy)
                self.entries[entry["registry_entry_id"]] = entry

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "registry_id": self.registry_id,
            "entries": [
                self.entries[key] for key in sorted(self.entries)
            ],
        }
        _write(self.path, state)

    def register(self, raw_entry: Mapping[str, Any]) -> bool:
        entry = parse_entry(raw_entry, policy=self.policy)
        entry_id = entry["registry_entry_id"]
        prior = self.entries.get(entry_id)
        if prior is not None:
            if prior["entry_digest"] != entry["entry_digest"]:
                raise ReplayConflict(
                    "same registry_entry_id changed immutable bytes"
                )
            return False
        self.entries[entry_id] = entry
        self._save()
        return True

    def register_many(self, entries: Sequence[Mapping[str, Any]]) -> list[bool]:
        # Parse first so hard rejects cannot partially mutate the registry.
        parsed = [parse_entry(entry, policy=self.policy) for entry in entries]
        temp = dict(self.entries)
        results = []
        for entry in parsed:
            entry_id = entry["registry_entry_id"]
            prior = temp.get(entry_id)
            if prior is not None:
                if prior["entry_digest"] != entry["entry_digest"]:
                    raise ReplayConflict(
                        "same registry_entry_id changed immutable bytes"
                    )
                results.append(False)
                continue
            temp[entry_id] = entry
            results.append(True)
        self.entries = temp
        self._save()
        return results

    def snapshot(
        self,
        *,
        snapshot_at: str,
        growth_sha: str,
        growth_ci_run_id: int,
    ) -> dict[str, Any]:
        _dt(snapshot_at, "snapshot_at")
        _git_sha(growth_sha, "growth_sha")
        if (
            isinstance(growth_ci_run_id, bool)
            or not isinstance(growth_ci_run_id, int)
            or growth_ci_run_id < 1
        ):
            raise R35Error("growth_ci_run_id invalid")
        analysis = analyze_entries(
            list(self.entries.values()),
            policy=self.policy,
        )
        material = {
            "contract_version": SNAPSHOT_VERSION,
            "registry_id": self.registry_id,
            "registry_snapshot_digest": "",
            "snapshot_at": snapshot_at,
            "policy_version": self.policy["policy_version"],
            "registry_policy_digest": policy_digest(self.policy),
            "authority": {
                "growth_r35": {
                    "producer_sha": growth_sha,
                    "ci_run_id": growth_ci_run_id,
                    "contract": CONTRACT_VERSION,
                    "authority_digest": authority_digest(self.authority),
                },
                "growth_r34": r34_tuple(),
                "growth_r33": r33_tuple(),
                "growth_r32": r32_tuple(),
                "qa_r6": qa_r6_tuple(),
            },
            "entries": analysis["entries"],
            "conflicts": analysis["conflicts"],
            "duplicate_groups": analysis["duplicate_groups"],
            "supersession_graph": analysis["supersession_graph"],
            "valid_superseded_entry_ids": analysis[
                "valid_superseded_entry_ids"
            ],
            "explicitly_resolved_entry_ids": analysis[
                "explicitly_resolved_entry_ids"
            ],
            "advisory_boundary": {
                "advisory_only": True,
                "live_authorization": False,
                "creator_mutation_allowed": False,
                "provider_mutation_allowed": False,
                "browser_mutation_allowed": False,
                "traffic_allocation_allowed": False,
                "publish_allowed": False,
                "credential_access_allowed": False,
                "human_ground_truth": False,
            },
        }
        digest_material = copy.deepcopy(material)
        digest_material["registry_snapshot_digest"] = ""
        material["registry_snapshot_digest"] = sha256_json(digest_material)
        return _clone(material)


def parse_snapshot(
    snapshot: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    validate_authority(authority)
    validate_policy(policy)
    expected = {
        "contract_version",
        "registry_id",
        "registry_snapshot_digest",
        "snapshot_at",
        "policy_version",
        "registry_policy_digest",
        "authority",
        "entries",
        "conflicts",
        "duplicate_groups",
        "supersession_graph",
        "valid_superseded_entry_ids",
        "explicitly_resolved_entry_ids",
        "advisory_boundary",
    }
    if not isinstance(snapshot, Mapping) or set(snapshot) != expected:
        raise RegistryConflict("registry snapshot fields invalid")
    if snapshot["contract_version"] != SNAPSHOT_VERSION:
        raise RegistryConflict("registry snapshot contract mismatch")
    if snapshot["registry_policy_digest"] != policy_digest(policy):
        raise RegistryConflict("registry snapshot policy digest mismatch")
    if snapshot["authority"]["growth_r34"] != r34_tuple():
        raise AuthorityDrift("snapshot R34 authority drift")
    if snapshot["authority"]["growth_r33"] != r33_tuple():
        raise AuthorityDrift("snapshot R33 authority drift")
    if snapshot["authority"]["growth_r32"] != r32_tuple():
        raise AuthorityDrift("snapshot R32 authority drift")
    if snapshot["authority"]["qa_r6"] != qa_r6_tuple():
        raise AuthorityDrift("snapshot QA-R6 authority drift")
    digest_material = copy.deepcopy(snapshot)
    digest_material["registry_snapshot_digest"] = ""
    if snapshot["registry_snapshot_digest"] != sha256_json(digest_material):
        raise RegistryConflict("registry snapshot digest mismatch")
    # Reanalyze to detect tampering in conflicts/duplicates/supersession fields.
    analysis = analyze_entries(snapshot["entries"], policy=policy)
    for field in (
        "conflicts",
        "duplicate_groups",
        "supersession_graph",
        "valid_superseded_entry_ids",
        "explicitly_resolved_entry_ids",
    ):
        if snapshot[field] != analysis[field]:
            raise RegistryConflict(f"registry snapshot {field} mismatch")
    return _clone(snapshot)


def _duplicate_exclusions(snapshot: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for group in snapshot["duplicate_groups"]:
        for entry_id in group["duplicate_entry_ids"]:
            result[entry_id] = "COPIED_REPLAY_SAME_UNDERLYING_EVIDENCE"
    return result


def _supersession_exclusions(snapshot: Mapping[str, Any]) -> dict[str, str]:
    resolved = set(snapshot["explicitly_resolved_entry_ids"])
    result: dict[str, str] = {}
    for entry_id in snapshot["valid_superseded_entry_ids"]:
        result[entry_id] = (
            "SUPERSEDED_CONFLICT_EXPLICITLY_RESOLVED_BY_STRONGER_EVIDENCE"
            if entry_id in resolved
            else "SUPERSEDED_BY_STRONGER_EVIDENCE"
        )
    return result


def _certificate_conflicts(
    snapshot: Mapping[str, Any],
    target_entries: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
) -> list[dict[str, Any]]:
    target_ids = {entry["registry_entry_id"] for entry in target_entries}
    unresolved = [
        conflict
        for conflict in snapshot["conflicts"]
        if target_ids.intersection(conflict["entry_ids"])
    ]
    resolved_ids = set(snapshot["explicitly_resolved_entry_ids"])
    # Contradiction detection spans mature, fresh, non-synthetic evidence classes.
    candidate_entries = []
    negative_entries = []
    for entry in target_entries:
        if entry["registry_entry_id"] in resolved_ids:
            continue
        if entry["maturity"]["state"] != "MATURE":
            continue
        if _dt(entry["maturity"]["expires_at"], "expires_at") < as_of:
            continue
        if entry["provenance"]["source_class"] == "SYNTHETIC_TEST":
            continue
        if entry["decision_recommendation"] == "SHADOW_CANARY_CANDIDATE":
            candidate_entries.append(entry)
        if entry["decision_recommendation"] in {
            "SHADOW_ROLLBACK",
            "HUMAN_REVIEW_REQUIRED",
        }:
            negative_entries.append(entry)
    if candidate_entries and negative_entries:
        unresolved.append(
            _conflict(
                "CONTRADICTORY_MATURE_EVIDENCE",
                [
                    entry["registry_entry_id"]
                    for entry in candidate_entries + negative_entries
                ],
                "mature positive and negative evidence coexist across the registry",
            )
        )
    unique = {
        conflict["conflict_id"]: conflict for conflict in unresolved
    }
    return [unique[key] for key in sorted(unique)]


def _weighted_summary(
    entries: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    rows = []
    numerator = 0.0
    weight_total = 0.0
    for entry in entries:
        meta = policy["source_classes"][entry["provenance"]["source_class"]]
        weight = float(meta["weight"])
        point = float(entry["confidence_interval"]["point_estimate"])
        rows.append(
            {
                "registry_entry_id": entry["registry_entry_id"],
                "source_class": entry["provenance"]["source_class"],
                "weight": weight,
                "point_estimate": point,
            }
        )
        numerator += weight * point
        weight_total += weight
    return {
        "method": policy["aggregation"]["method"],
        "diagnostic_only": True,
        "rows": sorted(rows, key=lambda x: x["registry_entry_id"]),
        "weighted_point_estimate": (
            None if weight_total == 0 else round(numerator / weight_total, 12)
        ),
        "weight_total": round(weight_total, 12),
        "negative_guardrail_evidence_can_be_averaged_away": False,
        "best_corpus_cherry_picking_allowed": False,
    }


def build_certificate(
    *,
    snapshot: Mapping[str, Any],
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    target_policy_digest: str,
    as_of: str,
) -> dict[str, Any]:
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    snapshot = parse_snapshot(snapshot, authority=authority, policy=policy)
    _sha(target_policy_digest, "target_policy_digest")
    as_of_dt = _dt(as_of, "as_of")
    target_entries = [
        entry
        for entry in snapshot["entries"]
        if entry["policy_digest"] == target_policy_digest
    ]

    duplicate_excluded = _duplicate_exclusions(snapshot)
    supersession_excluded = _supersession_exclusions(snapshot)
    excluded: list[dict[str, str]] = []
    active: list[dict[str, Any]] = []
    negative_visible: list[dict[str, Any]] = []
    no_current_authority = False

    for entry in target_entries:
        entry_id = entry["registry_entry_id"]
        reason: str | None = None
        if entry["authority"]["growth_r34"] != r34_tuple():
            reason = "STALE_R34_AUTHORITY"
            no_current_authority = True
        elif entry_id in duplicate_excluded:
            reason = duplicate_excluded[entry_id]
        elif entry_id in supersession_excluded:
            reason = supersession_excluded[entry_id]
        elif _dt(entry["maturity"]["expires_at"], "expires_at") < as_of_dt:
            reason = "EXPIRED_EVIDENCE"
        elif entry["maturity"]["state"] != "MATURE":
            reason = "EVIDENCE_NOT_MATURE"
        elif entry["drift_diagnostics"]["stale_or_shifted"]:
            reason = "DISTRIBUTION_SHIFTED"
        elif (
            entry["diagnostics"]["effective_sample_size"]
            < policy["readiness"]["minimum_effective_sample_size"]
        ):
            reason = "EFFECTIVE_SAMPLE_SIZE_BELOW_FLOOR"
        elif (
            entry["diagnostics"]["minimum_behavior_propensity"]
            < policy["readiness"]["minimum_behavior_propensity"]
        ):
            reason = "OVERLAP_BELOW_THRESHOLD"
        elif entry["diagnostics"]["unsupported_action_regions"] > 0:
            reason = "UNSUPPORTED_ACTION_REGION"
        elif entry["provenance"]["readiness_disqualified"]:
            reason = "EVALUATION_LEAKAGE_NONCAUSAL_DISQUALIFIED"
        elif not policy["source_classes"][
            entry["provenance"]["source_class"]
        ]["readiness_eligible"]:
            reason = "SOURCE_CLASS_NOT_READINESS_ELIGIBLE"

        if reason is None:
            active.append(entry)
        else:
            excluded.append(
                {
                    "registry_entry_id": entry_id,
                    "reason": reason,
                }
            )
        if entry["decision_recommendation"] in {
            "SHADOW_ROLLBACK",
            "HUMAN_REVIEW_REQUIRED",
        }:
            negative_visible.append(entry)

    unresolved = _certificate_conflicts(
        snapshot,
        target_entries,
        as_of=as_of_dt,
    )

    # Vetoes are evaluated across fresh mature non-synthetic evidence even if it
    # is otherwise excluded from positive readiness aggregation.
    critical_veto_ids = []
    for entry in target_entries:
        if entry["maturity"]["state"] != "MATURE":
            continue
        if _dt(entry["maturity"]["expires_at"], "expires_at") < as_of_dt:
            continue
        if entry["provenance"]["source_class"] == "SYNTHETIC_TEST":
            continue
        if (
            entry["critical_guardrail_failures"]
            or entry["critical_stratum_reversals"]
        ):
            critical_veto_ids.append(entry["registry_entry_id"])

    active_candidate = [
        entry
        for entry in active
        if entry["decision_recommendation"] == "SHADOW_CANARY_CANDIDATE"
    ]
    active_rollback = [
        entry
        for entry in active
        if entry["decision_recommendation"] == "SHADOW_ROLLBACK"
    ]
    active_human = [
        entry
        for entry in active
        if entry["decision_recommendation"] == "HUMAN_REVIEW_REQUIRED"
    ]
    active_other = [
        entry
        for entry in active
        if entry["decision_recommendation"]
        in {"KEEP_BASELINE", "TEST_MORE"}
    ]

    readiness = "NOT_READY"
    reason_codes: list[str] = []
    if no_current_authority:
        readiness = "NOT_READY"
        reason_codes.append("STALE_PARENT_AUTHORITY")
    elif unresolved:
        readiness = "HUMAN_REVIEW_REQUIRED"
        reason_codes.append("UNRESOLVED_REGISTRY_CONFLICT")
    elif critical_veto_ids:
        readiness = "HUMAN_REVIEW_REQUIRED"
        reason_codes.append("CRITICAL_GUARDRAIL_OR_STRATUM_VETO")
    elif active_human:
        readiness = "HUMAN_REVIEW_REQUIRED"
        reason_codes.append("ACTIVE_HUMAN_REVIEW_EVIDENCE")
    elif active_rollback and active_candidate:
        readiness = "HUMAN_REVIEW_REQUIRED"
        reason_codes.append("CONTRADICTORY_ELIGIBLE_EVIDENCE")
    elif active_rollback:
        readiness = "SHADOW_ROLLBACK"
        reason_codes.append("ACTIVE_ROLLBACK_EVIDENCE")
    elif active_candidate:
        distinct_corpora = {
            entry["underlying_event_corpus_digest"]
            for entry in active_candidate
        }
        lower_bounds_clear = all(
            float(entry["confidence_interval"]["lower_95"])
            >= float(
                policy["readiness"]["positive_confidence_lower_bound_min"]
            )
            for entry in active_candidate
        )
        enough = (
            len(active_candidate)
            >= policy["readiness"]["minimum_independent_eligible_entries"]
            and len(distinct_corpora)
            >= policy["readiness"]["minimum_distinct_underlying_corpora"]
        )
        if enough and lower_bounds_clear and not active_other:
            readiness = "SHADOW_CANARY_CANDIDATE"
            reason_codes.append("INDEPENDENT_FRESH_ELIGIBLE_SUPPORT")
        else:
            readiness = "TEST_MORE"
            if not enough:
                reason_codes.append("INSUFFICIENT_INDEPENDENT_EVIDENCE")
            if not lower_bounds_clear:
                reason_codes.append("CONFIDENCE_BOUND_BELOW_READINESS_MARGIN")
            if active_other:
                reason_codes.append("MIXED_NONNEGATIVE_EVIDENCE")
    elif active_other:
        readiness = "TEST_MORE"
        reason_codes.append("ELIGIBLE_EVIDENCE_INCONCLUSIVE")
    elif target_entries:
        readiness = "NOT_READY"
        reason_codes.append("NO_FRESH_READINESS_ELIGIBLE_EVIDENCE")
    else:
        readiness = "NOT_READY"
        reason_codes.append("NO_EVIDENCE_FOR_POLICY")

    used_ids = sorted(
        entry["registry_entry_id"]
        for entry in active
    )
    excluded_sorted = sorted(
        excluded,
        key=lambda x: (x["registry_entry_id"], x["reason"]),
    )
    corpus_digests = sorted(
        {entry["corpus_digest"] for entry in target_entries}
    )
    underlying_digests = sorted(
        {
            entry["underlying_event_corpus_digest"]
            for entry in target_entries
        }
    )
    aggregation = _weighted_summary(active, policy)
    certificate = {
        "contract_version": CERTIFICATE_VERSION,
        "certificate_id": "",
        "certificate_digest": "",
        "status": STATUS,
        "disposition": "ADVISORY_ONLY",
        "registry_id": snapshot["registry_id"],
        "registry_snapshot_digest": snapshot["registry_snapshot_digest"],
        "as_of": as_of,
        "policy_digest": target_policy_digest,
        "policy_corpus_authority_tuple": {
            "policy_digest": target_policy_digest,
            "corpus_digests": corpus_digests,
            "underlying_event_corpus_digests": underlying_digests,
            "growth_r34": r34_tuple(),
            "growth_r33": r33_tuple(),
            "growth_r32": r32_tuple(),
            "qa_r6": qa_r6_tuple(),
        },
        "readiness": readiness,
        "reason_codes": sorted(set(reason_codes)),
        "evidence_entries_used": used_ids,
        "excluded_entries": excluded_sorted,
        "negative_history_entry_ids": sorted(
            entry["registry_entry_id"] for entry in negative_visible
        ),
        "critical_veto_entry_ids": sorted(set(critical_veto_ids)),
        "unresolved_conflicts": unresolved,
        "aggregation_summary": aggregation,
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
        "browser_mutation_allowed": False,
        "traffic_allocation_allowed": False,
        "publish_allowed": False,
        "credential_access_allowed": False,
        "human_ground_truth": False,
        "creator_advisory": None,
    }
    certificate["certificate_id"] = "gr35cert1:" + sha256_json(
        {
            "registry_snapshot_digest": snapshot["registry_snapshot_digest"],
            "policy_digest": target_policy_digest,
            "as_of": as_of,
        }
    )
    digest_material = copy.deepcopy(certificate)
    digest_material["certificate_digest"] = ""
    certificate["certificate_digest"] = sha256_json(digest_material)
    advisory = {
        "contract_version": ADVISORY_VERSION,
        "advisory_id": "gr35adv1:" + sha256_json(
            {
                "certificate_digest": certificate["certificate_digest"],
                "registry_snapshot_digest": snapshot["registry_snapshot_digest"],
            }
        ),
        "disposition": "ADVISORY_ONLY",
        "readiness": readiness,
        "certificate_digest": certificate["certificate_digest"],
        "registry_snapshot_digest": snapshot["registry_snapshot_digest"],
        "policy_digest": target_policy_digest,
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
        "browser_mutation_allowed": False,
        "traffic_allocation_allowed": False,
        "publish_allowed": False,
        "credential_access_allowed": False,
        "human_ground_truth": False,
    }
    certificate["creator_advisory"] = advisory
    return _clone(certificate)


def conformance_manifest(
    *,
    root: Path,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    conformance = (
        root
        / "conformance"
        / "growth.canary_evidence_registry.r35.v1"
    )
    names = [
        "authority.json",
        "policy.json",
        "contract.json",
        "entry.schema.json",
        "snapshot.schema.json",
        "certificate.schema.json",
        "creator-advisory.schema.json",
    ]
    files = []
    for name in names:
        path = conformance / name
        data = path.read_bytes()
        files.append(
            {
                "path": str(path.relative_to(root)).replace("\\", "/"),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    material = {
        "manifest_version": MANIFEST_VERSION,
        "contract": CONTRACT_VERSION,
        "authority_digest": authority_digest(authority),
        "registry_policy_digest": policy_digest(policy),
        "parent_authority": {
            "growth_r34": r34_tuple(),
            "growth_r33": r33_tuple(),
            "growth_r32": r32_tuple(),
            "qa_r6": qa_r6_tuple(),
        },
        "files": files,
        "advisory_only": True,
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
    }
    material["manifest_digest"] = sha256_json(material)
    return material


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-r35-canary-evidence-registry")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--entries", required=True)
    build.add_argument("--registry-id", required=True)
    build.add_argument("--registry-ledger", required=True)
    build.add_argument("--authority", required=True)
    build.add_argument("--policy", required=True)
    build.add_argument("--snapshot-at", required=True)
    build.add_argument("--as-of", required=True)
    build.add_argument("--target-policy-digest", required=True)
    build.add_argument("--out-dir", required=True)
    build.add_argument("--growth-sha", required=True)
    build.add_argument("--growth-ci-run-id", required=True, type=int)

    rehearsal = sub.add_parser("rehearse-fixtures")
    rehearsal.add_argument("--authority", required=True)
    rehearsal.add_argument("--policy", required=True)
    rehearsal.add_argument("--out-dir", required=True)
    rehearsal.add_argument("--growth-sha", required=True)
    rehearsal.add_argument("--growth-ci-run-id", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    authority = _load(Path(args.authority))
    policy = _load(Path(args.policy))
    out = Path(args.out_dir)
    try:
        if args.command == "rehearse-fixtures":
            from .canary_evidence_registry_r35_sim import build_rehearsal

            report = build_rehearsal(
                authority=authority,
                policy=policy,
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
                root=Path(__file__).resolve().parents[1],
            )
            _write(out / "registry-snapshot.json", report["registry_snapshot"])
            _write(
                out / "readiness-certificate.json",
                report["readiness_certificate"],
            )
            _write(
                out / "creator-advisory.json",
                report["readiness_certificate"]["creator_advisory"],
            )
            _write(out / "adversarial-results.json", report["adversarial_results"])
            _write(out / "conformance-manifest.json", report["conformance_manifest"])
            _write(out / "readiness-report.json", report["readiness_report"])
            print(json.dumps(report["readiness_report"], sort_keys=True))
            return 0

        raw_entries = _load(Path(args.entries))
        if not isinstance(raw_entries, list):
            raise R35Error("--entries must contain JSON array")
        registry = EvidenceRegistry(
            registry_id=args.registry_id,
            path=Path(args.registry_ledger),
            authority=authority,
            policy=policy,
        )
        register_results = registry.register_many(raw_entries)
        snapshot = registry.snapshot(
            snapshot_at=args.snapshot_at,
            growth_sha=args.growth_sha,
            growth_ci_run_id=args.growth_ci_run_id,
        )
        certificate = build_certificate(
            snapshot=snapshot,
            authority=authority,
            policy=policy,
            target_policy_digest=args.target_policy_digest,
            as_of=args.as_of,
        )
        _write(out / "registry-snapshot.json", snapshot)
        _write(out / "readiness-certificate.json", certificate)
        _write(out / "creator-advisory.json", certificate["creator_advisory"])
        report = {
            "report_version": REPORT_VERSION,
            "status": STATUS,
            "disposition": "ADVISORY_ONLY",
            "registry_snapshot_digest": snapshot["registry_snapshot_digest"],
            "certificate_digest": certificate["certificate_digest"],
            "readiness": certificate["readiness"],
            "registered_new_count": sum(1 for value in register_results if value),
            "idempotent_count": sum(1 for value in register_results if not value),
            "live_authorization": False,
            "provider_mutation_allowed": False,
            "creator_mutation_allowed": False,
            "traffic_allocation_allowed": False,
            "publish_allowed": False,
            "credential_access_allowed": False,
            "human_ground_truth": False,
        }
        report["report_digest"] = sha256_json(report)
        _write(out / "readiness-report.json", report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as exc:
        blocked = {
            "report_version": REPORT_VERSION,
            "status": STATUS,
            "disposition": "ADVISORY_ONLY",
            "readiness": "HUMAN_REVIEW_REQUIRED",
            "reason": type(exc).__name__,
            "detail": str(exc),
            "live_authorization": False,
            "provider_mutation_allowed": False,
            "creator_mutation_allowed": False,
            "traffic_allocation_allowed": False,
            "publish_allowed": False,
            "credential_access_allowed": False,
            "human_ground_truth": False,
        }
        blocked["report_digest"] = sha256_json(blocked)
        _write(out / "readiness-report.json", blocked)
        print(json.dumps(blocked, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
