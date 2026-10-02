from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .dynamic_review_capture_r26 import (
    BRIDGE_AUTHORITY_VERSION,
    CREATOR_ENVELOPE_VERSION,
    DYNAMIC_CAPTURE_VERSION,
    build_creator_envelope,
    convert_dynamic_capture,
    parse_bridge_authority,
    parse_dynamic_bridge_capture,
)

AUTHORITY_VERSION = "growth.exact_dynamic_authorities.r27.v1"
OPERATOR_VERSION = "growth.live_ingest_operator.r27.v1"
INDEX_VERSION = "growth.dynamic_live_review_ingest_index.r27.v1"
LEDGER_VERSION = "growth.dynamic_live_review_ingest_ledger.r27.v1"
REPORT_VERSION = "growth.live_ingest_operator.r27.readiness.v1"

GROWTH_R26_BASE_SHA = "e844ed2daaaca9e9694fe1e0fb6b8b7bfac69cbc"
MEDIA_R21_SHA = "d753e9e4c1f4448386608a1425232dbc1dba87ea"
MEDIA_R21_CI = 36994000619
MEDIA_R21_ARTIFACT_ID = 11221240371
MEDIA_R21_ARTIFACT_DIGEST = (
    "sha256:1036800923196882590ace62edbaa123ab4250b9d242e14adba909ba256ab022"
)
BRIDGE_R30_SHA = "ceaee873231a8552c5b7324083baa800eec566a8"
BRIDGE_R30_CI = 36993885456
BRIDGE_R30_CAPTURE_CONTRACT = "bridge.dynamic_existing_chat_video_review_capture.v1"
BRIDGE_R31_SHA = "104281e49122233f251c692abba726ae31cee0d5"
BRIDGE_R31_CI = 36999908386
BRIDGE_R31_RESULT_CONTRACT = "bridge.r31_live_dynamic_operator_result.v1"
CREATOR_R29_GROWTH_R26_SHA = GROWTH_R26_BASE_SHA
CREATOR_R29_GROWTH_R26_CI = 36996617627

R21_BUNDLE_FILE = "media.review_round_bundle.r21.v1.json"
R21_EVIDENCE_FILE = "media.review_round_bundle.r21.evidence.json"
R21_MAPPING_FILE = "media.review_round_sealed_mapping.r21.v1.json"
R21_HANDOFF_FILE = "media.review_round_transport_handoff.r21.v1.json"
R21_PROMPT_FILE = "model-review-prompt.txt.json"
R21_FILES = (
    R21_BUNDLE_FILE,
    R21_EVIDENCE_FILE,
    R21_MAPPING_FILE,
    R21_HANDOFF_FILE,
    R21_PROMPT_FILE,
    "review-A.mp4",
    "review-B.mp4",
)


class R27Error(ValueError):
    pass


class AuthorityDrift(R27Error):
    pass


class PackageDrift(R27Error):
    pass


class CaptureDrift(R27Error):
    pass


class ReplayConflict(R27Error):
    pass


class NonLiveCapture(R27Error):
    pass


class MalformedModelResponse(R27Error):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise R27Error(f"{field} must be non-empty string")
    return value


def _hex(value: Any, size: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != size
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R27Error(f"{field} must be lowercase {size}-hex")
    return value


def _sha256(value: Any, field: str) -> str:
    return _hex(value, 64, field)


def _sha1(value: Any, field: str) -> str:
    return _hex(value, 40, field)


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise R27Error(f"{field} must be positive integer")
    return value


def _round(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2:
        raise R27Error(f"{field} must be integer 0..2")
    return value


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _artifact_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise AuthorityDrift(f"{field} must use sha256: prefix")
    _sha256(value[7:], field)
    return value


def _forbid_branch_authority(value: Any, path: str = "authority") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"branch", "branch_name", "branchName", "branchHeadSha"}:
                raise AuthorityDrift(f"{path}.{key} is moving-ref authority")
            _forbid_branch_authority(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _forbid_branch_authority(child, f"{path}[{index}]")


def validate_authority_profile(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract_version",
        "media_r21",
        "bridge_r30",
        "moving_branch_authority",
        "human_ground_truth",
        "provider_mutation",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise AuthorityDrift("R27 authority profile fields invalid")
    if payload["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R27 authority profile contract mismatch")
    if (
        payload["moving_branch_authority"] is not False
        or payload["human_ground_truth"] is not False
        or payload["provider_mutation"] is not False
    ):
        raise AuthorityDrift("R27 authority/evidence boundary drift")
    _forbid_branch_authority(payload)

    media = payload["media_r21"]
    media_required = {
        "repository",
        "producer_sha",
        "ci_run_id",
        "round_bundle_contract",
        "transport_handoff_contract",
        "blobs",
        "actions_artifact",
        "bundles",
        "contract_file_sha256",
        "r30_transport_profile",
    }
    if not isinstance(media, Mapping) or set(media) != media_required:
        raise AuthorityDrift("Media R21 authority fields invalid")
    if (
        media["repository"] != "foto6/video2"
        or media["producer_sha"] != MEDIA_R21_SHA
        or media["ci_run_id"] != MEDIA_R21_CI
        or media["round_bundle_contract"] != "media.review_round_bundle.r21.v1"
        or media["transport_handoff_contract"]
        != "media.review_round_transport_handoff.r21.v1"
    ):
        raise AuthorityDrift("Media R21 exact producer/contract authority drift")
    _sha1(media["producer_sha"], "media_r21.producer_sha")
    expected_media_blobs = {
        "contract": "65358261775f0fcd2ab9e21f3f621aee977f29da",
        "schema": "f04925e317d849434852e6b706533f909da47b22",
        "manifest": "f76033375e7ee03b56491722e2e99e7334bd2cad",
        "implementation": "c6f556b8a177b6182d787356625094cdcad5a58e",
        "runner": "c93e9a69de31b66189031932ccfa7f2c83cf043c",
    }
    if media["blobs"] != expected_media_blobs:
        raise AuthorityDrift("Media R21 contract/schema/implementation blob drift")
    for key, value in media["blobs"].items():
        _sha1(value, f"media_r21.blobs.{key}")
    artifact = media["actions_artifact"]
    if artifact != {
        "id": MEDIA_R21_ARTIFACT_ID,
        "name": "media-r21-round-pair-review",
        "digest": MEDIA_R21_ARTIFACT_DIGEST,
    }:
        raise AuthorityDrift("Media R21 Actions artifact authority drift")
    _artifact_digest(artifact["digest"], "media_r21.actions_artifact.digest")
    _sha256(media["contract_file_sha256"], "media_r21.contract_file_sha256")

    transport = media["r30_transport_profile"]
    if transport != {
        "handoff_contract": "media.dynamic_review_handoff.v1",
        "producer_round": "R21",
        "producer_contract_name": "media.review_round_bundle.r21.v1",
        "producer_contract_schema": "media.review_round_bundle.r21.v1",
        "producer_contract_file_sha256":
            "d4887d8d1b1e9d0e396ea533162f69eb8539528de07d1e395d7b93702a390aa3",
        "prompt_format": "json_prompt_text",
        "prompt_text_field": "text",
    }:
        raise AuthorityDrift("Media R21 -> Bridge R30 transport profile drift")

    bundles = media["bundles"]
    if not isinstance(bundles, Mapping) or set(bundles) != {"initial", "round-1"}:
        raise AuthorityDrift("Media R21 exact bundle set invalid")
    for key, bundle in bundles.items():
        required_bundle = {
            "mode",
            "review_round",
            "package_digest",
            "bundle_file_sha256",
            "evidence_file_sha256",
            "transport_handoff_file_sha256",
            "prompt_digest",
            "prompt_file_sha256",
            "sealed_mapping_digest",
            "sealed_mapping_file_sha256",
            "round_lineage_digest",
            "r20_package_digest",
            "brief_lineage_digest",
            "source",
            "attachments",
        }
        if not isinstance(bundle, Mapping) or set(bundle) != required_bundle:
            raise AuthorityDrift(f"Media R21 bundle authority fields invalid: {key}")
        expected_round = 0 if key == "initial" else 1
        expected_mode = "initial" if key == "initial" else "targeted_reedit"
        if bundle["review_round"] != expected_round or bundle["mode"] != expected_mode:
            raise AuthorityDrift(f"Media R21 bundle mode/round drift: {key}")
        for digest_key in (
            "package_digest",
            "bundle_file_sha256",
            "evidence_file_sha256",
            "transport_handoff_file_sha256",
            "prompt_digest",
            "prompt_file_sha256",
            "sealed_mapping_digest",
            "sealed_mapping_file_sha256",
            "round_lineage_digest",
            "r20_package_digest",
            "brief_lineage_digest",
        ):
            _sha256(bundle[digest_key], f"media_r21.bundles.{key}.{digest_key}")
        source = bundle["source"]
        if not isinstance(source, Mapping) or set(source) != {
            "source_id",
            "sha256",
            "size",
        }:
            raise AuthorityDrift(f"Media R21 source authority invalid: {key}")
        _nonempty(source["source_id"], f"{key}.source_id")
        _sha256(source["sha256"], f"{key}.source.sha256")
        _positive(source["size"], f"{key}.source.size")
        attachments = bundle["attachments"]
        if not isinstance(attachments, list) or len(attachments) != 2:
            raise AuthorityDrift(f"Media R21 attachment authority invalid: {key}")
        labels = set()
        for row in attachments:
            if not isinstance(row, Mapping) or set(row) != {
                "blind_label",
                "path",
                "sha256",
                "size",
                "mime_type",
            }:
                raise AuthorityDrift(f"Media R21 attachment fields invalid: {key}")
            if row["blind_label"] not in {"A", "B"}:
                raise AuthorityDrift("Media R21 blind label invalid")
            labels.add(row["blind_label"])
            _nonempty(row["path"], "attachment.path")
            _sha256(row["sha256"], "attachment.sha256")
            _positive(row["size"], "attachment.size")
            if row["mime_type"] != "video/mp4":
                raise AuthorityDrift("Media R21 attachment MIME drift")
        if labels != {"A", "B"}:
            raise AuthorityDrift("Media R21 bundle must bind A/B")

    bridge = payload["bridge_r30"]
    bridge_required = {
        "repository",
        "producer_sha",
        "ci_run_id",
        "capture_contract",
        "capture_schema_id",
        "handoff_contract",
        "blobs",
        "actions_artifacts",
    }
    if not isinstance(bridge, Mapping) or set(bridge) != bridge_required:
        raise AuthorityDrift("Bridge R30 authority fields invalid")
    if (
        bridge["repository"] != "foto6/WebAIBridge"
        or bridge["producer_sha"] != BRIDGE_R30_SHA
        or bridge["ci_run_id"] != BRIDGE_R30_CI
        or bridge["capture_contract"] != BRIDGE_R30_CAPTURE_CONTRACT
        or bridge["capture_schema_id"]
        != "bridge://bridge.dynamic_existing_chat_video_review_capture.v1"
        or bridge["handoff_contract"] != "media.dynamic_review_handoff.v1"
    ):
        raise AuthorityDrift("Bridge R30 exact producer/contract authority drift")
    _sha1(bridge["producer_sha"], "bridge_r30.producer_sha")
    expected_bridge_blobs = {
        "handoff_schema": "93968dc1fb65a334493acdb587b20753f0a8494a",
        "capture_schema": "2cbe22ad6c7fe877764bad8dcfc1496aef3f3737",
        "implementation": "c5bd2f95a6d58a86cddd9a6fdc127e68e3346c20",
        "contract_test": "7e233ac8cc8107a9b0f7106d5118f02d328d40d0",
    }
    if bridge["blobs"] != expected_bridge_blobs:
        raise AuthorityDrift("Bridge R30 schema/implementation blob drift")
    for key, value in bridge["blobs"].items():
        _sha1(value, f"bridge_r30.blobs.{key}")
    artifacts = bridge["actions_artifacts"]
    expected_artifacts = [
        {
            "platform": "ubuntu-latest",
            "id": 11220801997,
            "digest":
                "sha256:5712290e088d28539aae077bdc2c9f809931c21c2ef8e7ac55175fdb4acdf840",
        },
        {
            "platform": "windows-latest",
            "id": 11220977361,
            "digest":
                "sha256:58037c4a368a04e5c890c32b528bde965b792c31c04a7461f937ff8e6dc6bbb6",
        },
    ]
    if artifacts != expected_artifacts:
        raise AuthorityDrift("Bridge R30 Actions authority drift")
    for row in artifacts:
        _artifact_digest(row["digest"], "bridge_r30.actions_artifact.digest")
    return _clone(payload)


def authority_profile_digest(profile: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority_profile(profile))


def _exact_file_set(package_dir: Path) -> None:
    missing = [name for name in R21_FILES if not (package_dir / name).is_file()]
    if missing:
        raise PackageDrift("Media R21 operator bundle missing: " + ",".join(missing))


def _validate_attachment_row(row: Mapping[str, Any], field: str) -> dict[str, Any]:
    required = {
        "blindLabel",
        "path",
        "sha256",
        "size",
        "mimeType",
        "derivative_for_model_review",
    }
    if not isinstance(row, Mapping) or set(row) != required:
        raise PackageDrift(f"{field} fields invalid")
    label = row["blindLabel"]
    if label not in {"A", "B"}:
        raise PackageDrift(f"{field}.blindLabel invalid")
    if row["mimeType"] != "video/mp4":
        raise PackageDrift(f"{field}.mimeType must be video/mp4")
    if row["derivative_for_model_review"] is not False:
        raise PackageDrift("exact R21 frozen bundles are non-derivative attachments")
    return {
        "blind_label": label,
        "path": _nonempty(row["path"], f"{field}.path"),
        "sha256": _sha256(row["sha256"], f"{field}.sha256"),
        "size": _positive(row["size"], f"{field}.size"),
        "mime_type": "video/mp4",
    }


def _mapping_entry(row: Mapping[str, Any], field: str) -> dict[str, Any]:
    required = {
        "blindLabel",
        "genericFileName",
        "role",
        "candidateId",
        "roundNumber",
        "briefLineageDigest",
        "source",
        "render",
        "renderExport",
        "renderProducerSha",
        "baselineReviewCandidateId",
        "applicationParentCandidateId",
        "parentRenderSha256",
        "growthHandoffDigest",
        "mediaApplicationDigest",
        "attachment",
    }
    if not isinstance(row, Mapping) or set(row) != required:
        raise PackageDrift(f"{field} fields invalid")
    label = row["blindLabel"]
    if label not in {"A", "B"}:
        raise PackageDrift("sealed mapping blindLabel invalid")
    source = row["source"]
    if not isinstance(source, Mapping) or set(source) != {
        "artifactPath",
        "sha256",
        "size",
        "sourceId",
    }:
        raise PackageDrift("sealed mapping source fields invalid")
    render = row["render"]
    if not isinstance(render, Mapping) or set(render) != {
        "artifactPath",
        "sha256",
        "size",
    }:
        raise PackageDrift("sealed mapping render fields invalid")
    export = row["renderExport"]
    if not isinstance(export, Mapping) or set(export) != {
        "artifactPath",
        "digest",
        "fileSha256",
    }:
        raise PackageDrift("sealed mapping renderExport fields invalid")
    attachment = row["attachment"]
    if not isinstance(attachment, Mapping) or set(attachment) != {
        "derivative",
        "derivative_for_model_review",
        "mimeType",
        "sha256",
        "size",
    }:
        raise PackageDrift("sealed mapping attachment fields invalid")
    if (
        attachment["derivative"] is not None
        or attachment["derivative_for_model_review"] is not False
        or attachment["mimeType"] != "video/mp4"
    ):
        raise PackageDrift("exact R21 sealed attachment derivative/MIME drift")
    out = {
        "blind_label": label,
        "generic_file_name": _nonempty(
            row["genericFileName"], f"{field}.genericFileName"
        ),
        "role": _nonempty(row["role"], f"{field}.role"),
        "candidate_id": _nonempty(row["candidateId"], f"{field}.candidateId"),
        "candidate_round": _round(row["roundNumber"], f"{field}.roundNumber"),
        "brief_lineage_digest": _sha256(
            row["briefLineageDigest"], f"{field}.briefLineageDigest"
        ),
        "source": {
            "source_id": _nonempty(source["sourceId"], f"{field}.source.sourceId"),
            "sha256": _sha256(source["sha256"], f"{field}.source.sha256"),
            "size": _positive(source["size"], f"{field}.source.size"),
        },
        "render": {
            "sha256": _sha256(render["sha256"], f"{field}.render.sha256"),
            "size": _positive(render["size"], f"{field}.render.size"),
        },
        "render_export": {
            "digest": _sha256(export["digest"], f"{field}.renderExport.digest"),
            "file_sha256": _sha256(
                export["fileSha256"], f"{field}.renderExport.fileSha256"
            ),
        },
        "render_producer_sha": _sha1(
            row["renderProducerSha"], f"{field}.renderProducerSha"
        ),
        "baseline_review_candidate_id": row["baselineReviewCandidateId"],
        "application_parent_candidate_id": row["applicationParentCandidateId"],
        "parent_render_sha256": row["parentRenderSha256"],
        "growth_handoff_digest": row["growthHandoffDigest"],
        "media_application_digest": row["mediaApplicationDigest"],
        "attachment": {
            "sha256": _sha256(
                attachment["sha256"], f"{field}.attachment.sha256"
            ),
            "size": _positive(
                attachment["size"], f"{field}.attachment.size"
            ),
            "mime_type": "video/mp4",
            "derivative_for_model_review": False,
            "derivative": None,
        },
    }
    for key in (
        "baseline_review_candidate_id",
        "application_parent_candidate_id",
    ):
        if out[key] is not None:
            _nonempty(out[key], f"{field}.{key}")
    for key in (
        "parent_render_sha256",
        "growth_handoff_digest",
        "media_application_digest",
    ):
        if out[key] is not None:
            _sha256(out[key], f"{field}.{key}")
    return out


def validate_media_r21_operator_bundle(
    package_dir: Path,
    *,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    profile = validate_authority_profile(profile)
    package_dir = Path(package_dir).resolve()
    if not package_dir.is_dir():
        raise PackageDrift("Media R21 operator bundle directory missing")
    _exact_file_set(package_dir)

    bundle_path = package_dir / R21_BUNDLE_FILE
    evidence_path = package_dir / R21_EVIDENCE_FILE
    mapping_path = package_dir / R21_MAPPING_FILE
    handoff_path = package_dir / R21_HANDOFF_FILE
    prompt_path = package_dir / R21_PROMPT_FILE
    bundle = _load(bundle_path)
    evidence = _load(evidence_path)
    mapping = _load(mapping_path)
    handoff = _load(handoff_path)
    prompt = _load(prompt_path)

    media = profile["media_r21"]
    if bundle.get("contractVersion") != media["round_bundle_contract"]:
        raise PackageDrift("Media R21 bundle contract mismatch")
    if bundle.get("state") != "ROUND_PAIR_PACKAGE_READY":
        raise PackageDrift("Media R21 bundle state invalid")
    if bundle.get("producer") != {
        "repository": media["repository"],
        "sha": media["producer_sha"],
    }:
        raise AuthorityDrift("Media R21 bundle producer drift")
    for flag in (
        "modelReviewPerformed",
        "liveModelReviewed",
        "providerPublish",
        "humanQuality",
    ):
        if bundle.get(flag) is not False:
            raise PackageDrift(f"Media R21 bundle {flag} boundary drift")

    package_digest = handoff.get("packageDigest")
    _sha256(package_digest, "transportHandoff.packageDigest")
    matching = [
        (key, value)
        for key, value in media["bundles"].items()
        if value["package_digest"] == package_digest
    ]
    if len(matching) != 1:
        raise PackageDrift("Media R21 package digest not frozen by authority profile")
    bundle_key, exact = matching[0]

    actual_files = {name: _file_sha(package_dir / name) for name in R21_FILES}
    expected_file_hashes = {
        R21_BUNDLE_FILE: exact["bundle_file_sha256"],
        R21_EVIDENCE_FILE: exact["evidence_file_sha256"],
        R21_MAPPING_FILE: exact["sealed_mapping_file_sha256"],
        R21_HANDOFF_FILE: exact["transport_handoff_file_sha256"],
        R21_PROMPT_FILE: exact["prompt_file_sha256"],
        "review-A.mp4": next(
            x["sha256"] for x in exact["attachments"] if x["blind_label"] == "A"
        ),
        "review-B.mp4": next(
            x["sha256"] for x in exact["attachments"] if x["blind_label"] == "B"
        ),
    }
    for name, expected_sha in expected_file_hashes.items():
        if actual_files[name] != expected_sha:
            raise PackageDrift(f"Media R21 exact file bytes drift: {name}")

    if evidence.get("evidenceVersion") != "media.review_round_bundle.r21.evidence.v1":
        raise PackageDrift("Media R21 evidence contract mismatch")
    if evidence.get("producer") != {
        "repository": media["repository"],
        "sha": media["producer_sha"],
    }:
        raise AuthorityDrift("Media R21 evidence producer drift")
    evidence_checks = {
        "packageDigest": exact["package_digest"],
        "bundleFileSha256": exact["bundle_file_sha256"],
        "sealedMappingDigest": exact["sealed_mapping_digest"],
        "sealedMappingFileSha256": exact["sealed_mapping_file_sha256"],
        "roundLineageDigest": exact["round_lineage_digest"],
        "promptDigest": exact["prompt_digest"],
        "promptFileSha256": exact["prompt_file_sha256"],
        "transportHandoffFileSha256": exact["transport_handoff_file_sha256"],
        "r20PackageDigest": exact["r20_package_digest"],
        "briefLineageDigest": exact["brief_lineage_digest"],
        "reviewRound": exact["review_round"],
        "mode": exact["mode"],
    }
    for key, expected_value in evidence_checks.items():
        if evidence.get(key) != expected_value:
            raise PackageDrift(f"Media R21 evidence drift: {key}")
    for flag in ("modelReviewPerformed", "liveModelReviewed", "providerPublish", "humanQuality"):
        if evidence.get(flag) is not False:
            raise PackageDrift(f"Media R21 evidence {flag} boundary drift")

    if not isinstance(prompt, Mapping) or set(prompt) != {"text", "digest", "bytes"}:
        raise PackageDrift("Media R21 prompt manifest fields invalid")
    prompt_text = _nonempty(prompt["text"], "prompt.text")
    prompt_digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    if (
        prompt_digest != exact["prompt_digest"]
        or prompt["digest"] != exact["prompt_digest"]
        or prompt["bytes"] != len(prompt_text.encode("utf-8"))
    ):
        raise PackageDrift("Media R21 prompt text/digest/byte drift")
    if bundle.get("prompt") != {
        "text": prompt_text,
        "digest": exact["prompt_digest"],
    }:
        raise PackageDrift("Media R21 bundle prompt binding drift")

    if (
        not isinstance(mapping, Mapping)
        or set(mapping) != {"digest", "entries"}
        or mapping["digest"] != exact["sealed_mapping_digest"]
        or sha256_json(mapping["entries"]) != mapping["digest"]
        or bundle.get("sealedMapping") != mapping
    ):
        raise PackageDrift("Media R21 sealed mapping digest/content drift")

    bundle_attachments = [
        _validate_attachment_row(row, f"bundle.attachments[{index}]")
        for index, row in enumerate(bundle.get("attachments", []))
    ]
    if len(bundle_attachments) != 2:
        raise PackageDrift("Media R21 bundle requires exactly two attachments")
    by_label = {row["blind_label"]: row for row in bundle_attachments}
    exact_by_label = {row["blind_label"]: row for row in exact["attachments"]}
    if set(by_label) != {"A", "B"}:
        raise PackageDrift("Media R21 bundle attachment labels invalid")
    for label in ("A", "B"):
        if by_label[label] != exact_by_label[label]:
            raise PackageDrift(f"Media R21 frozen attachment authority drift: {label}")
        path = package_dir / by_label[label]["path"]
        if path.stat().st_size != by_label[label]["size"]:
            raise PackageDrift(f"Media R21 attachment size drift: {label}")

    if handoff.get("contractVersion") != media["transport_handoff_contract"]:
        raise PackageDrift("Media R21 transport handoff contract mismatch")
    handoff_checks = {
        "state": "ROUND_PAIR_PACKAGE_READY",
        "packageDigest": exact["package_digest"],
        "sealedMappingDigest": exact["sealed_mapping_digest"],
        "promptText": prompt_text,
        "promptDigest": exact["prompt_digest"],
        "promptBytes": len(prompt_text.encode("utf-8")),
    }
    for key, expected_value in handoff_checks.items():
        if handoff.get(key) != expected_value:
            raise PackageDrift(f"Media R21 transport handoff drift: {key}")
    for flag in ("modelReviewPerformed", "liveModelReviewed", "providerPublish", "humanQuality"):
        if handoff.get(flag) is not False:
            raise PackageDrift(f"Media R21 transport {flag} boundary drift")
    if handoff.get("attachments") != bundle.get("attachments"):
        raise PackageDrift("Media R21 transport attachment binding drift")

    source = bundle.get("source")
    expected_source = {
        "sourceId": exact["source"]["source_id"],
        "sha256": exact["source"]["sha256"],
        "size": exact["source"]["size"],
    }
    if source != expected_source:
        raise PackageDrift("Media R21 source lineage drift")
    if bundle.get("briefLineageDigest") != exact["brief_lineage_digest"]:
        raise PackageDrift("Media R21 brief lineage drift")
    if bundle.get("reviewRound") != exact["review_round"] or bundle.get("mode") != exact["mode"]:
        raise PackageDrift("Media R21 review round/mode drift")
    if (
        handoff.get("sourceLineage")
        != {
            **expected_source,
            "briefLineageDigest": exact["brief_lineage_digest"],
        }
    ):
        raise PackageDrift("Media R21 transport source lineage drift")
    round_lineage = handoff.get("roundLineage")
    if round_lineage != {
        "mode": exact["mode"],
        "reviewRound": exact["review_round"],
        "digest": exact["round_lineage_digest"],
    }:
        raise PackageDrift("Media R21 transport round lineage drift")

    entries_raw = mapping["entries"]
    if not isinstance(entries_raw, list) or len(entries_raw) != 2:
        raise PackageDrift("Media R21 sealed mapping must contain two entries")
    entries = [
        _mapping_entry(row, f"sealedMapping.entries[{index}]")
        for index, row in enumerate(entries_raw)
    ]
    entry_by_label = {row["blind_label"]: row for row in entries}
    if set(entry_by_label) != {"A", "B"} or len({x["candidate_id"] for x in entries}) != 2:
        raise PackageDrift("Media R21 sealed mapping A/B candidate identity invalid")
    for label in ("A", "B"):
        row = entry_by_label[label]
        attachment = by_label[label]
        if (
            row["generic_file_name"] != attachment["path"]
            or row["attachment"]["sha256"] != attachment["sha256"]
            or row["attachment"]["size"] != attachment["size"]
            or row["render"]["sha256"] != attachment["sha256"]
            or row["render"]["size"] != attachment["size"]
            or row["source"]
            != {
                "source_id": exact["source"]["source_id"],
                "sha256": exact["source"]["sha256"],
                "size": exact["source"]["size"],
            }
            or row["brief_lineage_digest"] != exact["brief_lineage_digest"]
            or row["render_producer_sha"] != MEDIA_R21_SHA
        ):
            raise PackageDrift(f"Media R21 mapping lineage drift: {label}")

    if exact["mode"] == "initial":
        if any(row["candidate_round"] != 0 or row["role"] != "initial_candidate" for row in entries):
            raise PackageDrift("Media R21 initial candidate round/role drift")
        if bundle.get("roundLineage") is not None:
            raise PackageDrift("Media R21 initial bundle roundLineage must be null")
    else:
        if sorted(row["candidate_round"] for row in entries) != [0, 1]:
            raise PackageDrift("Media R21 targeted candidate rounds drift")
        roles = {row["role"]: row for row in entries}
        if set(roles) != {"baseline", "challenger"}:
            raise PackageDrift("Media R21 targeted roles drift")
        baseline = roles["baseline"]
        challenger = roles["challenger"]
        if (
            challenger["baseline_review_candidate_id"] != baseline["candidate_id"]
            or challenger["parent_render_sha256"] != baseline["render"]["sha256"]
            or challenger["growth_handoff_digest"] is None
            or challenger["media_application_digest"] is None
        ):
            raise PackageDrift("Media R21 targeted parent/child lineage drift")
        if not isinstance(bundle.get("roundLineage"), Mapping):
            raise PackageDrift("Media R21 targeted roundLineage missing")

    media_authority = {
        "contract_version": "growth.media_dynamic_review_authority.r26.v1",
        "repository": media["repository"],
        "producer_sha": media["producer_sha"],
        "ci_run_id": media["ci_run_id"],
        # Canonical R26/R29 envelope compatibility marker. The exact source
        # contract remains media.review_round_bundle.r21.v1 in this R27 profile.
        "package_contract": "media.dynamic_review_package.r21.v1",
        "contract_blob_sha1": media["blobs"]["contract"],
        "schema_blob_sha1": media["blobs"]["schema"],
        "implementation_blob_sha1": media["blobs"]["implementation"],
        "artifact_id": media["actions_artifact"]["id"],
        "artifact_name": media["actions_artifact"]["name"],
        "artifact_digest": media["actions_artifact"]["digest"],
        "package_digest": exact["package_digest"],
        "package_file_sha256": exact["bundle_file_sha256"],
        "evidence_file_sha256": exact["evidence_file_sha256"],
        "prompt_digest": exact["prompt_digest"],
        "prompt_file_sha256": exact["prompt_file_sha256"],
        "sealed_mapping_digest": exact["sealed_mapping_digest"],
        "sealed_mapping_file_sha256": exact["sealed_mapping_file_sha256"],
        "review_round": exact["review_round"],
        "source": exact["source"],
        "attachments": [
            {
                "blind_label": row["blind_label"],
                "generic_file_name": row["path"],
                "sha256": row["sha256"],
                "size": row["size"],
                "mime_type": row["mime_type"],
            }
            for row in sorted(bundle_attachments, key=lambda x: x["blind_label"])
        ],
    }
    normalized_mapping = {}
    for label, row in entry_by_label.items():
        normalized_mapping[label] = {
            "blind_label": label,
            "candidate_id": row["candidate_id"],
            "generic_file_name": row["generic_file_name"],
            "candidate_round": row["candidate_round"],
            "render_producer_sha": row["render_producer_sha"],
            "source": row["source"],
            "render": row["render"],
            "render_export": row["render_export"],
            "attachment": row["attachment"],
            "editorial_application": {
                "role": row["role"],
                "baseline_review_candidate_id": row[
                    "baseline_review_candidate_id"
                ],
                "application_parent_candidate_id": row[
                    "application_parent_candidate_id"
                ],
                "parent_render_sha256": row["parent_render_sha256"],
                "growth_handoff_digest": row["growth_handoff_digest"],
                "media_application_digest": row["media_application_digest"],
            },
        }
    normalized = {
        "authority": media_authority,
        "package_digest": exact["package_digest"],
        "prompt_digest": exact["prompt_digest"],
        "sealed_mapping_digest": exact["sealed_mapping_digest"],
        "review_round": exact["review_round"],
        "source": exact["source"],
        "request_id": "",
        "idempotency_key": "",
        "attachments_by_label": {
            row["blind_label"]: {
                "blind_label": row["blind_label"],
                "generic_file_name": row["path"],
                "mime_type": row["mime_type"],
                "sha256": row["sha256"],
                "size": row["size"],
                "derivative": None,
            }
            for row in bundle_attachments
        },
        "mapping_by_label": normalized_mapping,
        "prompt_manifest": {
            "promptText": prompt_text,
            "promptDigest": exact["prompt_digest"],
        },
    }
    return _clone(
        {
            "bundle_key": bundle_key,
            "package_dir": str(package_dir),
            "profile_digest": authority_profile_digest(profile),
            "bundle": bundle,
            "evidence": evidence,
            "transport_handoff": handoff,
            "sealed_mapping": mapping,
            "prompt": prompt,
            "file_sha256": actual_files,
            "exact": exact,
            "normalized_media_package": normalized,
        }
    )


def _bridge_authority(profile: Mapping[str, Any]) -> dict[str, Any]:
    bridge = profile["bridge_r30"]
    return parse_bridge_authority(
        {
            "contract_version": BRIDGE_AUTHORITY_VERSION,
            "repository": bridge["repository"],
            "producer_sha": bridge["producer_sha"],
            "ci_run_id": bridge["ci_run_id"],
            "capture_contract": bridge["capture_contract"],
            "capture_schema_id": bridge["capture_schema_id"],
            "contract_blob_sha1": bridge["blobs"]["handoff_schema"],
            "schema_blob_sha1": bridge["blobs"]["capture_schema"],
            "implementation_blob_sha1": bridge["blobs"]["implementation"],
        }
    )


def _reject_fixture_markers(capture: Mapping[str, Any]) -> None:
    direct = (
        "fixture",
        "fixture_only",
        "fixtureOnly",
        "fakeCdp",
        "fake_cdp",
        "synthetic",
        "rehearsalOnly",
    )
    for key in direct:
        if capture.get(key) is True:
            raise NonLiveCapture(f"fixture/fake-CDP marker rejected: {key}")
    live = capture.get("liveEvidence")
    if isinstance(live, Mapping):
        for key in direct:
            if live.get(key) is True:
                raise NonLiveCapture(f"fixture/fake-CDP liveEvidence rejected: {key}")
        if live.get("realBrowserUsed") is False:
            raise NonLiveCapture("fake-CDP/non-browser capture rejected")
    if capture.get("realBrowserUsed") is False:
        raise NonLiveCapture("fake-CDP/non-browser capture rejected")


def _source_lineage_value(
    source_lineage: Mapping[str, Any],
    *names: str,
) -> Any:
    values = [source_lineage[name] for name in names if name in source_lineage]
    if not values:
        return None
    if any(value != values[0] for value in values[1:]):
        raise CaptureDrift("Bridge source lineage aliases conflict")
    return values[0]


def _bridge_transport_digest(
    *,
    producer: Mapping[str, Any],
    prompt_file_sha256: str,
    prompt_digest: str,
    attachments: Sequence[Mapping[str, Any]],
    sealed_mapping_digest: str,
    source_lineage: Mapping[str, Any],
    prompt_format: str,
) -> str:
    stable = {
        "contract": "media.dynamic_review_handoff.v1",
        "producer": {
            "repository": producer["repository"],
            "sha": producer["sha"],
            "round": producer["round"],
            "contract": {
                "name": producer["contractName"],
                "schemaVersion": producer["contractSchema"],
                "blobSha256": producer["contractBlobSha256"],
            },
        },
        "prompt": {
            "fileSha256": prompt_file_sha256,
            "textSha256": prompt_digest,
            "format": prompt_format,
        },
        "attachments": sorted(
            [
                {
                    "blindLabel": row["blind_label"],
                    "blindedName": row["path"],
                    "size": row["size"],
                    "sha256": row["sha256"],
                    "mime": row["mime_type"],
                }
                for row in attachments
            ],
            key=lambda row: row["blindLabel"],
        ),
        "sealedMappingDigest": sealed_mapping_digest,
        "sourceLineage": _clone(source_lineage),
    }
    return sha256_json(stable)


def validate_bridge_r30_live_capture(
    capture: Mapping[str, Any],
    *,
    media: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    profile = validate_authority_profile(profile)
    if not isinstance(capture, Mapping):
        raise CaptureDrift("Bridge R30 capture must be object")
    if capture.get("contract") != BRIDGE_R30_CAPTURE_CONTRACT:
        raise NonLiveCapture("only Bridge R30 genuine dynamic capture contract accepted")
    _reject_fixture_markers(capture)
    disposition = capture.get("disposition")
    if disposition == "MALFORMED_MODEL_RESPONSE":
        raise MalformedModelResponse("Bridge capture disposition MALFORMED_MODEL_RESPONSE")
    if disposition != "LIVE_REVIEW_PASS":
        raise NonLiveCapture(
            f"Bridge capture disposition is not LIVE_REVIEW_PASS: {disposition}"
        )
    if capture.get("model_evidence") is not True:
        raise NonLiveCapture("Bridge capture requires model_evidence=true")
    if capture.get("human_ground_truth") is not False:
        raise NonLiveCapture("Bridge capture must preserve human_ground_truth=false")
    if capture.get("human_label", False) is not False:
        raise NonLiveCapture("Bridge capture must preserve human_label=false")
    if capture.get("live_platform_evidence", False) is not False:
        raise NonLiveCapture("Bridge review cannot become live platform evidence")

    for field in ("requestId", "operationId", "conversationId", "conversationUrl"):
        _nonempty(capture.get(field), f"capture.{field}")
    response_text = _nonempty(capture.get("responseText"), "capture.responseText")
    response_digest = _sha256(capture.get("responseDigest"), "capture.responseDigest")
    if hashlib.sha256(response_text.encode("utf-8")).hexdigest() != response_digest:
        raise CaptureDrift("assistant-response digest mismatch")

    exact = media["exact"]
    dynamic = capture.get("dynamicPackage")
    required_dynamic = {
        "handoffContract",
        "handoffSha256",
        "packageDigest",
        "sealedMappingDigestRef",
        "producer",
        "sourceLineage",
        "sourceBindingFingerprint",
    }
    if not isinstance(dynamic, Mapping) or not required_dynamic.issubset(set(dynamic)):
        raise CaptureDrift("Bridge R30 dynamicPackage binding incomplete")
    if dynamic["handoffContract"] != "media.dynamic_review_handoff.v1":
        raise CaptureDrift("Bridge R30 dynamic handoff contract drift")
    _sha256(dynamic["handoffSha256"], "dynamicPackage.handoffSha256")
    bridge_package_digest = _sha256(
        dynamic["packageDigest"], "dynamicPackage.packageDigest"
    )
    if dynamic["sealedMappingDigestRef"] != exact["sealed_mapping_digest"]:
        raise CaptureDrift("Bridge R30 sealed mapping reference drift")
    _sha256(
        dynamic["sourceBindingFingerprint"],
        "dynamicPackage.sourceBindingFingerprint",
    )

    producer = dynamic["producer"]
    required_producer = {
        "repository",
        "sha",
        "round",
        "contractName",
        "contractSchema",
        "contractBlobSha256",
    }
    if not isinstance(producer, Mapping) or not required_producer.issubset(set(producer)):
        raise CaptureDrift("Bridge R30 Media producer binding incomplete")
    transport = profile["media_r21"]["r30_transport_profile"]
    if (
        producer["repository"] != profile["media_r21"]["repository"]
        or producer["sha"] != profile["media_r21"]["producer_sha"]
        or producer["round"] != transport["producer_round"]
        or producer["contractName"] != transport["producer_contract_name"]
        or producer["contractSchema"] != transport["producer_contract_schema"]
        or producer["contractBlobSha256"]
        != transport["producer_contract_file_sha256"]
    ):
        raise AuthorityDrift("Bridge R30 Media R21 producer/contract bytes drift")

    source_lineage = dynamic["sourceLineage"]
    if not isinstance(source_lineage, Mapping):
        raise CaptureDrift("Bridge R30 sourceLineage must be object")
    expected_source = exact["source"]
    source_id = _source_lineage_value(source_lineage, "sourceId", "source_id")
    source_sha = _source_lineage_value(
        source_lineage, "sha256", "sourceSha256", "source_sha256"
    )
    source_size = _source_lineage_value(
        source_lineage, "size", "sourceSize", "source_size"
    )
    brief = _source_lineage_value(
        source_lineage, "briefLineageDigest", "brief_lineage_digest"
    )
    review_round = _source_lineage_value(
        source_lineage, "reviewRound", "review_round"
    )
    round_digest = _source_lineage_value(
        source_lineage, "roundLineageDigest", "round_lineage_digest"
    )
    if (
        source_id != expected_source["source_id"]
        or source_sha != expected_source["sha256"]
        or source_size != expected_source["size"]
        or brief != exact["brief_lineage_digest"]
        or review_round != exact["review_round"]
        or round_digest != exact["round_lineage_digest"]
    ):
        raise CaptureDrift("Bridge R30 source/round lineage drift")
    if "mediaPackageDigest" in source_lineage and (
        source_lineage["mediaPackageDigest"] != exact["package_digest"]
    ):
        raise CaptureDrift("Bridge R30 Media R21 package lineage drift")

    transport_digest = _bridge_transport_digest(
        producer=producer,
        prompt_file_sha256=exact["prompt_file_sha256"],
        prompt_digest=exact["prompt_digest"],
        attachments=exact["attachments"],
        sealed_mapping_digest=exact["sealed_mapping_digest"],
        source_lineage=source_lineage,
        prompt_format=transport["prompt_format"],
    )
    if transport_digest != bridge_package_digest:
        raise CaptureDrift("Bridge R30 dynamic package digest mismatch")

    prompt_digest = _sha256(capture.get("promptDigest"), "capture.promptDigest")
    if prompt_digest != exact["prompt_digest"]:
        raise CaptureDrift("Bridge R30 prompt digest drift")
    attachments = capture.get("attachments")
    if not isinstance(attachments, list) or len(attachments) != 2:
        raise CaptureDrift("Bridge R30 capture requires exact A/B attachments")
    expected_by_label = {row["blind_label"]: row for row in exact["attachments"]}
    seen = set()
    for index, row in enumerate(attachments):
        if not isinstance(row, Mapping):
            raise CaptureDrift(f"capture.attachments[{index}] invalid")
        label = row.get("blindLabel")
        name = row.get("name") or row.get("blindedName")
        if label not in {"A", "B"}:
            matches = [
                key
                for key, expected in expected_by_label.items()
                if expected["path"] == name
            ]
            if len(matches) != 1:
                raise CaptureDrift("Bridge R30 capture attachment label unresolved")
            label = matches[0]
        if label in seen:
            raise CaptureDrift("duplicate Bridge R30 capture attachment label")
        seen.add(label)
        expected = expected_by_label[label]
        if (
            name != expected["path"]
            or row.get("sha256") != expected["sha256"]
            or row.get("size") != expected["size"]
        ):
            raise CaptureDrift(f"Bridge R30 stale attachment bytes: {label}")
        if "mimeType" in row and row["mimeType"] != "video/mp4":
            raise CaptureDrift(f"Bridge R30 attachment MIME drift: {label}")
    if seen != {"A", "B"}:
        raise CaptureDrift("Bridge R30 capture does not bind A/B")

    normalized_media = copy.deepcopy(media["normalized_media_package"])
    normalized_media["request_id"] = capture["requestId"]
    normalized_media["idempotency_key"] = (
        f"r27:{capture['conversationId']}:{capture['requestId']}:"
        f"{exact['package_digest']}"
    )
    capture_for_r26 = copy.deepcopy(capture)
    # Bridge R30 inherits R29's frozen-R18 promptFileSha256 field even though
    # the dynamicPackage fingerprint binds the exact R21 prompt file. R27
    # validates the R21 prompt file/digest above, then removes only that stale
    # inherited compatibility field before invoking the generic R26 parser.
    capture_for_r26.pop("promptFileSha256", None)
    parsed = parse_dynamic_bridge_capture(
        capture_for_r26,
        media_package=normalized_media,
        bridge_authority=_bridge_authority(profile),
    )
    return _clone(
        {
            "parsed_capture": parsed,
            "normalized_media_package": normalized_media,
            "bridge_transport_package_digest": bridge_package_digest,
            "bridge_handoff_sha256": dynamic["handoffSha256"],
            "source_binding_fingerprint": dynamic["sourceBindingFingerprint"],
        }
    )


def _envelope_filename(candidate_id: str) -> str:
    short = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:16]
    return f"creator-dynamic-review-{short}.json"


def _index_material(
    *,
    media: Mapping[str, Any],
    ingest: Mapping[str, Any] | None,
    envelopes: Mapping[str, Mapping[str, Any]] | None,
    growth_sha: str,
    growth_ci_run_id: int,
    authority_digest: str,
    state: str,
    live_capture_gate: str,
    new_effect: bool,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    pairwise = None
    candidates: dict[str, Any] = {}
    selected_result = None
    capture = None
    if ingest is not None:
        pairwise = {
            "model_facing_selection": ingest["unblinding"]["model_facing_selection"],
            "selected_candidate_id": ingest["unblinding"]["selected_candidate_id"],
            "pairwise_output_digest": ingest["pairwise_output"]["output_digest"],
            "pairwise_comparison_id": ingest["pairwise_output"]["comparison_id"],
            "rationale": ingest["pairwise_output"]["rationale"],
            "confidence": ingest["pairwise_output"]["confidence"],
            "uncertainty": ingest["pairwise_output"]["uncertainty"],
        }
        capture = {
            "capture_id": ingest["capture"]["capture_id"],
            "capture_digest": ingest["capture"]["capture_digest"],
            "assistant_response_digest": ingest["capture"][
                "assistant_response_digest"
            ],
            "conversation": ingest["capture"]["conversation"],
        }
        for candidate_id, envelope in sorted((envelopes or {}).items()):
            candidate = envelope["candidate"]
            candidates[candidate_id] = {
                "envelope_file": _envelope_filename(candidate_id),
                "envelope_digest": envelope["envelope_digest"],
                "handoff_digest": candidate["handoff_digest"],
                "state": candidate["state"],
                "candidate_round": candidate["candidate_round"],
                "source_id": candidate["source_id"],
                "source_sha256": candidate["source_sha256"],
                "render_sha256": candidate["render_sha256"],
                "render_size": candidate["render_size"],
                "attachment_sha256": candidate["attachment_sha256"],
                "attachment_size": candidate["attachment_size"],
                "attachment_mime_type": candidate["attachment_mime_type"],
            }
        selected = ingest["unblinding"]["selected_candidate_id"]
        if selected is not None and selected in candidates:
            selected_result = {
                "candidate_id": selected,
                **candidates[selected],
            }
    index = {
        "contract_version": INDEX_VERSION,
        "index_id": "",
        "index_digest": "",
        "state": state,
        "live_capture_gate": live_capture_gate,
        "new_ingest_effect": bool(new_effect),
        "growth_r27": {
            "repository": "foto6/video3",
            "producer_sha": _sha1(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "starting_r26_sha": GROWTH_R26_BASE_SHA,
            "operator_contract": OPERATOR_VERSION,
            "authority_profile_digest": authority_digest,
        },
        "media_r21": {
            "producer_sha": MEDIA_R21_SHA,
            "ci_run_id": MEDIA_R21_CI,
            "bundle_key": media["bundle_key"],
            "package_digest": media["exact"]["package_digest"],
            "bundle_file_sha256": media["exact"]["bundle_file_sha256"],
            "sealed_mapping_digest": media["exact"]["sealed_mapping_digest"],
            "prompt_digest": media["exact"]["prompt_digest"],
            "review_round": media["exact"]["review_round"],
            "round_lineage_digest": media["exact"]["round_lineage_digest"],
            "source": media["exact"]["source"],
        },
        "bridge_r30": {
            "producer_sha": BRIDGE_R30_SHA,
            "ci_run_id": BRIDGE_R30_CI,
            "capture_contract": BRIDGE_R30_CAPTURE_CONTRACT,
        },
        "capture": capture,
        "pairwise": pairwise,
        "selected_result": selected_result,
        "candidates": candidates,
        "rejection_reason": rejection_reason,
        "evidence_boundary": {
            "model_evidence": ingest is not None,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "human_parity_inferred": False,
            "fixture_promoted": False,
            "provider_mutation": False,
            "browser_mutation": False,
            "creator_mutation": False,
            "media_mutation": False,
        },
    }
    index["index_id"] = "gr27idx1:" + sha256_json(
        {
            "growth_sha": growth_sha,
            "authority_digest": authority_digest,
            "media_package_digest": media["exact"]["package_digest"],
            "capture_digest": (
                None if capture is None else capture["capture_digest"]
            ),
            "state": state,
        }
    )
    material = copy.deepcopy(index)
    material["index_digest"] = ""
    index["index_digest"] = sha256_json(material)
    return _clone(index)


class R27Ledger:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory).resolve()
        self.path = self.directory / "r27-live-ingest-ledger.json"
        self.records: dict[str, Any] = {}
        self.requests: dict[str, str] = {}
        if self.path.exists():
            raw = _load(self.path)
            if not isinstance(raw, Mapping) or set(raw) != {
                "version",
                "records",
                "requests",
            }:
                raise ReplayConflict("R27 ledger fields invalid")
            if raw["version"] != LEDGER_VERSION:
                raise ReplayConflict("R27 ledger version mismatch")
            self.records = dict(raw["records"])
            self.requests = dict(raw["requests"])

    def _persist(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        body = {
            "version": LEDGER_VERSION,
            "records": self.records,
            "requests": self.requests,
        }
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(body, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def check_or_record(
        self,
        *,
        identity: str,
        request_identity: str,
        fingerprint: str,
        output_index: Mapping[str, Any],
    ) -> bool:
        prior = self.records.get(identity)
        if prior is not None:
            if prior["fingerprint"] != fingerprint:
                raise ReplayConflict(
                    "same capture/package identity changed response/package/mapping/capture bytes"
                )
            if prior["index_digest"] != output_index["index_digest"]:
                raise ReplayConflict("exact replay would change coordinator index")
            return False
        prior_request = self.requests.get(request_identity)
        if prior_request is not None and prior_request != fingerprint:
            raise ReplayConflict(
                "same conversation/request/package has conflicting capture"
            )
        self.records[identity] = {
            "fingerprint": fingerprint,
            "index_digest": output_index["index_digest"],
            "capture_digest": output_index["capture"]["capture_digest"],
            "assistant_response_digest": output_index["capture"][
                "assistant_response_digest"
            ],
            "media_package_digest": output_index["media_r21"]["package_digest"],
        }
        self.requests[request_identity] = fingerprint
        self._persist()
        return True


def _write_outputs(
    *,
    out_dir: Path,
    index: Mapping[str, Any],
    envelopes: Mapping[str, Mapping[str, Any]],
    effect: bool,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not effect:
        existing_index = out_dir / "growth.dynamic_live_review_ingest_index.r27.v1.json"
        if not existing_index.is_file():
            raise ReplayConflict("replay is no-op but prior coordinator index is missing")
        prior = _load(existing_index)
        if prior.get("index_digest") != index["index_digest"]:
            raise ReplayConflict("replay coordinator index bytes/digest drift")
        for candidate_id, envelope in envelopes.items():
            path = out_dir / _envelope_filename(candidate_id)
            if not path.is_file():
                raise ReplayConflict("replay is no-op but prior Creator envelope is missing")
            prior_envelope = _load(path)
            if prior_envelope.get("envelope_digest") != envelope["envelope_digest"]:
                raise ReplayConflict("replay Creator envelope digest drift")
        return
    for candidate_id, envelope in envelopes.items():
        (out_dir / _envelope_filename(candidate_id)).write_text(
            json.dumps(envelope, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (out_dir / "growth.dynamic_live_review_ingest_index.r27.v1.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def ingest_live(
    *,
    package_dir: Path,
    authority_profile: Mapping[str, Any],
    capture: Mapping[str, Any],
    ledger_dir: Path,
    out_dir: Path,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    profile = validate_authority_profile(authority_profile)
    media = validate_media_r21_operator_bundle(package_dir, profile=profile)
    validated_capture = validate_bridge_r30_live_capture(
        capture,
        media=media,
        profile=profile,
    )
    ingest = convert_dynamic_capture(
        media_package=validated_capture["normalized_media_package"],
        parsed_capture=validated_capture["parsed_capture"],
    )
    if ingest["evidence_state"] != "LIVE_REVIEW_INGESTED":
        raise NonLiveCapture("R26 conversion did not produce LIVE_REVIEW_INGESTED")
    if (
        ingest["evidence_boundary"]["human_ground_truth"] is not False
        or ingest["authority"]["human_rating_evidence"] is not False
        or ingest["authority"]["provider_mutation"] is not False
    ):
        raise NonLiveCapture("dynamic ingest evidence boundary drift")

    envelopes = {
        candidate_id: build_creator_envelope(
            ingest_result=ingest,
            candidate_id=candidate_id,
            growth_producer_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        )
        for candidate_id in sorted(ingest["dynamic_handoffs"])
    }
    authority_digest = authority_profile_digest(profile)
    provisional = _index_material(
        media=media,
        ingest=ingest,
        envelopes=envelopes,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
        authority_digest=authority_digest,
        state="LIVE_REVIEW_INGESTED",
        live_capture_gate="SATISFIED_GENUINE_DYNAMIC_CAPTURE",
        new_effect=True,
    )
    conversation = ingest["capture"]["conversation"]
    identity = (
        ingest["capture"]["capture_id"]
        + "\n"
        + ingest["package_digest"]
    )
    request_identity = (
        conversation["conversation_id"]
        + "\n"
        + conversation["request_id"]
        + "\n"
        + ingest["package_digest"]
    )
    fingerprint = sha256_json(
        {
            "capture_payload": capture,
            "capture_digest": ingest["capture"]["capture_digest"],
            "assistant_response_digest": ingest["capture"][
                "assistant_response_digest"
            ],
            "media_package_digest": media["exact"]["package_digest"],
            "bundle_file_sha256": media["exact"]["bundle_file_sha256"],
            "mapping_file_sha256": media["exact"]["sealed_mapping_file_sha256"],
            "bridge_transport_package_digest": validated_capture[
                "bridge_transport_package_digest"
            ],
            "bridge_handoff_sha256": validated_capture["bridge_handoff_sha256"],
            "authority_profile_digest": authority_digest,
            "growth_sha": growth_sha,
            "growth_ci_run_id": growth_ci_run_id,
        }
    )
    ledger = R27Ledger(ledger_dir)
    effect = ledger.check_or_record(
        identity=identity,
        request_identity=request_identity,
        fingerprint=fingerprint,
        output_index=provisional,
    )
    index = copy.deepcopy(provisional)
    index["new_ingest_effect"] = effect
    material = copy.deepcopy(index)
    material["index_digest"] = ""
    # Keep the logical index digest stable across exact replay: new_ingest_effect
    # is operational metadata and is excluded from the logical digest.
    material["new_ingest_effect"] = True
    index["index_digest"] = sha256_json(material)
    if index["index_digest"] != provisional["index_digest"]:
        raise ReplayConflict("R27 index digest construction drift")
    _write_outputs(
        out_dir=out_dir,
        index=index,
        envelopes=envelopes,
        effect=effect,
    )
    return _clone(index)


def readiness(
    *,
    package_dir: Path,
    authority_profile: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    profile = validate_authority_profile(authority_profile)
    media = validate_media_r21_operator_bundle(package_dir, profile=profile)
    index = _index_material(
        media=media,
        ingest=None,
        envelopes=None,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
        authority_digest=authority_profile_digest(profile),
        state="SOURCE_READY",
        live_capture_gate="BLOCKED_WAITING_GENUINE_CAPTURE",
        new_effect=False,
    )
    return index


def _write_report(
    *,
    path: Path,
    state: str,
    index: Mapping[str, Any] | None,
    error: Exception | None = None,
) -> dict[str, Any]:
    report = {
        "report_version": REPORT_VERSION,
        "state": state,
        "source_ready": index is not None,
        "live_review_ingested": (
            index is not None and index.get("state") == "LIVE_REVIEW_INGESTED"
        ),
        "index_digest": None if index is None else index["index_digest"],
        "authority_profile_digest": (
            None
            if index is None
            else index["growth_r27"]["authority_profile_digest"]
        ),
        "media_r21_sha": MEDIA_R21_SHA,
        "media_r21_ci_run_id": MEDIA_R21_CI,
        "bridge_r30_sha": BRIDGE_R30_SHA,
        "bridge_r30_ci_run_id": BRIDGE_R30_CI,
        "error_type": None if error is None else type(error).__name__,
        "error_detail": None if error is None else str(error),
        "provider_mutation": False,
        "browser_mutation": False,
        "creator_mutation": False,
        "media_mutation": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
    }
    report["report_digest"] = sha256_json(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-live-ingest-r27")
    parser.add_argument("--package-dir", required=True)
    parser.add_argument("--authority-profile", required=True)
    parser.add_argument("--capture")
    parser.add_argument("--ledger-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--growth-sha", required=True)
    parser.add_argument("--growth-ci-run-id", type=int, required=True)
    parser.add_argument("--report", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    package_dir = Path(args.package_dir)
    profile = _load(Path(args.authority_profile))
    out_dir = Path(args.out_dir)
    report_path = Path(args.report)

    if not args.capture:
        try:
            index = readiness(
                package_dir=package_dir,
                authority_profile=profile,
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "growth.dynamic_live_review_ingest_index.r27.v1.json").write_text(
                json.dumps(index, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = _write_report(
                path=report_path,
                state="SOURCE_READY",
                index=index,
            )
            print(json.dumps(report, sort_keys=True))
            return 0
        except Exception as exc:
            report = _write_report(
                path=report_path,
                state="BLOCKED_WAITING_GENUINE_CAPTURE",
                index=None,
                error=exc,
            )
            print(json.dumps(report, sort_keys=True))
            return 2

    capture = _load(Path(args.capture))
    try:
        index = ingest_live(
            package_dir=package_dir,
            authority_profile=profile,
            capture=capture,
            ledger_dir=Path(args.ledger_dir),
            out_dir=out_dir,
            growth_sha=args.growth_sha,
            growth_ci_run_id=args.growth_ci_run_id,
        )
        report = _write_report(
            path=report_path,
            state="LIVE_REVIEW_INGESTED",
            index=index,
        )
        print(json.dumps(report, sort_keys=True))
        return 0
    except MalformedModelResponse as exc:
        report = _write_report(
            path=report_path,
            state="MALFORMED_MODEL_RESPONSE",
            index=None,
            error=exc,
        )
        print(json.dumps(report, sort_keys=True))
        return 2
    except Exception as exc:
        report = _write_report(
            path=report_path,
            state="BLOCKED_WAITING_GENUINE_CAPTURE",
            index=None,
            error=exc,
        )
        print(json.dumps(report, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
