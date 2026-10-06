from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json

CONTRACT_VERSION = "growth.local_fullstack_verifier.r37.v1"
AUTHORITY_VERSION = "growth.local_fullstack_verifier_authority.r37.v1"
POLICY_VERSION = "growth.local_fullstack_verifier_policy.r37.v1"
BUNDLE_VERSION = "growth.sealed_media_local_evidence.r37.v1"
REVIEW_VERSION = "growth.local_review_vote.r37.v1"
FINAL_MANIFEST_VERSION = "growth.local_final_manifest.r37.v1"
CREATOR_AUTHORITY_VERSION = "growth.creator_r38_authority.r37.v1"
VERIFICATION_VERSION = "growth.local_fullstack_verification.r37.v1"
LEDGER_VERSION = "growth.local_fullstack_verifier_ledger.r37.v1"
REPORT_VERSION = "growth.local_fullstack_verifier.r37.report.v1"

WAITING_CREATOR_AUTHORITY = "WAITING_CREATOR_AUTHORITY"
VERIFIED = "VERIFIED"
BLOCKED = "BLOCKED"

READY = "READY_FOR_LOCAL_DEMO"
NEEDS_REEDIT = "NEEDS_REEDIT"
HUMAN_REVIEW = "HUMAN_REVIEW_REQUIRED"
BLOCKED_INCOMPLETE = "BLOCKED_INCOMPLETE_EVIDENCE"

EVIDENCE_CLASSES = {"FIXTURE", "OFFLINE_MODEL", "GENUINE_REVIEW"}
WINNERS = {"A", "B", "tie", "insufficient_evidence"}

R36_SHA = "a53f9deb180bb256d193f0422c6ffd7a5923d97a"
R36_CI = 37398558145
R36_ARTIFACT_ID = 11383953328
R36_ARTIFACT_DIGEST = (
    "sha256:89e719718b5480ad889190a09c837efdfff31a15ea899f1e836ddb9d033f0894"
)
R36_AUTHORITY_BLOB = "a44963e13c10fd607cf4259e355dc990a297ecd9"
R36_POLICY_BLOB = "e6f6dd25b70f1e1ec4031d7eb2d0e8bb7ceb1b82"
R36_CONTRACT_BLOB = "3e0cd5baa85b4c59eb75386766aed0300a91b16d"

OBSERVED_CREATOR_R38_BRANCH = "agent/creator-r38-local-fullstack-rehearsal-20261006"
OBSERVED_CREATOR_SHA = "1f7cb9ed8f1985cd4faca79ce55f1c5fda9e3a57"


class R37Error(ValueError):
    pass


class AuthorityDrift(R37Error):
    pass


class EvidenceConflict(R37Error):
    pass


class IncompleteEvidence(R37Error):
    pass


class ReplayConflict(R37Error):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def _sha_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _file_size(path: Path) -> int:
    return Path(path).stat().st_size


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise IncompleteEvidence(f"{field} must be lowercase SHA-256")
    return value


def _git_sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise IncompleteEvidence(f"{field} must be exact Git SHA")
    return value


def _artifact_digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
    ):
        raise IncompleteEvidence(f"{field} must be sha256:<64 hex>")
    _sha(value[7:], field)
    return value


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise IncompleteEvidence(f"{field} must be positive integer")
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise IncompleteEvidence(f"{field} must be non-empty string")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise IncompleteEvidence(f"{field} must be canonical UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise IncompleteEvidence(f"{field} invalid timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise IncompleteEvidence(f"{field} must be UTC")
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise IncompleteEvidence(f"{field} timestamp canonical form drift")
    return parsed


def _safe_child(root: Path, relative: Any, field: str) -> Path:
    rel = _nonempty(relative, field)
    candidate = (root / rel).resolve()
    root = root.resolve()
    if candidate == root or root not in candidate.parents:
        raise IncompleteEvidence(f"{field} escapes bundle root")
    return candidate


def _expected_parent() -> dict[str, Any]:
    return {
        "repository": "foto6/video3",
        "producer_sha": R36_SHA,
        "ci_run_id": R36_CI,
        "artifact_id": R36_ARTIFACT_ID,
        "artifact_name": "growth-r36-local-rehearsal-evidence",
        "artifact_digest": R36_ARTIFACT_DIGEST,
        "contract": "growth.local_rehearsal_evidence.r36.v1",
        "authority_blob": R36_AUTHORITY_BLOB,
        "policy_blob": R36_POLICY_BLOB,
        "contract_blob": R36_CONTRACT_BLOB,
    }


def validate_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "growth_r36_parent",
        "creator_r38",
        "media_local_evidence_policy",
        "observed_unaccepted_media",
        "boundary",
    }:
        raise AuthorityDrift("R37 authority fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R37 authority contract drift")
    if value["growth_r36_parent"] != _expected_parent():
        raise AuthorityDrift("exact Growth R36 parent authority drift")
    creator = value["creator_r38"]
    if creator != {
        "status": WAITING_CREATOR_AUTHORITY,
        "repository": "foto6/video1",
        "expected_contract_prefix": "creator.",
        "observed_branch": OBSERVED_CREATOR_R38_BRANCH,
        "observed_sha": OBSERVED_CREATOR_SHA,
        "observed_commit_message": "Add R37 local integration driver",
        "observed_distinct_r38_authority": False,
        "exact_authority": None,
    }:
        raise AuthorityDrift("checked-in Creator R38 observation drift")
    media = value["media_local_evidence_policy"]
    if media != {
        "required_repository_for_real_evidence": "foto6/video2",
        "required_authority_fields": [
            "repository",
            "producer_sha",
            "ci_run_id",
            "artifact_id",
            "artifact_name",
            "artifact_digest",
            "contract",
        ],
        "allowed_authority_classes": ["EXACT_GREEN", "SYNTHETIC_FIXTURE"],
        "synthetic_fixture_can_be_ready_for_local_demo": False,
        "moving_branch_ref_is_authority": False,
    }:
        raise AuthorityDrift("Media evidence policy drift")
    observed = value["observed_unaccepted_media"]
    if observed != {
        "local_fix_branch": "agent/media-r25-final-local-fix-20261006",
        "local_fix_sha": "19a660e839b3d81d1fed80db562e8a1a82500c17",
        "exact_green_run": None,
        "multicandidate_branch":
            "agent/media-r25-multicandidate-round-engine-20261004",
        "observed_sha": "b11a93664f9b64bf6da626119758c841a0a75c1e",
        "observed_run_id": 37247948495,
        "observed_run_conclusion": "failure",
        "accepted": False,
    }:
        raise AuthorityDrift("observed unaccepted Media evidence drift")
    if value["boundary"] != {
        "local_only": True,
        "network_call": False,
        "browser_call": False,
        "provider_call": False,
        "credential_access": False,
        "creator_mutation": False,
        "provider_mutation": False,
        "publish": False,
        "merge": False,
        "human_ground_truth": False,
    }:
        raise AuthorityDrift("R37 no-side-effect boundary drift")
    return _clone(value)


def validate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "policy_version",
        "review_consensus",
        "targeted_reedit",
        "decision_precedence",
        "evidence_classes",
        "boundaries",
    }:
        raise AuthorityDrift("R37 policy fields invalid")
    if value["contract_version"] != POLICY_VERSION or value["policy_version"] != 1:
        raise AuthorityDrift("R37 policy version drift")
    if value["review_consensus"] != {
        "reviewers_per_round": 3,
        "high_confidence_min": 0.82,
        "high_confidence_mean": 0.86,
        "majority_dissent_confidence_max": 0.45,
        "winner_values": ["A", "B", "tie", "insufficient_evidence"],
    }:
        raise AuthorityDrift("R37 consensus policy drift")
    targeted = value["targeted_reedit"]
    if not isinstance(targeted, Mapping) or set(targeted) != {
        "high_severity_selected_candidate_requires_reedit",
        "allowed_operations",
        "defect_to_operation",
        "free_form_requested_edit_is_audit_only",
    }:
        raise AuthorityDrift("R37 targeted re-edit policy fields invalid")
    if targeted["high_severity_selected_candidate_requires_reedit"] is not True:
        raise AuthorityDrift("high severity selected defects must require re-edit")
    allowed = [
        "trim", "cut", "crop_scale_reframe", "speed_change",
        "fade_transition", "text_overlay", "subtitles_captions",
        "audio_duck_mix", "intro_outro_cta",
    ]
    if targeted["allowed_operations"] != allowed:
        raise AuthorityDrift("R37 operation allowlist drift")
    if any(x not in allowed for x in targeted["defect_to_operation"].values()):
        raise AuthorityDrift("R37 defect operation map escapes allowlist")
    if targeted["free_form_requested_edit_is_audit_only"] is not True:
        raise AuthorityDrift("free-form requested edits must remain audit-only")
    if value["decision_precedence"] != [
        BLOCKED_INCOMPLETE, HUMAN_REVIEW, NEEDS_REEDIT, READY
    ]:
        raise AuthorityDrift("R37 decision precedence drift")
    if value["evidence_classes"] != [
        "FIXTURE", "OFFLINE_MODEL", "GENUINE_REVIEW"
    ]:
        raise AuthorityDrift("R37 evidence class policy drift")
    if value["boundaries"] != {
        "ready_for_local_demo_is_publish_authorization": False,
        "provider_mutation_allowed": False,
        "browser_mutation_allowed": False,
        "credential_access_allowed": False,
        "publish_allowed": False,
    }:
        raise AuthorityDrift("R37 safety policy drift")
    return _clone(value)


def validate_creator_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "status",
        "repository",
        "observed_branch",
        "observed_sha",
        "observed_commit_message",
        "distinct_r38_authority_available",
        "exact_authority",
        "exact_authority_digest",
    }:
        raise AuthorityDrift("Creator R38 authority manifest fields invalid")
    if value["contract_version"] != CREATOR_AUTHORITY_VERSION:
        raise AuthorityDrift("Creator R38 authority manifest contract drift")
    if value["repository"] != "foto6/video1":
        raise AuthorityDrift("Creator R38 repository drift")
    status = value["status"]
    if status == WAITING_CREATOR_AUTHORITY:
        if (
            value["observed_branch"] != OBSERVED_CREATOR_R38_BRANCH
            or value["observed_sha"] != OBSERVED_CREATOR_SHA
            or value["observed_commit_message"] != "Add R37 local integration driver"
            or value["distinct_r38_authority_available"] is not False
            or value["exact_authority"] is not None
            or value["exact_authority_digest"] is not None
        ):
            raise AuthorityDrift("pending Creator R38 observation drift")
    elif status == "ACCEPTED":
        if value["distinct_r38_authority_available"] is not True:
            raise AuthorityDrift("accepted Creator R38 must be distinct authority")
        exact = value["exact_authority"]
        if not isinstance(exact, Mapping) or set(exact) != {
            "repository",
            "producer_sha",
            "ci_run_id",
            "artifact_id",
            "artifact_name",
            "artifact_digest",
            "contract",
        }:
            raise AuthorityDrift("accepted Creator R38 exact tuple fields invalid")
        if exact["repository"] != "foto6/video1":
            raise AuthorityDrift("accepted Creator R38 repository drift")
        _git_sha(exact["producer_sha"], "creator.producer_sha")
        if exact["producer_sha"] == OBSERVED_CREATOR_SHA:
            raise AuthorityDrift("Creator R38 authority must differ from observed R37 SHA")
        _positive(exact["ci_run_id"], "creator.ci_run_id")
        _positive(exact["artifact_id"], "creator.artifact_id")
        _nonempty(exact["artifact_name"], "creator.artifact_name")
        _artifact_digest(exact["artifact_digest"], "creator.artifact_digest")
        contract = _nonempty(exact["contract"], "creator.contract")
        if not contract.startswith("creator."):
            raise AuthorityDrift("Creator R38 contract must be creator.*")
        expected_digest = _sha_json(exact)
        if value["exact_authority_digest"] != expected_digest:
            raise AuthorityDrift("Creator R38 exact authority digest drift")
    else:
        raise AuthorityDrift("Creator R38 status invalid")
    return _clone(value)


def creator_authority_digest(value: Mapping[str, Any]) -> str:
    return _sha_json(validate_creator_authority(value))


def validate_media_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "authority_class",
        "repository",
        "producer_sha",
        "ci_run_id",
        "artifact_id",
        "artifact_name",
        "artifact_digest",
        "contract",
    }:
        raise IncompleteEvidence("exact Media authority tuple fields missing")
    klass = value["authority_class"]
    if klass not in {"EXACT_GREEN", "SYNTHETIC_FIXTURE"}:
        raise IncompleteEvidence("Media authority class invalid")
    if klass == "EXACT_GREEN" and value["repository"] != "foto6/video2":
        raise IncompleteEvidence("real Media authority must be foto6/video2")
    if klass == "SYNTHETIC_FIXTURE" and value["repository"] != "fixture/media":
        raise IncompleteEvidence("synthetic Media authority must use fixture/media")
    _git_sha(value["producer_sha"], "media.producer_sha")
    _positive(value["ci_run_id"], "media.ci_run_id")
    _positive(value["artifact_id"], "media.artifact_id")
    _nonempty(value["artifact_name"], "media.artifact_name")
    _artifact_digest(value["artifact_digest"], "media.artifact_digest")
    contract = _nonempty(value["contract"], "media.contract")
    if not contract.startswith("media."):
        raise IncompleteEvidence("Media contract must be media.*")
    return _clone(value)


def media_authority_digest(value: Mapping[str, Any]) -> str:
    return _sha_json(validate_media_authority(value))


def _verify_file(root: Path, ref: Mapping[str, Any], field: str) -> Path:
    if not isinstance(ref, Mapping) or set(ref) < {"path", "sha256", "size"}:
        raise IncompleteEvidence(f"{field} file reference incomplete")
    path = _safe_child(root, ref["path"], f"{field}.path")
    if not path.is_file():
        raise IncompleteEvidence(f"{field} file missing")
    expected_sha = _sha(ref["sha256"], f"{field}.sha256")
    expected_size = ref["size"]
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 1:
        raise IncompleteEvidence(f"{field}.size invalid")
    if _file_sha(path) != expected_sha or _file_size(path) != expected_size:
        raise IncompleteEvidence(f"{field} bytes/hash/size drift")
    return path


def _manifest_semantic_digest(value: Mapping[str, Any]) -> str:
    material = copy.deepcopy(dict(value))
    material["manifest_digest"] = ""
    return _sha_json(material)


def _candidate_map(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {row["candidate_id"]: row for row in manifest["candidates"]}


def _validate_final_manifest(
    root: Path,
    manifest: Mapping[str, Any],
    media_digest: str,
) -> dict[str, Any]:
    final = manifest["final"]
    final_manifest_path = _safe_child(
        root, final["manifest_path"], "final.manifest_path"
    )
    if not final_manifest_path.is_file():
        raise IncompleteEvidence("final manifest missing")
    if (
        _file_sha(final_manifest_path)
        != _sha(final["manifest_sha256"], "final.manifest_sha256")
        or _file_size(final_manifest_path) != final["manifest_size"]
    ):
        raise IncompleteEvidence("final manifest bytes/hash/size drift")
    value = _load(final_manifest_path)
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "created_at",
        "selected_candidate_id",
        "selected_round",
        "source_sha256",
        "candidate_sha256",
        "final_sha256",
        "final_size",
        "media_authority_digest",
        "manifest_digest",
    }:
        raise IncompleteEvidence("final manifest fields invalid")
    if value["contract_version"] != FINAL_MANIFEST_VERSION:
        raise IncompleteEvidence("final manifest contract drift")
    _timestamp(value["created_at"], "final_manifest.created_at")
    observed = _sha(value["manifest_digest"], "final_manifest.manifest_digest")
    if _manifest_semantic_digest(value) != observed:
        raise IncompleteEvidence("final manifest semantic digest drift")
    if value["media_authority_digest"] != media_digest:
        raise IncompleteEvidence("final manifest Media authority binding drift")
    return _clone(value)


def validate_bundle(
    root: Path,
    *,
    expected_bundle_digest: str,
) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise IncompleteEvidence("sealed bundle manifest.json missing")
    manifest = _load(manifest_path)
    required = {
        "contract_version",
        "bundle_id",
        "bundle_kind",
        "created_at",
        "sealed_at",
        "media_authority",
        "media_authority_digest",
        "source",
        "candidates",
        "rounds",
        "targeted_reedits",
        "final",
        "evidence_boundary",
        "manifest_digest",
    }
    if not isinstance(manifest, Mapping) or set(manifest) != required:
        raise IncompleteEvidence("sealed bundle manifest fields invalid")
    if manifest["contract_version"] != BUNDLE_VERSION:
        raise IncompleteEvidence("sealed bundle contract drift")
    _nonempty(manifest["bundle_id"], "bundle_id")
    if manifest["bundle_kind"] not in {"SYNTHETIC_FIXTURE", "LOCAL_EVIDENCE"}:
        raise IncompleteEvidence("bundle_kind invalid")
    created_at = _timestamp(manifest["created_at"], "created_at")
    sealed_at = _timestamp(manifest["sealed_at"], "sealed_at")
    if sealed_at < created_at:
        raise IncompleteEvidence("bundle sealed_at precedes created_at")
    observed_digest = _sha(manifest["manifest_digest"], "manifest_digest")
    if _manifest_semantic_digest(manifest) != observed_digest:
        raise IncompleteEvidence("sealed manifest semantic digest drift")
    if _sha(expected_bundle_digest, "expected_bundle_digest") != observed_digest:
        raise IncompleteEvidence("expected bundle digest mismatch")

    media = validate_media_authority(manifest["media_authority"])
    media_digest = media_authority_digest(media)
    if manifest["media_authority_digest"] != media_digest:
        raise IncompleteEvidence("Media authority digest drift")
    if (
        manifest["bundle_kind"] == "SYNTHETIC_FIXTURE"
        and media["authority_class"] != "SYNTHETIC_FIXTURE"
    ):
        raise IncompleteEvidence("synthetic bundle must use synthetic Media authority")
    if (
        manifest["bundle_kind"] == "LOCAL_EVIDENCE"
        and media["authority_class"] != "EXACT_GREEN"
    ):
        raise IncompleteEvidence("real local bundle requires EXACT_GREEN Media tuple")

    boundary = manifest["evidence_boundary"]
    if boundary != {
        "fixture": manifest["bundle_kind"] == "SYNTHETIC_FIXTURE",
        "network_call": False,
        "browser_call": False,
        "provider_call": False,
        "credentials_accessed": False,
        "provider_mutation": False,
        "publish": False,
        "human_ground_truth": False,
    }:
        raise IncompleteEvidence("bundle evidence boundary drift")

    source = manifest["source"]
    if not isinstance(source, Mapping) or set(source) != {"path", "sha256", "size"}:
        raise IncompleteEvidence("source reference fields invalid")
    _verify_file(root, source, "source")
    source_sha = source["sha256"]

    candidates = manifest["candidates"]
    if not isinstance(candidates, list) or len(candidates) < 2:
        raise IncompleteEvidence("at least two candidates required")
    ids: set[str] = set()
    hashes: set[str] = set()
    candidate_map: dict[str, Any] = {}
    for index, row in enumerate(candidates):
        if not isinstance(row, Mapping) or set(row) != {
            "candidate_id",
            "round",
            "path",
            "sha256",
            "size",
            "source_sha256",
            "parent_candidate_id",
            "applied_directive_digest",
        }:
            raise IncompleteEvidence(f"candidate[{index}] fields invalid")
        candidate_id = _nonempty(row["candidate_id"], f"candidate[{index}].candidate_id")
        if candidate_id in ids:
            raise EvidenceConflict("duplicate candidate id")
        ids.add(candidate_id)
        if isinstance(row["round"], bool) or not isinstance(row["round"], int) or row["round"] < 0:
            raise IncompleteEvidence("candidate round invalid")
        _verify_file(root, row, f"candidate[{candidate_id}]")
        if row["source_sha256"] != source_sha:
            raise IncompleteEvidence("candidate source drift")
        digest = row["sha256"]
        if digest in hashes:
            raise EvidenceConflict("duplicate candidate bytes")
        hashes.add(digest)
        parent = row["parent_candidate_id"]
        directive = row["applied_directive_digest"]
        if parent is None:
            if directive is not None:
                raise IncompleteEvidence("root candidate cannot carry re-edit directive")
        else:
            _nonempty(parent, "candidate.parent_candidate_id")
            _sha(directive, "candidate.applied_directive_digest")
        candidate_map[candidate_id] = _clone(row)

    for candidate_id, row in candidate_map.items():
        parent = row["parent_candidate_id"]
        if parent is not None:
            if parent not in candidate_map:
                raise IncompleteEvidence("targeted re-edit parent candidate missing")
            if candidate_map[parent]["round"] >= row["round"]:
                raise IncompleteEvidence("targeted re-edit parent round is stale/invalid")

    rounds = manifest["rounds"]
    if not isinstance(rounds, list) or not rounds:
        raise IncompleteEvidence("review rounds missing")
    seen_rounds: set[int] = set()
    normalized_rounds = []
    review_file_digests: set[str] = set()
    for row in rounds:
        if not isinstance(row, Mapping) or set(row) != {
            "review_round",
            "opened_at",
            "package_digest",
            "prompt",
            "sealed_mapping_digest",
            "labels",
            "reviews",
        }:
            raise IncompleteEvidence("review round fields invalid")
        round_number = row["review_round"]
        if isinstance(round_number, bool) or not isinstance(round_number, int) or round_number < 0:
            raise IncompleteEvidence("review round number invalid")
        if round_number in seen_rounds:
            raise EvidenceConflict("duplicate review round")
        seen_rounds.add(round_number)
        opened = _timestamp(row["opened_at"], f"round[{round_number}].opened_at")
        if opened < created_at or opened > sealed_at:
            raise IncompleteEvidence("review round timestamp outside bundle window")
        _sha(row["package_digest"], "round.package_digest")
        _sha(row["sealed_mapping_digest"], "round.sealed_mapping_digest")
        _verify_file(root, row["prompt"], f"round[{round_number}].prompt")
        labels = row["labels"]
        if not isinstance(labels, Mapping) or set(labels) != {"A", "B"}:
            raise IncompleteEvidence("round must map exact A/B labels")
        if labels["A"] == labels["B"]:
            raise EvidenceConflict("round A/B labels map same candidate")
        for label in ("A", "B"):
            candidate_id = labels[label]
            if candidate_id not in candidate_map:
                raise IncompleteEvidence("review round references missing candidate")
            if candidate_map[candidate_id]["round"] > round_number:
                raise IncompleteEvidence("review round references future candidate")
        refs = row["reviews"]
        if not isinstance(refs, list) or len(refs) != 3:
            raise IncompleteEvidence("exactly three reviews required per round")
        parsed_reviews = []
        reviewer_ids: set[str] = set()
        for ref in refs:
            path = _verify_file(root, ref, f"round[{round_number}].review")
            if ref["sha256"] in review_file_digests:
                raise EvidenceConflict("duplicate/replayed review bytes")
            review_file_digests.add(ref["sha256"])
            review = _load(path)
            parsed = _validate_review(
                review,
                round_row=row,
                candidate_map=candidate_map,
                source_sha=source_sha,
                sealed_at=sealed_at,
            )
            if parsed["reviewer_id"] in reviewer_ids:
                raise EvidenceConflict("duplicate reviewer identity in round")
            reviewer_ids.add(parsed["reviewer_id"])
            parsed_reviews.append(parsed)
        normalized_rounds.append({
            **_clone(row),
            "reviews_parsed": sorted(parsed_reviews, key=lambda x: x["reviewer_id"]),
        })

    expected_rounds = list(range(min(seen_rounds), max(seen_rounds) + 1))
    if sorted(seen_rounds) != expected_rounds or expected_rounds[0] != 0:
        raise IncompleteEvidence("review rounds must be contiguous from zero")
    normalized_rounds.sort(key=lambda x: x["review_round"])

    targeted = manifest["targeted_reedits"]
    if not isinstance(targeted, list):
        raise IncompleteEvidence("targeted_reedits must be array")
    targeted_by_parent: dict[int, Any] = {}
    for row in targeted:
        if not isinstance(row, Mapping) or set(row) != {
            "parent_round",
            "child_round",
            "parent_candidate_id",
            "child_candidate_id",
            "directives",
            "directive_digest",
        }:
            raise IncompleteEvidence("targeted re-edit fields invalid")
        if row["parent_round"] in targeted_by_parent:
            raise EvidenceConflict("duplicate targeted re-edit parent round")
        if row["child_round"] != row["parent_round"] + 1:
            raise IncompleteEvidence("targeted re-edit must advance exactly one round")
        if (
            row["parent_candidate_id"] not in candidate_map
            or row["child_candidate_id"] not in candidate_map
        ):
            raise IncompleteEvidence("targeted re-edit candidate missing")
        if candidate_map[row["child_candidate_id"]]["parent_candidate_id"] != row["parent_candidate_id"]:
            raise IncompleteEvidence("targeted re-edit child parent lineage drift")
        digest = _sha(row["directive_digest"], "targeted_reedit.directive_digest")
        if _sha_json(row["directives"]) != digest:
            raise IncompleteEvidence("targeted re-edit directive digest drift")
        if candidate_map[row["child_candidate_id"]]["applied_directive_digest"] != digest:
            raise IncompleteEvidence("targeted re-edit candidate directive binding drift")
        targeted_by_parent[row["parent_round"]] = _clone(row)

    final = manifest["final"]
    if not isinstance(final, Mapping) or set(final) != {
        "selected_candidate_id",
        "selected_round",
        "path",
        "sha256",
        "size",
        "manifest_path",
        "manifest_sha256",
        "manifest_size",
    }:
        raise IncompleteEvidence("final evidence fields invalid")
    final_path = _verify_file(root, final, "final")
    if final["selected_candidate_id"] not in candidate_map:
        raise IncompleteEvidence("final selected candidate missing")
    selected = candidate_map[final["selected_candidate_id"]]
    if final["selected_round"] != selected["round"]:
        raise IncompleteEvidence("final selected round lineage drift")
    final_manifest = _validate_final_manifest(root, manifest, media_digest)
    final_created = _timestamp(
        final_manifest["created_at"], "final_manifest.created_at"
    )
    if final_created < created_at or final_created > sealed_at:
        raise IncompleteEvidence("final manifest timestamp outside bundle window")
    if (
        final_manifest["selected_candidate_id"] != final["selected_candidate_id"]
        or final_manifest["selected_round"] != final["selected_round"]
        or final_manifest["source_sha256"] != source_sha
        or final_manifest["candidate_sha256"] != selected["sha256"]
        or final_manifest["final_sha256"] != final["sha256"]
        or final_manifest["final_size"] != final["size"]
    ):
        raise IncompleteEvidence("final manifest selected lineage drift")

    return {
        "root": str(root),
        "manifest": _clone(manifest),
        "manifest_digest": observed_digest,
        "media_authority": media,
        "media_authority_digest": media_digest,
        "source": _clone(source),
        "candidates": candidate_map,
        "rounds": normalized_rounds,
        "targeted_by_parent": targeted_by_parent,
        "final": _clone(final),
        "final_manifest": final_manifest,
        "final_file_sha256": _file_sha(final_path),
    }


def _validate_review(
    value: Mapping[str, Any],
    *,
    round_row: Mapping[str, Any],
    candidate_map: Mapping[str, Any],
    source_sha: str,
    sealed_at: datetime,
) -> dict[str, Any]:
    required = {
        "contract_version",
        "review_id",
        "reviewer_id",
        "evidence_class",
        "created_at",
        "review_round",
        "package_digest",
        "source_sha256",
        "candidate_hashes",
        "winner",
        "confidence",
        "policy_valid",
        "format_valid",
        "defects",
        "uncertainty",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise IncompleteEvidence("review vote fields invalid")
    if value["contract_version"] != REVIEW_VERSION:
        raise IncompleteEvidence("review vote contract drift")
    _nonempty(value["review_id"], "review_id")
    _nonempty(value["reviewer_id"], "reviewer_id")
    if value["evidence_class"] not in EVIDENCE_CLASSES:
        raise IncompleteEvidence("review evidence class invalid")
    created = _timestamp(value["created_at"], "review.created_at")
    opened = _timestamp(round_row["opened_at"], "round.opened_at")
    if created < opened or created > sealed_at:
        raise IncompleteEvidence("stale review timestamp outside round/seal window")
    if value["review_round"] != round_row["review_round"]:
        raise IncompleteEvidence("stale review round mismatch")
    if value["package_digest"] != round_row["package_digest"]:
        raise IncompleteEvidence("stale review package digest")
    if value["source_sha256"] != source_sha:
        raise IncompleteEvidence("review source drift")
    expected_hashes = {
        label: candidate_map[candidate_id]["sha256"]
        for label, candidate_id in round_row["labels"].items()
    }
    if value["candidate_hashes"] != expected_hashes:
        raise IncompleteEvidence("review candidate hash binding drift")
    if value["winner"] not in WINNERS:
        raise IncompleteEvidence("review winner invalid")
    conf = value["confidence"]
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        raise IncompleteEvidence("review confidence outside 0..1")
    if not isinstance(value["policy_valid"], bool) or not isinstance(value["format_valid"], bool):
        raise IncompleteEvidence("review policy/format validity invalid")
    if not isinstance(value["uncertainty"], str):
        raise IncompleteEvidence("review uncertainty invalid")
    defects = value["defects"]
    if not isinstance(defects, list):
        raise IncompleteEvidence("review defects must be array")
    for defect in defects:
        if not isinstance(defect, Mapping) or set(defect) != {
            "candidate_label",
            "start_ms",
            "end_ms",
            "category",
            "severity",
            "direction",
            "requested_edit",
            "confidence",
        }:
            raise IncompleteEvidence("review defect fields invalid")
        if defect["candidate_label"] not in {"A", "B"}:
            raise IncompleteEvidence("review defect candidate label invalid")
        if defect["severity"] not in {"low", "medium", "high"}:
            raise IncompleteEvidence("review defect severity invalid")
        if defect["direction"] not in {"increase", "decrease", "replace", "remove", "preserve"}:
            raise IncompleteEvidence("review defect direction invalid")
        if (
            isinstance(defect["start_ms"], bool)
            or isinstance(defect["end_ms"], bool)
            or not isinstance(defect["start_ms"], int)
            or not isinstance(defect["end_ms"], int)
            or defect["start_ms"] < 0
            or defect["end_ms"] <= defect["start_ms"]
        ):
            raise IncompleteEvidence("review defect timestamp invalid")
        _nonempty(defect["category"], "review.defect.category")
        _nonempty(defect["requested_edit"], "review.defect.requested_edit")
        dconf = defect["confidence"]
        if isinstance(dconf, bool) or not isinstance(dconf, (int, float)) or not 0 <= dconf <= 1:
            raise IncompleteEvidence("review defect confidence invalid")
    return _clone(value)


def _consensus(
    round_row: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    reviews = round_row["reviews_parsed"]
    thresholds = policy["review_consensus"]
    classes = sorted({row["evidence_class"] for row in reviews})
    reasons: list[str] = []
    if any(not row["policy_valid"] for row in reviews):
        reasons.append("POLICY_INVALID")
    if any(not row["format_valid"] for row in reviews):
        reasons.append("FORMAT_INVALID")
    votes = Counter(row["winner"] for row in reviews)
    ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
    modal, count = ranked[0]
    selected_label = None
    rule = "none"
    if not reasons and modal in {"A", "B"} and count == 3:
        confidences = [row["confidence"] for row in reviews]
        if (
            min(confidences) >= thresholds["high_confidence_min"]
            and sum(confidences) / 3 >= thresholds["high_confidence_mean"]
        ):
            selected_label = modal
            rule = "unanimous_high_confidence"
        else:
            reasons.append("LOW_CONFIDENCE")
    elif not reasons and modal in {"A", "B"} and count == 2:
        majority = [row for row in reviews if row["winner"] == modal]
        dissent = [row for row in reviews if row["winner"] != modal]
        majority_conf = [row["confidence"] for row in majority]
        if (
            min(majority_conf) >= thresholds["high_confidence_min"]
            and sum(majority_conf) / 2 >= thresholds["high_confidence_mean"]
            and max(row["confidence"] for row in dissent)
                <= thresholds["majority_dissent_confidence_max"]
        ):
            selected_label = modal
            rule = "two_of_three_bounded_dissent"
        else:
            reasons.append("EXCESSIVE_DISAGREEMENT")
    else:
        reasons.append("NO_ACCEPTABLE_WINNER")
    selected_candidate_id = (
        None if selected_label is None else round_row["labels"][selected_label]
    )
    return {
        "review_round": round_row["review_round"],
        "state": "CONSENSUS_ACCEPTED" if selected_label else HUMAN_REVIEW,
        "selected_label": selected_label,
        "selected_candidate_id": selected_candidate_id,
        "rule": rule,
        "reason_codes": sorted(set(reasons)),
        "evidence_classes": classes,
        "mixed_evidence_classes": len(classes) > 1,
        "review_ids": [row["review_id"] for row in reviews],
        "review_response_digests": [
            _sha_json(row) for row in reviews
        ],
    }


def _derived_directives(
    round_row: Mapping[str, Any],
    consensus: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    selected_label = consensus["selected_label"]
    if selected_label is None:
        return []
    candidate_id = consensus["selected_candidate_id"]
    defect_map = policy["targeted_reedit"]["defect_to_operation"]
    rows = []
    for review in round_row["reviews_parsed"]:
        for defect in review["defects"]:
            if defect["candidate_label"] != selected_label or defect["severity"] != "high":
                continue
            operation = defect_map.get(defect["category"])
            if operation is None:
                continue
            rows.append({
                "candidate_id": candidate_id,
                "start_ms": defect["start_ms"],
                "end_ms": defect["end_ms"],
                "operation": operation,
                "category": defect["category"],
                "direction": defect["direction"],
            })
    rows.sort(
        key=lambda x: (
            x["candidate_id"],
            x["start_ms"],
            x["end_ms"],
            x["operation"],
            x["category"],
            x["direction"],
        )
    )
    unique = []
    seen = set()
    for row in rows:
        digest = _sha_json(row)
        if digest not in seen:
            seen.add(digest)
            unique.append(row)
    return unique


def _evaluate_closed_loop(
    parsed: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    rounds = parsed["rounds"]
    targeted = parsed["targeted_by_parent"]
    round_results = []
    pending_reedit_reason = None
    final_selected = None
    for index, row in enumerate(rounds):
        consensus = _consensus(row, policy=policy)
        directives = _derived_directives(row, consensus, policy=policy)
        if consensus["state"] == HUMAN_REVIEW:
            round_decision = HUMAN_REVIEW
        elif directives:
            round_decision = NEEDS_REEDIT
        else:
            round_decision = READY
        round_results.append({
            "review_round": row["review_round"],
            "consensus": consensus,
            "derived_directives": directives,
            "derived_directive_digest": _sha_json(directives),
            "round_decision": round_decision,
        })
        if round_decision == HUMAN_REVIEW:
            final_selected = None
            pending_reedit_reason = None
            if index != len(rounds) - 1:
                raise IncompleteEvidence(
                    "later round exists after unresolved human review"
                )
            break
        if round_decision == NEEDS_REEDIT:
            expected = targeted.get(row["review_round"])
            if expected is None:
                pending_reedit_reason = "TARGETED_REEDIT_MISSING"
                final_selected = consensus["selected_candidate_id"]
                if index != len(rounds) - 1:
                    raise IncompleteEvidence(
                        "later round exists without targeted re-edit binding"
                    )
                break
            expected_digest = _sha_json(directives)
            if (
                expected["parent_candidate_id"] != consensus["selected_candidate_id"]
                or expected["directive_digest"] != expected_digest
                or expected["directives"] != directives
            ):
                pending_reedit_reason = "TARGETED_REEDIT_DIRECTIVE_MISMATCH"
                final_selected = consensus["selected_candidate_id"]
                break
            child = parsed["candidates"][expected["child_candidate_id"]]
            if (
                child["round"] != expected["child_round"]
                or child["parent_candidate_id"] != expected["parent_candidate_id"]
                or child["applied_directive_digest"] != expected_digest
            ):
                pending_reedit_reason = "TARGETED_REEDIT_LINEAGE_MISMATCH"
                final_selected = consensus["selected_candidate_id"]
                break
            if index + 1 >= len(rounds):
                pending_reedit_reason = "TARGETED_REEDIT_NOT_REVIEWED"
                final_selected = child["candidate_id"]
                break
            next_round = rounds[index + 1]
            if (
                next_round["review_round"] != expected["child_round"]
                or expected["child_candidate_id"] not in next_round["labels"].values()
            ):
                pending_reedit_reason = "TARGETED_REEDIT_WRONG_NEXT_ROUND"
                final_selected = child["candidate_id"]
                break
            continue
        final_selected = consensus["selected_candidate_id"]
        if index != len(rounds) - 1:
            raise IncompleteEvidence("unexpected extra review round after ready winner")

    last = round_results[-1]
    if pending_reedit_reason is not None:
        underlying = NEEDS_REEDIT
        reasons = [pending_reedit_reason]
    elif last["round_decision"] == HUMAN_REVIEW:
        underlying = HUMAN_REVIEW
        reasons = last["consensus"]["reason_codes"]
    elif last["round_decision"] == NEEDS_REEDIT:
        underlying = NEEDS_REEDIT
        reasons = ["TARGETED_REEDIT_REQUIRED"]
    else:
        underlying = READY
        reasons = []

    final = parsed["final"]
    selected_row = (
        None if final_selected is None else parsed["candidates"].get(final_selected)
    )
    if underlying == READY:
        if selected_row is None:
            raise IncompleteEvidence("ready result missing selected candidate")
        if (
            final["selected_candidate_id"] != final_selected
            or final["selected_round"] != selected_row["round"]
            or final["sha256"] != selected_row["sha256"]
            or final["size"] != selected_row["size"]
            or parsed["final_file_sha256"] != selected_row["sha256"]
        ):
            raise IncompleteEvidence("final.mp4 does not match selected lineage")
    return {
        "underlying_decision": underlying,
        "reason_codes": reasons,
        "selected_candidate_id": final_selected,
        "round_results": round_results,
    }


def _overall_evidence_class(parsed: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for round_row in parsed["rounds"]:
        for review in round_row["reviews_parsed"]:
            rows.append({
                "review_id": review["review_id"],
                "review_round": review["review_round"],
                "evidence_class": review["evidence_class"],
            })
    classes = sorted({row["evidence_class"] for row in rows})
    if classes == ["GENUINE_REVIEW"]:
        classification = "GENUINE_REVIEW"
    elif classes == ["OFFLINE_MODEL"]:
        classification = "OFFLINE_MODEL"
    elif classes == ["FIXTURE"]:
        classification = "FIXTURE"
    else:
        classification = "MIXED"
    return {
        "classification": classification,
        "review_evidence": rows,
        "classes_present": classes,
    }


def build_verification(
    *,
    parsed: Mapping[str, Any],
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    creator_authority: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    creator = validate_creator_authority(creator_authority)
    evaluation = _evaluate_closed_loop(parsed, policy=policy)
    evidence = _overall_evidence_class(parsed)

    blockers = []
    state = VERIFIED
    final_decision = evaluation["underlying_decision"]
    if creator["status"] != "ACCEPTED":
        state = WAITING_CREATOR_AUTHORITY
        blockers.append("WAITING_CREATOR_AUTHORITY")
        final_decision = BLOCKED_INCOMPLETE
    if parsed["media_authority"]["authority_class"] != "EXACT_GREEN":
        blockers.append("MEDIA_AUTHORITY_NOT_EXACT_GREEN")
        final_decision = BLOCKED_INCOMPLETE
    if evidence["classification"] == "FIXTURE":
        blockers.append("FIXTURE_EVIDENCE_NONPROMOTABLE")
        final_decision = BLOCKED_INCOMPLETE
    if final_decision == BLOCKED_INCOMPLETE and state == VERIFIED:
        state = BLOCKED

    result = {
        "contract_version": VERIFICATION_VERSION,
        "state": state,
        "final_decision": final_decision,
        "underlying_closed_loop_decision": evaluation["underlying_decision"],
        "reason_codes": sorted(set(blockers + evaluation["reason_codes"])),
        "bundle_id": parsed["manifest"]["bundle_id"],
        "bundle_manifest_digest": parsed["manifest_digest"],
        "media_authority": parsed["media_authority"],
        "media_authority_digest": parsed["media_authority_digest"],
        "creator_r38_authority_status": creator["status"],
        "creator_r38_authority_digest": creator_authority_digest(creator),
        "creator_r38_exact_authority": creator["exact_authority"],
        "growth_r37": {
            "repository": "foto6/video3",
            "producer_sha": _git_sha(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "authority_digest": _sha_json(authority),
            "policy_digest": _sha_json(policy),
            "parent_r36": _expected_parent(),
        },
        "source": parsed["source"],
        "candidates": [
            parsed["candidates"][key]
            for key in sorted(parsed["candidates"])
        ],
        "final": parsed["final"],
        "final_manifest": parsed["final_manifest"],
        "selected_candidate_id": evaluation["selected_candidate_id"],
        "round_results": evaluation["round_results"],
        "evidence_classification": evidence,
        "provider_mutation_authorized": False,
        "browser_mutation_authorized": False,
        "credential_access_authorized": False,
        "publish_authorized": False,
        "live_publish_recommendation": False,
        "human_ground_truth": False,
        "verification_digest": "",
    }
    material = copy.deepcopy(result)
    material["verification_digest"] = ""
    result["verification_digest"] = _sha_json(material)
    return _clone(result)


class Ledger:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory).resolve()
        self.path = self.directory / "growth-r37-local-fullstack-ledger.json"
        self.bundles: dict[str, str] = {}
        self.evaluations: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            raw = _load(self.path)
            if not isinstance(raw, Mapping) or set(raw) != {
                "contract_version", "bundles", "evaluations"
            }:
                raise ReplayConflict("R37 ledger shape invalid")
            if raw["contract_version"] != LEDGER_VERSION:
                raise ReplayConflict("R37 ledger contract drift")
            self.bundles = dict(raw["bundles"])
            self.evaluations = dict(raw["evaluations"])

    def apply(
        self,
        *,
        bundle_id: str,
        bundle_digest: str,
        creator_digest: str,
        result: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        prior_bundle = self.bundles.get(bundle_id)
        if prior_bundle is not None and prior_bundle != bundle_digest:
            raise ReplayConflict("same bundle identity changed sealed manifest bytes")
        context = _sha_json({
            "bundle_id": bundle_id,
            "bundle_digest": bundle_digest,
            "creator_authority_digest": creator_digest,
        })
        prior = self.evaluations.get(context)
        if prior is not None:
            if prior["verification_digest"] != result["verification_digest"]:
                raise ReplayConflict("exact replay changed verification result")
            return _clone(prior["result"]), False
        self.bundles[bundle_id] = bundle_digest
        self.evaluations[context] = {
            "verification_digest": result["verification_digest"],
            "result": _clone(result),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        _write_atomic(
            self.path,
            {
                "contract_version": LEDGER_VERSION,
                "bundles": self.bundles,
                "evaluations": self.evaluations,
            },
        )
        return _clone(result), True


def verify_local_bundle(
    *,
    bundle_dir: Path,
    expected_bundle_digest: str,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    creator_authority: Mapping[str, Any],
    ledger_dir: Path,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    parsed = validate_bundle(
        Path(bundle_dir),
        expected_bundle_digest=expected_bundle_digest,
    )
    result = build_verification(
        parsed=parsed,
        authority=authority,
        policy=policy,
        creator_authority=creator_authority,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    ledger = Ledger(ledger_dir)
    canonical, changed = ledger.apply(
        bundle_id=parsed["manifest"]["bundle_id"],
        bundle_digest=parsed["manifest_digest"],
        creator_digest=result["creator_r38_authority_digest"],
        result=result,
    )
    canonical["replay_noop"] = not changed
    canonical["ledger_path"] = str(ledger.path)
    return canonical


def _blocked_report(
    *,
    reason: Exception,
    state: str = BLOCKED,
) -> dict[str, Any]:
    report = {
        "contract_version": VERIFICATION_VERSION,
        "state": state,
        "final_decision": BLOCKED_INCOMPLETE,
        "underlying_closed_loop_decision": BLOCKED_INCOMPLETE,
        "reason_codes": [type(reason).__name__],
        "detail": str(reason),
        "provider_mutation_authorized": False,
        "browser_mutation_authorized": False,
        "credential_access_authorized": False,
        "publish_authorized": False,
        "live_publish_recommendation": False,
        "human_ground_truth": False,
        "verification_digest": "",
    }
    material = copy.deepcopy(report)
    material["verification_digest"] = ""
    report["verification_digest"] = _sha_json(material)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-r37-local-fullstack-verifier")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--bundle-dir", required=True)
    verify.add_argument("--expected-bundle-digest", required=True)
    verify.add_argument("--authority", required=True)
    verify.add_argument("--policy", required=True)
    verify.add_argument("--creator-authority", required=True)
    verify.add_argument("--ledger-dir", required=True)
    verify.add_argument("--out", required=True)
    verify.add_argument("--growth-sha", required=True)
    verify.add_argument("--growth-ci-run-id", required=True, type=int)

    rehearse = sub.add_parser("rehearse-adversarial")
    rehearse.add_argument("--bundle-dir", required=True)
    rehearse.add_argument("--expected-bundle-digest", required=True)
    rehearse.add_argument("--authority", required=True)
    rehearse.add_argument("--policy", required=True)
    rehearse.add_argument("--creator-authority", required=True)
    rehearse.add_argument("--out", required=True)
    rehearse.add_argument("--growth-sha", required=True)
    rehearse.add_argument("--growth-ci-run-id", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = Path(args.out)
    try:
        if args.command == "rehearse-adversarial":
            from .local_fullstack_verifier_r37_sim import rehearse

            result = rehearse(
                bundle_dir=Path(args.bundle_dir),
                expected_bundle_digest=args.expected_bundle_digest,
                authority=_load(Path(args.authority)),
                policy=_load(Path(args.policy)),
                creator_authority=_load(Path(args.creator_authority)),
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            _write_atomic(out, result)
            print(json.dumps(result, sort_keys=True))
            return 0

        result = verify_local_bundle(
            bundle_dir=Path(args.bundle_dir),
            expected_bundle_digest=args.expected_bundle_digest,
            authority=_load(Path(args.authority)),
            policy=_load(Path(args.policy)),
            creator_authority=_load(Path(args.creator_authority)),
            ledger_dir=Path(args.ledger_dir),
            growth_sha=args.growth_sha,
            growth_ci_run_id=args.growth_ci_run_id,
        )
        _write_atomic(out, result)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["final_decision"] != BLOCKED_INCOMPLETE else 3
    except R37Error as exc:
        report = _blocked_report(reason=exc)
        _write_atomic(out, report)
        print(json.dumps(report, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
