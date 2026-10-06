from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from . import consensus_review_oracle_r30 as r30

CONTRACT_VERSION = "growth.local_rehearsal_evidence.r36.v1"
AUTHORITY_VERSION = "growth.local_rehearsal_authority.r36.v1"
POLICY_VERSION = "growth.local_rehearsal_policy.r36.v1"
BUNDLE_VERSION = "growth.local_media_review_bundle.r36.v1"
NORMALIZATION_VERSION = "growth.local_review_normalization.r36.v1"
DECISION_VERSION = "growth.local_offline_decision.r36.v1"
CANARY_VERSION = "growth.local_canary_evidence_envelope.r36.v1"
REPORT_VERSION = "growth.local_rehearsal_evidence.r36.report.v1"
VERIFICATION_VERSION = "growth.local_rehearsal_verification.r36.v1"

R35_SHA = "c97f987e6ae780489f2a5fef1883d5596843c64c"
R35_CI = 37247403389
R35_ARTIFACT_ID = 11319801779
R35_ARTIFACT_DIGEST = (
    "sha256:d6a01a548f05c14723a0740218ce39a8a6edee902ed66cfa716e3aa78000cf02"
)
R35_AUTHORITY_BLOB = "8f1dd5d33395c8c4e7d8fda6b2f75ee917c3d340"
R35_POLICY_BLOB = "ff6c93e42d748c254eba659c518f262a1576dabf"
R35_CONTRACT_BLOB = "b0e3a8bb4c0faf1d3081ef42d34955327533159b"

R34_SHA = "9ff243bc5ec6977bc5f0eb8f16cd5e51aa0dcdfc"
R34_CI = 37244660304
R34_ARTIFACT_ID = 11318054384
R34_ARTIFACT_DIGEST = (
    "sha256:6fc329964c14ce7c11f27fd2dd47235912470a377c6a1f5a6f3e66f4481f5ac9"
)

MEDIA_R24_SHA = "244acdf154741e669991b17df3ef2a47e2dfdfa9"
MEDIA_R24_CI = 37195239582
MEDIA_R24_ARTIFACT_ID = 11301055747
MEDIA_R24_ARTIFACT_DIGEST = (
    "sha256:fc5c9b9635d49b643e66efafe602d21ce1ef695a553f81a797bdf16d7b8cf228"
)

R30_AUTHORITY_BLOB = "57fbeb0a50045abdacd83f22a2aa7c017bb8b12d"
R30_POLICY_BLOB = "a927ff888a74652adbf8da218ef36562342b22ef"
R30_EXTERNAL_CONTRACT_BLOB = "d1f399dbc1cd3b10d52c123e24f53db83595d40a"

WAITING_PARENT_QA = "WAITING_PARENT_QA"
READY = "LOCAL_REHEARSAL_EVIDENCE_READY"
BLOCKED_INVALID_EVIDENCE = "BLOCKED_INVALID_EVIDENCE"


class R36Error(ValueError):
    pass


class AuthorityDrift(R36Error):
    pass


class ParentQARequired(R36Error):
    pass


class BundleDrift(R36Error):
    pass


class VerificationError(R36Error):
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


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R36Error(f"{field} must be lowercase SHA-256")
    return value


def _git_sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R36Error(f"{field} must be exact Git SHA")
    return value


def _artifact_digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
    ):
        raise R36Error(f"{field} must be sha256:<hex>")
    _sha(value.removeprefix("sha256:"), field)
    return value


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise R36Error(f"{field} must be positive integer")
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise R36Error(f"{field} must be non-empty string")
    return value


def _git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def default_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.local_rehearsal_evidence.r36.v1"
        / "authority.json"
    )


def default_policy() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.local_rehearsal_evidence.r36.v1"
        / "policy.json"
    )


def _expected_r35_parent() -> dict[str, Any]:
    return {
        "repository": "foto6/video3",
        "producer_sha": R35_SHA,
        "ci_run_id": R35_CI,
        "artifact_id": R35_ARTIFACT_ID,
        "artifact_name": "growth-r35-canary-evidence-registry",
        "artifact_digest": R35_ARTIFACT_DIGEST,
        "contract": "growth.canary_evidence_registry.r35.v1",
        "authority_blob": R35_AUTHORITY_BLOB,
        "policy_blob": R35_POLICY_BLOB,
        "contract_blob": R35_CONTRACT_BLOB,
    }


def _expected_r34() -> dict[str, Any]:
    return {
        "producer_sha": R34_SHA,
        "ci_run_id": R34_CI,
        "artifact_id": R34_ARTIFACT_ID,
        "artifact_digest": R34_ARTIFACT_DIGEST,
        "contract": "growth.counterfactual_policy_promotion.r34.v1",
    }


def _expected_local_semantics() -> dict[str, Any]:
    return {
        "growth_r30_authority_profile_blob": R30_AUTHORITY_BLOB,
        "growth_r30_policy_blob": R30_POLICY_BLOB,
        "growth_r30_external_contract_blob": R30_EXTERNAL_CONTRACT_BLOB,
        "media_r24": {
            "repository": "foto6/video2",
            "producer_sha": MEDIA_R24_SHA,
            "ci_run_id": MEDIA_R24_CI,
            "artifact_id": MEDIA_R24_ARTIFACT_ID,
            "artifact_digest": MEDIA_R24_ARTIFACT_DIGEST,
        },
    }


def _expected_boundary() -> dict[str, Any]:
    return {
        "local_only": True,
        "network_required_by_adapter": False,
        "browser_call": False,
        "provider_call": False,
        "live_metrics": False,
        "creator_mutation": False,
        "provider_mutation": False,
        "live_authorization": False,
        "publish": False,
        "credential_access": False,
        "merge": False,
    }


def validate_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "growth_r35_parent",
        "growth_r34_ancestry",
        "independent_parent_qa",
        "local_review_semantics",
        "boundary",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise AuthorityDrift("R36 authority fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R36 authority contract mismatch")
    if value["growth_r35_parent"] != _expected_r35_parent():
        raise AuthorityDrift("exact Growth R35 parent authority drift")
    if value["growth_r34_ancestry"] != _expected_r34():
        raise AuthorityDrift("exact Growth R34 ancestry drift")
    if value["local_review_semantics"] != _expected_local_semantics():
        raise AuthorityDrift("local R30/Media R24 semantic authority drift")
    if value["boundary"] != _expected_boundary():
        raise AuthorityDrift("R36 no-side-effect boundary drift")

    qa = value["independent_parent_qa"]
    if not isinstance(qa, Mapping) or set(qa) != {
        "disposition",
        "repository",
        "accepted_parent_sha",
        "accepted_parent_ci_run_id",
        "accepted_parent_artifact_id",
        "accepted_parent_artifact_digest",
        "producer_sha",
        "ci_run_id",
        "artifact_id",
        "artifact_digest",
        "matrix_digest",
    }:
        raise AuthorityDrift("independent parent QA fields invalid")
    if (
        qa["repository"] != "foto6/boss"
        or qa["accepted_parent_sha"] != R35_SHA
        or qa["accepted_parent_ci_run_id"] != R35_CI
        or qa["accepted_parent_artifact_id"] != R35_ARTIFACT_ID
        or qa["accepted_parent_artifact_digest"] != R35_ARTIFACT_DIGEST
    ):
        raise AuthorityDrift("independent QA does not bind exact R35 parent tuple")

    disposition = qa["disposition"]
    if disposition == "PENDING":
        if any(
            qa[field] is not None
            for field in (
                "producer_sha",
                "ci_run_id",
                "artifact_id",
                "artifact_digest",
                "matrix_digest",
            )
        ):
            raise AuthorityDrift("pending parent QA must not carry acceptance pins")
    elif disposition == "ACCEPTED":
        _git_sha(qa["producer_sha"], "independent_parent_qa.producer_sha")
        _positive(qa["ci_run_id"], "independent_parent_qa.ci_run_id")
        _positive(qa["artifact_id"], "independent_parent_qa.artifact_id")
        _artifact_digest(
            qa["artifact_digest"],
            "independent_parent_qa.artifact_digest",
        )
        _sha(qa["matrix_digest"], "independent_parent_qa.matrix_digest")
    else:
        raise AuthorityDrift("parent QA disposition must be PENDING or ACCEPTED")
    return _clone(value)


def parent_qa_accepted(value: Mapping[str, Any]) -> bool:
    parsed = validate_authority(value)
    return parsed["independent_parent_qa"]["disposition"] == "ACCEPTED"


def authority_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority(value))


def validate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "policy_version",
        "parent_gate",
        "consensus",
        "directives",
        "evidence",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise R36Error("R36 policy fields invalid")
    if value["contract_version"] != POLICY_VERSION or value["policy_version"] != 1:
        raise R36Error("R36 policy version drift")
    if value["parent_gate"] != {
        "accepted_disposition": "ACCEPTED",
        "pending_state": WAITING_PARENT_QA,
        "accepted_state": READY,
    }:
        raise R36Error("R36 parent gate policy drift")
    if value["consensus"] != {
        "delegate_contract": "growth.consensus_review_oracle.r30.v1",
        "reviewer_count": 3,
        "allowed_outcomes": ["A", "B", "tie", "insufficient_evidence"],
    }:
        raise R36Error("R36 consensus policy drift")
    directives = value["directives"]
    if not isinstance(directives, Mapping) or set(directives) != {
        "executable_operations",
        "defect_to_operation",
        "free_form_requested_edit_is_audit_only",
    }:
        raise R36Error("R36 directive policy fields invalid")
    if directives["free_form_requested_edit_is_audit_only"] is not True:
        raise R36Error("free-form edit text must remain audit-only")
    allowed = directives["executable_operations"]
    if allowed != [
        "trim",
        "cut",
        "crop_scale_reframe",
        "speed_change",
        "fade_transition",
        "text_overlay",
        "subtitles_captions",
        "audio_duck_mix",
        "intro_outro_cta",
    ]:
        raise R36Error("R36 executable operation allowlist drift")
    if any(op not in allowed for op in directives["defect_to_operation"].values()):
        raise R36Error("defect mapping contains non-allowlisted operation")
    if value["evidence"] != {
        "fixture_source_class": "SYNTHETIC_TEST",
        "local_rehearsal_is_human_ground_truth": False,
        "registry_submission_allowed": False,
        "live_authorization": False,
    }:
        raise R36Error("R36 evidence boundary policy drift")
    return _clone(value)


def policy_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_policy(value))


def validate_bundle_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"contract_version", "bundle_id", "bundle_kind", "media", "reviews", "boundary"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise BundleDrift("local bundle manifest fields invalid")
    if value["contract_version"] != BUNDLE_VERSION:
        raise BundleDrift("local bundle contract mismatch")
    _nonempty(value["bundle_id"], "bundle_id")
    if value["bundle_kind"] not in {
        "FROZEN_LOCAL_REHEARSAL_FIXTURE",
        "FROZEN_LOCAL_REHEARSAL_EVIDENCE",
    }:
        raise BundleDrift("bundle_kind invalid")
    media = value["media"]
    if not isinstance(media, Mapping) or set(media) != {
        "producer_sha",
        "ci_run_id",
        "artifact_id",
        "artifact_digest",
        "session_id",
        "session_identity",
        "review_round",
        "package_digest",
        "r29_package_digest",
        "prompt_digest",
        "sealed_mapping_digest",
        "source",
        "candidates",
    }:
        raise BundleDrift("bundle media fields invalid")
    if (
        media["producer_sha"] != MEDIA_R24_SHA
        or media["ci_run_id"] != MEDIA_R24_CI
        or media["artifact_id"] != MEDIA_R24_ARTIFACT_ID
        or media["artifact_digest"] != MEDIA_R24_ARTIFACT_DIGEST
    ):
        raise BundleDrift("bundle Media R24 authority drift")
    if media["review_round"] not in (0, 1, 2):
        raise BundleDrift("bundle review round invalid")
    for field in (
        "session_identity",
        "package_digest",
        "r29_package_digest",
        "prompt_digest",
        "sealed_mapping_digest",
    ):
        _sha(media[field], f"media.{field}")
    source = media["source"]
    if not isinstance(source, Mapping) or set(source) != {"source_id", "source_sha256"}:
        raise BundleDrift("bundle source fields invalid")
    _nonempty(source["source_id"], "source.source_id")
    _sha(source["source_sha256"], "source.source_sha256")
    candidates = media["candidates"]
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise BundleDrift("bundle must contain exact two candidates")
    normalized_candidates = []
    labels = set()
    ids = set()
    for row in candidates:
        if not isinstance(row, Mapping) or set(row) != {
            "blind_label",
            "candidate_id",
            "render_sha256",
            "render_size",
            "attachment_sha256",
            "attachment_size",
            "mime",
        }:
            raise BundleDrift("bundle candidate fields invalid")
        if row["blind_label"] not in {"A", "B"} or row["blind_label"] in labels:
            raise BundleDrift("bundle blind labels invalid")
        labels.add(row["blind_label"])
        candidate_id = _nonempty(row["candidate_id"], "candidate_id")
        if candidate_id in ids:
            raise BundleDrift("bundle candidate IDs must be distinct")
        ids.add(candidate_id)
        _sha(row["render_sha256"], "candidate.render_sha256")
        _sha(row["attachment_sha256"], "candidate.attachment_sha256")
        _positive(row["render_size"], "candidate.render_size")
        _positive(row["attachment_size"], "candidate.attachment_size")
        if row["mime"] != "video/mp4":
            raise BundleDrift("bundle candidate MIME must be video/mp4")
        normalized_candidates.append(dict(row))
    if labels != {"A", "B"}:
        raise BundleDrift("bundle exact A/B labels required")

    reviews = value["reviews"]
    if not isinstance(reviews, list) or len(reviews) != 3:
        raise BundleDrift("bundle must bind exactly three review manifests")
    seen_paths = set()
    normalized_reviews = []
    for row in reviews:
        if not isinstance(row, Mapping) or set(row) != {"path", "git_blob_sha1"}:
            raise BundleDrift("bundle review reference fields invalid")
        path = _nonempty(row["path"], "review.path")
        if "/" in path or "\\" in path or path in seen_paths:
            raise BundleDrift("review paths must be distinct local filenames")
        seen_paths.add(path)
        _git_sha(row["git_blob_sha1"], "review.git_blob_sha1")
        normalized_reviews.append(dict(row))
    if value["boundary"] != {
        "fixture": value["bundle_kind"] == "FROZEN_LOCAL_REHEARSAL_FIXTURE",
        "model_evidence": True,
        "human_ground_truth": False,
        "live_metrics": False,
        "browser_call": False,
        "provider_call": False,
        "provider_mutation": False,
    }:
        raise BundleDrift("bundle no-side-effect boundary drift")

    normalized = _clone(value)
    normalized["media"]["candidates"] = sorted(
        normalized_candidates, key=lambda row: row["blind_label"]
    )
    normalized["reviews"] = sorted(normalized_reviews, key=lambda row: row["path"])
    return normalized


def bundle_manifest_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_bundle_manifest(value))


def _candidate_from_media(media: Mapping[str, Any], label: str) -> dict[str, Any]:
    row = media["mapping_by_label"][label]
    source = row["source"]
    return {
        "blind_label": label,
        "candidate_id": row["candidateId"],
        "round": row["roundNumber"],
        "source_id": source["sourceId"],
        "source_sha256": source["sha256"],
        "source_size": source["size"],
        "render_sha256": row["render"]["sha256"],
        "render_size": row["render"]["size"],
        "attachment_sha256": row["attachment"]["sha256"],
        "attachment_size": row["attachment"]["size"],
        "attachment_mime": row["attachment"]["mimeType"],
        "render_export_digest": row["renderExport"]["digest"],
        "render_export_sha256": row["renderExport"]["fileSha256"],
    }


def _verify_bundle_against_media(
    bundle: Mapping[str, Any],
    media: Mapping[str, Any],
) -> None:
    expected = bundle["media"]
    if (
        media["session_id"] != expected["session_id"]
        or media["session_identity"] != expected["session_identity"]
        or media["review_round"] != expected["review_round"]
        or media["package_digest"] != expected["package_digest"]
        or media["r29_package_digest"] != expected["r29_package_digest"]
        or media["prompt_digest"] != expected["prompt_digest"]
        or media["sealed_mapping_digest"] != expected["sealed_mapping_digest"]
    ):
        raise BundleDrift("materialized Media package identity drift")
    source = media["source"]
    if (
        source.get("sourceId") != expected["source"]["source_id"]
        or source.get("sha256") != expected["source"]["source_sha256"]
    ):
        raise BundleDrift("materialized source lineage drift")
    actual = []
    for label in ("A", "B"):
        row = _candidate_from_media(media, label)
        actual.append(
            {
                "blind_label": label,
                "candidate_id": row["candidate_id"],
                "render_sha256": row["render_sha256"],
                "render_size": row["render_size"],
                "attachment_sha256": row["attachment_sha256"],
                "attachment_size": row["attachment_size"],
                "mime": row["attachment_mime"],
            }
        )
    if actual != expected["candidates"]:
        raise BundleDrift("materialized candidate/render/attachment identity drift")


def _verify_review_manifest_blobs(
    reviews_dir: Path,
    bundle: Mapping[str, Any],
) -> list[Path]:
    root = Path(reviews_dir).resolve()
    paths = []
    for ref in bundle["reviews"]:
        path = (root / ref["path"]).resolve()
        if path.parent != root or not path.is_file():
            raise BundleDrift("frozen review manifest missing or escapes review root")
        if _git_blob_sha1(path) != ref["git_blob_sha1"]:
            raise BundleDrift(f"frozen review manifest Git blob drift: {ref['path']}")
        paths.append(path)
    return sorted(paths)


def normalize_local_bundle(
    *,
    media_dir: Path,
    reviews_dir: Path,
    bundle_manifest: Mapping[str, Any],
    r30_profile: Mapping[str, Any],
    r30_policy: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    bundle = validate_bundle_manifest(bundle_manifest)
    r30.validate_authority_profile(r30_profile)
    r30.validate_policy(r30_policy)
    media = r30.validate_media_r24(Path(media_dir), profile=r30_profile)
    _verify_bundle_against_media(bundle, media)
    review_paths = _verify_review_manifest_blobs(Path(reviews_dir), bundle)
    reviews = [
        r30.load_review(path, media=media, profile=r30_profile)
        for path in review_paths
    ]
    consensus = r30.aggregate_reviews(
        reviews,
        media=media,
        policy=r30_policy,
        profile=r30_profile,
        growth_sha=_git_sha(growth_sha, "growth_sha"),
        growth_ci_run_id=_positive(growth_ci_run_id, "growth_ci_run_id"),
    )
    normalized_reviews = []
    for review in sorted(reviews, key=lambda row: row["conversation_id"]):
        vote = review["vote"]
        normalized_reviews.append(
            {
                "review_id": review["review_id"],
                "evidence_state": review["evidence_state"],
                "conversation_id": review["conversation_id"],
                "request_id": review["request_id"],
                "operation_id": review["operation_id"],
                "capture_digest": review["capture_digest"],
                "response_digest": review["response_digest"],
                "model_metadata": review["model_metadata"],
                "vote": vote,
                "malformed_reason": review["malformed_reason"],
            }
        )
    candidates = [_candidate_from_media(media, label) for label in ("A", "B")]
    normalization = {
        "contract_version": NORMALIZATION_VERSION,
        "normalization_digest": "",
        "bundle_id": bundle["bundle_id"],
        "bundle_manifest_digest": bundle_manifest_digest(bundle),
        "fixture": bundle["boundary"]["fixture"],
        "session_id": media["session_id"],
        "session_identity": media["session_identity"],
        "review_round": media["review_round"],
        "package_digest": media["package_digest"],
        "r29_package_digest": media["r29_package_digest"],
        "prompt_digest": media["prompt_digest"],
        "sealed_mapping_digest": media["sealed_mapping_digest"],
        "source": {
            "source_id": media["source"]["sourceId"],
            "source_sha256": media["source"]["sha256"],
            "source_size": media["source"]["size"],
        },
        "candidates": candidates,
        "reviews": normalized_reviews,
        "consensus": {
            "state": consensus["state"],
            "consensus_id": consensus["consensus_id"],
            "consensus_digest": consensus["consensus_digest"],
            "winner_blind_label": consensus["winner_blind_label"],
            "selected_candidate_id": consensus["selected_candidate_id"],
            "disagreement_score": consensus["disagreement_score"],
            "acceptance_rule": consensus["audit"]["acceptance_rule"],
            "rejection_reasons": consensus["audit"]["rejection_reasons"],
            "fixture_or_nonlive_input": consensus["fixture_or_nonlive_input"],
        },
        "authority": {
            "media_r24_sha": MEDIA_R24_SHA,
            "r30_authority_profile_digest": r30.authority_digest(r30_profile),
            "r30_aggregation_policy_digest": r30.policy_digest(r30_policy),
        },
        "boundary": {
            "local_only": True,
            "network_call": False,
            "browser_call": False,
            "provider_call": False,
            "live_metrics": False,
            "human_ground_truth": False,
            "provider_mutation": False,
        },
    }
    material = copy.deepcopy(normalization)
    material["normalization_digest"] = ""
    normalization["normalization_digest"] = sha256_json(material)
    return _clone(normalization), _clone(consensus), reviews, media


def _safe_directives(
    *,
    normalization: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    parsed = validate_policy(policy)
    mapping = parsed["directives"]["defect_to_operation"]
    rows = []
    for review in normalization["reviews"]:
        vote = review["vote"]
        if vote is None:
            continue
        for defect in vote["defects"]:
            op = mapping.get(defect["defect_category"])
            if op is None:
                continue
            rows.append(
                {
                    "candidate_id": next(
                        row["candidate_id"]
                        for row in normalization["candidates"]
                        if row["blind_label"] == defect["attachment_label"]
                    ),
                    "blind_label": defect["attachment_label"],
                    "start_ms": defect["start_ms"],
                    "end_ms": defect["end_ms"],
                    "operation": op,
                    "severity": defect["severity"],
                    "confidence": defect["confidence"],
                    "evidence": defect["evidence"],
                    "free_form_requested_edit_audit_only": defect["requested_edit"],
                    "uncertainty": defect["uncertainty"],
                }
            )
    rows.sort(
        key=lambda row: (
            row["candidate_id"],
            row["start_ms"],
            row["end_ms"],
            row["operation"],
            row["evidence"],
        )
    )
    unique = []
    seen = set()
    for row in rows:
        key = sha256_json(row)
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _decision_preview(
    normalization: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    consensus = normalization["consensus"]
    winner = None
    directives = []
    if consensus["state"] == "CONSENSUS_ACCEPTED":
        selected_id = consensus["selected_candidate_id"]
        candidate = next(
            row for row in normalization["candidates"]
            if row["candidate_id"] == selected_id
        )
        winner = {
            "candidate_id": candidate["candidate_id"],
            "blind_label": candidate["blind_label"],
            "round": candidate["round"],
            "source_sha256": candidate["source_sha256"],
            "render_sha256": candidate["render_sha256"],
            "attachment_sha256": candidate["attachment_sha256"],
            "consensus_digest": consensus["consensus_digest"],
        }
    else:
        directives = _safe_directives(
            normalization=normalization,
            policy=policy,
        )
    return {
        "consensus_state": consensus["state"],
        "winner": winner,
        "targeted_reedit_directives": directives,
        "human_review_required": consensus["state"] != "CONSENSUS_ACCEPTED",
        "rejection_reasons": consensus["rejection_reasons"],
    }


def run_local_rehearsal(
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
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    normalization, consensus, _reviews, _media = normalize_local_bundle(
        media_dir=media_dir,
        reviews_dir=reviews_dir,
        bundle_manifest=bundle_manifest,
        r30_profile=r30_profile,
        r30_policy=r30_policy,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    preview = _decision_preview(normalization, policy=policy)
    accepted = parent_qa_accepted(authority)
    state = READY if accepted else WAITING_PARENT_QA
    decision = None
    if accepted:
        decision = {
            "contract_version": DECISION_VERSION,
            "decision_digest": "",
            "state": READY,
            "effective_offline_advisory": True,
            "normalization_digest": normalization["normalization_digest"],
            "consensus_digest": consensus["consensus_digest"],
            "winner": preview["winner"],
            "targeted_reedit_directives": preview["targeted_reedit_directives"],
            "human_review_required": preview["human_review_required"],
            "rejection_reasons": preview["rejection_reasons"],
            "parent_r35": _expected_r35_parent(),
            "parent_qa": authority["independent_parent_qa"],
            "authority_manifest_digest": authority_digest(authority),
            "policy_digest": policy_digest(policy),
            "boundary": {
                "offline_only": True,
                "live_authorization": False,
                "creator_mutation": False,
                "provider_mutation": False,
                "browser_call": False,
                "provider_call": False,
                "publish": False,
                "human_ground_truth": False,
            },
        }
        material = copy.deepcopy(decision)
        material["decision_digest"] = ""
        decision["decision_digest"] = sha256_json(material)

    canary = {
        "contract_version": CANARY_VERSION,
        "envelope_digest": "",
        "state": state,
        "disposition": "ADVISORY_ONLY",
        "source_class": (
            "SYNTHETIC_TEST"
            if normalization["fixture"]
            else "OFF_POLICY_REPLAY"
        ),
        "readiness_eligible": False,
        "registry_submission_allowed": False,
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
        "publish_allowed": False,
        "credential_access_allowed": False,
        "normalization_digest": normalization["normalization_digest"],
        "decision_digest": None if decision is None else decision["decision_digest"],
        "consensus_digest": consensus["consensus_digest"],
        "bundle_manifest_digest": normalization["bundle_manifest_digest"],
        "source": normalization["source"],
        "candidates": normalization["candidates"],
        "review_round": normalization["review_round"],
        "parent_r35": _expected_r35_parent(),
        "parent_qa_disposition": authority["independent_parent_qa"]["disposition"],
        "authority_manifest_digest": authority_digest(authority),
        "policy_digest": policy_digest(policy),
        "fixture": normalization["fixture"],
        "human_ground_truth": False,
    }
    material = copy.deepcopy(canary)
    material["envelope_digest"] = ""
    canary["envelope_digest"] = sha256_json(material)

    result = {
        "contract_version": CONTRACT_VERSION,
        "status": state,
        "disposition": "ADVISORY_ONLY",
        "normalization": normalization,
        "offline_decision_preview": {
            **preview,
            "preview_only": not accepted,
        },
        "decision": decision,
        "canary_evidence_envelope": canary,
        "parent_r35_independently_accepted": accepted,
        "boundary": {
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
        },
        "result_digest": "",
    }
    digest_material = copy.deepcopy(result)
    digest_material["result_digest"] = ""
    result["result_digest"] = sha256_json(digest_material)
    return _clone(result)


def write_outputs(result: Mapping[str, Any], out_dir: Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "review-normalization.json", result["normalization"])
    _write(out / "offline-decision-preview.json", result["offline_decision_preview"])
    if result["decision"] is not None:
        _write(out / "offline-decision.json", result["decision"])
    _write(out / "canary-evidence-envelope.json", result["canary_evidence_envelope"])
    report = {
        "report_version": REPORT_VERSION,
        "status": result["status"],
        "disposition": result["disposition"],
        "result_digest": result["result_digest"],
        "normalization_digest": result["normalization"]["normalization_digest"],
        "consensus_digest": result["normalization"]["consensus"]["consensus_digest"],
        "canary_envelope_digest": result["canary_evidence_envelope"]["envelope_digest"],
        "decision_digest": (
            None if result["decision"] is None else result["decision"]["decision_digest"]
        ),
        "parent_r35_independently_accepted": result[
            "parent_r35_independently_accepted"
        ],
        "fixture": result["normalization"]["fixture"],
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
        "report_digest": "",
    }
    report_material = copy.deepcopy(report)
    report_material["report_digest"] = ""
    report["report_digest"] = sha256_json(report_material)
    _write(out / "readiness-report.json", report)
    _write(out / "result.json", result)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-r36-local-rehearsal")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--media-dir", required=True)
    run.add_argument("--reviews-dir", required=True)
    run.add_argument("--bundle-manifest", required=True)
    run.add_argument("--authority", required=True)
    run.add_argument("--policy", required=True)
    run.add_argument("--r30-authority-profile", required=True)
    run.add_argument("--r30-policy", required=True)
    run.add_argument("--out-dir", required=True)
    run.add_argument("--growth-sha", required=True)
    run.add_argument("--growth-ci-run-id", required=True, type=int)

    rehearse = sub.add_parser("rehearse-adversarial")
    rehearse.add_argument("--media-dir", required=True)
    rehearse.add_argument("--reviews-dir", required=True)
    rehearse.add_argument("--bundle-manifest", required=True)
    rehearse.add_argument("--authority", required=True)
    rehearse.add_argument("--policy", required=True)
    rehearse.add_argument("--r30-authority-profile", required=True)
    rehearse.add_argument("--r30-policy", required=True)
    rehearse.add_argument("--out-dir", required=True)
    rehearse.add_argument("--growth-sha", required=True)
    rehearse.add_argument("--growth-ci-run-id", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = Path(args.out_dir)
    try:
        if args.command == "rehearse-adversarial":
            from .local_rehearsal_evidence_r36_sim import build_rehearsal

            report = build_rehearsal(
                media_dir=Path(args.media_dir),
                reviews_dir=Path(args.reviews_dir),
                bundle_manifest=_load(Path(args.bundle_manifest)),
                authority=_load(Path(args.authority)),
                policy=_load(Path(args.policy)),
                r30_profile=_load(Path(args.r30_authority_profile)),
                r30_policy=_load(Path(args.r30_policy)),
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            out.mkdir(parents=True, exist_ok=True)
            _write(out / "adversarial-report.json", report)
            print(json.dumps(report, sort_keys=True))
            return 0

        result = run_local_rehearsal(
            media_dir=Path(args.media_dir),
            reviews_dir=Path(args.reviews_dir),
            bundle_manifest=_load(Path(args.bundle_manifest)),
            authority=_load(Path(args.authority)),
            policy=_load(Path(args.policy)),
            r30_profile=_load(Path(args.r30_authority_profile)),
            r30_policy=_load(Path(args.r30_policy)),
            growth_sha=args.growth_sha,
            growth_ci_run_id=args.growth_ci_run_id,
        )
        write_outputs(result, out)
        print(json.dumps({
            "status": result["status"],
            "result_digest": result["result_digest"],
            "normalization_digest": result["normalization"]["normalization_digest"],
            "canary_envelope_digest": result["canary_evidence_envelope"]["envelope_digest"],
            "decision_digest": None if result["decision"] is None else result["decision"]["decision_digest"],
            "parent_r35_independently_accepted": result["parent_r35_independently_accepted"],
        }, sort_keys=True))
        return 0 if result["status"] == READY else 3
    except (R36Error, r30.R30Error) as exc:
        report = {
            "report_version": REPORT_VERSION,
            "status": BLOCKED_INVALID_EVIDENCE,
            "reason": type(exc).__name__,
            "detail": str(exc),
            "disposition": "ADVISORY_ONLY",
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
            "report_digest": "",
        }
        material = copy.deepcopy(report)
        material["report_digest"] = ""
        report["report_digest"] = sha256_json(material)
        out.mkdir(parents=True, exist_ok=True)
        _write(out / "readiness-report.json", report)
        print(json.dumps(report, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
