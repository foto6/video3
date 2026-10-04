from __future__ import annotations

import argparse
import copy
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .event_stream import parse_timestamp
from . import adaptive_portfolio_governor_r33 as r33

CONTRACT_VERSION = "growth.counterfactual_policy_promotion.r34.v1"
AUTHORITY_VERSION = "growth.counterfactual_policy_promotion_authority.r34.v1"
POLICY_CONFIG_VERSION = "growth.counterfactual_policy_config.r34.v1"
CORPUS_VERSION = "growth.counterfactual_replay_corpus.r34.v1"
CANDIDATE_POLICY_VERSION = "growth.counterfactual_candidate_policy.r34.v1"
DECISION_VERSION = "growth.counterfactual_policy_promotion_decision.r34.v1"
ENVELOPE_VERSION = "growth.counterfactual_policy_promotion_envelope.r34.v1"
REPORT_VERSION = "growth.counterfactual_policy_promotion.r34.rehearsal.v1"

STATUS = "ADVISORY_ONLY_SOURCE_READY"
RECOMMENDATIONS = {
    "KEEP_BASELINE",
    "TEST_MORE",
    "SHADOW_CANARY_CANDIDATE",
    "SHADOW_ROLLBACK",
    "HUMAN_REVIEW_REQUIRED",
}

R33_SHA = "8bedb5ad79023006b87b17933863ff915ab5e046"
R33_CI = 37210963972
R33_ARTIFACT_ID = 11306727215
R33_ARTIFACT_DIGEST = (
    "sha256:132845c0aaa6d4fec5aaf60e1ade60d779183f2a637a9513e4176bd2ee569660"
)
R33_AUTHORITY_BLOB = "35426e8f05f708d0a6db4356c4211458bf033c16"
R33_POLICY_BLOB = "0f4da630a87d08ccac2e22dfc307b1abb6120dd9"
R33_CONTRACT_BLOB = "518f293756564ca9bf0f004a728d34201f491dfa"
R33_IMPLEMENTATION_BLOB = "a6fddc11e5c5014ff16a69fd7b6944d17a71e9f2"

QA_R5_SHA = "1583c853108b0fb88507448eb8b4c61f0f07b0bc"
QA_R5_CHECKPOINT_BLOB = "e80fae9f8e20708ddd6f98b9741ff1dd5ec8245e"

ALLOWED_FEATURE_PROVENANCE = {
    "pre_outcome_observed",
    "post_outcome",
    "future",
    "proxy_for_unobserved_treatment_driver",
}
MODES = {"randomized_controlled", "observational_monitoring"}


class R34Error(ValueError):
    pass


class AuthorityDrift(R34Error):
    pass


class PolicyConflict(R34Error):
    pass


class EvidenceConflict(R34Error):
    pass


class ReplayConflict(R34Error):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise R34Error(f"{field} must be non-empty string")
    return value


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R34Error(f"{field} must be lowercase SHA-256")
    return value


def _git_sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R34Error(f"{field} must be exact Git SHA")
    return value


def _number(
    value: Any,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R34Error(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise R34Error(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise R34Error(f"{field} below minimum")
    if maximum is not None and result > maximum:
        raise R34Error(f"{field} above maximum")
    return result


def default_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.counterfactual_policy_promotion.r34.v1"
        / "authority.json"
    )


def default_policy_config() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.counterfactual_policy_promotion.r34.v1"
        / "policy.json"
    )


def _local_r33_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.adaptive_portfolio_governor.r33.v1"
        / "authority.json"
    )


def _local_r33_policy() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.adaptive_portfolio_governor.r33.v1"
        / "policy.json"
    )


def parent_r33_tuple() -> dict[str, Any]:
    return {
        "producer_sha": R33_SHA,
        "ci_run_id": R33_CI,
        "artifact_id": R33_ARTIFACT_ID,
        "artifact_digest": R33_ARTIFACT_DIGEST,
        "contract": "growth.adaptive_portfolio_governor.r33.v1",
    }


def validate_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "growth_r33_parent",
        "hard_wave_qa_r5",
        "evidence_boundary",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise AuthorityDrift("R34 authority fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R34 authority version mismatch")
    if value["growth_r33_parent"] != {
        "repository": "foto6/video3",
        "producer_sha": R33_SHA,
        "ci_run_id": R33_CI,
        "artifact_id": R33_ARTIFACT_ID,
        "artifact_name": "growth-r33-adaptive-portfolio-governor",
        "artifact_digest": R33_ARTIFACT_DIGEST,
        "contract": "growth.adaptive_portfolio_governor.r33.v1",
        "authority_blob": R33_AUTHORITY_BLOB,
        "policy_blob": R33_POLICY_BLOB,
        "contract_blob": R33_CONTRACT_BLOB,
        "implementation_blob": R33_IMPLEMENTATION_BLOB,
    }:
        raise AuthorityDrift("exact R33 parent authority drift")
    if value["hard_wave_qa_r5"] != {
        "repository": "foto6/boss",
        "producer_sha": QA_R5_SHA,
        "checkpoint_path": "CHECKPOINTS/2026-10-04-hardwave-acceptance-r5.md",
        "checkpoint_blob": QA_R5_CHECKPOINT_BLOB,
        "disposition": "ACCEPTED",
        "accepted_growth_r33_sha": R33_SHA,
        "accepted_growth_r33_ci_run_id": R33_CI,
        "accepted_growth_r33_artifact_id": R33_ARTIFACT_ID,
        "accepted_growth_r33_artifact_digest": R33_ARTIFACT_DIGEST,
    }:
        raise AuthorityDrift("exact Hard Wave QA-R5 authority drift")
    if value["evidence_boundary"] != {
        "advisory_only": True,
        "shadow_only": True,
        "creator_mutation": False,
        "provider_mutation": False,
        "browser_mutation": False,
        "traffic_routing": False,
        "budget_allocation": False,
        "live_publish": False,
        "human_ground_truth": False,
        "observational_is_causal": False,
        "merge": False,
    }:
        raise AuthorityDrift("R34 evidence boundary drift")
    # Local parent conformance must still parse exactly as R33.
    r33.validate_authority(_local_r33_authority())
    r33.validate_policy(_local_r33_policy())
    return _clone(value)


def validate_policy_config(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "policy_version",
        "estimator",
        "decision",
        "maturity",
        "drift",
        "complexity",
        "leakage",
        "multiplicity",
        "recommendations",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PolicyConflict("R34 policy config fields invalid")
    if value["contract_version"] != POLICY_CONFIG_VERSION or value["policy_version"] != 1:
        raise PolicyConflict("R34 policy config version drift")
    if value["estimator"] != {
        "method": "doubly_robust_aipw",
        "confidence_method": "analytic_influence_interval",
        "confidence_z": 1.96,
        "minimum_behavior_propensity": 0.05,
        "minimum_effective_sample_size": 80.0,
        "maximum_importance_weight": 5.0,
        "maximum_clipped_weight_sensitivity": 0.02,
    }:
        raise PolicyConflict("R34 estimator policy drift")
    if value["decision"] != {
        "primary_metric": "completion_rate",
        "promotion_margin": 0.02,
        "harmful_threshold": -0.03,
        "guardrail_max_degradation": -0.02,
        "critical_stratum_reversal": -0.02,
        "minimum_critical_stratum_events": 20,
        "keep_equivalence_margin": 0.005,
    }:
        raise PolicyConflict("R34 decision policy drift")
    if value["maturity"] != {
        "required_state": "MATURE",
        "maximum_censored_fraction": 0.0,
        "allow_retroactive_window_selection": False,
    }:
        raise PolicyConflict("R34 maturity policy drift")
    if value["drift"] != {
        "platform_total_variation_max": 0.25,
        "account_total_variation_max": 0.25,
        "topic_total_variation_max": 0.25,
    }:
        raise PolicyConflict("R34 drift policy drift")
    if value["complexity"] != {
        "maximum_rule_count": 8,
        "minimum_exploration_floor": 0.2,
        "maximum_action_concentration": 0.7,
        "winner_take_all_allowed": False,
    }:
        raise PolicyConflict("R34 complexity policy drift")
    if value["leakage"] != {
        "allowed_feature_names": [
            "platform",
            "account_pseudonym",
            "topic_cluster",
            "hour_bucket",
            "source_type",
        ],
        "post_outcome_features_allowed": False,
        "future_features_allowed": False,
        "evaluation_corpus_training_allowed": False,
        "duplicate_exposure_allowed": False,
    }:
        raise PolicyConflict("R34 leakage policy drift")
    if value["multiplicity"] != {
        "family_alpha": 0.05,
        "maximum_declared_tests": 4,
        "method": "bonferroni_declared_offline_policy_family",
    }:
        raise PolicyConflict("R34 multiplicity policy drift")
    if value["recommendations"] != [
        "KEEP_BASELINE",
        "TEST_MORE",
        "SHADOW_CANARY_CANDIDATE",
        "SHADOW_ROLLBACK",
        "HUMAN_REVIEW_REQUIRED",
    ]:
        raise PolicyConflict("R34 recommendation policy drift")
    return _clone(value)


def authority_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority(value))


def policy_config_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_policy_config(value))


def _distribution(values: Sequence[str]) -> dict[str, float]:
    counts = Counter(values)
    total = len(values)
    if total == 0:
        return {}
    return {
        key: round(count / total, 12)
        for key, count in sorted(counts.items())
    }


def _tv(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = set(a) | set(b)
    return round(
        0.5
        * sum(
            abs(float(a.get(key, 0.0)) - float(b.get(key, 0.0)))
            for key in keys
        ),
        12,
    )


def _event_digest(event: Mapping[str, Any]) -> str:
    material = {key: event[key] for key in event if key != "event_digest"}
    return sha256_json(material)


def build_corpus(
    *,
    corpus_id: str,
    frozen_at: str,
    evidence_mode: str,
    metric_window_seconds: int,
    evaluation_split_id: str,
    evaluation_split_digest: str,
    source_portfolio_id: str,
    source_portfolio_digest: str,
    source_r33_decision_digest: str,
    campaigns: Sequence[Mapping[str, Any]],
    critical_strata: Sequence[Mapping[str, Any]],
    critical_guardrails: Sequence[str],
    outcome_model: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    _nonempty(corpus_id, "corpus_id")
    parse_timestamp(frozen_at)
    if evidence_mode not in MODES:
        raise EvidenceConflict("unsupported evidence mode")
    if (
        isinstance(metric_window_seconds, bool)
        or not isinstance(metric_window_seconds, int)
        or metric_window_seconds < 1
    ):
        raise EvidenceConflict("metric_window_seconds invalid")
    _nonempty(evaluation_split_id, "evaluation_split_id")
    _sha(evaluation_split_digest, "evaluation_split_digest")
    _nonempty(source_portfolio_id, "source_portfolio_id")
    _sha(source_portfolio_digest, "source_portfolio_digest")
    _sha(source_r33_decision_digest, "source_r33_decision_digest")

    if not isinstance(campaigns, Sequence) or isinstance(campaigns, (str, bytes)) or not campaigns:
        raise EvidenceConflict("campaign declarations required")
    campaign_map: dict[str, dict[str, Any]] = {}
    for raw in campaigns:
        required = {"campaign_id", "platforms", "accounts", "topics"}
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise EvidenceConflict("campaign declaration fields invalid")
        campaign_id = _nonempty(raw["campaign_id"], "campaign_id")
        if campaign_id in campaign_map:
            raise EvidenceConflict("duplicate campaign declaration")
        parsed = {"campaign_id": campaign_id}
        for field in ("platforms", "accounts", "topics"):
            values = raw[field]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(x, str) or not x for x in values)
                or len(values) != len(set(values))
            ):
                raise EvidenceConflict(f"campaign {field} invalid")
            parsed[field] = sorted(values)
        campaign_map[campaign_id] = parsed

    parsed_strata = []
    for raw in critical_strata:
        if not isinstance(raw, Mapping) or set(raw) != {"dimension", "value"}:
            raise EvidenceConflict("critical stratum fields invalid")
        if raw["dimension"] not in {"platform", "account_pseudonym", "topic_cluster"}:
            raise EvidenceConflict("critical stratum dimension invalid")
        parsed_strata.append(
            {"dimension": raw["dimension"], "value": _nonempty(raw["value"], "stratum value")}
        )
    if len({(x["dimension"], x["value"]) for x in parsed_strata}) != len(parsed_strata):
        raise EvidenceConflict("duplicate critical stratum")

    guardrails = list(critical_guardrails)
    if (
        not guardrails
        or any(not isinstance(x, str) or not x for x in guardrails)
        or len(guardrails) != len(set(guardrails))
    ):
        raise EvidenceConflict("critical guardrails invalid")
    if guardrails != sorted(guardrails):
        raise EvidenceConflict("critical guardrails must be canonical sorted")

    outcome_required = {
        "model_id",
        "model_digest",
        "training_corpus_digest",
        "training_split_id",
        "trained_on_evaluation_corpus",
        "feature_names",
    }
    if not isinstance(outcome_model, Mapping) or set(outcome_model) != outcome_required:
        raise EvidenceConflict("outcome model descriptor invalid")
    _nonempty(outcome_model["model_id"], "outcome_model.model_id")
    _sha(outcome_model["model_digest"], "outcome_model.model_digest")
    _sha(outcome_model["training_corpus_digest"], "outcome_model.training_corpus_digest")
    _nonempty(outcome_model["training_split_id"], "outcome_model.training_split_id")
    if outcome_model["trained_on_evaluation_corpus"] is not False:
        raise EvidenceConflict("outcome model trained on evaluation corpus")
    if (
        not isinstance(outcome_model["feature_names"], list)
        or not outcome_model["feature_names"]
        or len(outcome_model["feature_names"]) != len(set(outcome_model["feature_names"]))
    ):
        raise EvidenceConflict("outcome model feature names invalid")

    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)) or not events:
        raise EvidenceConflict("replay events required")
    parsed_events = []
    event_ids = set()
    exposures = set()
    for raw in events:
        required = {
            "event_id",
            "event_digest",
            "campaign_id",
            "session_id",
            "exposure_id",
            "action",
            "logging_propensities",
            "platform",
            "account_pseudonym",
            "topic_cluster",
            "features",
            "feature_provenance",
            "feature_as_of",
            "exposure_at",
            "outcome_mature_at",
            "outcome_state",
            "source_sha256",
            "render_sha256",
            "r31_lineage_digest",
            "r32_decision_digest",
            "r33_campaign_state_digest",
            "outcome",
            "outcome_model",
        }
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise EvidenceConflict("replay event fields invalid")
        event = _clone(raw)
        event_id = _nonempty(event["event_id"], "event_id")
        exposure_id = _nonempty(event["exposure_id"], "exposure_id")
        if event_id in event_ids:
            raise EvidenceConflict("duplicate event identity")
        if exposure_id in exposures:
            raise EvidenceConflict("duplicate exposure identity")
        event_ids.add(event_id)
        exposures.add(exposure_id)
        campaign = campaign_map.get(event["campaign_id"])
        if campaign is None:
            raise EvidenceConflict("event references unknown campaign")
        if event["platform"] not in campaign["platforms"]:
            raise EvidenceConflict("event platform outside frozen campaign strata")
        if event["account_pseudonym"] not in campaign["accounts"]:
            raise EvidenceConflict("cross-account contamination")
        if event["topic_cluster"] not in campaign["topics"]:
            raise EvidenceConflict("event topic outside frozen campaign strata")
        for field in (
            "source_sha256",
            "render_sha256",
            "r31_lineage_digest",
            "r32_decision_digest",
            "r33_campaign_state_digest",
        ):
            _sha(event[field], field)
        exposure_at = parse_timestamp(event["exposure_at"])
        feature_as_of = parse_timestamp(event["feature_as_of"])
        mature_at = parse_timestamp(event["outcome_mature_at"])
        if feature_as_of > exposure_at:
            raise EvidenceConflict("future feature leakage")
        if mature_at < exposure_at:
            raise EvidenceConflict("outcome maturity precedes exposure")
        if event["outcome_state"] not in {"MATURE", "CENSORED", "DELAYED"}:
            raise EvidenceConflict("outcome state invalid")

        features = event["features"]
        provenance = event["feature_provenance"]
        if not isinstance(features, Mapping) or not isinstance(provenance, Mapping):
            raise EvidenceConflict("feature maps invalid")
        if set(features) != set(provenance):
            raise EvidenceConflict("feature provenance coverage mismatch")
        for name, source in provenance.items():
            if source not in ALLOWED_FEATURE_PROVENANCE:
                raise EvidenceConflict("feature provenance value invalid")

        propensities = event["logging_propensities"]
        if (
            not isinstance(propensities, Mapping)
            or len(propensities) < 2
            or event["action"] not in propensities
        ):
            raise EvidenceConflict("logging propensity map invalid")
        total = 0.0
        for action, probability in propensities.items():
            _nonempty(action, "action")
            total += _number(probability, f"logging_propensity.{action}", 0.0, 1.0)
        if abs(total - 1.0) > 1e-9:
            raise EvidenceConflict("logging propensities do not sum to one")
        if float(propensities[event["action"]]) <= 0:
            raise EvidenceConflict("logged action has zero propensity")

        outcome = event["outcome"]
        if not isinstance(outcome, Mapping) or set(outcome) != {"primary", "guardrails"}:
            raise EvidenceConflict("outcome fields invalid")
        _number(outcome["primary"], "outcome.primary")
        if not isinstance(outcome["guardrails"], Mapping):
            raise EvidenceConflict("outcome guardrails invalid")
        if set(outcome["guardrails"]) != set(guardrails):
            raise EvidenceConflict("critical guardrail outcome coverage mismatch")
        for name, metric in outcome["guardrails"].items():
            _number(metric, f"outcome.guardrails.{name}")

        predictions = event["outcome_model"]
        if not isinstance(predictions, Mapping) or set(predictions) != set(propensities):
            raise EvidenceConflict("outcome model action coverage mismatch")
        for action, prediction in predictions.items():
            if not isinstance(prediction, Mapping) or set(prediction) != {"primary", "guardrails"}:
                raise EvidenceConflict("outcome model prediction fields invalid")
            _number(prediction["primary"], f"qhat.{action}.primary")
            if set(prediction["guardrails"]) != set(guardrails):
                raise EvidenceConflict("outcome model guardrail coverage mismatch")
            for name, metric in prediction["guardrails"].items():
                _number(metric, f"qhat.{action}.guardrails.{name}")

        if event["event_digest"] != _event_digest(event):
            raise EvidenceConflict("event digest mismatch")
        parsed_events.append(event)

    parsed_events.sort(key=lambda x: (x["campaign_id"], x["exposure_at"], x["event_id"]))
    material = {
        "contract_version": CORPUS_VERSION,
        "corpus_id": corpus_id,
        "corpus_digest": "",
        "frozen_at": frozen_at,
        "evidence_mode": evidence_mode,
        "metric_window_seconds": metric_window_seconds,
        "evaluation_split_id": evaluation_split_id,
        "evaluation_split_digest": evaluation_split_digest,
        "parent_r33_authority": parent_r33_tuple(),
        "source_portfolio_id": source_portfolio_id,
        "source_portfolio_digest": source_portfolio_digest,
        "source_r33_decision_digest": source_r33_decision_digest,
        "campaigns": [campaign_map[key] for key in sorted(campaign_map)],
        "critical_strata": sorted(parsed_strata, key=lambda x: (x["dimension"], x["value"])),
        "critical_guardrails": guardrails,
        "outcome_model": _clone(outcome_model),
        "events": parsed_events,
    }
    digest_material = copy.deepcopy(material)
    digest_material["corpus_digest"] = ""
    material["corpus_digest"] = sha256_json(digest_material)
    return _clone(material)


def parse_corpus(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "corpus_id",
        "corpus_digest",
        "frozen_at",
        "evidence_mode",
        "metric_window_seconds",
        "evaluation_split_id",
        "evaluation_split_digest",
        "parent_r33_authority",
        "source_portfolio_id",
        "source_portfolio_digest",
        "source_r33_decision_digest",
        "campaigns",
        "critical_strata",
        "critical_guardrails",
        "outcome_model",
        "events",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise EvidenceConflict("replay corpus fields invalid")
    if value["contract_version"] != CORPUS_VERSION:
        raise EvidenceConflict("replay corpus contract mismatch")
    if value["parent_r33_authority"] != parent_r33_tuple():
        raise AuthorityDrift("corpus R33 authority mismatch")
    rebuilt = build_corpus(
        corpus_id=value["corpus_id"],
        frozen_at=value["frozen_at"],
        evidence_mode=value["evidence_mode"],
        metric_window_seconds=value["metric_window_seconds"],
        evaluation_split_id=value["evaluation_split_id"],
        evaluation_split_digest=value["evaluation_split_digest"],
        source_portfolio_id=value["source_portfolio_id"],
        source_portfolio_digest=value["source_portfolio_digest"],
        source_r33_decision_digest=value["source_r33_decision_digest"],
        campaigns=value["campaigns"],
        critical_strata=value["critical_strata"],
        critical_guardrails=value["critical_guardrails"],
        outcome_model=value["outcome_model"],
        events=value["events"],
    )
    if rebuilt != value:
        raise EvidenceConflict("replay corpus digest or canonical bytes mismatch")
    return rebuilt


def _validate_probability_map(
    value: Mapping[str, Any],
    *,
    actions: Sequence[str],
    field: str,
) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(actions):
        raise PolicyConflict(f"{field} action coverage mismatch")
    parsed = {
        action: _number(value[action], f"{field}.{action}", 0.0, 1.0)
        for action in actions
    }
    if abs(sum(parsed.values()) - 1.0) > 1e-9:
        raise PolicyConflict(f"{field} probabilities do not sum to one")
    return {key: parsed[key] for key in sorted(parsed)}


def build_candidate_policy(
    *,
    policy_id: str,
    policy_role: str,
    policy_version: int,
    evaluation_corpus_digest: str,
    training_corpus_digest: str,
    training_split_id: str,
    trained_on_evaluation_corpus: bool,
    actions: Sequence[str],
    features_used: Sequence[str],
    default_probabilities: Mapping[str, Any],
    rules: Sequence[Mapping[str, Any]],
    reference_distribution: Mapping[str, Mapping[str, float]],
    evaluation_family: Mapping[str, Any],
) -> dict[str, Any]:
    _nonempty(policy_id, "policy_id")
    if policy_role not in {"BASELINE_POLICY", "CANDIDATE_POLICY"}:
        raise PolicyConflict("policy role invalid")
    if isinstance(policy_version, bool) or not isinstance(policy_version, int) or policy_version < 1:
        raise PolicyConflict("policy version invalid")
    _sha(evaluation_corpus_digest, "evaluation_corpus_digest")
    _sha(training_corpus_digest, "training_corpus_digest")
    _nonempty(training_split_id, "training_split_id")
    if not isinstance(trained_on_evaluation_corpus, bool):
        raise PolicyConflict("trained_on_evaluation_corpus invalid")
    action_list = sorted(actions)
    if len(action_list) < 2 or len(action_list) != len(set(action_list)):
        raise PolicyConflict("policy actions invalid")
    feature_list = sorted(features_used)
    if len(feature_list) != len(set(feature_list)) or any(not x for x in feature_list):
        raise PolicyConflict("features_used invalid")
    default = _validate_probability_map(
        default_probabilities,
        actions=action_list,
        field="default_probabilities",
    )
    parsed_rules = []
    for index, raw in enumerate(rules):
        if not isinstance(raw, Mapping) or set(raw) != {"match", "probabilities"}:
            raise PolicyConflict("policy rule fields invalid")
        match = raw["match"]
        if not isinstance(match, Mapping) or not match:
            raise PolicyConflict("policy rule match invalid")
        allowed_match = {
            "platform",
            "account_pseudonym",
            "topic_cluster",
            "hour_bucket",
            "source_type",
        }
        if not set(match) <= allowed_match:
            raise PolicyConflict("policy rule uses unsupported match dimension")
        for key, value in match.items():
            if not isinstance(value, str) or not value:
                raise PolicyConflict("policy rule match value invalid")
        parsed_rules.append(
            {
                "match": {key: match[key] for key in sorted(match)},
                "probabilities": _validate_probability_map(
                    raw["probabilities"],
                    actions=action_list,
                    field=f"rules[{index}].probabilities",
                ),
            }
        )

    if not isinstance(reference_distribution, Mapping) or set(reference_distribution) != {
        "platform",
        "account_pseudonym",
        "topic_cluster",
    }:
        raise PolicyConflict("reference distribution fields invalid")
    reference = {}
    for dimension, raw in reference_distribution.items():
        if not isinstance(raw, Mapping) or not raw:
            raise PolicyConflict("reference distribution invalid")
        parsed = {
            key: _number(value, f"reference.{dimension}.{key}", 0.0, 1.0)
            for key, value in raw.items()
        }
        if abs(sum(parsed.values()) - 1.0) > 1e-9:
            raise PolicyConflict("reference distribution does not sum to one")
        reference[dimension] = {key: parsed[key] for key in sorted(parsed)}

    family_required = {
        "family_id",
        "test_index",
        "test_count",
        "alpha_requested",
    }
    if not isinstance(evaluation_family, Mapping) or set(evaluation_family) != family_required:
        raise PolicyConflict("evaluation family fields invalid")
    family = {
        "family_id": _nonempty(evaluation_family["family_id"], "family_id"),
        "test_index": evaluation_family["test_index"],
        "test_count": evaluation_family["test_count"],
        "alpha_requested": _number(
            evaluation_family["alpha_requested"], "alpha_requested", 0.0, 1.0
        ),
    }
    if (
        isinstance(family["test_index"], bool)
        or not isinstance(family["test_index"], int)
        or isinstance(family["test_count"], bool)
        or not isinstance(family["test_count"], int)
        or family["test_count"] < 1
        or not 0 <= family["test_index"] < family["test_count"]
    ):
        raise PolicyConflict("evaluation family index/count invalid")

    material = {
        "contract_version": CANDIDATE_POLICY_VERSION,
        "policy_id": policy_id,
        "policy_digest": "",
        "policy_role": policy_role,
        "policy_version": policy_version,
        "evaluation_corpus_digest": evaluation_corpus_digest,
        "training_corpus_digest": training_corpus_digest,
        "training_split_id": training_split_id,
        "trained_on_evaluation_corpus": trained_on_evaluation_corpus,
        "actions": action_list,
        "features_used": feature_list,
        "default_probabilities": default,
        "rules": parsed_rules,
        "reference_distribution": reference,
        "evaluation_family": family,
    }
    digest_material = copy.deepcopy(material)
    digest_material["policy_digest"] = ""
    material["policy_digest"] = sha256_json(digest_material)
    return _clone(material)


def parse_candidate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "policy_id",
        "policy_digest",
        "policy_role",
        "policy_version",
        "evaluation_corpus_digest",
        "training_corpus_digest",
        "training_split_id",
        "trained_on_evaluation_corpus",
        "actions",
        "features_used",
        "default_probabilities",
        "rules",
        "reference_distribution",
        "evaluation_family",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PolicyConflict("candidate policy fields invalid")
    if value["contract_version"] != CANDIDATE_POLICY_VERSION:
        raise PolicyConflict("candidate policy contract mismatch")
    rebuilt = build_candidate_policy(
        policy_id=value["policy_id"],
        policy_role=value["policy_role"],
        policy_version=value["policy_version"],
        evaluation_corpus_digest=value["evaluation_corpus_digest"],
        training_corpus_digest=value["training_corpus_digest"],
        training_split_id=value["training_split_id"],
        trained_on_evaluation_corpus=value["trained_on_evaluation_corpus"],
        actions=value["actions"],
        features_used=value["features_used"],
        default_probabilities=value["default_probabilities"],
        rules=value["rules"],
        reference_distribution=value["reference_distribution"],
        evaluation_family=value["evaluation_family"],
    )
    if rebuilt != value:
        raise PolicyConflict("candidate policy digest or canonical bytes mismatch")
    return rebuilt


def _policy_probabilities(policy: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, float]:
    context = {
        "platform": event["platform"],
        "account_pseudonym": event["account_pseudonym"],
        "topic_cluster": event["topic_cluster"],
        **event["features"],
    }
    for rule in policy["rules"]:
        if all(context.get(key) == value for key, value in rule["match"].items()):
            return rule["probabilities"]
    return policy["default_probabilities"]


def _validate_leakage(
    corpus: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    config: Mapping[str, Any],
) -> None:
    allowed = set(config["leakage"]["allowed_feature_names"])
    for policy in (baseline, candidate):
        if not set(policy["features_used"]) <= allowed:
            raise EvidenceConflict("policy uses undeclared feature")
        if policy["trained_on_evaluation_corpus"]:
            raise EvidenceConflict("policy trained on evaluation corpus")
        if policy["training_corpus_digest"] == corpus["corpus_digest"]:
            raise EvidenceConflict("policy training corpus equals evaluation corpus")
        if policy["evaluation_corpus_digest"] != corpus["corpus_digest"]:
            raise EvidenceConflict("policy evaluation corpus binding mismatch")
    model = corpus["outcome_model"]
    if model["trained_on_evaluation_corpus"]:
        raise EvidenceConflict("outcome model trained on evaluation corpus")
    if model["training_corpus_digest"] == corpus["corpus_digest"]:
        raise EvidenceConflict("outcome model training corpus equals evaluation corpus")
    if not set(model["feature_names"]) <= allowed:
        raise EvidenceConflict("outcome model uses undeclared feature")
    for event in corpus["events"]:
        for name, provenance in event["feature_provenance"].items():
            if provenance == "post_outcome":
                raise EvidenceConflict("post-outcome feature leakage")
            if provenance == "future":
                raise EvidenceConflict("future data leakage")
            if provenance == "proxy_for_unobserved_treatment_driver":
                raise EvidenceConflict("hidden confounder proxy detected")
            if name not in allowed:
                raise EvidenceConflict("event uses undeclared feature")


def _validate_complexity(
    policy: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> list[str]:
    blockers = []
    limits = config["complexity"]
    if len(policy["rules"]) > limits["maximum_rule_count"]:
        blockers.append("POLICY_RULE_EXPLOSION")
    min_seen = 1.0
    max_seen = 0.0
    for event in events:
        probs = _policy_probabilities(policy, event)
        min_seen = min(min_seen, min(float(x) for x in probs.values()))
        max_seen = max(max_seen, max(float(x) for x in probs.values()))
    if min_seen < limits["minimum_exploration_floor"] - 1e-12:
        blockers.append("EXPLORATION_BELOW_FLOOR")
    if max_seen > limits["maximum_action_concentration"] + 1e-12:
        blockers.append("ACTION_CONCENTRATION_ABOVE_CAP")
    return sorted(set(blockers))


def _validate_multiplicity(
    policy: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[str]:
    family = policy["evaluation_family"]
    limits = config["multiplicity"]
    blockers = []
    if family["test_count"] > limits["maximum_declared_tests"]:
        blockers.append("MULTIPLE_TESTING_BUDGET_OVERSPEND")
    if family["alpha_requested"] * family["test_count"] > limits["family_alpha"] + 1e-12:
        blockers.append("MULTIPLE_TESTING_BUDGET_OVERSPEND")
    return blockers


def _propensity_diagnostics(
    events: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    minimum = config["estimator"]["minimum_behavior_propensity"]
    weights = []
    unsupported = []
    minimum_supported = 1.0
    for event in events:
        target = _policy_probabilities(policy, event)
        behavior = event["logging_propensities"]
        for action, probability in target.items():
            if probability > 0:
                bp = float(behavior.get(action, 0.0))
                minimum_supported = min(minimum_supported, bp)
                if bp < minimum:
                    unsupported.append(
                        {
                            "event_id": event["event_id"],
                            "action": action,
                            "behavior_propensity": bp,
                            "target_probability": float(probability),
                        }
                    )
        logged = event["action"]
        weights.append(float(target[logged]) / float(behavior[logged]))
    weight_sum = sum(weights)
    weight_sq = sum(weight * weight for weight in weights)
    ess = 0.0 if weight_sq == 0 else (weight_sum * weight_sum) / weight_sq
    return {
        "minimum_supported_behavior_propensity": round(minimum_supported, 12),
        "unsupported_action_regions": unsupported,
        "effective_sample_size": round(ess, 8),
        "maximum_weight": round(max(weights) if weights else 0.0, 8),
        "mean_weight": round(statistics.mean(weights) if weights else 0.0, 8),
        "weights": weights,
    }


def _metric_value(event: Mapping[str, Any], metric: str) -> float:
    if metric == "primary":
        return float(event["outcome"]["primary"])
    return float(event["outcome"]["guardrails"][metric])


def _qhat(event: Mapping[str, Any], action: str, metric: str) -> float:
    if metric == "primary":
        return float(event["outcome_model"][action]["primary"])
    return float(event["outcome_model"][action]["guardrails"][metric])


def _dr_scores(
    events: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    metric: str,
    *,
    weight_clip: float | None,
) -> list[float]:
    scores = []
    for event in events:
        target = _policy_probabilities(policy, event)
        regression = sum(
            float(target[action]) * _qhat(event, action, metric)
            for action in policy["actions"]
        )
        logged = event["action"]
        weight = float(target[logged]) / float(event["logging_propensities"][logged])
        if weight_clip is not None:
            weight = min(weight, weight_clip)
        residual = _metric_value(event, metric) - _qhat(event, logged, metric)
        scores.append(regression + weight * residual)
    return scores


def _paired_interval(
    baseline_scores: Sequence[float],
    candidate_scores: Sequence[float],
    z: float,
) -> dict[str, Any]:
    if len(baseline_scores) != len(candidate_scores) or not baseline_scores:
        raise EvidenceConflict("paired estimator score cardinality mismatch")
    differences = [
        float(candidate) - float(baseline)
        for baseline, candidate in zip(baseline_scores, candidate_scores)
    ]
    mean = statistics.mean(differences)
    if len(differences) > 1:
        sd = statistics.stdev(differences)
        se = sd / math.sqrt(len(differences))
    else:
        sd = 0.0
        se = 0.0
    lower = mean - z * se
    upper = mean + z * se
    return {
        "n": len(differences),
        "difference": round(mean, 12),
        "standard_deviation": round(sd, 12),
        "standard_error": round(se, 12),
        "lower_95": round(lower, 12),
        "upper_95": round(upper, 12),
    }


def _estimate(
    events: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    metric: str,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    z = float(config["estimator"]["confidence_z"])
    raw_base = _dr_scores(events, baseline, metric, weight_clip=None)
    raw_candidate = _dr_scores(events, candidate, metric, weight_clip=None)
    raw = _paired_interval(raw_base, raw_candidate, z)
    clip = float(config["estimator"]["maximum_importance_weight"])
    clipped_base = _dr_scores(events, baseline, metric, weight_clip=clip)
    clipped_candidate = _dr_scores(events, candidate, metric, weight_clip=clip)
    clipped = _paired_interval(clipped_base, clipped_candidate, z)
    return {
        "metric": metric,
        "raw": raw,
        "clipped": clipped,
        "clipped_weight_sensitivity": round(
            abs(float(raw["difference"]) - float(clipped["difference"])), 12
        ),
    }


def _strata_estimates(
    corpus: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    minimum = int(config["decision"]["minimum_critical_stratum_events"])
    for stratum in corpus["critical_strata"]:
        dimension = stratum["dimension"]
        value = stratum["value"]
        events = [event for event in corpus["events"] if event[dimension] == value]
        row = {
            "dimension": dimension,
            "value": value,
            "event_count": len(events),
            "estimate": None,
            "inside_safety_bound": False,
            "critical_reversal": False,
        }
        if len(events) >= minimum:
            estimate = _estimate(events, baseline, candidate, "primary", config)
            row["estimate"] = estimate
            row["inside_safety_bound"] = bool(
                estimate["raw"]["lower_95"]
                >= float(config["decision"]["critical_stratum_reversal"])
            )
            row["critical_reversal"] = bool(
                estimate["raw"]["difference"]
                <= float(config["decision"]["critical_stratum_reversal"])
            )
        rows.append(row)
    return rows


def _drift(
    corpus: Mapping[str, Any],
    candidate: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    events = corpus["events"]
    empirical = {
        "platform": _distribution([event["platform"] for event in events]),
        "account_pseudonym": _distribution(
            [event["account_pseudonym"] for event in events]
        ),
        "topic_cluster": _distribution([event["topic_cluster"] for event in events]),
    }
    thresholds = {
        "platform": config["drift"]["platform_total_variation_max"],
        "account_pseudonym": config["drift"]["account_total_variation_max"],
        "topic_cluster": config["drift"]["topic_total_variation_max"],
    }
    rows = {}
    stale = False
    for dimension in ("platform", "account_pseudonym", "topic_cluster"):
        tv = _tv(empirical[dimension], candidate["reference_distribution"][dimension])
        shifted = tv > float(thresholds[dimension])
        stale = stale or shifted
        rows[dimension] = {
            "empirical": empirical[dimension],
            "reference": candidate["reference_distribution"][dimension],
            "total_variation": tv,
            "threshold": thresholds[dimension],
            "shifted": shifted,
        }
    return {"dimensions": rows, "stale_or_shifted": stale}


def _maturity(corpus: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    states = Counter(event["outcome_state"] for event in corpus["events"])
    censored = states.get("CENSORED", 0) + states.get("DELAYED", 0)
    fraction = censored / len(corpus["events"])
    return {
        "event_count": len(corpus["events"]),
        "mature_count": states.get("MATURE", 0),
        "censored_or_delayed_count": censored,
        "censored_fraction": round(fraction, 12),
        "complete": bool(
            fraction <= float(config["maturity"]["maximum_censored_fraction"])
            and states.get("MATURE", 0) == len(corpus["events"])
        ),
    }


def _advisory_envelope(
    *,
    decision: Mapping[str, Any],
    corpus: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    authority: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any] | None:
    if decision["recommendation"] != "SHADOW_CANARY_CANDIDATE":
        return None
    material = {
        "contract_version": ENVELOPE_VERSION,
        "envelope_id": "",
        "envelope_digest": "",
        "disposition": "ADVISORY_ONLY",
        "creator_target": "Creator R35 advisory input",
        "creator_execution_allowed": False,
        "provider_mutation": False,
        "browser_mutation": False,
        "live_traffic_allowed": False,
        "live_publish_allowed": False,
        "human_ground_truth": False,
        "recommendation": decision["recommendation"],
        "decision_digest": decision["decision_digest"],
        "corpus_id": corpus["corpus_id"],
        "corpus_digest": corpus["corpus_digest"],
        "baseline_policy_id": baseline["policy_id"],
        "baseline_policy_digest": baseline["policy_digest"],
        "candidate_policy_id": candidate["policy_id"],
        "candidate_policy_digest": candidate["policy_digest"],
        "parent_r33_authority": parent_r33_tuple(),
        "r34_authority": {
            "producer_sha": growth_sha,
            "ci_run_id": growth_ci_run_id,
            "authority_digest": authority_digest(authority),
            "contract": CONTRACT_VERSION,
        },
    }
    material["envelope_id"] = "gr34e1:" + sha256_json(
        {
            "corpus_digest": corpus["corpus_digest"],
            "candidate_policy_digest": candidate["policy_digest"],
            "decision_digest": decision["decision_digest"],
        }
    )
    digest_material = copy.deepcopy(material)
    digest_material["envelope_digest"] = ""
    material["envelope_digest"] = sha256_json(digest_material)
    return _clone(material)


def evaluate(
    *,
    corpus: Mapping[str, Any],
    baseline_policy: Mapping[str, Any],
    candidate_policy: Mapping[str, Any],
    authority: Mapping[str, Any],
    policy_config: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = validate_authority(authority)
    config = validate_policy_config(policy_config)
    corpus = parse_corpus(corpus)
    baseline = parse_candidate_policy(baseline_policy)
    candidate = parse_candidate_policy(candidate_policy)
    _git_sha(growth_sha, "growth_sha")
    if (
        isinstance(growth_ci_run_id, bool)
        or not isinstance(growth_ci_run_id, int)
        or growth_ci_run_id < 1
    ):
        raise R34Error("growth_ci_run_id invalid")
    if baseline["policy_role"] != "BASELINE_POLICY":
        raise PolicyConflict("baseline role mismatch")
    if candidate["policy_role"] != "CANDIDATE_POLICY":
        raise PolicyConflict("candidate role mismatch")
    if baseline["actions"] != candidate["actions"]:
        raise PolicyConflict("baseline/candidate action set mismatch")
    if set(baseline["actions"]) != set(corpus["events"][0]["logging_propensities"]):
        raise PolicyConflict("policy/logging action set mismatch")
    if baseline["evaluation_corpus_digest"] != candidate["evaluation_corpus_digest"]:
        raise EvidenceConflict("policies are not bound to same frozen corpus")
    if baseline["evaluation_family"] != candidate["evaluation_family"]:
        raise PolicyConflict("baseline/candidate evaluation family mismatch")

    _validate_leakage(corpus, baseline, candidate, config)

    blockers = []
    blockers.extend(_validate_complexity(candidate, corpus["events"], config))
    blockers.extend(_validate_multiplicity(candidate, config))

    candidate_propensity = _propensity_diagnostics(corpus["events"], candidate, config)
    baseline_propensity = _propensity_diagnostics(corpus["events"], baseline, config)
    if candidate_propensity["unsupported_action_regions"]:
        blockers.append("UNSUPPORTED_ACTION_REGION")
    if (
        candidate_propensity["effective_sample_size"]
        < float(config["estimator"]["minimum_effective_sample_size"])
    ):
        blockers.append("EFFECTIVE_SAMPLE_SIZE_BELOW_FLOOR")
    if (
        baseline_propensity["effective_sample_size"]
        < float(config["estimator"]["minimum_effective_sample_size"])
    ):
        blockers.append("BASELINE_EFFECTIVE_SAMPLE_SIZE_BELOW_FLOOR")

    maturity = _maturity(corpus, config)
    if not maturity["complete"]:
        blockers.append("INCOMPLETE_OR_CENSORED_OUTCOMES")

    drift = _drift(corpus, candidate, config)
    if drift["stale_or_shifted"]:
        blockers.append("POLICY_DISTRIBUTION_DRIFT")

    primary = _estimate(corpus["events"], baseline, candidate, "primary", config)
    if (
        primary["clipped_weight_sensitivity"]
        > float(config["estimator"]["maximum_clipped_weight_sensitivity"])
    ):
        blockers.append("CLIPPED_WEIGHT_SENSITIVITY_EXCEEDED")

    guardrails = {}
    guardrail_safe = True
    for metric in corpus["critical_guardrails"]:
        estimate = _estimate(corpus["events"], baseline, candidate, metric, config)
        safe = bool(
            estimate["raw"]["lower_95"]
            >= float(config["decision"]["guardrail_max_degradation"])
        )
        guardrails[metric] = {
            "estimate": estimate,
            "inside_safety_bound": safe,
        }
        guardrail_safe = guardrail_safe and safe
        if not safe:
            blockers.append("CRITICAL_GUARDRAIL_UNSAFE")

    strata = _strata_estimates(corpus, baseline, candidate, config)
    for row in strata:
        if row["event_count"] < int(config["decision"]["minimum_critical_stratum_events"]):
            blockers.append("CRITICAL_STRATUM_INSUFFICIENT_EVIDENCE")
        elif row["critical_reversal"]:
            blockers.append("CRITICAL_STRATUM_REVERSED")
        elif not row["inside_safety_bound"]:
            blockers.append("CRITICAL_STRATUM_SAFETY_INTERVAL_FAILED")

    if corpus["evidence_mode"] == "observational_monitoring":
        blockers.append("OBSERVATIONAL_EVIDENCE_NONCAUSAL")

    blockers = sorted(set(blockers))
    hard_human = {
        "UNSUPPORTED_ACTION_REGION",
        "EFFECTIVE_SAMPLE_SIZE_BELOW_FLOOR",
        "BASELINE_EFFECTIVE_SAMPLE_SIZE_BELOW_FLOOR",
        "POLICY_DISTRIBUTION_DRIFT",
        "POLICY_RULE_EXPLOSION",
        "EXPLORATION_BELOW_FLOOR",
        "ACTION_CONCENTRATION_ABOVE_CAP",
        "MULTIPLE_TESTING_BUDGET_OVERSPEND",
        "CLIPPED_WEIGHT_SENSITIVITY_EXCEEDED",
        "CRITICAL_GUARDRAIL_UNSAFE",
        "CRITICAL_STRATUM_REVERSED",
        "CRITICAL_STRATUM_SAFETY_INTERVAL_FAILED",
        "CRITICAL_STRATUM_INSUFFICIENT_EVIDENCE",
    }
    recommendation = "TEST_MORE"
    reason_codes = list(blockers)
    if any(code in hard_human for code in blockers):
        recommendation = "HUMAN_REVIEW_REQUIRED"
    elif "OBSERVATIONAL_EVIDENCE_NONCAUSAL" in blockers:
        recommendation = "TEST_MORE"
    elif "INCOMPLETE_OR_CENSORED_OUTCOMES" in blockers:
        recommendation = "TEST_MORE"
    else:
        lower = float(primary["raw"]["lower_95"])
        upper = float(primary["raw"]["upper_95"])
        effect = float(primary["raw"]["difference"])
        margin = float(config["decision"]["promotion_margin"])
        harmful = float(config["decision"]["harmful_threshold"])
        keep = float(config["decision"]["keep_equivalence_margin"])
        if upper <= harmful:
            recommendation = "SHADOW_ROLLBACK"
            reason_codes.append("OFFLINE_HARM_INTERVAL_CLEARS_ROLLBACK_THRESHOLD")
        elif lower > margin and guardrail_safe:
            recommendation = "SHADOW_CANARY_CANDIDATE"
            reason_codes.append("OFFLINE_PROMOTION_INTERVAL_CLEARS_MARGIN")
        elif abs(effect) <= keep and lower > harmful:
            recommendation = "KEEP_BASELINE"
            reason_codes.append("OFFLINE_EFFECT_WITHIN_BASELINE_EQUIVALENCE_MARGIN")
        else:
            recommendation = "TEST_MORE"
            reason_codes.append("OFFLINE_INTERVAL_DOES_NOT_CLEAR_DECISION_BOUNDARY")

    result = {
        "contract_version": DECISION_VERSION,
        "decision_id": "",
        "decision_digest": "",
        "status": STATUS,
        "disposition": "ADVISORY_ONLY",
        "recommendation": recommendation,
        "reason_codes": sorted(set(reason_codes)),
        "corpus_id": corpus["corpus_id"],
        "corpus_digest": corpus["corpus_digest"],
        "evidence_mode": corpus["evidence_mode"],
        "baseline_policy_id": baseline["policy_id"],
        "baseline_policy_digest": baseline["policy_digest"],
        "candidate_policy_id": candidate["policy_id"],
        "candidate_policy_digest": candidate["policy_digest"],
        "parent_r33_authority": parent_r33_tuple(),
        "r34_authority": {
            "producer_sha": growth_sha,
            "ci_run_id": growth_ci_run_id,
            "authority_digest": authority_digest(authority),
            "contract": CONTRACT_VERSION,
        },
        "estimator": {
            "method": config["estimator"]["method"],
            "confidence_method": config["estimator"]["confidence_method"],
            "primary": primary,
            "guardrails": guardrails,
            "candidate_propensity": {
                key: value
                for key, value in candidate_propensity.items()
                if key != "weights"
            },
            "baseline_propensity": {
                key: value
                for key, value in baseline_propensity.items()
                if key != "weights"
            },
        },
        "maturity": maturity,
        "drift": drift,
        "critical_strata": strata,
        "complexity": {
            "candidate_rule_count": len(candidate["rules"]),
            "maximum_rule_count": config["complexity"]["maximum_rule_count"],
            "minimum_exploration_floor": config["complexity"]["minimum_exploration_floor"],
            "maximum_action_concentration": config["complexity"]["maximum_action_concentration"],
        },
        "multiplicity": _clone(candidate["evaluation_family"]),
        "assumptions": [
            "consistency",
            "positivity/overlap",
            "source-bound logging propensities",
            "outcome model trained outside evaluation corpus",
            "same frozen corpus for baseline and candidate",
            "no interference across declared evaluation units",
        ],
        "limitations": [
            "offline replay does not prove live canary safety",
            "unmeasured confounding can invalidate causal interpretation",
            "observational evidence remains non-causal",
            "doubly-robust guarantees require at least one nuisance model to be valid",
        ],
        "advisory_boundary": {
            "advisory_only": True,
            "shadow_only": True,
            "creator_mutation": False,
            "provider_mutation": False,
            "browser_mutation": False,
            "traffic_routing": False,
            "budget_allocation": False,
            "live_publish": False,
            "human_ground_truth": False,
            "observational_is_causal": False,
        },
        "creator_r35_advisory_envelope": None,
    }
    result["decision_id"] = "gr34d1:" + sha256_json(
        {
            "corpus_digest": corpus["corpus_digest"],
            "baseline_policy_digest": baseline["policy_digest"],
            "candidate_policy_digest": candidate["policy_digest"],
            "policy_config_digest": policy_config_digest(config),
        }
    )
    digest_material = copy.deepcopy(result)
    digest_material["decision_digest"] = ""
    result["decision_digest"] = sha256_json(digest_material)
    result["creator_r35_advisory_envelope"] = _advisory_envelope(
        decision=result,
        corpus=corpus,
        baseline=baseline,
        candidate=candidate,
        authority=authority,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    # Envelope is nested after decision digest by design and binds that immutable digest.
    return _clone(result)


class PromotionLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.state = {"evaluations": {}}
        if self.path.exists():
            self.state = _load(self.path)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def record(self, decision: Mapping[str, Any]) -> bool:
        key = (
            f"{decision['corpus_id']}@"
            f"{decision['baseline_policy_id']}@{decision['candidate_policy_id']}"
        )
        prior = self.state["evaluations"].get(key)
        digest = decision["decision_digest"]
        if prior is not None:
            if prior != digest:
                raise ReplayConflict(
                    "same corpus/baseline/candidate identity changed replay bytes"
                )
            return False
        self.state["evaluations"][key] = digest
        self._save()
        return True


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-r34-counterfactual-policy-promotion")
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate_cmd = sub.add_parser("evaluate")
    evaluate_cmd.add_argument("--corpus", required=True)
    evaluate_cmd.add_argument("--baseline-policy", required=True)
    evaluate_cmd.add_argument("--candidate-policy", required=True)
    evaluate_cmd.add_argument("--authority", required=True)
    evaluate_cmd.add_argument("--policy-config", required=True)
    evaluate_cmd.add_argument("--ledger", required=True)
    evaluate_cmd.add_argument("--out-dir", required=True)
    evaluate_cmd.add_argument("--growth-sha", required=True)
    evaluate_cmd.add_argument("--growth-ci-run-id", required=True, type=int)

    rehearsal = sub.add_parser("rehearse-fixtures")
    rehearsal.add_argument("--authority", required=True)
    rehearsal.add_argument("--policy-config", required=True)
    rehearsal.add_argument("--out-dir", required=True)
    rehearsal.add_argument("--growth-sha", required=True)
    rehearsal.add_argument("--growth-ci-run-id", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = Path(args.out_dir)
    authority = _load(Path(args.authority))
    config = _load(Path(args.policy_config))
    try:
        if args.command == "rehearse-fixtures":
            from .counterfactual_policy_promotion_r34_sim import build_rehearsal

            report = build_rehearsal(
                authority=authority,
                policy_config=config,
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            _write(out / "simulation-report.json", report)
        else:
            decision = evaluate(
                corpus=_load(Path(args.corpus)),
                baseline_policy=_load(Path(args.baseline_policy)),
                candidate_policy=_load(Path(args.candidate_policy)),
                authority=authority,
                policy_config=config,
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            ledger = PromotionLedger(Path(args.ledger))
            newly_recorded = ledger.record(decision)
            _write(out / "decision.json", decision)
            if decision["creator_r35_advisory_envelope"] is not None:
                _write(
                    out / "creator-r35-advisory-envelope.json",
                    decision["creator_r35_advisory_envelope"],
                )
            report = {
                "report_version": REPORT_VERSION,
                "status": decision["status"],
                "disposition": "ADVISORY_ONLY",
                "recommendation": decision["recommendation"],
                "decision_digest": decision["decision_digest"],
                "corpus_digest": decision["corpus_digest"],
                "candidate_policy_digest": decision["candidate_policy_digest"],
                "newly_recorded": newly_recorded,
                "creator_envelope_digest": (
                    None
                    if decision["creator_r35_advisory_envelope"] is None
                    else decision["creator_r35_advisory_envelope"]["envelope_digest"]
                ),
                "creator_mutation": False,
                "provider_mutation": False,
                "traffic_routing": False,
                "live_publish": False,
                "human_ground_truth": False,
            }
            report["report_digest"] = sha256_json(report)
            _write(out / "readiness.json", report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as exc:
        blocked = {
            "report_version": REPORT_VERSION,
            "status": STATUS,
            "disposition": "ADVISORY_ONLY",
            "recommendation": "HUMAN_REVIEW_REQUIRED",
            "reason": type(exc).__name__,
            "detail": str(exc),
            "creator_mutation": False,
            "provider_mutation": False,
            "traffic_routing": False,
            "live_publish": False,
            "human_ground_truth": False,
        }
        blocked["report_digest"] = sha256_json(blocked)
        _write(out / "readiness.json", blocked)
        print(json.dumps(blocked, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
