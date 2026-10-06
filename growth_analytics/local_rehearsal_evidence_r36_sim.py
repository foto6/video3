from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from .autonomous_reels import sha256_json
from . import local_rehearsal_evidence_r36 as r36
from . import local_rehearsal_verifier_r36 as verifier
from . import consensus_review_oracle_r30 as r30


def _synthetic_accepted_authority(authority: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(authority))
    value["independent_parent_qa"] = {
        "disposition": "ACCEPTED",
        "repository": "foto6/boss",
        "accepted_parent_sha": r36.R35_SHA,
        "accepted_parent_ci_run_id": r36.R35_CI,
        "accepted_parent_artifact_id": r36.R35_ARTIFACT_ID,
        "accepted_parent_artifact_digest": r36.R35_ARTIFACT_DIGEST,
        "producer_sha": "a" * 40,
        "ci_run_id": 999001,
        "artifact_id": 999002,
        "artifact_digest": "sha256:" + "b" * 64,
        "matrix_digest": "c" * 64,
    }
    return value


def _case(
    name: str,
    expected: str,
    fn: Callable[[], Any],
) -> dict[str, Any]:
    try:
        value = fn()
    except Exception as exc:
        actual = f"REJECTED:{type(exc).__name__}"
        return {
            "name": name,
            "expected": expected,
            "actual": actual,
            "passed": actual == expected,
            "detail": str(exc),
            "live_authorization": False,
            "provider_mutation": False,
        }
    if isinstance(value, Mapping) and "status" in value:
        actual = value["status"]
    elif isinstance(value, Mapping) and "verified" in value:
        actual = "VERIFIED" if value["verified"] else "NOT_VERIFIED"
    else:
        actual = str(value)
    return {
        "name": name,
        "expected": expected,
        "actual": actual,
        "passed": actual == expected,
        "result_digest": value.get("result_digest") if isinstance(value, Mapping) else None,
        "live_authorization": False,
        "provider_mutation": False,
    }


def build_rehearsal(
    *,
    media_dir: Path,
    reviews_dir: Path,
    bundle_manifest: Mapping[str, Any],
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    r30_profile: Mapping[str, Any],
    r30_policy: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = r36.validate_authority(authority)
    policy = r36.validate_policy(policy)
    bundle = r36.validate_bundle_manifest(bundle_manifest)
    cases: dict[str, Any] = {}

    def run(
        *,
        bundle_value: Mapping[str, Any] | None = None,
        authority_value: Mapping[str, Any] | None = None,
        profile_value: Mapping[str, Any] | None = None,
        r30_policy_value: Mapping[str, Any] | None = None,
        media_value: Path | None = None,
        reviews_value: Path | None = None,
    ) -> dict[str, Any]:
        return r36.run_local_rehearsal(
            media_dir=media_dir if media_value is None else media_value,
            reviews_dir=reviews_dir if reviews_value is None else reviews_value,
            bundle_manifest=bundle if bundle_value is None else bundle_value,
            authority=authority if authority_value is None else authority_value,
            policy=policy,
            r30_profile=r30_profile if profile_value is None else profile_value,
            r30_policy=r30_policy if r30_policy_value is None else r30_policy_value,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        )

    # Parent-gate behavior.
    cases["01_pending_parent_waits"] = _case(
        "01_pending_parent_waits", r36.WAITING_PARENT_QA, lambda: run()
    )
    accepted = _synthetic_accepted_authority(authority)
    accepted_result = run(authority_value=accepted)
    cases["02_synthetic_accepted_parent_ready"] = {
        "name": "02_synthetic_accepted_parent_ready",
        "expected": r36.READY,
        "actual": accepted_result["status"],
        "passed": (
            accepted_result["status"] == r36.READY
            and accepted_result["decision"] is not None
            and accepted_result["canary_evidence_envelope"]["live_authorization"] is False
        ),
        "synthetic_parent_acceptance_fixture_only": True,
        "decision_digest": accepted_result["decision"]["decision_digest"],
        "live_authorization": False,
        "provider_mutation": False,
    }

    bad = copy.deepcopy(accepted)
    bad["independent_parent_qa"]["producer_sha"] = None
    cases["03_accepted_qa_missing_producer"] = _case(
        "03_accepted_qa_missing_producer",
        "REJECTED:R36Error",
        lambda: run(authority_value=bad),
    )

    bad = copy.deepcopy(accepted)
    bad["independent_parent_qa"]["accepted_parent_sha"] = "0" * 40
    cases["04_accepted_qa_wrong_parent"] = _case(
        "04_accepted_qa_wrong_parent",
        "REJECTED:AuthorityDrift",
        lambda: run(authority_value=bad),
    )

    bad = copy.deepcopy(authority)
    bad["independent_parent_qa"]["producer_sha"] = "a" * 40
    cases["05_pending_qa_with_acceptance_pins"] = _case(
        "05_pending_qa_with_acceptance_pins",
        "REJECTED:AuthorityDrift",
        lambda: run(authority_value=bad),
    )

    bad = copy.deepcopy(authority)
    bad["growth_r35_parent"]["producer_sha"] = "0" * 40
    cases["06_r35_parent_sha_drift"] = _case(
        "06_r35_parent_sha_drift",
        "REJECTED:AuthorityDrift",
        lambda: run(authority_value=bad),
    )

    bad = copy.deepcopy(authority)
    bad["growth_r35_parent"]["artifact_digest"] = "sha256:" + "0" * 64
    cases["07_r35_parent_artifact_drift"] = _case(
        "07_r35_parent_artifact_drift",
        "REJECTED:AuthorityDrift",
        lambda: run(authority_value=bad),
    )

    bad = copy.deepcopy(authority)
    bad["growth_r34_ancestry"]["producer_sha"] = "0" * 40
    cases["08_r34_ancestry_drift"] = _case(
        "08_r34_ancestry_drift",
        "REJECTED:AuthorityDrift",
        lambda: run(authority_value=bad),
    )

    bad = copy.deepcopy(authority)
    bad["local_review_semantics"]["growth_r30_policy_blob"] = "0" * 40
    cases["09_r30_semantic_blob_drift"] = _case(
        "09_r30_semantic_blob_drift",
        "REJECTED:AuthorityDrift",
        lambda: run(authority_value=bad),
    )

    bad = copy.deepcopy(authority)
    bad["local_review_semantics"]["media_r24"]["artifact_digest"] = (
        "sha256:" + "0" * 64
    )
    cases["10_media_authority_drift"] = _case(
        "10_media_authority_drift",
        "REJECTED:AuthorityDrift",
        lambda: run(authority_value=bad),
    )

    # Bundle identity drift.
    def bundle_mutation(path, value):
        b = copy.deepcopy(bundle)
        target = b
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        return b

    cases["11_bundle_media_producer_drift"] = _case(
        "11_bundle_media_producer_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bundle_mutation(
            ["media", "producer_sha"], "0" * 40
        )),
    )
    cases["12_bundle_session_drift"] = _case(
        "12_bundle_session_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bundle_mutation(
            ["media", "session_id"], "wrong-session"
        )),
    )
    cases["13_bundle_round_drift"] = _case(
        "13_bundle_round_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bundle_mutation(["media", "review_round"], 1)),
    )
    cases["14_bundle_package_drift"] = _case(
        "14_bundle_package_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bundle_mutation(
            ["media", "package_digest"], "0" * 64
        )),
    )
    cases["15_bundle_prompt_drift"] = _case(
        "15_bundle_prompt_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bundle_mutation(
            ["media", "prompt_digest"], "0" * 64
        )),
    )
    cases["16_bundle_mapping_drift"] = _case(
        "16_bundle_mapping_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bundle_mutation(
            ["media", "sealed_mapping_digest"], "0" * 64
        )),
    )
    cases["17_bundle_source_hash_drift"] = _case(
        "17_bundle_source_hash_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bundle_mutation(
            ["media", "source", "source_sha256"], "0" * 64
        )),
    )

    bad = copy.deepcopy(bundle)
    bad["media"]["candidates"][0]["candidate_id"] = "wrong-candidate"
    cases["18_candidate_id_drift"] = _case(
        "18_candidate_id_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bad),
    )

    bad = copy.deepcopy(bundle)
    bad["media"]["candidates"][0]["render_sha256"] = "0" * 64
    cases["19_candidate_render_hash_drift"] = _case(
        "19_candidate_render_hash_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bad),
    )

    bad = copy.deepcopy(bundle)
    bad["media"]["candidates"][1]["attachment_sha256"] = "0" * 64
    cases["20_candidate_attachment_hash_drift"] = _case(
        "20_candidate_attachment_hash_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bad),
    )

    bad = copy.deepcopy(bundle)
    bad["reviews"] = bad["reviews"][:2]
    cases["21_review_count_wrong"] = _case(
        "21_review_count_wrong",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bad),
    )

    bad = copy.deepcopy(bundle)
    bad["reviews"][1]["path"] = bad["reviews"][0]["path"]
    cases["22_duplicate_review_path"] = _case(
        "22_duplicate_review_path",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bad),
    )

    bad = copy.deepcopy(bundle)
    bad["reviews"][0]["git_blob_sha1"] = "0" * 40
    cases["23_review_manifest_blob_drift"] = _case(
        "23_review_manifest_blob_drift",
        "REJECTED:BundleDrift",
        lambda: run(bundle_value=bad),
    )

    with tempfile.TemporaryDirectory() as tmp:
        copied = Path(tmp) / "reviews"
        shutil.copytree(reviews_dir, copied)
        p = copied / "review-a.json"
        p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        cases["24_review_manifest_bytes_tamper"] = _case(
            "24_review_manifest_bytes_tamper",
            "REJECTED:BundleDrift",
            lambda: run(reviews_value=copied),
        )

    bad_profile = copy.deepcopy(r30_profile)
    bad_profile["media_r24"]["producer_sha"] = "0" * 40
    cases["25_r30_authority_profile_drift"] = _case(
        "25_r30_authority_profile_drift",
        "REJECTED:AuthorityDrift",
        lambda: run(profile_value=bad_profile),
    )

    bad_policy = copy.deepcopy(r30_policy)
    bad_policy["thresholds"]["high_confidence_min"] = 0.5
    cases["26_r30_consensus_policy_drift"] = _case(
        "26_r30_consensus_policy_drift",
        "REJECTED:PolicyDrift",
        lambda: run(r30_policy_value=bad_policy),
    )

    with tempfile.TemporaryDirectory() as tmp:
        copied = Path(tmp) / "media"
        shutil.copytree(media_dir, copied)
        target = copied / "payload" / "review-A.mp4"
        target.write_bytes(target.read_bytes() + b"x")
        cases["27_media_attachment_bytes_tamper"] = _case(
            "27_media_attachment_bytes_tamper",
            "REJECTED:PackageDrift",
            lambda: run(media_value=copied),
        )

    with tempfile.TemporaryDirectory() as tmp:
        copied = Path(tmp) / "media"
        shutil.copytree(media_dir, copied)
        target = (
            copied
            / "payload"
            / "machine"
            / "r21"
            / "media.review_round_sealed_mapping.r21.v1.json"
        )
        data = json.loads(target.read_text(encoding="utf-8"))
        data["entries"][0], data["entries"][1] = data["entries"][1], data["entries"][0]
        target.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        cases["28_sealed_mapping_bytes_tamper"] = _case(
            "28_sealed_mapping_bytes_tamper",
            "REJECTED:PackageDrift",
            lambda: run(media_value=copied),
        )

    reversed_bundle = copy.deepcopy(bundle)
    reversed_bundle["reviews"] = list(reversed(reversed_bundle["reviews"]))
    ordered = run()
    reversed_result = run(bundle_value=reversed_bundle)
    same = (
        ordered["result_digest"] == reversed_result["result_digest"]
        and ordered["normalization"]["normalization_digest"]
        == reversed_result["normalization"]["normalization_digest"]
    )
    cases["29_review_manifest_order_invariant"] = {
        "name": "29_review_manifest_order_invariant",
        "expected": "BYTE_STABLE",
        "actual": "BYTE_STABLE" if same else "DIFFERENT",
        "passed": same,
        "result_digest": ordered["result_digest"],
        "live_authorization": False,
        "provider_mutation": False,
    }

    # Independent verifier cases.
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        r36.write_outputs(ordered, out)
        verification = verifier.verify_output_dir(
            output_dir=out,
            authority=authority,
            bundle_manifest=bundle,
        )
        cases["30_independent_verifier_accepts_canonical_output"] = {
            "name": "30_independent_verifier_accepts_canonical_output",
            "expected": "VERIFIED",
            "actual": "VERIFIED" if verification["verified"] else "NOT_VERIFIED",
            "passed": verification["verified"] is True,
            "verification_digest": verification["verification_digest"],
            "live_authorization": False,
            "provider_mutation": False,
        }

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        r36.write_outputs(ordered, out)
        p = out / "review-normalization.json"
        value = json.loads(p.read_text(encoding="utf-8"))
        value["source"]["source_sha256"] = "0" * 64
        p.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        cases["31_verifier_detects_normalization_tamper"] = _case(
            "31_verifier_detects_normalization_tamper",
            "REJECTED:IndependentVerificationError",
            lambda: verifier.verify_output_dir(
                output_dir=out,
                authority=authority,
                bundle_manifest=bundle,
            ),
        )

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        r36.write_outputs(ordered, out)
        p = out / "canary-evidence-envelope.json"
        value = json.loads(p.read_text(encoding="utf-8"))
        value["live_authorization"] = True
        p.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        cases["32_verifier_detects_live_authorization_tamper"] = _case(
            "32_verifier_detects_live_authorization_tamper",
            "REJECTED:IndependentVerificationError",
            lambda: verifier.verify_output_dir(
                output_dir=out,
                authority=authority,
                bundle_manifest=bundle,
            ),
        )

    # Safe directive mapping is allowlisted and free-form text is audit-only.
    normalization = ordered["normalization"]
    directives = r36._safe_directives(
        normalization=normalization,
        policy=policy,
    )
    safe = all(
        row["operation"] in policy["directives"]["executable_operations"]
        and "free_form_requested_edit_audit_only" in row
        for row in directives
    )
    cases["33_directive_allowlist_and_audit_only_text"] = {
        "name": "33_directive_allowlist_and_audit_only_text",
        "expected": "SAFE",
        "actual": "SAFE" if safe else "UNSAFE",
        "passed": safe,
        "directive_count": len(directives),
        "live_authorization": False,
        "provider_mutation": False,
    }

    no_side_effect = all(
        ordered["boundary"][field] is False
        for field in (
            "network_call",
            "browser_call",
            "provider_call",
            "live_metrics",
            "live_action",
            "creator_mutation",
            "provider_mutation",
            "publish",
            "credential_access",
            "human_ground_truth",
        )
    )
    cases["34_no_network_provider_or_live_action"] = {
        "name": "34_no_network_provider_or_live_action",
        "expected": "NO_SIDE_EFFECT",
        "actual": "NO_SIDE_EFFECT" if no_side_effect else "VIOLATION",
        "passed": no_side_effect,
        "live_authorization": False,
        "provider_mutation": False,
    }

    # Fixture canary evidence must remain non-registry-submittable.
    env = ordered["canary_evidence_envelope"]
    fixture_safe = (
        env["source_class"] == "SYNTHETIC_TEST"
        and env["readiness_eligible"] is False
        and env["registry_submission_allowed"] is False
        and env["live_authorization"] is False
    )
    cases["35_fixture_canary_envelope_is_nonpromotable"] = {
        "name": "35_fixture_canary_envelope_is_nonpromotable",
        "expected": "NONPROMOTABLE",
        "actual": "NONPROMOTABLE" if fixture_safe else "PROMOTABLE",
        "passed": fixture_safe,
        "live_authorization": False,
        "provider_mutation": False,
    }

    values = [cases[key] for key in sorted(cases)]
    failures = [row["name"] for row in values if not row["passed"]]
    report = {
        "report_version": "growth.local_rehearsal_adversarial.r36.v1",
        "scenario_count": len(values),
        "all_expected_dispositions_stable": not failures,
        "failed_scenarios": failures,
        "cases": {key: cases[key] for key in sorted(cases)},
        "current_parent_qa_disposition": authority["independent_parent_qa"]["disposition"],
        "current_source_ready_state": ordered["status"],
        "synthetic_acceptance_fixture_only": True,
        "real_parent_r35_acceptance_claimed": False,
        "independent_verifier_present": True,
        "network_call": False,
        "browser_call": False,
        "provider_call": False,
        "live_metrics": False,
        "live_action": False,
        "creator_mutation": False,
        "provider_mutation": False,
        "publish": False,
        "credential_access": False,
        "human_ground_truth": False,
    }
    report["report_digest"] = sha256_json(report)
    if failures:
        raise AssertionError(f"R36 adversarial scenario failures: {failures}")
    return report
