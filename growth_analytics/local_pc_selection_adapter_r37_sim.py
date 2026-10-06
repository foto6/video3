from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from .autonomous_reels import sha256_json
from . import local_pc_selection_adapter_r37 as r37


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _ts(offset_seconds: int) -> str:
    value = datetime(2026, 10, 6, 8, 0, 0, tzinfo=timezone.utc) + timedelta(
        seconds=offset_seconds
    )
    return value.isoformat().replace("+00:00", "Z")


def _phase(
    phase_id: str,
    operation_id: str,
    start: int,
    end: int,
    *,
    resumed: bool = False,
    checkpoint_digest: str | None = None,
) -> dict[str, Any]:
    return {
        "phase_id": phase_id,
        "operation_id": operation_id,
        "started_at": _ts(start),
        "ended_at": _ts(end),
        "duration_ms": (end - start) * 1000,
        "resumed": resumed,
        "checkpoint_digest": checkpoint_digest,
    }


def _review(
    *,
    review_id: str,
    candidate_id: str,
    render_sha256: str,
    score: float,
    captured_at: str,
    evidence_class: str,
) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "candidate_id": candidate_id,
        "evidence_class": evidence_class,
        "score": score,
        "evidence_digest": _hex(f"review:{review_id}:{score}:{evidence_class}"),
        "review_policy_contract": r37.POLICY_VERSION,
        "review_policy_version": 1,
        "captured_at": captured_at,
        "source_render_sha256": render_sha256,
        "human_ground_truth": False,
    }


def _seal(bundle: dict[str, Any]) -> dict[str, Any]:
    phases = sorted(bundle["phase_timings"], key=lambda row: row["phase_id"])
    resumes = sorted(bundle["resume_events"], key=lambda row: row["resume_event_id"])
    candidates = sorted(bundle["candidates"], key=lambda row: row["candidate_id"])
    review_material = {
        "candidates": {
            row["candidate_id"]: sorted(
                row["reviews"],
                key=lambda review: review["review_id"],
            )
            for row in candidates
        },
        "targeted_reedit": sorted(
            bundle["targeted_reedit"]["reviews"],
            key=lambda review: review["review_id"],
        ),
    }
    manifest = {
        "manifest_version": "media.local_render_runner.r26.local_run_manifest.v1",
        "input_video": copy.deepcopy(bundle["input_video"]),
        "runtime_identity_digest": sha256_json(bundle["runtime"]),
        "candidate_hashes": {
            row["candidate_id"]: row["render_sha256"] for row in candidates
        },
        "review_evidence_digest": sha256_json(review_material),
        "targeted_reedit_sha256": bundle["targeted_reedit"]["artifact_sha256"],
        "final_artifact_sha256": bundle["final_artifact"]["sha256"],
        "phase_timings_digest": sha256_json(phases),
        "resume_events_digest": sha256_json(resumes),
    }
    bundle["local_run_manifest"] = manifest
    bundle["local_run_manifest_digest"] = sha256_json(manifest)
    return bundle


def build_bundle(
    *,
    evidence_class: str = "OFFLINE_MODEL",
    baseline_scores: tuple[float, float] = (0.60, 0.62),
    candidate_scores: tuple[float, float] = (0.80, 0.82),
    reedit_scores: tuple[float, float] = (0.87, 0.88),
    final_lineage: str = "TARGETED_REEDIT",
) -> dict[str, Any]:
    baseline_hash = _hex("r37-baseline-render")
    candidate_hash = _hex("r37-challenger-render")
    reedit_hash = _hex("r37-targeted-reedit-render")
    checkpoint = _hex("r37-resume-checkpoint")

    phases = [
        _phase("phase-baseline", "op-baseline", 10, 20),
        _phase(
            "phase-candidate",
            "op-candidate",
            21,
            35,
            resumed=True,
            checkpoint_digest=checkpoint,
        ),
        _phase("phase-reedit", "op-reedit", 36, 47),
        _phase("phase-final", "op-final", 48, 52),
    ]
    resume_material = {
        "resume_event_id": "resume-candidate-1",
        "phase_id": "phase-candidate",
        "operation_id": "op-candidate",
        "checkpoint_digest": checkpoint,
        "resumed_at": _ts(28),
    }
    resume_event = {
        **resume_material,
        "event_digest": sha256_json(resume_material),
    }

    candidates = [
        {
            "candidate_id": "baseline",
            "role": "BASELINE",
            "operation_id": "op-baseline",
            "render_phase_id": "phase-baseline",
            "render_sha256": baseline_hash,
            "render_size": 101001,
            "reviews": [
                _review(
                    review_id="review-baseline-1",
                    candidate_id="baseline",
                    render_sha256=baseline_hash,
                    score=baseline_scores[0],
                    captured_at=_ts(53),
                    evidence_class=evidence_class,
                ),
                _review(
                    review_id="review-baseline-2",
                    candidate_id="baseline",
                    render_sha256=baseline_hash,
                    score=baseline_scores[1],
                    captured_at=_ts(54),
                    evidence_class=evidence_class,
                ),
            ],
        },
        {
            "candidate_id": "candidate-a",
            "role": "CANDIDATE",
            "operation_id": "op-candidate",
            "render_phase_id": "phase-candidate",
            "render_sha256": candidate_hash,
            "render_size": 102002,
            "reviews": [
                _review(
                    review_id="review-candidate-1",
                    candidate_id="candidate-a",
                    render_sha256=candidate_hash,
                    score=candidate_scores[0],
                    captured_at=_ts(55),
                    evidence_class=evidence_class,
                ),
                _review(
                    review_id="review-candidate-2",
                    candidate_id="candidate-a",
                    render_sha256=candidate_hash,
                    score=candidate_scores[1],
                    captured_at=_ts(56),
                    evidence_class=evidence_class,
                ),
            ],
        },
    ]
    targeted = {
        "targeted_reedit_id": "targeted-reedit-a",
        "operation_id": "op-reedit",
        "render_phase_id": "phase-reedit",
        "derived_from_candidate_id": "candidate-a",
        "derived_from_render_sha256": candidate_hash,
        "artifact_sha256": reedit_hash,
        "artifact_size": 103003,
        "operation_graph_digest": _hex("r37-targeted-operation-graph"),
        "reviews": [
            _review(
                review_id="review-reedit-1",
                candidate_id="targeted-reedit-a",
                render_sha256=reedit_hash,
                score=reedit_scores[0],
                captured_at=_ts(57),
                evidence_class=evidence_class,
            ),
            _review(
                review_id="review-reedit-2",
                candidate_id="targeted-reedit-a",
                render_sha256=reedit_hash,
                score=reedit_scores[1],
                captured_at=_ts(58),
                evidence_class=evidence_class,
            ),
        ],
    }
    if final_lineage == "TARGETED_REEDIT":
        final_id = "targeted-reedit-a"
        final_hash = reedit_hash
        kind = "TARGETED_REEDIT"
        size = 103003
    elif final_lineage == "CANDIDATE":
        final_id = "candidate-a"
        final_hash = candidate_hash
        kind = "CANDIDATE"
        size = 102002
    elif final_lineage == "BASELINE":
        final_id = "baseline"
        final_hash = baseline_hash
        kind = "CANDIDATE"
        size = 101001
    else:
        raise ValueError(final_lineage)

    bundle = {
        "contract_version": r37.INPUT_VERSION,
        "media_contract_id": r37.MEDIA_CONTRACT_ID,
        "bundle_id": f"r37-fixture-{evidence_class.lower()}",
        "media_claim": {
            "repository": "foto6/video2",
            "producer_sha": "1" * 40,
            "ci_run_id": 999101,
            "ci_conclusion": "SUCCESS",
            "artifact": {
                "availability": "AVAILABLE",
                "id": 999102,
                "name": "media-r26-local-fixture",
                "digest": "sha256:" + _hex("r37-media-artifact"),
            },
        },
        "execution_context": {
            "mode": "LOCAL_WINDOWS",
            "evidence_origin": "LOCAL_PC",
            "hosted_ci": False,
            "local_run": True,
            "provider_network_used": False,
            "browser_used": False,
            "machine_pseudonym": "fixture-windows-host",
            "started_at": _ts(0),
            "completed_at": _ts(60),
        },
        "local_run_manifest": {},
        "local_run_manifest_digest": "0" * 64,
        "input_video": {
            "sha256": _hex("r37-input-video"),
            "size": 777001,
        },
        "runtime": {
            "ffmpeg": {
                "sha256": _hex("r37-ffmpeg-binary"),
                "version_digest": _hex("r37-ffmpeg-version"),
            },
            "ffprobe": {
                "sha256": _hex("r37-ffprobe-binary"),
                "version_digest": _hex("r37-ffprobe-version"),
            },
        },
        "candidates": candidates,
        "targeted_reedit": targeted,
        "final_artifact": {
            "operation_id": "op-final",
            "render_phase_id": "phase-final",
            "sha256": final_hash,
            "size": size,
            "lineage_kind": kind,
            "lineage_id": final_id,
            "lineage_sha256": final_hash,
        },
        "phase_timings": phases,
        "resume_events": [resume_event],
        "boundary": {
            "local_pc_evidence": True,
            "hosted_ci_evidence": False,
            "network_call": False,
            "browser_call": False,
            "provider_call": False,
            "live_metrics": False,
            "human_ground_truth": False,
            "provider_mutation": False,
            "creator_mutation": False,
            "social_publish": False,
            "credential_access": False,
        },
    }
    return _seal(bundle)


def accepted_authority(
    base: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    value = copy.deepcopy(dict(base))
    claim = bundle["media_claim"]
    value["media_r26_authority"] = {
        "state": "ACCEPTED",
        "required_contract_id": r37.MEDIA_CONTRACT_ID,
        "repository": "foto6/video2",
        "producer_sha": claim["producer_sha"],
        "ci_run_id": claim["ci_run_id"],
        "ci_conclusion": claim["ci_conclusion"],
        "artifact": copy.deepcopy(claim["artifact"]),
        "independent_qa": {
            "disposition": "ACCEPTED",
            "producer_sha": "2" * 40,
            "ci_run_id": 999201,
            "artifact_id": 999202,
            "artifact_digest": "sha256:" + _hex("r37-qa-artifact"),
            "matrix_digest": _hex("r37-qa-matrix"),
        },
    }
    return value


def _evaluate(
    bundle: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    return r37.evaluate(
        bundle=bundle,
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )


def _case(
    name: str,
    expected: str,
    fn: Callable[[], Mapping[str, Any]],
) -> dict[str, Any]:
    try:
        result = fn()
        actual = result["status"]
        return {
            "name": name,
            "expected": expected,
            "actual": actual,
            "passed": actual == expected,
            "result_digest": result.get("result_digest"),
            "reason_codes": (
                result["decision"]["reason_codes"]
                if result.get("decision") is not None
                else result.get("invalid_evidence", {})
            ),
            "live_authorization": result["creator_advisory"][
                "live_authorization"
            ],
            "provider_mutation_allowed": result["creator_advisory"][
                "provider_mutation_allowed"
            ],
        }
    except Exception as exc:
        actual = f"REJECTED:{type(exc).__name__}"
        return {
            "name": name,
            "expected": expected,
            "actual": actual,
            "passed": actual == expected,
            "detail": str(exc),
            "live_authorization": False,
            "provider_mutation_allowed": False,
        }


def build_rehearsal(
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = r37.validate_authority(authority)
    policy = r37.validate_policy(policy)
    cases: dict[str, Any] = {}

    source_ready_bundle = build_bundle(evidence_class="FIXTURE")
    source_ready_result = _evaluate(
        source_ready_bundle,
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    cases["01_unresolved_authority_waits"] = {
        "name": "01_unresolved_authority_waits",
        "expected": r37.WAITING,
        "actual": source_ready_result["status"],
        "passed": source_ready_result["status"] == r37.WAITING,
        "result_digest": source_ready_result["result_digest"],
        "live_authorization": False,
        "provider_mutation_allowed": False,
    }

    base = build_bundle()
    accepted = accepted_authority(authority, base)
    cases["02_targeted_reedit_selected"] = _case(
        "02_targeted_reedit_selected",
        r37.TARGETED_REEDIT,
        lambda: _evaluate(
            base,
            authority=accepted,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    baseline = build_bundle(
        baseline_scores=(0.86, 0.88),
        candidate_scores=(0.72, 0.74),
        reedit_scores=(0.75, 0.76),
        final_lineage="BASELINE",
    )
    cases["03_keep_baseline_when_best"] = _case(
        "03_keep_baseline_when_best",
        r37.KEEP_BASELINE,
        lambda: _evaluate(
            baseline,
            authority=accepted_authority(authority, baseline),
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    select = build_bundle(
        candidate_scores=(0.84, 0.85),
        reedit_scores=(0.85, 0.86),
        final_lineage="CANDIDATE",
    )
    cases["04_select_candidate_without_reedit_margin"] = _case(
        "04_select_candidate_without_reedit_margin",
        r37.SELECT_CANDIDATE,
        lambda: _evaluate(
            select,
            authority=accepted_authority(authority, select),
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    tie = build_bundle(
        baseline_scores=(0.80, 0.81),
        candidate_scores=(0.81, 0.80),
        reedit_scores=(0.81, 0.82),
        final_lineage="BASELINE",
    )
    cases["05_tie_keeps_baseline"] = _case(
        "05_tie_keeps_baseline",
        r37.KEEP_BASELINE,
        lambda: _evaluate(
            tie,
            authority=accepted_authority(authority, tie),
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    conflicting = build_bundle(final_lineage="CANDIDATE")
    conflicting["candidates"][1]["reviews"][0]["score"] = 0.50
    conflicting["candidates"][1]["reviews"][0]["evidence_digest"] = _hex(
        "conflicting-score-1"
    )
    conflicting["candidates"][1]["reviews"][1]["score"] = 0.90
    conflicting["candidates"][1]["reviews"][1]["evidence_digest"] = _hex(
        "conflicting-score-2"
    )
    _seal(conflicting)
    cases["06_conflicting_scores_require_human_review"] = _case(
        "06_conflicting_scores_require_human_review",
        r37.HUMAN_REVIEW,
        lambda: _evaluate(
            conflicting,
            authority=accepted_authority(authority, conflicting),
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    missing_review = build_bundle(final_lineage="CANDIDATE")
    missing_review["candidates"][1]["reviews"] = missing_review["candidates"][1][
        "reviews"
    ][:1]
    _seal(missing_review)
    cases["07_missing_review_evidence_requires_human"] = _case(
        "07_missing_review_evidence_requires_human",
        r37.HUMAN_REVIEW,
        lambda: _evaluate(
            missing_review,
            authority=accepted_authority(authority, missing_review),
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    partial = build_bundle()
    partial["candidates"] = partial["candidates"][:1]
    _seal(partial)
    cases["08_partial_candidate_set_invalid"] = _case(
        "08_partial_candidate_set_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            partial,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    duplicate_id = build_bundle()
    duplicate_id["candidates"][1]["candidate_id"] = "baseline"
    _seal(duplicate_id)
    cases["09_duplicate_candidate_identity_invalid"] = _case(
        "09_duplicate_candidate_identity_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            duplicate_id,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    duplicate_bytes = build_bundle()
    duplicate_bytes["candidates"][1]["render_sha256"] = duplicate_bytes["candidates"][0][
        "render_sha256"
    ]
    for review in duplicate_bytes["candidates"][1]["reviews"]:
        review["source_render_sha256"] = duplicate_bytes["candidates"][1][
            "render_sha256"
        ]
    duplicate_bytes["targeted_reedit"]["derived_from_render_sha256"] = duplicate_bytes[
        "candidates"
    ][1]["render_sha256"]
    _seal(duplicate_bytes)
    cases["10_duplicate_candidate_bytes_invalid"] = _case(
        "10_duplicate_candidate_bytes_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            duplicate_bytes,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    input_mismatch = build_bundle()
    input_mismatch["input_video"]["sha256"] = _hex("changed-input")
    cases["11_input_hash_mismatch_invalid"] = _case(
        "11_input_hash_mismatch_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            input_mismatch,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    runtime_mismatch = build_bundle()
    runtime_mismatch["runtime"]["ffmpeg"]["sha256"] = _hex("wrong-ffmpeg")
    cases["12_runtime_hash_mismatch_invalid"] = _case(
        "12_runtime_hash_mismatch_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            runtime_mismatch,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    resume_missing = build_bundle()
    resume_missing["resume_events"] = []
    _seal(resume_missing)
    cases["13_resumed_phase_missing_checkpoint_invalid"] = _case(
        "13_resumed_phase_missing_checkpoint_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            resume_missing,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    resume_bad = build_bundle()
    resume_bad["resume_events"][0]["checkpoint_digest"] = _hex("wrong-checkpoint")
    material = copy.deepcopy(resume_bad["resume_events"][0])
    material.pop("event_digest")
    resume_bad["resume_events"][0]["event_digest"] = sha256_json(material)
    _seal(resume_bad)
    cases["14_resume_checkpoint_mismatch_invalid"] = _case(
        "14_resume_checkpoint_mismatch_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            resume_bad,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    manifest_tamper = build_bundle()
    manifest_tamper["local_run_manifest"]["final_artifact_sha256"] = _hex(
        "tampered-final"
    )
    cases["15_final_manifest_tamper_invalid"] = _case(
        "15_final_manifest_tamper_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            manifest_tamper,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    authority_drift = build_bundle()
    wrong_authority = accepted_authority(authority, authority_drift)
    wrong_authority["media_r26_authority"]["producer_sha"] = "9" * 40
    cases["16_media_authority_drift_invalid"] = _case(
        "16_media_authority_drift_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            authority_drift,
            authority=wrong_authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    hosted = build_bundle()
    hosted["execution_context"]["hosted_ci"] = True
    cases["17_local_evidence_pretending_hosted_ci_invalid"] = _case(
        "17_local_evidence_pretending_hosted_ci_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            hosted,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    stale = build_bundle()
    stale["candidates"][0]["reviews"][0]["review_policy_version"] = 0
    _seal(stale)
    cases["18_stale_review_policy_invalid"] = _case(
        "18_stale_review_policy_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            stale,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    wrong_final = build_bundle(final_lineage="CANDIDATE")
    wrong_final["final_artifact"]["lineage_id"] = "baseline"
    cases["19_wrong_final_lineage_invalid"] = _case(
        "19_wrong_final_lineage_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            wrong_final,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    wrong_reedit_source = build_bundle()
    wrong_reedit_source["targeted_reedit"]["derived_from_candidate_id"] = "baseline"
    wrong_reedit_source["targeted_reedit"]["derived_from_render_sha256"] = (
        wrong_reedit_source["candidates"][0]["render_sha256"]
    )
    _seal(wrong_reedit_source)
    cases["20_targeted_reedit_wrong_selected_source_invalid"] = _case(
        "20_targeted_reedit_wrong_selected_source_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            wrong_reedit_source,
            authority=accepted_authority(authority, wrong_reedit_source),
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    duplicate_op = build_bundle()
    duplicate_op["targeted_reedit"]["operation_id"] = "op-candidate"
    cases["21_duplicate_operation_ids_invalid"] = _case(
        "21_duplicate_operation_ids_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            duplicate_op,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    target_source_hash = build_bundle()
    target_source_hash["targeted_reedit"]["derived_from_render_sha256"] = _hex(
        "wrong-source-render"
    )
    cases["22_targeted_reedit_source_hash_invalid"] = _case(
        "22_targeted_reedit_source_hash_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            target_source_hash,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    bad_boundary = build_bundle()
    bad_boundary["boundary"]["provider_call"] = True
    cases["23_provider_boundary_tamper_invalid"] = _case(
        "23_provider_boundary_tamper_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            bad_boundary,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    final_hash = build_bundle()
    final_hash["final_artifact"]["sha256"] = _hex("wrong-final-bytes")
    cases["24_final_hash_mismatch_invalid"] = _case(
        "24_final_hash_mismatch_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            final_hash,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    duplicate_review = build_bundle()
    duplicate_review["candidates"][1]["reviews"][0]["review_id"] = (
        duplicate_review["candidates"][0]["reviews"][0]["review_id"]
    )
    _seal(duplicate_review)
    cases["25_duplicate_review_identity_invalid"] = _case(
        "25_duplicate_review_identity_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            duplicate_review,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    stale_review = build_bundle()
    stale_review["candidates"][1]["reviews"][0]["captured_at"] = _ts(30)
    _seal(stale_review)
    cases["26_stale_review_timestamp_invalid"] = _case(
        "26_stale_review_timestamp_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            stale_review,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    review_source = build_bundle()
    review_source["candidates"][1]["reviews"][0]["source_render_sha256"] = _hex(
        "wrong-review-source"
    )
    _seal(review_source)
    cases["27_review_source_hash_invalid"] = _case(
        "27_review_source_hash_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            review_source,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    artifact_drift = build_bundle()
    wrong_artifact_auth = accepted_authority(authority, artifact_drift)
    wrong_artifact_auth["media_r26_authority"]["artifact"]["id"] += 1
    cases["28_media_artifact_authority_drift_invalid"] = _case(
        "28_media_artifact_authority_drift_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            artifact_drift,
            authority=wrong_artifact_auth,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    fixtures = build_bundle(evidence_class="FIXTURE", final_lineage="CANDIDATE")
    cases["29_fixture_reviews_never_positive"] = _case(
        "29_fixture_reviews_never_positive",
        r37.HUMAN_REVIEW,
        lambda: _evaluate(
            fixtures,
            authority=accepted_authority(authority, fixtures),
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    genuine = build_bundle(evidence_class="GENUINE_REVIEW")
    cases["30_genuine_review_path_is_deterministic"] = _case(
        "30_genuine_review_path_is_deterministic",
        r37.TARGETED_REEDIT,
        lambda: _evaluate(
            genuine,
            authority=accepted_authority(authority, genuine),
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    order_a = build_bundle(evidence_class="FIXTURE")
    order_b = copy.deepcopy(order_a)
    order_b["candidates"] = list(reversed(order_b["candidates"]))
    order_b["phase_timings"] = list(reversed(order_b["phase_timings"]))
    order_b["resume_events"] = list(reversed(order_b["resume_events"]))
    result_a = _evaluate(
        order_a,
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    result_b = _evaluate(
        order_b,
        authority=authority,
        policy=policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    same_order = result_a["result_digest"] == result_b["result_digest"]
    cases["31_input_order_invariant"] = {
        "name": "31_input_order_invariant",
        "expected": "BYTE_STABLE",
        "actual": "BYTE_STABLE" if same_order else "DIFFERENT",
        "passed": same_order,
        "result_digest": result_a["result_digest"],
        "live_authorization": False,
        "provider_mutation_allowed": False,
    }

    duration_bad = build_bundle()
    duration_bad["phase_timings"][0]["duration_ms"] += 1
    _seal(duration_bad)
    cases["32_phase_duration_tamper_invalid"] = _case(
        "32_phase_duration_tamper_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            duration_bad,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    duplicate_resume = build_bundle()
    duplicate_resume["resume_events"].append(
        copy.deepcopy(duplicate_resume["resume_events"][0])
    )
    _seal(duplicate_resume)
    cases["33_duplicate_resume_event_invalid"] = _case(
        "33_duplicate_resume_event_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            duplicate_resume,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    bad_ci = build_bundle()
    bad_ci["media_claim"]["ci_conclusion"] = "FAILURE"
    cases["34_media_ci_not_success_invalid"] = _case(
        "34_media_ci_not_success_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            bad_ci,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    review_tamper = build_bundle()
    review_tamper["candidates"][1]["reviews"][0]["score"] = 0.01
    cases["35_review_score_tamper_without_manifest_reseal_invalid"] = _case(
        "35_review_score_tamper_without_manifest_reseal_invalid",
        r37.EVIDENCE_INVALID,
        lambda: _evaluate(
            review_tamper,
            authority=authority,
            policy=policy,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        ),
    )

    values = [cases[key] for key in sorted(cases)]
    failures = [case["name"] for case in values if not case["passed"]]
    if failures:
        raise AssertionError(f"R37 adversarial failures: {failures}")
    report = {
        "report_version": "growth.local_pc_selection_adapter.r37.rehearsal.v1",
        "scenario_count": len(values),
        "all_expected_dispositions_stable": True,
        "failed_scenarios": [],
        "cases": {key: cases[key] for key in sorted(cases)},
        "source_ready_bundle": source_ready_bundle,
        "source_ready_result": source_ready_result,
        "current_media_r26_authority": authority["media_r26_authority"],
        "media_r26_exact_authority_claimed": False,
        "source_ready_status": source_ready_result["status"],
        "r36_parent": r37._expected_r36_parent(),
        "network_call": False,
        "browser_call": False,
        "provider_call": False,
        "provider_mutation": False,
        "creator_mutation": False,
        "live_authorization": False,
        "social_publish": False,
        "credential_access": False,
        "human_ground_truth": False,
        "report_digest": "",
    }
    material = copy.deepcopy(report)
    material["report_digest"] = ""
    report["report_digest"] = sha256_json(material)
    return report
