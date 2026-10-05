from __future__ import annotations

import copy
import hashlib
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .autonomous_reels import sha256_json
from . import canary_evidence_registry_r35 as r35


SNAPSHOT_AT = "2026-10-05T00:00:00Z"
AS_OF = "2026-10-05T00:00:00Z"
CREATED_AT = "2026-10-01T00:00:00Z"
MATURE_AT = "2026-10-01T00:00:00Z"
TARGET_POLICY_DIGEST = hashlib.sha256(b"r35-target-policy").hexdigest()


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _provenance(
    *,
    source_class: str,
    seed: str,
    run_id: int | None = None,
    artifact_id: int | None = None,
    artifact_digest: str | None = None,
    trained_on_eval: bool = False,
    causal_claim_allowed: bool | None = None,
    post_outcome: bool = False,
    future: bool = False,
) -> dict[str, Any]:
    if source_class == "RANDOMIZED":
        evidence_mode = "randomized_controlled"
        derivation = "direct_randomized"
        fixture = False
        causal = True
    elif source_class == "OFF_POLICY_REPLAY":
        evidence_mode = "randomized_controlled"
        derivation = "off_policy_replay"
        fixture = False
        causal = False
    elif source_class == "OBSERVATIONAL":
        evidence_mode = "observational_monitoring"
        derivation = "off_policy_replay"
        fixture = False
        causal = False
    elif source_class == "SYNTHETIC_TEST":
        evidence_mode = "randomized_controlled"
        derivation = "off_policy_replay"
        fixture = True
        causal = False
    else:
        raise ValueError(source_class)
    if causal_claim_allowed is not None:
        causal = causal_claim_allowed
    if run_id is None:
        run_id = 900000 + int(_hex(seed)[:6], 16) % 50000
    if artifact_id is None:
        artifact_id = 800000 + int(_hex(seed + ":artifact")[:6], 16) % 50000
    if artifact_digest is None:
        artifact_digest = "sha256:" + _hex(seed + ":artifact-bytes")
    return {
        "source_class": source_class,
        "evidence_mode": evidence_mode,
        "derivation": derivation,
        "fixture": fixture,
        "causal_claim_allowed": causal,
        "source_run_id": run_id,
        "source_artifact_id": artifact_id,
        "source_artifact_digest": artifact_digest,
        "source_decision_digest": _hex(seed + ":source-decision"),
        "trained_on_evaluation_corpus": trained_on_eval,
        "post_outcome_feature_leakage": post_outcome,
        "future_feature_leakage": future,
    }


def _entry(
    *,
    policy: Mapping[str, Any],
    seed: str,
    source_class: str = "OFF_POLICY_REPLAY",
    policy_digest_value: str = TARGET_POLICY_DIGEST,
    policy_id: str = "policy-target",
    corpus: str | None = None,
    underlying: str | None = None,
    exposure: str | None = None,
    recommendation: str = "SHADOW_CANARY_CANDIDATE",
    lower: float = 0.03,
    point: float = 0.04,
    upper: float = 0.05,
    ess: float = 160.0,
    min_propensity: float = 0.20,
    max_weight: float = 2.0,
    clip_sensitivity: float = 0.005,
    unsupported_regions: int = 0,
    drift: bool = False,
    platform_tv: float = 0.10,
    account_tv: float = 0.10,
    topic_tv: float = 0.10,
    guardrail_failures: Sequence[str] = (),
    stratum_reversals: Sequence[str] = (),
    created_at: str = CREATED_AT,
    mature_at: str = MATURE_AT,
    maturity_state: str = "MATURE",
    maturity_window_seconds: int = 604800,
    run_id: int | None = None,
    artifact_id: int | None = None,
    artifact_digest: str | None = None,
    trained_on_eval: bool = False,
    causal_claim_allowed: bool | None = None,
    post_outcome: bool = False,
    future: bool = False,
    decision_salt: str = "",
    supersedes: Sequence[str] = (),
    resolves: Sequence[str] = (),
) -> dict[str, Any]:
    corpus_id = corpus or f"corpus-{seed}"
    corpus_digest = _hex(f"{corpus_id}:digest")
    underlying_digest = _hex(
        f"{underlying or corpus_id}:underlying-event-corpus"
    )
    exposure_digest = _hex(
        f"{exposure or corpus_id}:exposure-identities"
    )
    provenance = _provenance(
        source_class=source_class,
        seed=seed,
        run_id=run_id,
        artifact_id=artifact_id,
        artifact_digest=artifact_digest,
        trained_on_eval=trained_on_eval,
        causal_claim_allowed=causal_claim_allowed,
        post_outcome=post_outcome,
        future=future,
    )
    return r35.build_entry(
        policy=policy,
        policy_id=policy_id,
        policy_digest_value=policy_digest_value,
        corpus_id=corpus_id,
        corpus_digest=corpus_digest,
        underlying_event_corpus_digest=underlying_digest,
        exposure_identity_digest=exposure_digest,
        estimator_config_digest=_hex("r34-estimator-config"),
        strata_guardrail_digest=_hex("r34-strata-guardrail-config"),
        decision_digest=_hex(
            f"{seed}:decision:{recommendation}:{decision_salt}"
        ),
        decision_recommendation=recommendation,
        confidence_interval={
            "lower_95": lower,
            "point_estimate": point,
            "upper_95": upper,
        },
        effective_sample_size=ess,
        minimum_behavior_propensity=min_propensity,
        maximum_importance_weight=max_weight,
        clipped_weight_sensitivity=clip_sensitivity,
        unsupported_action_regions=unsupported_regions,
        drift_diagnostics={
            "platform_total_variation": platform_tv,
            "account_total_variation": account_tv,
            "topic_total_variation": topic_tv,
            "stale_or_shifted": drift,
        },
        critical_guardrail_failures=list(guardrail_failures),
        critical_stratum_reversals=list(stratum_reversals),
        provenance=provenance,
        created_at=created_at,
        evidence_mature_at=mature_at,
        maturity_window_seconds=maturity_window_seconds,
        maturity_state=maturity_state,
        supersedes_entry_ids=list(supersedes),
        resolves_conflict_entry_ids=list(resolves),
    )


def _registry(
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    registry_id: str,
) -> r35.EvidenceRegistry:
    return r35.EvidenceRegistry(
        registry_id=registry_id,
        path=None,
        authority=authority,
        policy=policy,
    )


def _certificate_case(
    *,
    name: str,
    entries: Sequence[Mapping[str, Any]],
    expected: str,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
    target_policy_digest: str = TARGET_POLICY_DIGEST,
    as_of: str = AS_OF,
) -> dict[str, Any]:
    registry = _registry(
        authority=authority,
        policy=policy,
        registry_id=f"registry-{name}",
    )
    results = registry.register_many(entries)
    snapshot = registry.snapshot(
        snapshot_at=SNAPSHOT_AT,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cert = r35.build_certificate(
        snapshot=snapshot,
        authority=authority,
        policy=policy,
        target_policy_digest=target_policy_digest,
        as_of=as_of,
    )
    return {
        "name": name,
        "scenario_only": True,
        "expected_disposition": expected,
        "actual_disposition": cert["readiness"],
        "passed": cert["readiness"] == expected,
        "registry_snapshot_digest": snapshot["registry_snapshot_digest"],
        "certificate_digest": cert["certificate_digest"],
        "entry_ids": sorted(
            entry["registry_entry_id"] for entry in entries
        ),
        "register_results": results,
        "excluded_entries": cert["excluded_entries"],
        "unresolved_conflicts": cert["unresolved_conflicts"],
        "negative_history_entry_ids": cert["negative_history_entry_ids"],
        "live_authorization": cert["live_authorization"],
        "provider_mutation_allowed": cert["provider_mutation_allowed"],
        "creator_mutation_allowed": cert["creator_mutation_allowed"],
    }


def _exception_case(
    *,
    name: str,
    expected_exception: str,
    fn: Callable[[], Any],
) -> dict[str, Any]:
    try:
        fn()
    except Exception as exc:
        actual = type(exc).__name__
        return {
            "name": name,
            "scenario_only": True,
            "expected_disposition": f"REJECTED:{expected_exception}",
            "actual_disposition": f"REJECTED:{actual}",
            "passed": actual == expected_exception,
            "detail": str(exc),
            "live_authorization": False,
            "provider_mutation_allowed": False,
            "creator_mutation_allowed": False,
        }
    return {
        "name": name,
        "scenario_only": True,
        "expected_disposition": f"REJECTED:{expected_exception}",
        "actual_disposition": "UNEXPECTED_ACCEPT",
        "passed": False,
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
    }


def _rebuild_links(
    entry: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    supersedes: Sequence[str],
    resolves: Sequence[str] = (),
) -> dict[str, Any]:
    p = entry["provenance"]
    return r35.build_entry(
        policy=policy,
        policy_id=entry["policy_id"],
        policy_digest_value=entry["policy_digest"],
        corpus_id=entry["corpus_id"],
        corpus_digest=entry["corpus_digest"],
        underlying_event_corpus_digest=entry["underlying_event_corpus_digest"],
        exposure_identity_digest=entry["exposure_identity_digest"],
        estimator_config_digest=entry["estimator_config_digest"],
        strata_guardrail_digest=entry["strata_guardrail_digest"],
        decision_digest=entry["decision_digest"],
        decision_recommendation=entry["decision_recommendation"],
        confidence_interval=entry["confidence_interval"],
        effective_sample_size=entry["diagnostics"]["effective_sample_size"],
        minimum_behavior_propensity=entry["diagnostics"][
            "minimum_behavior_propensity"
        ],
        maximum_importance_weight=entry["diagnostics"][
            "maximum_importance_weight"
        ],
        clipped_weight_sensitivity=entry["diagnostics"][
            "clipped_weight_sensitivity"
        ],
        unsupported_action_regions=entry["diagnostics"][
            "unsupported_action_regions"
        ],
        drift_diagnostics=entry["drift_diagnostics"],
        critical_guardrail_failures=entry["critical_guardrail_failures"],
        critical_stratum_reversals=entry["critical_stratum_reversals"],
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
            "post_outcome_feature_leakage": p[
                "post_outcome_feature_leakage"
            ],
            "future_feature_leakage": p["future_feature_leakage"],
        },
        created_at=entry["created_at"],
        evidence_mature_at=entry["maturity"]["evidence_mature_at"],
        maturity_window_seconds=entry["maturity"][
            "maturity_window_seconds"
        ],
        maturity_state=entry["maturity"]["state"],
        supersedes_entry_ids=list(supersedes),
        resolves_conflict_entry_ids=list(resolves),
    )


def build_rehearsal(
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
    root: Path,
) -> dict[str, Any]:
    authority = r35.validate_authority(authority)
    policy = r35.validate_policy(policy)
    r35._git_sha(growth_sha, "growth_sha")
    cases: dict[str, Any] = {}

    # 1: two independent fresh off-policy entries can nominate only a shadow canary.
    e1 = _entry(policy=policy, seed="01-a", corpus="corpus-01-a")
    e2 = _entry(policy=policy, seed="01-b", corpus="corpus-01-b")
    cases["01_two_independent_replays_candidate"] = _certificate_case(
        name="01_two_independent_replays_candidate",
        entries=[e1, e2],
        expected="SHADOW_CANARY_CANDIDATE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 2: one eligible corpus is insufficient.
    cases["02_single_replay_test_more"] = _certificate_case(
        name="02_single_replay_test_more",
        entries=[_entry(policy=policy, seed="02")],
        expected="TEST_MORE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 3/4 provenance hierarchy excludes observational and synthetic evidence.
    cases["03_observational_positive_not_ready"] = _certificate_case(
        name="03_observational_positive_not_ready",
        entries=[
            _entry(
                policy=policy,
                seed="03",
                source_class="OBSERVATIONAL",
            )
        ],
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cases["04_synthetic_positive_not_ready"] = _certificate_case(
        name="04_synthetic_positive_not_ready",
        entries=[
            _entry(
                policy=policy,
                seed="04",
                source_class="SYNTHETIC_TEST",
            )
        ],
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 5: direct randomized + independent replay meets the source hierarchy.
    cases["05_randomized_plus_replay_candidate"] = _certificate_case(
        name="05_randomized_plus_replay_candidate",
        entries=[
            _entry(
                policy=policy,
                seed="05-r",
                source_class="RANDOMIZED",
                corpus="corpus-05-r",
            ),
            _entry(
                policy=policy,
                seed="05-o",
                source_class="OFF_POLICY_REPLAY",
                corpus="corpus-05-o",
            ),
        ],
        expected="SHADOW_CANARY_CANDIDATE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 6: same evidence copied under a new run is counted once.
    copied_a = _entry(
        policy=policy,
        seed="06-base",
        corpus="corpus-06",
        run_id=1001,
        artifact_id=2001,
        artifact_digest="sha256:" + _hex("06-artifact"),
    )
    copied_b = _entry(
        policy=policy,
        seed="06-base",
        corpus="corpus-06",
        run_id=1002,
        artifact_id=2001,
        artifact_digest="sha256:" + _hex("06-artifact"),
    )
    cases["06_duplicate_artifact_new_run_counted_once"] = _certificate_case(
        name="06_duplicate_artifact_new_run_counted_once",
        entries=[copied_a, copied_b],
        expected="TEST_MORE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 7: same policy/corpus identity with changed evidence bytes is a hard conflict.
    changed_a = _entry(
        policy=policy,
        seed="07-a",
        corpus="corpus-07",
        decision_salt="a",
    )
    changed_b = _entry(
        policy=policy,
        seed="07-b",
        corpus="corpus-07",
        decision_salt="b",
        point=0.045,
        lower=0.031,
        upper=0.059,
    )
    cases["07_same_policy_corpus_changed_bytes"] = _certificate_case(
        name="07_same_policy_corpus_changed_bytes",
        entries=[changed_a, changed_b],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 8: same policy on a genuinely different corpus is distinct evidence.
    cases["08_same_policy_new_corpus_distinct"] = _certificate_case(
        name="08_same_policy_new_corpus_distinct",
        entries=[
            _entry(policy=policy, seed="08-a", corpus="corpus-08-a"),
            _entry(policy=policy, seed="08-b", corpus="corpus-08-b"),
        ],
        expected="SHADOW_CANARY_CANDIDATE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 9: randomized positive versus observational mature rollback is contradictory.
    cases["09_randomized_vs_observational_contradiction"] = _certificate_case(
        name="09_randomized_vs_observational_contradiction",
        entries=[
            _entry(
                policy=policy,
                seed="09-r",
                source_class="RANDOMIZED",
                corpus="corpus-09-r",
            ),
            _entry(
                policy=policy,
                seed="09-o",
                source_class="OBSERVATIONAL",
                corpus="corpus-09-o",
                recommendation="SHADOW_ROLLBACK",
                lower=-0.08,
                point=-0.06,
                upper=-0.04,
            ),
        ],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 10: expired positive evidence remains visible but cannot confer readiness.
    expired = _entry(
        policy=policy,
        seed="10",
        created_at="2026-08-01T00:00:00Z",
        mature_at="2026-08-01T00:00:00Z",
    )
    cases["10_expired_positive_not_ready"] = _certificate_case(
        name="10_expired_positive_not_ready",
        entries=[expired],
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 11: critical guardrail evidence cannot be averaged away.
    cases["11_fresh_negative_guardrail_veto"] = _certificate_case(
        name="11_fresh_negative_guardrail_veto",
        entries=[
            _entry(policy=policy, seed="11-a", corpus="corpus-11-a"),
            _entry(
                policy=policy,
                seed="11-b",
                corpus="corpus-11-b",
                guardrail_failures=["share_rate"],
            ),
        ],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 12: positive evidence under material shift is retained but invalid for readiness.
    cases["12_shifted_positive_not_ready"] = _certificate_case(
        name="12_shifted_positive_not_ready",
        entries=[
            _entry(
                policy=policy,
                seed="12",
                drift=True,
                platform_tv=0.40,
            )
        ],
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 13: duplicate exposure identity across distinct corpora is a conflict.
    cases["13_copied_exposures_across_corpora"] = _certificate_case(
        name="13_copied_exposures_across_corpora",
        entries=[
            _entry(
                policy=policy,
                seed="13-a",
                corpus="corpus-13-a",
                exposure="shared-13",
            ),
            _entry(
                policy=policy,
                seed="13-b",
                corpus="corpus-13-b",
                exposure="shared-13",
            ),
        ],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 14: readiness-eligible evidence trained on eval is hard rejected.
    cases["14_hidden_evaluation_leakage_rejected"] = _exception_case(
        name="14_hidden_evaluation_leakage_rejected",
        expected_exception="EntryRejected",
        fn=lambda: _entry(
            policy=policy,
            seed="14",
            trained_on_eval=True,
        ),
    )

    # 15: explicit observational eval-training stays non-causal and disqualified.
    cases["15_observational_eval_training_disqualified"] = _certificate_case(
        name="15_observational_eval_training_disqualified",
        entries=[
            _entry(
                policy=policy,
                seed="15",
                source_class="OBSERVATIONAL",
                trained_on_eval=True,
            )
        ],
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 16: stale R34 parent tuple is rejected.
    stale = copy.deepcopy(_entry(policy=policy, seed="16"))
    stale["authority"]["growth_r34"]["producer_sha"] = "0" * 40
    cases["16_stale_parent_r34_authority"] = _exception_case(
        name="16_stale_parent_r34_authority",
        expected_exception="AuthorityDrift",
        fn=lambda: r35.parse_entry(stale, policy=policy),
    )

    # 17: alternate artifact tuple for same SHA is not accepted authority.
    alternate = copy.deepcopy(_entry(policy=policy, seed="17"))
    alternate["authority"]["growth_r34"]["artifact_id"] = r35.R34_ARTIFACT_ID + 99
    cases["17_alternate_same_sha_artifact_tuple"] = _exception_case(
        name="17_alternate_same_sha_artifact_tuple",
        expected_exception="AuthorityDrift",
        fn=lambda: r35.parse_entry(alternate, policy=policy),
    )

    # 18: candidate certificate cannot omit fresh negative evidence.
    positive18 = _entry(
        policy=policy,
        seed="18-p",
        source_class="RANDOMIZED",
        corpus="corpus-18-p",
    )
    negative18 = _entry(
        policy=policy,
        seed="18-n",
        source_class="RANDOMIZED",
        corpus="corpus-18-n",
        recommendation="SHADOW_ROLLBACK",
        lower=-0.08,
        point=-0.06,
        upper=-0.04,
    )
    case18 = _certificate_case(
        name="18_negative_evidence_cannot_be_omitted",
        entries=[positive18, negative18],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    case18["negative_visible"] = (
        negative18["registry_entry_id"]
        in case18["negative_history_entry_ids"]
    )
    case18["passed"] = case18["passed"] and case18["negative_visible"]
    cases["18_negative_evidence_cannot_be_omitted"] = case18

    # 19: insertion order does not change snapshot or certificate.
    order_entries = [
        _entry(policy=policy, seed="19-a", corpus="corpus-19-a"),
        _entry(policy=policy, seed="19-b", corpus="corpus-19-b"),
        _entry(
            policy=policy,
            seed="19-c",
            corpus="corpus-19-c",
            source_class="OBSERVATIONAL",
        ),
    ]
    reg19a = _registry(
        authority=authority,
        policy=policy,
        registry_id="registry-order-19",
    )
    reg19b = _registry(
        authority=authority,
        policy=policy,
        registry_id="registry-order-19",
    )
    reg19a.register_many(order_entries)
    reg19b.register_many(list(reversed(order_entries)))
    snap19a = reg19a.snapshot(
        snapshot_at=SNAPSHOT_AT,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    snap19b = reg19b.snapshot(
        snapshot_at=SNAPSHOT_AT,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cert19a = r35.build_certificate(
        snapshot=snap19a,
        authority=authority,
        policy=policy,
        target_policy_digest=TARGET_POLICY_DIGEST,
        as_of=AS_OF,
    )
    cert19b = r35.build_certificate(
        snapshot=snap19b,
        authority=authority,
        policy=policy,
        target_policy_digest=TARGET_POLICY_DIGEST,
        as_of=AS_OF,
    )
    same19 = (
        snap19a["registry_snapshot_digest"]
        == snap19b["registry_snapshot_digest"]
        and cert19a["certificate_digest"] == cert19b["certificate_digest"]
    )
    cases["19_registry_ordering_invariant"] = {
        "name": "19_registry_ordering_invariant",
        "scenario_only": True,
        "expected_disposition": "BYTE_STABLE",
        "actual_disposition": "BYTE_STABLE" if same19 else "DIFFERENT",
        "passed": same19,
        "snapshot_digest": snap19a["registry_snapshot_digest"],
        "certificate_digest": cert19a["certificate_digest"],
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
    }

    # 20: same exact input returns byte-stable canonical JSON.
    same20 = (
        canonical_snapshot := r35.sha256_json(snap19a)
    ) == r35.sha256_json(copy.deepcopy(snap19a))
    cases["20_same_exact_input_byte_stable"] = {
        "name": "20_same_exact_input_byte_stable",
        "scenario_only": True,
        "expected_disposition": "BYTE_STABLE",
        "actual_disposition": "BYTE_STABLE" if same20 else "DIFFERENT",
        "passed": same20,
        "canonical_digest": canonical_snapshot,
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
    }

    # 21: supersession cycle is explicit conflict.
    base21a = _entry(
        policy=policy,
        seed="21-a",
        source_class="OBSERVATIONAL",
        corpus="corpus-21-a",
    )
    base21b = _entry(
        policy=policy,
        seed="21-b",
        source_class="OFF_POLICY_REPLAY",
        corpus="corpus-21-b",
    )
    cycle21a = _rebuild_links(
        base21a,
        policy=policy,
        supersedes=[base21b["registry_entry_id"]],
    )
    cycle21b = _rebuild_links(
        base21b,
        policy=policy,
        supersedes=[base21a["registry_entry_id"]],
    )
    cases["21_supersession_cycle"] = _certificate_case(
        name="21_supersession_cycle",
        entries=[cycle21a, cycle21b],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 22: weaker evidence cannot supersede stronger evidence.
    strong22 = _entry(
        policy=policy,
        seed="22-strong",
        source_class="RANDOMIZED",
        corpus="corpus-22-a",
    )
    weak22base = _entry(
        policy=policy,
        seed="22-weak",
        source_class="OBSERVATIONAL",
        corpus="corpus-22-b",
    )
    weak22 = _rebuild_links(
        weak22base,
        policy=policy,
        supersedes=[strong22["registry_entry_id"]],
    )
    cases["22_supersession_to_weaker_evidence"] = _certificate_case(
        name="22_supersession_to_weaker_evidence",
        entries=[strong22, weak22],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 23: rollback memory cannot be erased by unresolving supersession.
    rollback23 = _entry(
        policy=policy,
        seed="23-old",
        source_class="OBSERVATIONAL",
        corpus="corpus-23-old",
        recommendation="SHADOW_ROLLBACK",
        lower=-0.08,
        point=-0.06,
        upper=-0.04,
    )
    successor23base = _entry(
        policy=policy,
        seed="23-new",
        source_class="OFF_POLICY_REPLAY",
        corpus="corpus-23-new",
    )
    successor23 = _rebuild_links(
        successor23base,
        policy=policy,
        supersedes=[rollback23["registry_entry_id"]],
    )
    cases["23_rollback_memory_erasure_attempt"] = _certificate_case(
        name="23_rollback_memory_erasure_attempt",
        entries=[rollback23, successor23],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 24: explicit stronger resolution plus a second independent positive may proceed.
    old24 = _entry(
        policy=policy,
        seed="24-old",
        source_class="OBSERVATIONAL",
        corpus="corpus-24-old",
        recommendation="SHADOW_ROLLBACK",
        lower=-0.08,
        point=-0.06,
        upper=-0.04,
    )
    stronger24base = _entry(
        policy=policy,
        seed="24-new",
        source_class="RANDOMIZED",
        corpus="corpus-24-new",
    )
    stronger24 = _rebuild_links(
        stronger24base,
        policy=policy,
        supersedes=[old24["registry_entry_id"]],
        resolves=[old24["registry_entry_id"]],
    )
    independent24 = _entry(
        policy=policy,
        seed="24-independent",
        source_class="OFF_POLICY_REPLAY",
        corpus="corpus-24-independent",
    )
    cases["24_explicit_stronger_conflict_resolution"] = _certificate_case(
        name="24_explicit_stronger_conflict_resolution",
        entries=[old24, stronger24, independent24],
        expected="SHADOW_CANARY_CANDIDATE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 25: exact duplicate registration is idempotent.
    e25 = _entry(policy=policy, seed="25")
    reg25 = _registry(
        authority=authority,
        policy=policy,
        registry_id="registry-25",
    )
    first25 = reg25.register(e25)
    second25 = reg25.register(e25)
    cases["25_exact_duplicate_idempotent"] = {
        "name": "25_exact_duplicate_idempotent",
        "scenario_only": True,
        "expected_disposition": "IDEMPOTENT_NO_OP",
        "actual_disposition": (
            "IDEMPOTENT_NO_OP"
            if first25 is True and second25 is False
            else "UNEXPECTED"
        ),
        "passed": first25 is True and second25 is False,
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
    }

    # 26: same immutable entry ID with changed bytes is rejected.
    tamper26 = copy.deepcopy(e25)
    tamper26["entry_digest"] = "0" * 64
    reg26 = _registry(
        authority=authority,
        policy=policy,
        registry_id="registry-26",
    )
    reg26.register(e25)
    cases["26_same_entry_id_changed_bytes"] = _exception_case(
        name="26_same_entry_id_changed_bytes",
        expected_exception="EntryRejected",
        fn=lambda: reg26.register(tamper26),
    )

    # 27/28 hard leakage rejection.
    cases["27_post_outcome_feature_leakage"] = _exception_case(
        name="27_post_outcome_feature_leakage",
        expected_exception="EntryRejected",
        fn=lambda: _entry(
            policy=policy,
            seed="27",
            post_outcome=True,
        ),
    )
    cases["28_future_feature_leakage"] = _exception_case(
        name="28_future_feature_leakage",
        expected_exception="EntryRejected",
        fn=lambda: _entry(
            policy=policy,
            seed="28",
            future=True,
        ),
    )

    # 29: same core evidence under a different provenance class is a conflict.
    class29a = _entry(
        policy=policy,
        seed="29",
        source_class="OFF_POLICY_REPLAY",
        corpus="corpus-29",
        run_id=2901,
        artifact_id=3901,
        artifact_digest="sha256:" + _hex("29-a"),
    )
    class29b = _entry(
        policy=policy,
        seed="29",
        source_class="RANDOMIZED",
        corpus="corpus-29",
        run_id=2902,
        artifact_id=3902,
        artifact_digest="sha256:" + _hex("29-b"),
    )
    cases["29_metadata_cannot_upgrade_provenance"] = _certificate_case(
        name="29_metadata_cannot_upgrade_provenance",
        entries=[class29a, class29b],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 30: nonrandom evidence cannot assert causal claim.
    cases["30_nonrandom_causal_claim_rejected"] = _exception_case(
        name="30_nonrandom_causal_claim_rejected",
        expected_exception="EntryRejected",
        fn=lambda: _entry(
            policy=policy,
            seed="30",
            source_class="OFF_POLICY_REPLAY",
            causal_claim_allowed=True,
        ),
    )

    # 31-35 quality/maturity exclusions.
    cases["31_low_ess_positive_not_ready"] = _certificate_case(
        name="31_low_ess_positive_not_ready",
        entries=[_entry(policy=policy, seed="31", ess=40.0)],
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cases["32_low_overlap_positive_not_ready"] = _certificate_case(
        name="32_low_overlap_positive_not_ready",
        entries=[
            _entry(
                policy=policy,
                seed="32",
                min_propensity=0.02,
            )
        ],
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cases["33_unsupported_region_positive_not_ready"] = _certificate_case(
        name="33_unsupported_region_positive_not_ready",
        entries=[
            _entry(
                policy=policy,
                seed="33",
                unsupported_regions=1,
            )
        ],
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cases["34_delayed_evidence_not_ready"] = _certificate_case(
        name="34_delayed_evidence_not_ready",
        entries=[
            _entry(
                policy=policy,
                seed="34",
                maturity_state="DELAYED",
            )
        ],
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cases["35_censored_evidence_not_ready"] = _certificate_case(
        name="35_censored_evidence_not_ready",
        entries=[
            _entry(
                policy=policy,
                seed="35",
                maturity_state="CENSORED",
            )
        ],
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 36: mixed nonnegative eligible evidence asks for more evidence.
    cases["36_candidate_plus_keep_test_more"] = _certificate_case(
        name="36_candidate_plus_keep_test_more",
        entries=[
            _entry(policy=policy, seed="36-a", corpus="corpus-36-a"),
            _entry(
                policy=policy,
                seed="36-b",
                corpus="corpus-36-b",
                recommendation="KEEP_BASELINE",
                lower=-0.003,
                point=0.0,
                upper=0.003,
            ),
        ],
        expected="TEST_MORE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 37/38 active negative evidence.
    cases["37_active_rollback_evidence"] = _certificate_case(
        name="37_active_rollback_evidence",
        entries=[
            _entry(
                policy=policy,
                seed="37",
                recommendation="SHADOW_ROLLBACK",
                lower=-0.08,
                point=-0.06,
                upper=-0.04,
            )
        ],
        expected="SHADOW_ROLLBACK",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cases["38_active_human_review_evidence"] = _certificate_case(
        name="38_active_human_review_evidence",
        entries=[
            _entry(
                policy=policy,
                seed="38",
                recommendation="HUMAN_REVIEW_REQUIRED",
                lower=-0.03,
                point=0.01,
                upper=0.05,
            )
        ],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 39/40 critical vetoes persist independently.
    cases["39_critical_stratum_reversal_veto"] = _certificate_case(
        name="39_critical_stratum_reversal_veto",
        entries=[
            _entry(
                policy=policy,
                seed="39",
                stratum_reversals=["platform:tiktok"],
            )
        ],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cases["40_critical_guardrail_veto"] = _certificate_case(
        name="40_critical_guardrail_veto",
        entries=[
            _entry(
                policy=policy,
                seed="40",
                guardrail_failures=["completion_guardrail"],
            )
        ],
        expected="HUMAN_REVIEW_REQUIRED",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 41: two independent randomized evidence entries are eligible.
    cases["41_two_randomized_entries_candidate"] = _certificate_case(
        name="41_two_randomized_entries_candidate",
        entries=[
            _entry(
                policy=policy,
                seed="41-a",
                source_class="RANDOMIZED",
                corpus="corpus-41-a",
            ),
            _entry(
                policy=policy,
                seed="41-b",
                source_class="RANDOMIZED",
                corpus="corpus-41-b",
            ),
        ],
        expected="SHADOW_CANARY_CANDIDATE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 42: copied artifact rewrapped under different artifact metadata counts once.
    wrap42a = _entry(
        policy=policy,
        seed="42",
        corpus="corpus-42",
        run_id=4201,
        artifact_id=5201,
        artifact_digest="sha256:" + _hex("42-wrap-a"),
    )
    wrap42b = _entry(
        policy=policy,
        seed="42",
        corpus="corpus-42",
        run_id=4202,
        artifact_id=5202,
        artifact_digest="sha256:" + _hex("42-wrap-b"),
    )
    cases["42_copied_artifact_different_metadata_counted_once"] = _certificate_case(
        name="42_copied_artifact_different_metadata_counted_once",
        entries=[wrap42a, wrap42b],
        expected="TEST_MORE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 43: evidence for another policy does not contaminate target policy.
    other_digest = _hex("other-policy")
    cases["43_other_policy_evidence_isolated"] = _certificate_case(
        name="43_other_policy_evidence_isolated",
        entries=[
            _entry(
                policy=policy,
                seed="43-target-a",
                corpus="corpus-43-a",
            ),
            _entry(
                policy=policy,
                seed="43-target-b",
                corpus="corpus-43-b",
            ),
            _entry(
                policy=policy,
                seed="43-other",
                policy_digest_value=other_digest,
                policy_id="other-policy",
                corpus="corpus-43-other",
                recommendation="SHADOW_ROLLBACK",
                lower=-0.08,
                point=-0.06,
                upper=-0.04,
            ),
        ],
        expected="SHADOW_CANARY_CANDIDATE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 44: no evidence for a target policy is not ready.
    cases["44_no_evidence_for_policy"] = _certificate_case(
        name="44_no_evidence_for_policy",
        entries=[
            _entry(
                policy=policy,
                seed="44-other",
                policy_digest_value=other_digest,
                policy_id="other-policy",
            )
        ],
        target_policy_digest=TARGET_POLICY_DIGEST,
        expected="NOT_READY",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )

    # 45: expired negative history stays visible but does not poison fresh independent support.
    expired45 = _entry(
        policy=policy,
        seed="45-old",
        source_class="OBSERVATIONAL",
        corpus="corpus-45-old",
        recommendation="SHADOW_ROLLBACK",
        lower=-0.08,
        point=-0.06,
        upper=-0.04,
        created_at="2026-08-01T00:00:00Z",
        mature_at="2026-08-01T00:00:00Z",
    )
    case45 = _certificate_case(
        name="45_expired_negative_history_visible",
        entries=[
            expired45,
            _entry(policy=policy, seed="45-a", corpus="corpus-45-a"),
            _entry(policy=policy, seed="45-b", corpus="corpus-45-b"),
        ],
        expected="SHADOW_CANARY_CANDIDATE",
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    case45["expired_negative_visible"] = (
        expired45["registry_entry_id"]
        in case45["negative_history_entry_ids"]
    )
    case45["passed"] = case45["passed"] and case45["expired_negative_visible"]
    cases["45_expired_negative_history_visible"] = case45

    # Final source-ready artifact intentionally uses only synthetic evidence.
    artifact_entry_a = _entry(
        policy=policy,
        seed="artifact-a",
        source_class="SYNTHETIC_TEST",
        corpus="artifact-corpus-a",
    )
    artifact_entry_b = _entry(
        policy=policy,
        seed="artifact-b",
        source_class="SYNTHETIC_TEST",
        corpus="artifact-corpus-b",
    )
    artifact_registry = _registry(
        authority=authority,
        policy=policy,
        registry_id="growth-r35-source-ready-registry",
    )
    artifact_registry.register_many([artifact_entry_a, artifact_entry_b])
    registry_snapshot = artifact_registry.snapshot(
        snapshot_at=SNAPSHOT_AT,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    readiness_certificate = r35.build_certificate(
        snapshot=registry_snapshot,
        authority=authority,
        policy=policy,
        target_policy_digest=TARGET_POLICY_DIGEST,
        as_of=AS_OF,
    )
    if readiness_certificate["readiness"] != "NOT_READY":
        raise AssertionError(
            "synthetic source-ready artifact must never be canary-ready"
        )

    case_values = [cases[key] for key in sorted(cases)]
    all_expected = all(case["passed"] for case in case_values)
    if not all_expected:
        failed = [
            case["name"] for case in case_values if not case["passed"]
        ]
        raise AssertionError(f"R35 adversarial expectations failed: {failed}")

    adversarial_results = {
        "report_version": r35.REPORT_VERSION,
        "fixture_scenarios_only": True,
        "case_count": len(case_values),
        "all_expected_dispositions_stable": all_expected,
        "cases": {key: cases[key] for key in sorted(cases)},
        "provenance_upgrade_proof": {
            "scenario": "29_metadata_cannot_upgrade_provenance",
            "same_core_evidence_with_different_source_class_is_conflict": True,
            "synthetic_and_observational_readiness_eligible": False,
        },
        "advisory_only": True,
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
        "traffic_allocation_allowed": False,
        "publish_allowed": False,
        "credential_access_allowed": False,
        "human_ground_truth": False,
    }
    adversarial_results["report_digest"] = sha256_json(
        adversarial_results
    )

    manifest = r35.conformance_manifest(
        root=root,
        authority=authority,
        policy=policy,
    )
    readiness_report = {
        "report_version": r35.REPORT_VERSION,
        "status": r35.STATUS,
        "disposition": "ADVISORY_ONLY",
        "registry_snapshot_digest": registry_snapshot[
            "registry_snapshot_digest"
        ],
        "readiness_certificate_digest": readiness_certificate[
            "certificate_digest"
        ],
        "readiness": readiness_certificate["readiness"],
        "synthetic_source_ready_evidence_only": True,
        "adversarial_case_count": len(case_values),
        "all_expected_dispositions_stable": all_expected,
        "adversarial_report_digest": adversarial_results[
            "report_digest"
        ],
        "conformance_manifest_digest": manifest["manifest_digest"],
        "parent_authority": {
            "growth_r34": r35.r34_tuple(),
            "growth_r33": r35.r33_tuple(),
            "growth_r32": r35.r32_tuple(),
            "qa_r6": r35.qa_r6_tuple(),
        },
        "provenance_classes_not_interchangeable": True,
        "metadata_can_upgrade_provenance": False,
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
        "browser_mutation_allowed": False,
        "traffic_allocation_allowed": False,
        "publish_allowed": False,
        "credential_access_allowed": False,
        "human_ground_truth": False,
    }
    readiness_report["report_digest"] = sha256_json(readiness_report)

    return {
        "registry_snapshot": registry_snapshot,
        "readiness_certificate": readiness_certificate,
        "adversarial_results": adversarial_results,
        "conformance_manifest": manifest,
        "readiness_report": readiness_report,
    }
