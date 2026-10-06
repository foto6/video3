from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json

VERIFICATION_VERSION = "growth.local_rehearsal_verification.r36.v1"
NORMALIZATION_VERSION = "growth.local_review_normalization.r36.v1"
DECISION_VERSION = "growth.local_offline_decision.r36.v1"
CANARY_VERSION = "growth.local_canary_evidence_envelope.r36.v1"
REPORT_VERSION = "growth.local_rehearsal_evidence.r36.report.v1"

R35_SHA = "c97f987e6ae780489f2a5fef1883d5596843c64c"
R35_CI = 37247403389
R35_ARTIFACT_ID = 11319801779
R35_ARTIFACT_DIGEST = (
    "sha256:d6a01a548f05c14723a0740218ce39a8a6edee902ed66cfa716e3aa78000cf02"
)


class IndependentVerificationError(ValueError):
    pass


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _digest_with_blank(value: Mapping[str, Any], field: str) -> str:
    material = copy.deepcopy(dict(value))
    material[field] = ""
    return sha256_json(material)


def _authority_digest(authority: Mapping[str, Any]) -> str:
    return sha256_json(authority)


def _bundle_digest(bundle: Mapping[str, Any]) -> str:
    normalized = copy.deepcopy(dict(bundle))
    normalized["media"]["candidates"] = sorted(
        normalized["media"]["candidates"],
        key=lambda row: row["blind_label"],
    )
    normalized["reviews"] = sorted(
        normalized["reviews"],
        key=lambda row: row["path"],
    )
    return sha256_json(normalized)


def _expected_parent() -> dict[str, Any]:
    return {
        "repository": "foto6/video3",
        "producer_sha": R35_SHA,
        "ci_run_id": R35_CI,
        "artifact_id": R35_ARTIFACT_ID,
        "artifact_name": "growth-r35-canary-evidence-registry",
        "artifact_digest": R35_ARTIFACT_DIGEST,
        "contract": "growth.canary_evidence_registry.r35.v1",
        "authority_blob": "8f1dd5d33395c8c4e7d8fda6b2f75ee917c3d340",
        "policy_blob": "ff6c93e42d748c254eba659c518f262a1576dabf",
        "contract_blob": "b0e3a8bb4c0faf1d3081ef42d34955327533159b",
    }


def _verify_boundary(boundary: Mapping[str, Any]) -> None:
    forbidden_true = (
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
    for field in forbidden_true:
        if boundary.get(field) is not False:
            raise IndependentVerificationError(
                f"side-effect boundary violation: {field}"
            )


def verify_output_dir(
    *,
    output_dir: Path,
    authority: Mapping[str, Any],
    bundle_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(output_dir)
    result = _load(root / "result.json")
    normalization = _load(root / "review-normalization.json")
    preview = _load(root / "offline-decision-preview.json")
    canary = _load(root / "canary-evidence-envelope.json")
    readiness = _load(root / "readiness-report.json")
    decision_path = root / "offline-decision.json"
    decision = _load(decision_path) if decision_path.is_file() else None

    if normalization.get("contract_version") != NORMALIZATION_VERSION:
        raise IndependentVerificationError("normalization contract mismatch")
    if normalization.get("normalization_digest") != _digest_with_blank(
        normalization, "normalization_digest"
    ):
        raise IndependentVerificationError("normalization digest mismatch")
    if normalization.get("bundle_manifest_digest") != _bundle_digest(bundle_manifest):
        raise IndependentVerificationError("bundle manifest digest mismatch")
    if normalization.get("review_round") not in (0, 1, 2):
        raise IndependentVerificationError("review round invalid")
    candidates = normalization.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise IndependentVerificationError("exact two normalized candidates required")
    labels = {row.get("blind_label") for row in candidates}
    if labels != {"A", "B"}:
        raise IndependentVerificationError("normalized A/B labels invalid")
    source_hashes = {row.get("source_sha256") for row in candidates}
    if source_hashes != {normalization["source"]["source_sha256"]}:
        raise IndependentVerificationError("candidate/source hash binding mismatch")
    if any(
        row.get("round") != normalization["review_round"]
        for row in candidates
    ):
        raise IndependentVerificationError("candidate/review round binding mismatch")
    if any(
        row.get("render_sha256") != row.get("attachment_sha256")
        for row in candidates
    ):
        raise IndependentVerificationError("render/attachment byte binding mismatch")

    if canary.get("contract_version") != CANARY_VERSION:
        raise IndependentVerificationError("canary envelope contract mismatch")
    if canary.get("envelope_digest") != _digest_with_blank(canary, "envelope_digest"):
        raise IndependentVerificationError("canary envelope digest mismatch")
    if canary.get("normalization_digest") != normalization["normalization_digest"]:
        raise IndependentVerificationError("canary/normalization linkage mismatch")
    if canary.get("bundle_manifest_digest") != normalization["bundle_manifest_digest"]:
        raise IndependentVerificationError("canary/bundle linkage mismatch")
    if canary.get("parent_r35") != _expected_parent():
        raise IndependentVerificationError("canary exact parent R35 tuple drift")
    if canary.get("authority_manifest_digest") != _authority_digest(authority):
        raise IndependentVerificationError("canary authority manifest digest mismatch")
    for field in (
        "live_authorization",
        "provider_mutation_allowed",
        "creator_mutation_allowed",
        "publish_allowed",
        "credential_access_allowed",
        "human_ground_truth",
        "readiness_eligible",
        "registry_submission_allowed",
    ):
        if canary.get(field) is not False:
            raise IndependentVerificationError(
                f"canary advisory boundary violation: {field}"
            )

    qa = authority.get("independent_parent_qa", {})
    expected_accepted = qa.get("disposition") == "ACCEPTED"
    if expected_accepted:
        if result.get("status") != "LOCAL_REHEARSAL_EVIDENCE_READY":
            raise IndependentVerificationError("accepted parent must yield ready state")
        if decision is None:
            raise IndependentVerificationError("accepted parent missing offline decision")
    else:
        if result.get("status") != "WAITING_PARENT_QA":
            raise IndependentVerificationError("pending parent must yield WAITING_PARENT_QA")
        if decision is not None:
            raise IndependentVerificationError("pending parent must not emit effective decision")

    if decision is not None:
        if decision.get("contract_version") != DECISION_VERSION:
            raise IndependentVerificationError("offline decision contract mismatch")
        if decision.get("decision_digest") != _digest_with_blank(
            decision, "decision_digest"
        ):
            raise IndependentVerificationError("offline decision digest mismatch")
        if decision.get("normalization_digest") != normalization["normalization_digest"]:
            raise IndependentVerificationError("decision/normalization linkage mismatch")
        if canary.get("decision_digest") != decision["decision_digest"]:
            raise IndependentVerificationError("canary/decision linkage mismatch")
        boundary = decision.get("boundary", {})
        for field in (
            "live_authorization",
            "creator_mutation",
            "provider_mutation",
            "browser_call",
            "provider_call",
            "publish",
            "human_ground_truth",
        ):
            if boundary.get(field) is not False:
                raise IndependentVerificationError(
                    f"decision boundary violation: {field}"
                )
    elif canary.get("decision_digest") is not None:
        raise IndependentVerificationError("pending canary must have null decision digest")

    if preview.get("preview_only") is not (not expected_accepted):
        raise IndependentVerificationError("preview gate mismatch")

    if result.get("result_digest") != _digest_with_blank(result, "result_digest"):
        raise IndependentVerificationError("result digest mismatch")
    if result.get("normalization") != normalization:
        raise IndependentVerificationError("result normalization copy mismatch")
    if result.get("offline_decision_preview") != preview:
        raise IndependentVerificationError("result preview copy mismatch")
    if result.get("decision") != decision:
        raise IndependentVerificationError("result decision copy mismatch")
    if result.get("canary_evidence_envelope") != canary:
        raise IndependentVerificationError("result canary copy mismatch")
    _verify_boundary(result.get("boundary", {}))

    report_material = copy.deepcopy(readiness)
    observed_report_digest = report_material.pop("report_digest", None)
    report_material["report_digest"] = ""
    if observed_report_digest != sha256_json(report_material):
        raise IndependentVerificationError("readiness report digest mismatch")
    if readiness.get("result_digest") != result["result_digest"]:
        raise IndependentVerificationError("report/result digest linkage mismatch")
    if readiness.get("normalization_digest") != normalization["normalization_digest"]:
        raise IndependentVerificationError("report/normalization digest linkage mismatch")
    if readiness.get("canary_envelope_digest") != canary["envelope_digest"]:
        raise IndependentVerificationError("report/canary linkage mismatch")
    if readiness.get("decision_digest") != (
        None if decision is None else decision["decision_digest"]
    ):
        raise IndependentVerificationError("report/decision linkage mismatch")
    for field in (
        "live_authorization",
        "network_call",
        "browser_call",
        "provider_call",
        "live_metrics",
        "creator_mutation",
        "provider_mutation",
        "publish",
        "credential_access",
        "human_ground_truth",
    ):
        if readiness.get(field) is not False:
            raise IndependentVerificationError(
                f"readiness boundary violation: {field}"
            )

    verification = {
        "contract_version": VERIFICATION_VERSION,
        "verified": True,
        "status": result["status"],
        "result_digest": result["result_digest"],
        "normalization_digest": normalization["normalization_digest"],
        "consensus_digest": normalization["consensus"]["consensus_digest"],
        "canary_envelope_digest": canary["envelope_digest"],
        "decision_digest": None if decision is None else decision["decision_digest"],
        "bundle_manifest_digest": normalization["bundle_manifest_digest"],
        "parent_r35_sha": R35_SHA,
        "parent_qa_disposition": qa.get("disposition"),
        "exact_source_candidate_round_binding": True,
        "live_authorization": False,
        "network_call": False,
        "browser_call": False,
        "provider_call": False,
        "live_metrics": False,
        "creator_mutation": False,
        "provider_mutation": False,
        "publish": False,
        "credential_access": False,
        "human_ground_truth": False,
    }
    verification["verification_digest"] = sha256_json(verification)
    return verification


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-r36-local-rehearsal-verifier")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--authority", required=True)
    parser.add_argument("--bundle-manifest", required=True)
    parser.add_argument("--report", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify_output_dir(
            output_dir=Path(args.output_dir),
            authority=_load(Path(args.authority)),
            bundle_manifest=_load(Path(args.bundle_manifest)),
        )
        _write(Path(args.report), report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as exc:
        report = {
            "contract_version": VERIFICATION_VERSION,
            "verified": False,
            "reason": type(exc).__name__,
            "detail": str(exc),
            "live_authorization": False,
            "network_call": False,
            "browser_call": False,
            "provider_call": False,
            "live_metrics": False,
            "creator_mutation": False,
            "provider_mutation": False,
            "publish": False,
            "credential_access": False,
            "human_ground_truth": False,
        }
        report["verification_digest"] = sha256_json(report)
        _write(Path(args.report), report)
        print(json.dumps(report, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
