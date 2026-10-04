from __future__ import annotations

import copy
import hashlib
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .autonomous_reels import sha256_json
from . import counterfactual_policy_promotion_r34 as r34


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _reference(corpus: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    events = corpus["events"]
    return {
        "platform": r34._distribution([event["platform"] for event in events]),
        "account_pseudonym": r34._distribution(
            [event["account_pseudonym"] for event in events]
        ),
        "topic_cluster": r34._distribution(
            [event["topic_cluster"] for event in events]
        ),
    }


def _make_events(
    *,
    label: str,
    n: int = 240,
    effect_a: float = 0.20,
    effect_b: float | None = None,
    guardrail_b_effect: float = 0.0,
    behavior_b: float = 0.5,
    action_b_every: int = 2,
    qhat_bias_b: float = 0.0,
    outcome_state: str = "MATURE",
) -> list[dict[str, Any]]:
    if effect_b is None:
        effect_b = effect_a
    rows = []
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for index in range(n):
        is_group_a = index < n // 2
        campaign_id = "campaign-a" if is_group_a else "campaign-b"
        platform = "instagram_reels" if is_group_a else "tiktok"
        account = "acct-a" if is_group_a else "acct-b"
        topic = "topic-a" if is_group_a else "topic-b"
        effect = effect_a if is_group_a else effect_b
        action = "B" if index % action_b_every == 1 else "A"
        exposure_at = start + timedelta(minutes=index)
        mature_at = exposure_at + timedelta(days=7)
        q_a = 0.40 + (index % 7) * 0.0005
        q_b = q_a + effect
        true = q_b if action == "B" else q_a
        residual = ((index % 5) - 2) * 0.0005
        primary = true + residual
        share_a = 0.05
        share_b = share_a + guardrail_b_effect
        comment_a = 0.03
        comment_b = comment_a + guardrail_b_effect / 2.0
        share = share_b if action == "B" else share_a
        comment = comment_b if action == "B" else comment_a
        event = {
            "event_id": f"evt-{label}-{index:04d}",
            "event_digest": "",
            "campaign_id": campaign_id,
            "session_id": f"session-{campaign_id}",
            "exposure_id": f"exp-{label}-{index:04d}",
            "action": action,
            "logging_propensities": {
                "A": round(1.0 - behavior_b, 12),
                "B": round(behavior_b, 12),
            },
            "platform": platform,
            "account_pseudonym": account,
            "topic_cluster": topic,
            "features": {
                "hour_bucket": f"h{(index // 60) % 4}",
                "source_type": "shortform_video",
            },
            "feature_provenance": {
                "hour_bucket": "pre_outcome_observed",
                "source_type": "pre_outcome_observed",
            },
            "feature_as_of": exposure_at.isoformat().replace("+00:00", "Z"),
            "exposure_at": exposure_at.isoformat().replace("+00:00", "Z"),
            "outcome_mature_at": mature_at.isoformat().replace("+00:00", "Z"),
            "outcome_state": outcome_state,
            "source_sha256": _hex(f"source:{label}:{index}"),
            "render_sha256": _hex(f"render:{label}:{index}"),
            "r31_lineage_digest": _hex(f"r31:{label}:{index}"),
            "r32_decision_digest": _hex(f"r32:{label}:{campaign_id}"),
            "r33_campaign_state_digest": _hex(f"r33:{label}:{campaign_id}"),
            "outcome": {
                "primary": round(primary, 12),
                "guardrails": {
                    "comment_rate": round(comment, 12),
                    "share_rate": round(share, 12),
                },
            },
            "outcome_model": {
                "A": {
                    "primary": round(q_a, 12),
                    "guardrails": {
                        "comment_rate": comment_a,
                        "share_rate": share_a,
                    },
                },
                "B": {
                    "primary": round(q_b + qhat_bias_b, 12),
                    "guardrails": {
                        "comment_rate": comment_b,
                        "share_rate": share_b,
                    },
                },
            },
        }
        event["event_digest"] = r34._event_digest(event)
        rows.append(event)
    return rows


def _make_corpus(
    *,
    label: str,
    evidence_mode: str = "randomized_controlled",
    n: int = 240,
    effect_a: float = 0.20,
    effect_b: float | None = None,
    guardrail_b_effect: float = 0.0,
    behavior_b: float = 0.5,
    action_b_every: int = 2,
    qhat_bias_b: float = 0.0,
    outcome_state: str = "MATURE",
    critical_strata: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if critical_strata is None:
        critical_strata = [
            {"dimension": "platform", "value": "instagram_reels"},
            {"dimension": "account_pseudonym", "value": "acct-a"},
            {"dimension": "topic_cluster", "value": "topic-a"},
        ]
    events = _make_events(
        label=label,
        n=n,
        effect_a=effect_a,
        effect_b=effect_b,
        guardrail_b_effect=guardrail_b_effect,
        behavior_b=behavior_b,
        action_b_every=action_b_every,
        qhat_bias_b=qhat_bias_b,
        outcome_state=outcome_state,
    )
    return r34.build_corpus(
        corpus_id=f"corpus-{label}",
        frozen_at="2026-09-15T00:00:00Z",
        evidence_mode=evidence_mode,
        metric_window_seconds=604800,
        evaluation_split_id=f"eval-{label}",
        evaluation_split_digest=_hex(f"eval-split:{label}"),
        source_portfolio_id=f"portfolio-{label}",
        source_portfolio_digest=_hex(f"portfolio:{label}"),
        source_r33_decision_digest=_hex(f"r33-decision:{label}"),
        campaigns=[
            {
                "campaign_id": "campaign-a",
                "platforms": ["instagram_reels"],
                "accounts": ["acct-a"],
                "topics": ["topic-a"],
            },
            {
                "campaign_id": "campaign-b",
                "platforms": ["tiktok"],
                "accounts": ["acct-b"],
                "topics": ["topic-b"],
            },
        ],
        critical_strata=critical_strata,
        critical_guardrails=["comment_rate", "share_rate"],
        outcome_model={
            "model_id": f"outcome-model-{label}",
            "model_digest": _hex(f"outcome-model:{label}"),
            "training_corpus_digest": _hex(f"outcome-training:{label}"),
            "training_split_id": f"outcome-train-{label}",
            "trained_on_evaluation_corpus": False,
            "feature_names": [
                "account_pseudonym",
                "platform",
                "topic_cluster",
            ],
        },
        events=events,
    )


def _make_policy(
    *,
    corpus: Mapping[str, Any],
    role: str,
    label: str,
    probability_b: float,
    rules: Sequence[Mapping[str, Any]] | None = None,
    reference_distribution: Mapping[str, Mapping[str, float]] | None = None,
    trained_on_eval: bool = False,
    training_digest: str | None = None,
    actions: Sequence[str] = ("A", "B"),
    features_used: Sequence[str] = (
        "account_pseudonym",
        "platform",
        "topic_cluster",
    ),
    test_count: int = 4,
    alpha_requested: float = 0.0125,
) -> dict[str, Any]:
    if rules is None:
        rules = []
    if reference_distribution is None:
        reference_distribution = _reference(corpus)
    if training_digest is None:
        training_digest = _hex(f"training:{label}:{role}")
    default = {}
    if set(actions) == {"A", "B"}:
        default = {"A": 1.0 - probability_b, "B": probability_b}
    else:
        each = 1.0 / len(actions)
        default = {action: each for action in actions}
    return r34.build_candidate_policy(
        policy_id=f"{role.lower()}-{label}",
        policy_role=role,
        policy_version=1,
        evaluation_corpus_digest=corpus["corpus_digest"],
        training_corpus_digest=training_digest,
        training_split_id=f"train-{label}-{role.lower()}",
        trained_on_evaluation_corpus=trained_on_eval,
        actions=actions,
        features_used=features_used,
        default_probabilities=default,
        rules=rules,
        reference_distribution=reference_distribution,
        evaluation_family={
            "family_id": f"offline-family-{label}",
            "test_index": 0,
            "test_count": test_count,
            "alpha_requested": alpha_requested,
        },
    )


def _pair(
    corpus: Mapping[str, Any],
    *,
    label: str,
    candidate_b: float = 0.7,
    candidate_rules: Sequence[Mapping[str, Any]] | None = None,
    candidate_reference: Mapping[str, Mapping[str, float]] | None = None,
    candidate_training_digest: str | None = None,
    candidate_trained_on_eval: bool = False,
    candidate_actions: Sequence[str] = ("A", "B"),
    candidate_features: Sequence[str] = (
        "account_pseudonym",
        "platform",
        "topic_cluster",
    ),
    test_count: int = 4,
    alpha_requested: float = 0.0125,
) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline = _make_policy(
        corpus=corpus,
        role="BASELINE_POLICY",
        label=label,
        probability_b=0.5,
        test_count=test_count,
        alpha_requested=alpha_requested,
    )
    candidate = _make_policy(
        corpus=corpus,
        role="CANDIDATE_POLICY",
        label=label,
        probability_b=candidate_b,
        rules=candidate_rules,
        reference_distribution=candidate_reference,
        trained_on_eval=candidate_trained_on_eval,
        training_digest=candidate_training_digest,
        actions=candidate_actions,
        features_used=candidate_features,
        test_count=test_count,
        alpha_requested=alpha_requested,
    )
    return baseline, candidate


def _evaluate(
    *,
    corpus: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    authority: Mapping[str, Any],
    config: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    return r34.evaluate(
        corpus=corpus,
        baseline_policy=baseline,
        candidate_policy=candidate,
        authority=authority,
        policy_config=config,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )


def _case(name: str, fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        value = fn()
    except Exception as exc:
        return {
            "name": name,
            "state": "REJECTED",
            "reason": type(exc).__name__,
            "detail": str(exc),
        }
    if isinstance(value, Mapping) and "recommendation" in value:
        envelope = value.get("creator_r35_advisory_envelope")
        return {
            "name": name,
            "state": "ACCEPTED",
            "recommendation": value["recommendation"],
            "reason_codes": value["reason_codes"],
            "decision_digest": value["decision_digest"],
            "primary": value["estimator"]["primary"],
            "candidate_effective_sample_size": value["estimator"][
                "candidate_propensity"
            ]["effective_sample_size"],
            "creator_envelope_digest": (
                None if envelope is None else envelope["envelope_digest"]
            ),
            "creator_execution_allowed": (
                None if envelope is None else envelope["creator_execution_allowed"]
            ),
            "provider_mutation": (
                False if envelope is None else envelope["provider_mutation"]
            ),
            "live_traffic_allowed": (
                False if envelope is None else envelope["live_traffic_allowed"]
            ),
            "advisory_only": value["advisory_boundary"]["advisory_only"],
        }
    return {"name": name, "state": "ACCEPTED", "value": value}


def _rebuild_from(
    corpus: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]] | None = None,
    evidence_mode: str | None = None,
    outcome_model: Mapping[str, Any] | None = None,
    critical_strata: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return r34.build_corpus(
        corpus_id=corpus["corpus_id"],
        frozen_at=corpus["frozen_at"],
        evidence_mode=corpus["evidence_mode"] if evidence_mode is None else evidence_mode,
        metric_window_seconds=corpus["metric_window_seconds"],
        evaluation_split_id=corpus["evaluation_split_id"],
        evaluation_split_digest=corpus["evaluation_split_digest"],
        source_portfolio_id=corpus["source_portfolio_id"],
        source_portfolio_digest=corpus["source_portfolio_digest"],
        source_r33_decision_digest=corpus["source_r33_decision_digest"],
        campaigns=corpus["campaigns"],
        critical_strata=(
            corpus["critical_strata"] if critical_strata is None else critical_strata
        ),
        critical_guardrails=corpus["critical_guardrails"],
        outcome_model=corpus["outcome_model"] if outcome_model is None else outcome_model,
        events=corpus["events"] if events is None else events,
    )


def build_rehearsal(
    *,
    authority: Mapping[str, Any],
    policy_config: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = r34.validate_authority(authority)
    config = r34.validate_policy_config(policy_config)
    r34._git_sha(growth_sha, "growth_sha")
    cases: dict[str, Any] = {}

    def run_corpus(
        name: str,
        corpus: Mapping[str, Any],
        **pair_kwargs: Any,
    ) -> dict[str, Any]:
        baseline, candidate = _pair(corpus, label=name, **pair_kwargs)
        return _evaluate(
            corpus=corpus,
            baseline=baseline,
            candidate=candidate,
            authority=authority,
            config=config,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        )

    clean = _make_corpus(label="clean")
    cases["01_clean_supported_uplift"] = _case(
        "01_clean_supported_uplift",
        lambda: run_corpus("clean", clean),
    )

    null = _make_corpus(label="null", effect_a=0.0, effect_b=0.0)
    cases["02_null_keep_baseline"] = _case(
        "02_null_keep_baseline",
        lambda: run_corpus("null", null),
    )

    weak = _make_corpus(label="weak", effect_a=0.04, effect_b=0.04)
    cases["03_weak_uplift_test_more"] = _case(
        "03_weak_uplift_test_more",
        lambda: run_corpus("weak", weak),
    )

    harmful = _make_corpus(
        label="harmful",
        effect_a=-0.20,
        effect_b=-0.20,
        critical_strata=[],
    )
    cases["04_clear_harm_shadow_rollback"] = _case(
        "04_clear_harm_shadow_rollback",
        lambda: run_corpus("harmful", harmful),
    )

    observational = _make_corpus(
        label="observational",
        evidence_mode="observational_monitoring",
    )
    cases["05_observational_never_causal"] = _case(
        "05_observational_never_causal",
        lambda: run_corpus("observational", observational),
    )

    unsupported = _make_corpus(label="unsupported", behavior_b=0.04)
    cases["06_weak_overlap_unsupported_region"] = _case(
        "06_weak_overlap_unsupported_region",
        lambda: run_corpus("unsupported", unsupported),
    )

    tiny_ess = _make_corpus(
        label="tiny-ess",
        behavior_b=0.05,
        action_b_every=4,
    )
    cases["07_tiny_effective_sample_size"] = _case(
        "07_tiny_effective_sample_size",
        lambda: run_corpus("tiny-ess", tiny_ess),
    )

    extreme = _make_corpus(
        label="extreme",
        behavior_b=0.06,
        action_b_every=4,
        qhat_bias_b=-0.20,
    )
    cases["08_extreme_weights_clipped_sensitivity"] = _case(
        "08_extreme_weights_clipped_sensitivity",
        lambda: run_corpus("extreme", extreme),
    )

    simpson = _make_corpus(
        label="simpson",
        effect_a=-0.20,
        effect_b=0.50,
    )
    cases["09_simpson_critical_stratum_reversal"] = _case(
        "09_simpson_critical_stratum_reversal",
        lambda: run_corpus("simpson", simpson),
    )

    all_key_negative = _make_corpus(
        label="all-key-negative",
        effect_a=-0.20,
        effect_b=0.60,
    )
    cases["10_overall_positive_every_key_stratum_negative"] = _case(
        "10_overall_positive_every_key_stratum_negative",
        lambda: run_corpus("all-key-negative", all_key_negative),
    )

    guardrail = _make_corpus(
        label="guardrail-collapse",
        guardrail_b_effect=-0.20,
    )
    cases["11_critical_guardrail_collapse"] = _case(
        "11_critical_guardrail_collapse",
        lambda: run_corpus("guardrail-collapse", guardrail),
    )

    censored = _make_corpus(label="censored", outcome_state="CENSORED")
    cases["12_censored_outcomes"] = _case(
        "12_censored_outcomes",
        lambda: run_corpus("censored", censored),
    )

    delayed = _make_corpus(label="delayed", outcome_state="DELAYED")
    cases["13_delayed_outcomes"] = _case(
        "13_delayed_outcomes",
        lambda: run_corpus("delayed", delayed),
    )

    novelty_decay = _make_corpus(
        label="novelty-decay",
        effect_a=0.0,
        effect_b=0.0,
    )
    cases["14_novelty_spike_decays_by_mature_replay"] = _case(
        "14_novelty_spike_decays_by_mature_replay",
        lambda: run_corpus("novelty-decay", novelty_decay),
    )

    platform_ref = _reference(clean)
    platform_ref["platform"] = {"instagram_reels": 1.0}
    cases["15_platform_distribution_drift"] = _case(
        "15_platform_distribution_drift",
        lambda: run_corpus(
            "platform-drift",
            clean,
            candidate_reference=platform_ref,
        ),
    )

    account_ref = _reference(clean)
    account_ref["account_pseudonym"] = {"acct-a": 1.0}
    cases["16_account_distribution_drift"] = _case(
        "16_account_distribution_drift",
        lambda: run_corpus(
            "account-drift",
            clean,
            candidate_reference=account_ref,
        ),
    )

    topic_ref = _reference(clean)
    topic_ref["topic_cluster"] = {"topic-a": 1.0}
    cases["17_topic_distribution_drift"] = _case(
        "17_topic_distribution_drift",
        lambda: run_corpus(
            "topic-drift",
            clean,
            candidate_reference=topic_ref,
        ),
    )

    leaked_events = copy.deepcopy(clean["events"])
    leaked_events[0]["feature_provenance"]["source_type"] = "post_outcome"
    leaked_events[0]["event_digest"] = r34._event_digest(leaked_events[0])
    leaked_corpus = _rebuild_from(clean, events=leaked_events)
    cases["18_leaked_outcome_feature"] = _case(
        "18_leaked_outcome_feature",
        lambda: run_corpus("leaked-outcome", leaked_corpus),
    )

    future_events = copy.deepcopy(clean["events"])
    future_events[0]["feature_as_of"] = "2026-10-01T00:00:00Z"
    future_events[0]["event_digest"] = r34._event_digest(future_events[0])
    bad_future = copy.deepcopy(clean)
    bad_future["events"] = future_events
    cases["19_future_data_leakage"] = _case(
        "19_future_data_leakage",
        lambda: r34.parse_corpus(bad_future),
    )

    proxy_events = copy.deepcopy(clean["events"])
    proxy_events[0]["feature_provenance"]["source_type"] = (
        "proxy_for_unobserved_treatment_driver"
    )
    proxy_events[0]["event_digest"] = r34._event_digest(proxy_events[0])
    proxy_corpus = _rebuild_from(clean, events=proxy_events)
    cases["20_hidden_confounder_proxy"] = _case(
        "20_hidden_confounder_proxy",
        lambda: run_corpus("hidden-proxy", proxy_corpus),
    )

    cases["21_policy_trained_on_evaluation_corpus"] = _case(
        "21_policy_trained_on_evaluation_corpus",
        lambda: run_corpus(
            "train-on-eval",
            clean,
            candidate_training_digest=clean["corpus_digest"],
        ),
    )

    cases["22_policy_declares_trained_on_eval"] = _case(
        "22_policy_declares_trained_on_eval",
        lambda: run_corpus(
            "trained-flag",
            clean,
            candidate_trained_on_eval=True,
        ),
    )

    outcome_bad = copy.deepcopy(clean["outcome_model"])
    outcome_bad["trained_on_evaluation_corpus"] = True
    cases["23_outcome_model_trained_on_eval"] = _case(
        "23_outcome_model_trained_on_eval",
        lambda: _rebuild_from(clean, outcome_model=outcome_bad),
    )

    duplicate_exposure = copy.deepcopy(clean)
    duplicate_exposure["events"][1]["exposure_id"] = duplicate_exposure["events"][0][
        "exposure_id"
    ]
    duplicate_exposure["events"][1]["event_digest"] = r34._event_digest(
        duplicate_exposure["events"][1]
    )
    cases["24_duplicate_exposure"] = _case(
        "24_duplicate_exposure",
        lambda: r34.parse_corpus(duplicate_exposure),
    )

    duplicate_event = copy.deepcopy(clean)
    duplicate_event["events"].append(copy.deepcopy(duplicate_event["events"][0]))
    cases["25_same_event_included_multiple_times"] = _case(
        "25_same_event_included_multiple_times",
        lambda: r34.parse_corpus(duplicate_event),
    )

    cross_account = copy.deepcopy(clean)
    cross_account["events"][0]["account_pseudonym"] = "acct-b"
    cross_account["events"][0]["event_digest"] = r34._event_digest(
        cross_account["events"][0]
    )
    cases["26_cross_account_contamination"] = _case(
        "26_cross_account_contamination",
        lambda: r34.parse_corpus(cross_account),
    )

    invalid_sum = copy.deepcopy(clean)
    invalid_sum["events"][0]["logging_propensities"] = {"A": 0.7, "B": 0.7}
    invalid_sum["events"][0]["event_digest"] = r34._event_digest(invalid_sum["events"][0])
    cases["27_invalid_propensity_sum"] = _case(
        "27_invalid_propensity_sum",
        lambda: r34.parse_corpus(invalid_sum),
    )

    zero_logged = copy.deepcopy(clean)
    first_action = zero_logged["events"][0]["action"]
    other_action = "B" if first_action == "A" else "A"
    zero_logged["events"][0]["logging_propensities"] = {
        first_action: 0.0,
        other_action: 1.0,
    }
    zero_logged["events"][0]["event_digest"] = r34._event_digest(zero_logged["events"][0])
    cases["28_zero_logged_action_propensity"] = _case(
        "28_zero_logged_action_propensity",
        lambda: r34.parse_corpus(zero_logged),
    )

    rules = [
        {
            "match": {"hour_bucket": f"h{index % 4}", "source_type": f"type-{index}"},
            "probabilities": {"A": 0.3, "B": 0.7},
        }
        for index in range(9)
    ]
    cases["29_policy_rule_explosion"] = _case(
        "29_policy_rule_explosion",
        lambda: run_corpus("rule-explosion", clean, candidate_rules=rules),
    )

    cases["30_reduced_exploration"] = _case(
        "30_reduced_exploration",
        lambda: run_corpus("low-exploration", clean, candidate_b=0.9),
    )

    cases["31_excessive_action_concentration"] = _case(
        "31_excessive_action_concentration",
        lambda: run_corpus("concentration", clean, candidate_b=0.75),
    )

    cases["32_multiple_testing_budget_overspend"] = _case(
        "32_multiple_testing_budget_overspend",
        lambda: run_corpus(
            "multiplicity",
            clean,
            test_count=5,
            alpha_requested=0.02,
        ),
    )

    bad_r33 = copy.deepcopy(clean)
    bad_r33["parent_r33_authority"]["producer_sha"] = "0" * 40
    cases["33_conflicting_r33_parent_authority"] = _case(
        "33_conflicting_r33_parent_authority",
        lambda: r34.parse_corpus(bad_r33),
    )

    bad_digest = copy.deepcopy(clean)
    bad_digest["corpus_digest"] = "0" * 64
    cases["34_replay_corpus_digest_tamper"] = _case(
        "34_replay_corpus_digest_tamper",
        lambda: r34.parse_corpus(bad_digest),
    )

    baseline, candidate = _pair(clean, label="action-mismatch", candidate_actions=("A", "C"))
    cases["35_candidate_action_set_mismatch"] = _case(
        "35_candidate_action_set_mismatch",
        lambda: _evaluate(
            corpus=clean,
            baseline=baseline,
            candidate=candidate,
            authority=authority,
            config=config,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    wrong_eval_candidate = _make_policy(
        corpus=clean,
        role="CANDIDATE_POLICY",
        label="wrong-eval",
        probability_b=0.7,
        training_digest=_hex("training:wrong-eval"),
    )
    wrong_eval_candidate = copy.deepcopy(wrong_eval_candidate)
    wrong_eval_candidate["evaluation_corpus_digest"] = "0" * 64
    material = copy.deepcopy(wrong_eval_candidate)
    material["policy_digest"] = ""
    wrong_eval_candidate["policy_digest"] = sha256_json(material)
    baseline_wrong = _make_policy(
        corpus=clean,
        role="BASELINE_POLICY",
        label="wrong-eval",
        probability_b=0.5,
    )
    cases["36_baseline_candidate_corpus_binding_mismatch"] = _case(
        "36_baseline_candidate_corpus_binding_mismatch",
        lambda: _evaluate(
            corpus=clean,
            baseline=baseline_wrong,
            candidate=wrong_eval_candidate,
            authority=authority,
            config=config,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    qa_bad = copy.deepcopy(authority)
    qa_bad["hard_wave_qa_r5"]["producer_sha"] = "0" * 40
    cases["37_qa_r5_authority_tamper"] = _case(
        "37_qa_r5_authority_tamper",
        lambda: _evaluate(
            corpus=clean,
            baseline=_pair(clean, label="qa-bad")[0],
            candidate=_pair(clean, label="qa-bad")[1],
            authority=qa_bad,
            config=config,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    # Reordered event bytes must canonicalize to the same corpus and decision.
    reversed_corpus = _rebuild_from(clean, events=list(reversed(clean["events"])))
    b1, c1 = _pair(clean, label="order")
    b2, c2 = _pair(reversed_corpus, label="order")
    d1 = _evaluate(
        corpus=clean,
        baseline=b1,
        candidate=c1,
        authority=authority,
        config=config,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    d2 = _evaluate(
        corpus=reversed_corpus,
        baseline=b2,
        candidate=c2,
        authority=authority,
        config=config,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cases["38_input_order_invariance"] = {
        "name": "38_input_order_invariance",
        "state": "ACCEPTED",
        "same_corpus_digest": clean["corpus_digest"] == reversed_corpus["corpus_digest"],
        "same_decision_digest": d1["decision_digest"] == d2["decision_digest"],
        "recommendation": d1["recommendation"],
    }

    with tempfile.TemporaryDirectory() as tmp:
        ledger = r34.PromotionLedger(Path(tmp) / "ledger.json")
        first = ledger.record(d1)
        second = ledger.record(d1)
        cases["39_exact_replay_idempotent"] = {
            "name": "39_exact_replay_idempotent",
            "state": "ACCEPTED",
            "first_recorded": first,
            "second_recorded": second,
            "decision_digest": d1["decision_digest"],
        }
        changed = copy.deepcopy(d1)
        changed["decision_digest"] = "0" * 64
        cases["40_changed_replay_conflict"] = _case(
            "40_changed_replay_conflict",
            lambda: ledger.record(changed),
        )

    # A changed genuinely new candidate policy is a new evaluation identity.
    new_baseline, new_candidate = _pair(clean, label="new-policy", candidate_b=0.65)
    cases["41_new_policy_identity_is_independently_evaluable"] = _case(
        "41_new_policy_identity_is_independently_evaluable",
        lambda: _evaluate(
            corpus=clean,
            baseline=new_baseline,
            candidate=new_candidate,
            authority=authority,
            config=config,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    safety_violations = []
    for name, case in cases.items():
        if case.get("state") != "ACCEPTED":
            continue
        if case.get("creator_execution_allowed") is True:
            safety_violations.append((name, "creator_execution_allowed"))
        if case.get("provider_mutation") is True:
            safety_violations.append((name, "provider_mutation"))
        if case.get("live_traffic_allowed") is True:
            safety_violations.append((name, "live_traffic_allowed"))

    clean_case = cases["01_clean_supported_uplift"]
    report = {
        "report_version": r34.REPORT_VERSION,
        "status": r34.STATUS,
        "disposition": "ADVISORY_ONLY",
        "case_count": len(cases),
        "accepted_case_count": sum(
            1 for case in cases.values() if case["state"] == "ACCEPTED"
        ),
        "rejected_case_count": sum(
            1 for case in cases.values() if case["state"] == "REJECTED"
        ),
        "cases": {key: cases[key] for key in sorted(cases)},
        "clean_promotion": {
            "recommendation": clean_case.get("recommendation"),
            "decision_digest": clean_case.get("decision_digest"),
            "creator_envelope_digest": clean_case.get("creator_envelope_digest"),
        },
        "counterfactual_assumptions": [
            "consistency",
            "positivity/overlap above 0.05",
            "valid source-bound logging propensities",
            "outcome model trained outside evaluation corpus",
            "same frozen corpus for baseline and candidate",
            "no undeclared interference",
        ],
        "limitations": [
            "offline replay does not prove live safety",
            "observational replay is non-causal",
            "unmeasured confounding can invalidate estimates",
            "weak overlap inflates variance and extrapolation risk",
            "doubly-robust protection requires at least one nuisance model to be valid",
        ],
        "advisory_safety": {
            "shadow_canary_candidate_is_advisory": True,
            "creator_mutation": False,
            "provider_mutation": False,
            "browser_mutation": False,
            "traffic_routing": False,
            "budget_allocation": False,
            "live_publish": False,
            "human_ground_truth": False,
            "violations": safety_violations,
        },
        "authority": {
            "growth_r33": r34.parent_r33_tuple(),
            "hard_wave_qa_r5": {
                "producer_sha": r34.QA_R5_SHA,
                "checkpoint_blob": r34.QA_R5_CHECKPOINT_BLOB,
                "disposition": "ACCEPTED",
            },
            "r34_authority_digest": r34.authority_digest(authority),
        },
    }
    report["report_digest"] = sha256_json(report)
    return report
