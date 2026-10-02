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
    DYNAMIC_CAPTURE_VERSION,
    build_creator_envelope,
    convert_dynamic_capture,
    parse_dynamic_bridge_capture,
)
from . import live_ingest_operator_r27 as r27

AUTHORITY_VERSION = "growth.multiround_session_authorities.r28.v1"
SESSION_VERSION = "growth.multiround_review_session.r28.v1"
LEDGER_VERSION = "growth.multiround_review_session_ledger.r28.v1"
ROUND_RESULT_VERSION = "growth.multiround_review_round_result.r28.v1"
SELECTED_RESULT_VERSION = "growth.creator_r30_selected_review_result.r28.v1"
REPORT_VERSION = "growth.multiround_session_ingest.r28.readiness.v1"
FIXTURE_REHEARSAL_VERSION = "growth.multiround_session_rehearsal.r28.v1"

R28_BASE_SHA = "d80592ad660b7b73ad13298880918a5944411c38"
R28_BASE_CI = 37002456031
MEDIA_R22_SHA = "e82a7ac04f3758d0e3e21ea3d05265dbc2822132"
MEDIA_R22_CI = 37001071721
MEDIA_R22_ARTIFACT_ID = 11224061610
MEDIA_R22_ARTIFACT_DIGEST = (
    "sha256:d534f1656e22b72cf42531c827e66516965b4167549906521dee04edafd01734"
)
BRIDGE_R31_SHA = "104281e49122233f251c692abba726ae31cee0d5"
BRIDGE_R31_CI = 36999908386
CREATOR_R29_SHA = "4a58561b07d615c141fa609d212070c894bfd0cb"
CREATOR_R29_CI = 37002020115
MAX_REVIEW_ROUND = 2

OPERATOR_MANIFEST = "media.live_review_operator_manifest.r22.v1.json"
PACKAGE_ARCHIVE = "media-r22-live-package.tar"
PAYLOAD_DIR = "payload"
AUTHORITY_PROFILE = "media.live_review_authority_profile.r22.v1.json"
PACKAGE_MANIFEST = "media.live_review_package_manifest.r22.v1.json"
R21_BUNDLE = "media.review_round_bundle.r21.v1.json"
R21_HANDOFF = "media.review_round_transport_handoff.r21.v1.json"
R21_MAPPING = "media.review_round_sealed_mapping.r21.v1.json"
R21_EVIDENCE = "media.review_round_bundle.r21.evidence.json"
PROMPT_JSON = "model-review-prompt.txt.json"
PROMPT_TEXT = "model-facing-prompt.txt"
ATTACHMENTS = ("review-A.mp4", "review-B.mp4")


class R28Error(ValueError):
    pass


class AuthorityDrift(R28Error):
    pass


class PackageDrift(R28Error):
    pass


class SessionConflict(R28Error):
    pass


class RoundSequenceError(R28Error):
    pass


class NonLiveCapture(R28Error):
    pass


class MalformedModelResponse(R28Error):
    pass


class ReconciliationRequired(R28Error):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_size(path: Path) -> int:
    return Path(path).stat().st_size


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R28Error(f"{field} must be lowercase SHA-256")
    return value


def _sha1(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R28Error(f"{field} must be lowercase SHA-1")
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise R28Error(f"{field} must be non-empty string")
    return value


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise R28Error(f"{field} must be positive integer")
    return value


def _round(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_REVIEW_ROUND:
        raise RoundSequenceError(f"{field} must be review round 0, 1, or 2")
    return value


def _artifact_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise AuthorityDrift(f"{field} must use sha256: prefix")
    _sha(value[7:], field)
    return value


def _forbid_moving_authority(value: Any, path: str = "authority") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"branch", "branch_name", "branchHeadSha"} and path != "authority.media_r23":
                raise AuthorityDrift(f"{path}.{key} is moving authority")
            _forbid_moving_authority(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _forbid_moving_authority(child, f"{path}[{index}]")


def validate_authority_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract_version",
        "growth_r27",
        "media_r22",
        "media_r23",
        "bridge_r31",
        "creator_r29",
        "evidence_boundary",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AuthorityDrift("R28 authority profile fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R28 authority contract mismatch")
    _forbid_moving_authority(value)

    growth = value["growth_r27"]
    if (
        growth.get("repository") != "foto6/video3"
        or growth.get("producer_sha") != R28_BASE_SHA
        or growth.get("ci_run_id") != R28_BASE_CI
        or growth.get("operator_contract") != r27.OPERATOR_VERSION
        or growth.get("blobs")
        != {
            "implementation": "125dba66b592ca12934c045c78539cf082a1de07",
            "authority_profile": "256e8fcf228025a2cc09a3173dd82998fd1658d9",
            "contract": "040097c5aae0b288de39e35f76eb1816e246568c",
            "index_schema": "23e7f90b4704fe173bd8842c04f00b1eeb15e5d6",
            "tests": "018ea2c487689d8ee4f5ca3ce78040496d74dc13",
        }
    ):
        raise AuthorityDrift("exact Growth R27 authority drift")

    media = value["media_r22"]
    if (
        media.get("repository") != "foto6/video2"
        or media.get("producer_sha") != MEDIA_R22_SHA
        or media.get("ci_run_id") != MEDIA_R22_CI
        or media.get("contract") != "media.live_review_artifact.r22.v1"
        or media.get("operator_manifest_contract")
        != "media.live_review_operator_manifest.r22.v1"
        or media.get("package_manifest_contract")
        != "media.live_review_package_manifest.r22.v1"
        or media.get("authority_profile_contract")
        != "media.live_review_authority_profile.r22.v1"
        or media.get("artifact")
        != {
            "id": MEDIA_R22_ARTIFACT_ID,
            "name": "media-r22-live-review-operator-bundle",
            "digest": MEDIA_R22_ARTIFACT_DIGEST,
        }
    ):
        raise AuthorityDrift("exact Media R22 authority drift")
    expected_media_blobs = {
        "contract": "d6e494950b5ab54383733db69912c2384cce188f",
        "manifest": "be117e83a44720e8e12284991b755c69c8ea0cde",
        "implementation": "31845333a6919364d81a7c2bc52aad1cb3ae82ed",
        "materializer": "ade9e7181ecccf8e78e7dd966240618c872c9b26",
        "verifier": "86b0a53eed6959af805fd0c492620eb037071e25",
        "extractor": "dc45ee753480fc62249bd69ca209b994d680d08a",
        "upstream_verifier": "4284d3705f8a8fa2543d15d1f17765edc406d857",
        "operator_manifest_schema": "7e78d7be9fffbbda6ee86cf39c4db8e59a635a3d",
    }
    if media.get("blobs") != expected_media_blobs:
        raise AuthorityDrift("Media R22 implementation/blob authority drift")
    for key, digest in expected_media_blobs.items():
        _sha1(digest, f"media_r22.blobs.{key}")
    _artifact_digest(media["artifact"]["digest"], "media_r22.artifact.digest")

    known = media.get("known_packages")
    if not isinstance(known, Mapping) or set(known) != {"round_0", "round_1"}:
        raise AuthorityDrift("Media R22 known package freeze invalid")
    for name, row in known.items():
        if not isinstance(row, Mapping):
            raise AuthorityDrift(f"Media R22 {name} authority invalid")
        expected_round = 0 if name == "round_0" else 1
        if row.get("review_round") != expected_round:
            raise AuthorityDrift(f"Media R22 {name} round drift")
        for key in (
            "operator_manifest_sha256",
            "archive_sha256",
            "authority_profile_sha256",
            "package_manifest_sha256",
            "r21_package_digest",
            "sealed_mapping_digest",
            "prompt_digest",
        ):
            _sha(row.get(key), f"media_r22.{name}.{key}")
        _positive(row.get("archive_size"), f"media_r22.{name}.archive_size")

    media23 = value["media_r23"]
    if (
        media23.get("repository") != "foto6/video2"
        or media23.get("observed_sha") != MEDIA_R22_SHA
        or media23.get("distinct_exact_green_authority_available") is not False
    ):
        raise AuthorityDrift("Media R23 availability boundary drift")

    bridge = value["bridge_r31"]
    if (
        bridge.get("repository") != "foto6/WebAIBridge"
        or bridge.get("producer_sha") != BRIDGE_R31_SHA
        or bridge.get("ci_run_id") != BRIDGE_R31_CI
        or bridge.get("result_contract") != r27.BRIDGE_R31_RESULT_CONTRACT
        or bridge.get("capture_contract") != r27.BRIDGE_R30_CAPTURE_CONTRACT
        or bridge.get("blobs")
        != {
            "manifest_schema": "f90cd2aa4bdc868af845bfba58c612ebef3f32ad",
            "authority_profile_schema": "25e2cbfe487ba88f70d233774e585691ad4f70c6",
            "result_schema": "55f403a2c0dca4a05a5002e104cfc29d20b536b5",
            "preflight_schema": "74b4579b4d9d3d08da04b1181863905f024f367a",
            "media_operator": "38509af174fc25aa4229c084fc3cd9b2e35b539e",
            "preflight_implementation": "c975d934d080deffdcf2a9ac4cf82ca142ba93d3",
            "finalizer_implementation": "a18a10e681efb249275816ab43ceb55e4acc6643",
            "powershell_operator": "1fd850863fb1e1c31894ccc54f5fcbc296122419",
            "readiness_implementation": "f248f94843c5d94737947929aaece33129e1b98a",
        }
    ):
        raise AuthorityDrift("exact Bridge R31 authority drift")

    creator = value["creator_r29"]
    if (
        creator.get("repository") != "foto6/video1"
        or creator.get("producer_sha") != CREATOR_R29_SHA
        or creator.get("ci_run_id") != CREATOR_R29_CI
        or creator.get("authority_contract")
        != "creator.exact_dynamic_e2e_authorities.r29.v1"
        or creator.get("input_contract") != r27.CREATOR_ENVELOPE_VERSION
        or creator.get("next_review_contract") != "media.review_round_bundle.r21.v1"
        or creator.get("max_reedit_rounds") != 2
        or creator.get("terminal_publishable") != ["winner"]
        or creator.get("non_publishable")
        != ["tie", "insufficient_evidence", "human_review", "reedit_limit_reached"]
        or creator.get("blobs")
        != {
            "authority_profile": "d3ae632828bf88dfa2a410a433513870c4c11571",
            "manifest": "0e0d45cc2944608b84f3d4922f13eee8252e95a8",
            "implementation": "ac1a5bebbdb1c9905339dcb33ad655d8ace87cb3",
            "workflow": "03195a3417611d7139b0c7898e181d5e14fdc9f5",
        }
        or creator.get("creator_r30_branch_same_exact_head") is not True
    ):
        raise AuthorityDrift("exact Creator R29/R30-facing authority drift")
    source_ready = creator.get("source_ready_artifact")
    if source_ready != {
        "id": 11223443861,
        "digest": "sha256:505732732fac756bea3f71eacdcb416779e6ad9fd6fd291d9587f470589af4a9",
    }:
        raise AuthorityDrift("Creator R29 source-ready artifact drift")
    _artifact_digest(source_ready["digest"], "creator_r29.source_ready_artifact.digest")

    boundary = value["evidence_boundary"]
    if boundary != {
        "fixtures_live": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
        "merge": False,
    }:
        raise AuthorityDrift("R28 evidence boundary drift")
    return _clone(value)


def authority_profile_digest(profile: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority_profile(profile))


def _r27_profile() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.live_ingest_operator.r27.v1"
        / "authority-profiles.json"
    )


def _media_bundle_core(bundle: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "contractVersion": bundle.get("contractVersion"),
        "state": bundle.get("state"),
        "producer": bundle.get("producer"),
        "bridgeAuthority": bundle.get("bridgeAuthority"),
        "mode": bundle.get("mode"),
        "source": bundle.get("source"),
        "briefLineageDigest": bundle.get("briefLineageDigest"),
        "reviewRound": bundle.get("reviewRound"),
        "attachments": bundle.get("attachments"),
        "prompt": bundle.get("prompt"),
        "r20PackageDigest": bundle.get("r20PackageDigest"),
        "r20SealedMappingDigest": bundle.get("r20SealedMappingDigest"),
        "sealedMapping": bundle.get("sealedMapping"),
        "roundLineage": bundle.get("roundLineage"),
        "modelReviewPerformed": bundle.get("modelReviewPerformed"),
        "liveModelReviewed": bundle.get("liveModelReviewed"),
        "providerPublish": bundle.get("providerPublish"),
        "humanQuality": bundle.get("humanQuality"),
    }


def _verify_payload_manifest(payload: Path, manifest: Mapping[str, Any]) -> dict[str, str]:
    if (
        manifest.get("contractVersion") != "media.live_review_package_manifest.r22.v1"
        or manifest.get("state") != "LIVE_REVIEW_ARTIFACT_READY"
    ):
        raise PackageDrift("Media R22 package manifest contract/state drift")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PackageDrift("Media R22 package manifest files missing")
    if sha256_json(files) != manifest.get("fileSetDigest"):
        raise PackageDrift("Media R22 file-set digest drift")
    hashes = {}
    for row in files:
        if not isinstance(row, Mapping) or set(row) != {"mime", "path", "sha256", "size"}:
            raise PackageDrift("Media R22 package-manifest row invalid")
        rel = _nonempty(row["path"], "package_manifest.path")
        path = (payload / rel).resolve()
        try:
            path.relative_to(payload.resolve())
        except ValueError as exc:
            raise PackageDrift("Media R22 payload path escape") from exc
        if not path.is_file():
            raise PackageDrift(f"Media R22 payload file missing: {rel}")
        digest = _file_sha(path)
        if digest != _sha(row["sha256"], f"package_manifest.{rel}.sha256"):
            raise PackageDrift(f"Media R22 payload hash drift: {rel}")
        if _file_size(path) != row["size"]:
            raise PackageDrift(f"Media R22 payload size drift: {rel}")
        hashes[rel] = digest
    return hashes


def _validate_materializer_profile(value: Mapping[str, Any], profile: Mapping[str, Any]) -> None:
    if value.get("contractVersion") != "media.live_review_authority_profile.r22.v1":
        raise AuthorityDrift("Media R22 authority-profile contract drift")
    if value.get("state") != "LIVE_REVIEW_ARTIFACT_READY":
        raise PackageDrift("Media R22 authority profile state drift")
    materializer = value.get("materializerR22")
    media = profile["media_r22"]
    if not isinstance(materializer, Mapping) or (
        materializer.get("repository") != media["repository"]
        or materializer.get("producerSha") != media["producer_sha"]
        or materializer.get("ciRunId") != media["ci_run_id"]
        or materializer.get("blobs")
        != {
            "contract": media["blobs"]["contract"],
            "extractor": media["blobs"]["extractor"],
            "implementation": media["blobs"]["implementation"],
            "materializer": media["blobs"]["materializer"],
            "operatorManifestSchema": media["blobs"]["operator_manifest_schema"],
            "verifier": media["blobs"]["verifier"],
        }
    ):
        raise AuthorityDrift("Media R22 materializer authority drift")
    source = value.get("sourceMediaR21")
    if not isinstance(source, Mapping) or (
        source.get("repository") != "foto6/video2"
        or source.get("producerSha") != r27.MEDIA_R21_SHA
        or source.get("ciRunId") != r27.MEDIA_R21_CI
        or source.get("bundleContract") != "media.review_round_bundle.r21.v1"
        or source.get("handoffContract") != "media.review_round_transport_handoff.r21.v1"
        or source.get("state") != "ROUND_PAIR_PACKAGE_READY"
    ):
        raise AuthorityDrift("Media R22 source Media R21 authority drift")
    bridge = value.get("bridgeR31")
    if not isinstance(bridge, Mapping) or (
        bridge.get("repository") != "foto6/WebAIBridge"
        or bridge.get("producerSha") != "31cfef82663d72d53e69e6345b50073ffcd461ca"
        or bridge.get("ciRunId") != 36997793086
        or bridge.get("mediaOperatorImplementationBlob")
        != "38509af174fc25aa4229c084fc3cd9b2e35b539e"
        or bridge.get("operatorManifestSchemaBlob")
        != "f90cd2aa4bdc868af845bfba58c612ebef3f32ad"
        or bridge.get("authorityProfileSchemaBlob")
        != "25e2cbfe487ba88f70d233774e585691ad4f70c6"
    ):
        raise AuthorityDrift("Media R22 embedded historical Bridge R31 authority drift")
    boundary = value.get("evidenceBoundary")
    if boundary != {
        "browserMutationPerformed": False,
        "humanQuality": False,
        "liveModelReviewed": False,
        "modelReviewPerformed": False,
        "providerPublish": False,
    }:
        raise PackageDrift("Media R22 evidence-boundary drift")


def validate_operator_package(
    operator_dir: Path,
    *,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    profile = validate_authority_profile(profile)
    root = Path(operator_dir).resolve()
    if not root.is_dir():
        raise PackageDrift("Media operator directory missing")
    manifest_path = root / OPERATOR_MANIFEST
    archive_path = root / PACKAGE_ARCHIVE
    payload = root / PAYLOAD_DIR
    if not manifest_path.is_file() or not archive_path.is_file() or not payload.is_dir():
        raise PackageDrift("Media R22 operator directory incomplete")
    manifest = _load(manifest_path)
    if (
        manifest.get("contractVersion") != "media.live_review_operator_manifest.r22.v1"
        or manifest.get("state") != "LIVE_REVIEW_ARTIFACT_READY"
        or manifest.get("sourceState") != "ROUND_PAIR_PACKAGE_READY"
    ):
        raise PackageDrift("Media R22 operator manifest contract/state drift")
    for field in ("modelReviewPerformed", "liveModelReviewed", "browserMutationPerformed", "providerPublish", "humanQuality"):
        if manifest.get(field) is not False:
            raise PackageDrift(f"Media R22 operator evidence boundary drift: {field}")

    archive_sha = _file_sha(archive_path)
    archive_size = _file_size(archive_path)
    package = manifest.get("package")
    if not isinstance(package, Mapping):
        raise PackageDrift("Media R22 operator package block missing")
    if (
        package.get("archiveSha256") != archive_sha
        or package.get("archiveSize") != archive_size
        or manifest.get("bridgeR31Inputs", {}).get("expectedArchiveSha256") != archive_sha
    ):
        raise PackageDrift("Media R22 archive identity drift")

    authority_path = payload / AUTHORITY_PROFILE
    package_manifest_path = payload / PACKAGE_MANIFEST
    if not authority_path.is_file() or not package_manifest_path.is_file():
        raise PackageDrift("Media R22 derived manifests missing")
    authority = _load(authority_path)
    package_manifest = _load(package_manifest_path)
    _validate_materializer_profile(authority, profile)
    hashes = _verify_payload_manifest(payload, package_manifest)
    for field in ("modelReviewPerformed", "liveModelReviewed", "providerPublish", "humanQuality"):
        if package_manifest.get(field) is not False:
            raise PackageDrift(f"Media R22 package-manifest evidence boundary drift: {field}")
    source_manifest = package_manifest.get("sourceMediaR21")
    if not isinstance(source_manifest, Mapping):
        raise PackageDrift("Media R22 package manifest source lineage missing")
    if package.get("packageManifestSha256") != _file_sha(package_manifest_path):
        raise PackageDrift("Media R22 package-manifest SHA drift")
    if package.get("packageManifestRelative") != f"{PAYLOAD_DIR}/{PACKAGE_MANIFEST}":
        raise PackageDrift("Media R22 package-manifest path drift")

    required_payload = {
        AUTHORITY_PROFILE,
        R21_BUNDLE,
        R21_HANDOFF,
        R21_MAPPING,
        R21_EVIDENCE,
        PROMPT_JSON,
        PROMPT_TEXT,
        *ATTACHMENTS,
    }
    if not required_payload.issubset(set(hashes)):
        missing = sorted(required_payload.difference(hashes))
        raise PackageDrift("Media R22 required payload missing: " + ",".join(missing))

    bundle = _load(payload / R21_BUNDLE)
    handoff = _load(payload / R21_HANDOFF)
    mapping = _load(payload / R21_MAPPING)
    evidence = _load(payload / R21_EVIDENCE)
    prompt_json = _load(payload / PROMPT_JSON)
    prompt_text = (payload / PROMPT_TEXT).read_text(encoding="utf-8")

    if (
        bundle.get("contractVersion") != "media.review_round_bundle.r21.v1"
        or handoff.get("contractVersion") != "media.review_round_transport_handoff.r21.v1"
        or evidence.get("evidenceVersion") != "media.review_round_bundle.r21.evidence.v1"
    ):
        raise PackageDrift("Media R21 embedded contract drift")
    if bundle.get("producer") != {"repository": "foto6/video2", "sha": r27.MEDIA_R21_SHA}:
        raise AuthorityDrift("Media R21 embedded producer drift")
    review_round = _round(bundle.get("reviewRound"), "bundle.reviewRound")
    if handoff.get("roundLineage", {}).get("reviewRound", review_round) != review_round:
        raise PackageDrift("Media R21 handoff round drift")
    if manifest.get("sourceLineage", {}).get("reviewRound") != review_round:
        raise PackageDrift("Media R22 manifest review round drift")
    if manifest.get("sourceLineage", {}).get("mode") != bundle.get("mode"):
        raise PackageDrift("Media R22 mode drift")
    package_digest = sha256_json(_media_bundle_core(bundle))
    if (
        handoff.get("packageDigest") != package_digest
        or evidence.get("packageDigest") != package_digest
        or package.get("r21PackageDigest") != package_digest
    ):
        raise PackageDrift("Media R21 package digest drift")
    if (
        source_manifest.get("producerSha") != r27.MEDIA_R21_SHA
        or source_manifest.get("ciRunId") != r27.MEDIA_R21_CI
        or source_manifest.get("mode") != bundle.get("mode")
        or source_manifest.get("reviewRound") != review_round
        or source_manifest.get("packageDigest") != package_digest
    ):
        raise PackageDrift("Media R22 package-manifest source Media R21 drift")

    if not isinstance(mapping, Mapping) or set(mapping) != {"digest", "entries"}:
        raise PackageDrift("sealed mapping file invalid")
    mapping_digest = sha256_json(mapping["entries"])
    if (
        mapping.get("digest") != mapping_digest
        or bundle.get("sealedMapping") != mapping
        or handoff.get("sealedMappingDigest") != mapping_digest
        or package.get("sealedMappingDigest") != mapping_digest
    ):
        raise PackageDrift("sealed mapping digest/content drift")

    if not isinstance(prompt_json, Mapping) or set(prompt_json) != {"text", "digest", "bytes"}:
        raise PackageDrift("prompt JSON fields invalid")
    prompt_digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    if (
        prompt_json["text"] != prompt_text
        or prompt_json["digest"] != prompt_digest
        or prompt_json["bytes"] != len(prompt_text.encode("utf-8"))
        or bundle.get("prompt") != {"text": prompt_text, "digest": prompt_digest}
        or handoff.get("promptText") != prompt_text
        or handoff.get("promptDigest") != prompt_digest
        or package.get("promptDigest") != prompt_digest
    ):
        raise PackageDrift("prompt bytes/digest drift")
    if mapping_digest in prompt_text:
        raise PackageDrift("sealed mapping digest leaked into model-facing prompt")

    attachments = []
    by_label = {}
    bundle_attachments = bundle.get("attachments")
    if not isinstance(bundle_attachments, list) or len(bundle_attachments) != 2:
        raise PackageDrift("exactly two Media R21 attachments required")
    for row in bundle_attachments:
        normalized = r27._validate_attachment_row(row, "bundle.attachment")
        label = normalized["blind_label"]
        path = payload / normalized["path"]
        if _file_sha(path) != normalized["sha256"] or _file_size(path) != normalized["size"]:
            raise PackageDrift(f"Media attachment bytes drift: {label}")
        attachments.append(normalized)
        by_label[label] = normalized
    if set(by_label) != {"A", "B"}:
        raise PackageDrift("Media attachments must be blind A/B")
    if manifest.get("attachments") != [
        {
            "blindLabel": row["blind_label"],
            "mime": "video/mp4",
            "name": row["path"],
            "relativePath": f"payload/{row['path']}",
            "sha256": row["sha256"],
            "size": row["size"],
        }
        for row in attachments
    ]:
        raise PackageDrift("Media R22 operator attachment identity drift")

    source = bundle.get("source")
    if not isinstance(source, Mapping) or set(source) != {"sourceId", "sha256", "size"}:
        raise PackageDrift("Media source lineage invalid")
    brief_digest = _sha(bundle.get("briefLineageDigest"), "bundle.briefLineageDigest")
    entries = mapping["entries"]
    if not isinstance(entries, list) or len(entries) != 2:
        raise PackageDrift("sealed mapping must contain exact A/B entries")
    normalized_mapping = {}
    for index, raw in enumerate(entries):
        row = r27._mapping_entry(raw, f"mapping.entries[{index}]")
        if row["blind_label"] in normalized_mapping:
            raise PackageDrift("duplicate sealed mapping label")
        normalized_mapping[row["blind_label"]] = row
    if set(normalized_mapping) != {"A", "B"}:
        raise PackageDrift("sealed mapping labels invalid")
    for label, row in normalized_mapping.items():
        attachment = by_label[label]
        if (
            row["generic_file_name"] != attachment["path"]
            or row["attachment"]["sha256"] != attachment["sha256"]
            or row["attachment"]["size"] != attachment["size"]
            or row["render"]["sha256"] != attachment["sha256"]
            or row["render"]["size"] != attachment["size"]
            or row["source"]
            != {
                "source_id": source["sourceId"],
                "sha256": source["sha256"],
                "size": source["size"],
            }
        ):
            raise PackageDrift(f"sealed mapping candidate/attachment drift: {label}")

    known = profile["media_r22"]["known_packages"].get(f"round_{review_round}")
    if known is not None:
        exact_checks = {
            "operator_manifest_sha256": _file_sha(manifest_path),
            "archive_sha256": archive_sha,
            "archive_size": archive_size,
            "authority_profile_sha256": _file_sha(authority_path),
            "package_manifest_sha256": _file_sha(package_manifest_path),
            "r21_package_digest": package_digest,
            "sealed_mapping_digest": mapping_digest,
            "prompt_digest": prompt_digest,
            "mode": bundle.get("mode"),
            "review_round": review_round,
        }
        if exact_checks != known:
            raise PackageDrift(f"known exact Media R22 round-{review_round} package drift")
    elif review_round < 2:
        raise PackageDrift("unknown Media R22 package for frozen existing review round")
    # Round 2 can be newly materialized by the exact R22 producer from the
    # Creator-produced exact R21 next-round bundle. A future distinct R23
    # producer is not trusted until this authority profile is updated.

    file_hashes = {
        "bundleFileSha256": hashes[R21_BUNDLE],
        "handoffFileSha256": hashes[R21_HANDOFF],
        "sealedMappingFileSha256": hashes[R21_MAPPING],
        "promptFileSha256": hashes[PROMPT_JSON],
        "evidenceFileSha256": hashes[R21_EVIDENCE],
    }
    round_lineage = handoff.get("roundLineage")
    round_lineage_digest = "" if round_lineage is None else _sha(
        round_lineage.get("digest"), "handoff.roundLineage.digest"
    )
    directory_digest = sha256_json(
        {
            "authoritySha": r27.MEDIA_R21_SHA,
            "packageDigest": package_digest,
            "promptDigest": prompt_digest,
            "sealedMappingDigest": mapping_digest,
            "archiveSha256": archive_sha,
            "fileHashes": file_hashes,
            "attachments": [
                {
                    "blindLabel": row["blind_label"],
                    "relativePath": row["path"],
                    "size": row["size"],
                    "sha256": row["sha256"],
                    "mime": "video/mp4",
                }
                for row in attachments
            ],
            "source": source,
            "mode": bundle.get("mode"),
            "reviewRound": review_round,
            "roundLineageDigest": round_lineage_digest,
        }
    )
    source_lineage = {
        "authority": {
            "producerRepository": "foto6/video2",
            "producerSha": r27.MEDIA_R21_SHA,
            "producerCiRunId": r27.MEDIA_R21_CI,
            "producerCiConclusion": "success",
            "bundleContract": "media.review_round_bundle.r21.v1",
            "handoffContract": "media.review_round_transport_handoff.r21.v1",
        },
        "mediaR21PackageDigest": package_digest,
        "mediaR21ArchiveSha256": archive_sha,
        "mediaR21DirectoryDigest": directory_digest,
        "mediaR21EvidenceFileSha256": file_hashes["evidenceFileSha256"],
        "mediaR21HandoffFileSha256": file_hashes["handoffFileSha256"],
        "mediaR21SealedMappingFileSha256": file_hashes["sealedMappingFileSha256"],
        "mediaR21PromptFileSha256": file_hashes["promptFileSha256"],
        "source": source,
        "mode": bundle.get("mode"),
        "reviewRound": review_round,
        "roundLineage": round_lineage,
    }
    producer = {
        "repository": "foto6/video2",
        "sha": r27.MEDIA_R21_SHA,
        "round": "R21",
        "contractName": "media.review_round_bundle.r21.v1",
        "contractSchema": "media.review_round_bundle.r21.v1",
        "contractBlobSha256": hashes[R21_BUNDLE],
    }
    dynamic_package_digest = r27._bridge_transport_digest(
        producer=producer,
        prompt_file_sha256=hashes[PROMPT_JSON],
        prompt_digest=prompt_digest,
        attachments=attachments,
        sealed_mapping_digest=mapping_digest,
        source_lineage=source_lineage,
        prompt_format="json_prompt_text",
    )
    r31_handoff = {
        "contract": "media.dynamic_review_handoff.v1",
        "producer": {
            "repository": "foto6/video2",
            "sha": r27.MEDIA_R21_SHA,
            "round": "R21",
            "contract": {
                "name": "media.review_round_bundle.r21.v1",
                "schemaVersion": "media.review_round_bundle.r21.v1",
                "blob": {
                    "relativePath": R21_BUNDLE,
                    "sha256": hashes[R21_BUNDLE],
                },
            },
        },
        "package": {
            "digestAlgorithm": "bridge.dynamic_review_package.sha256.v1",
            "digest": dynamic_package_digest,
        },
        "prompt": {
            "relativePath": PROMPT_JSON,
            "fileSha256": hashes[PROMPT_JSON],
            "textSha256": prompt_digest,
            "format": "json_prompt_text",
            "textField": "text",
        },
        "attachments": [
            {
                "blindLabel": row["blind_label"],
                "blindedName": row["path"],
                "relativePath": row["path"],
                "size": row["size"],
                "sha256": row["sha256"],
                "mime": "video/mp4",
            }
            for row in attachments
        ],
        "sealedMapping": {
            "digest": mapping_digest,
            "contract": "media.review_round_sealed_mapping.r21.v1",
        },
        "sourceLineage": source_lineage,
    }
    handoff_bytes = (json.dumps(r31_handoff, indent=2) + "\n").encode("utf-8")
    r31_handoff_sha = hashlib.sha256(handoff_bytes).hexdigest()
    source_binding = {
        "producerRepository": "foto6/video2",
        "producerSha": r27.MEDIA_R21_SHA,
        "sourceRound": "R21",
        "producerContractName": "media.review_round_bundle.r21.v1",
        "producerContractSchema": "media.review_round_bundle.r21.v1",
        "producerContractBlobSha256": hashes[R21_BUNDLE],
        "packageDigest": dynamic_package_digest,
        "promptFileSha256": hashes[PROMPT_JSON],
        "promptTextSha256": prompt_digest,
        "sealedMappingDigestRef": mapping_digest,
        "handoffSha256": r31_handoff_sha,
        "sourceLineage": source_lineage,
        "attachments": [
            {
                "blindLabel": row["blind_label"],
                "name": row["path"],
                "size": row["size"],
                "sha256": row["sha256"],
                "mime": "video/mp4",
            }
            for row in attachments
        ],
    }
    source_binding_fingerprint = sha256_json(source_binding)

    media_authority = {
        "contract_version": "growth.media_dynamic_review_authority.r26.v1",
        "repository": "foto6/video2",
        "producer_sha": r27.MEDIA_R21_SHA,
        "ci_run_id": r27.MEDIA_R21_CI,
        "package_contract": "media.dynamic_review_package.r21.v1",
        "contract_blob_sha1": "65358261775f0fcd2ab9e21f3f621aee977f29da",
        "schema_blob_sha1": "f04925e317d849434852e6b706533f909da47b22",
        "implementation_blob_sha1": "c6f556b8a177b6182d787356625094cdcad5a58e",
        "artifact_id": 11221240371,
        "artifact_name": "media-r21-round-pair-review",
        "artifact_digest": r27.MEDIA_R21_ARTIFACT_DIGEST,
        "package_digest": package_digest,
        "package_file_sha256": hashes[R21_BUNDLE],
        "evidence_file_sha256": hashes[R21_EVIDENCE],
        "prompt_digest": prompt_digest,
        "prompt_file_sha256": hashes[PROMPT_JSON],
        "sealed_mapping_digest": mapping_digest,
        "sealed_mapping_file_sha256": hashes[R21_MAPPING],
        "review_round": review_round,
        "source": {
            "source_id": source["sourceId"],
            "sha256": source["sha256"],
            "size": source["size"],
        },
        "attachments": [
            {
                "blind_label": row["blind_label"],
                "generic_file_name": row["path"],
                "sha256": row["sha256"],
                "size": row["size"],
                "mime_type": "video/mp4",
            }
            for row in attachments
        ],
    }
    normalized_media = {
        "authority": media_authority,
        "package_digest": package_digest,
        "prompt_digest": prompt_digest,
        "sealed_mapping_digest": mapping_digest,
        "review_round": review_round,
        "source": media_authority["source"],
        "request_id": "",
        "idempotency_key": "",
        "attachments_by_label": {
            row["blind_label"]: {
                "blind_label": row["blind_label"],
                "generic_file_name": row["path"],
                "mime_type": "video/mp4",
                "sha256": row["sha256"],
                "size": row["size"],
                "derivative": None,
            }
            for row in attachments
        },
        "mapping_by_label": {
            label: {
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
                    "baseline_review_candidate_id": row["baseline_review_candidate_id"],
                    "application_parent_candidate_id": row["application_parent_candidate_id"],
                    "parent_render_sha256": row["parent_render_sha256"],
                    "growth_handoff_digest": row["growth_handoff_digest"],
                    "media_application_digest": row["media_application_digest"],
                },
            }
            for label, row in normalized_mapping.items()
        },
        "prompt_manifest": {
            "promptText": prompt_text,
            "promptDigest": prompt_digest,
        },
    }
    return _clone(
        {
            "operator_dir": str(root),
            "review_round": review_round,
            "mode": bundle.get("mode"),
            "source": normalized_media["source"],
            "brief_lineage_digest": brief_digest,
            "package_digest": package_digest,
            "sealed_mapping_digest": mapping_digest,
            "prompt_digest": prompt_digest,
            "operator_manifest_sha256": _file_sha(manifest_path),
            "archive_sha256": archive_sha,
            "archive_size": archive_size,
            "package_manifest_sha256": _file_sha(package_manifest_path),
            "authority_profile_sha256": _file_sha(authority_path),
            "raw_mapping_file_sha256": hashes[R21_MAPPING],
            "normalized_media_package": normalized_media,
            "r31_transport": {
                "dynamic_package_digest": dynamic_package_digest,
                "handoff_sha256": r31_handoff_sha,
                "source_binding_fingerprint": source_binding_fingerprint,
                "source_lineage": source_lineage,
                "producer": producer,
            },
        }
    )


def _capture_state(path: Path) -> str:
    try:
        value = _load(path)
    except Exception:
        return "INVALID"
    if not isinstance(value, Mapping):
        return "INVALID"
    if value.get("contract") == r27.BRIDGE_R31_RESULT_CONTRACT:
        return str(value.get("state") or "")
    return str(value.get("disposition") or "")


def load_live_capture(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    state = _capture_state(path)
    if state == "MALFORMED_MODEL_RESPONSE":
        raise MalformedModelResponse("Bridge result is MALFORMED_MODEL_RESPONSE")
    if state == "RECONCILIATION_REQUIRED":
        raise ReconciliationRequired("Bridge result requires reconciliation")
    try:
        return r27.load_bridge_capture_input(path, profile=_r27_profile())
    except r27.MalformedModelResponse as exc:
        raise MalformedModelResponse(str(exc)) from exc
    except r27.NonLiveCapture as exc:
        if "RECONCILIATION_REQUIRED" in str(exc):
            raise ReconciliationRequired(str(exc)) from exc
        raise NonLiveCapture(str(exc)) from exc


def validate_capture_against_package(
    capture: Mapping[str, Any],
    *,
    package: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(capture, Mapping):
        raise NonLiveCapture("capture must be object")
    r27._reject_fixture_markers(capture)
    if capture.get("contract") != r27.BRIDGE_R30_CAPTURE_CONTRACT:
        raise NonLiveCapture("exact Bridge R30 capture contract required")
    if capture.get("disposition") == "MALFORMED_MODEL_RESPONSE":
        raise MalformedModelResponse("Bridge capture MALFORMED_MODEL_RESPONSE")
    if capture.get("disposition") == "RECONCILIATION_REQUIRED":
        raise ReconciliationRequired("Bridge capture RECONCILIATION_REQUIRED")
    if (
        capture.get("disposition") != "LIVE_REVIEW_PASS"
        or capture.get("model_evidence") is not True
        or capture.get("human_ground_truth") is not False
    ):
        raise NonLiveCapture("capture is not genuine LIVE_REVIEW_PASS model evidence")
    live = capture.get("liveEvidence")
    if not isinstance(live, Mapping) or live.get("realAttachment") is not True or live.get("realSendCaptured") is not True:
        raise NonLiveCapture("capture lacks genuine attachment/send evidence")

    dynamic = capture.get("dynamicPackage")
    if not isinstance(dynamic, Mapping):
        raise PackageDrift("capture dynamicPackage missing")
    expected = package["r31_transport"]
    if (
        dynamic.get("handoffContract") != "media.dynamic_review_handoff.v1"
        or dynamic.get("handoffSha256") != expected["handoff_sha256"]
        or dynamic.get("packageDigest") != expected["dynamic_package_digest"]
        or dynamic.get("sealedMappingDigestRef") != package["sealed_mapping_digest"]
        or dynamic.get("producer") != expected["producer"]
        or dynamic.get("sourceLineage") != expected["source_lineage"]
        or dynamic.get("sourceBindingFingerprint") != expected["source_binding_fingerprint"]
    ):
        raise PackageDrift("capture exact R31/R30 package/lineage binding drift")

    normalized = copy.deepcopy(package["normalized_media_package"])
    request_id = _nonempty(capture.get("requestId"), "capture.requestId")
    normalized["request_id"] = request_id
    normalized["idempotency_key"] = (
        f"r28:{capture.get('conversationId')}:{request_id}:"
        f"{package['package_digest']}:{package['review_round']}"
    )
    capture_for_r26 = copy.deepcopy(capture)
    capture_for_r26.pop("promptFileSha256", None)
    parsed = parse_dynamic_bridge_capture(
        capture_for_r26,
        media_package=normalized,
        bridge_authority=r27._bridge_authority(_r27_profile()),
    )
    return _clone(
        {
            "parsed_capture": parsed,
            "normalized_media_package": normalized,
        }
    )


def _session_identity(
    *,
    source: Mapping[str, Any],
    brief_digest: str,
    conversation_id: str,
    authority_digest: str,
) -> tuple[str, dict[str, Any]]:
    body = {
        "source": _clone(source),
        "brief_lineage_digest": brief_digest,
        "conversation_id": conversation_id,
        "authority_profile_digest": authority_digest,
        "authorities": {
            "growth_r27_sha": R28_BASE_SHA,
            "media_r22_sha": MEDIA_R22_SHA,
            "bridge_r31_sha": BRIDGE_R31_SHA,
            "creator_r29_sha": CREATOR_R29_SHA,
        },
    }
    return "gr28s1:" + sha256_json(body), body


def _selected_result(
    *,
    ingest: Mapping[str, Any],
    envelopes: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    selected = ingest["unblinding"]["selected_candidate_id"]
    if selected is None:
        return None
    envelope = envelopes[selected]
    candidate = envelope["candidate"]
    result = {
        "contract_version": SELECTED_RESULT_VERSION,
        "result_id": "",
        "result_digest": "",
        "candidate_id": selected,
        "candidate_round": candidate["candidate_round"],
        "state": candidate["state"],
        "render_sha256": candidate["render_sha256"],
        "render_size": candidate["render_size"],
        "attachment_sha256": candidate["attachment_sha256"],
        "attachment_size": candidate["attachment_size"],
        "handoff_digest": candidate["handoff_digest"],
        "envelope_digest": envelope["envelope_digest"],
        "pairwise_selection": ingest["unblinding"]["model_facing_selection"],
        "review_round": ingest["review_round"],
        "publish_authorized": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
    }
    result["result_id"] = "gr28sel1:" + sha256_json(
        {
            "candidate_id": selected,
            "envelope_digest": envelope["envelope_digest"],
            "review_round": ingest["review_round"],
        }
    )
    material = copy.deepcopy(result)
    material["result_digest"] = ""
    result["result_digest"] = sha256_json(material)
    return result


def _round_result(
    *,
    session_id: str,
    package: Mapping[str, Any],
    ingest: Mapping[str, Any],
    envelopes: Mapping[str, Mapping[str, Any]],
    selected: Mapping[str, Any] | None,
    r31_wrapper: Mapping[str, Any] | None,
    terminal_winner: bool,
) -> dict[str, Any]:
    selection = ingest["unblinding"]["model_facing_selection"]
    selected_candidate = ingest["unblinding"]["selected_candidate_id"]
    if selection == "tie":
        outcome = "tie"
    elif selection == "insufficient_evidence":
        outcome = "insufficient_evidence"
    elif selected_candidate is not None:
        outcome = "winner"
    else:
        outcome = "human_review"
    result = {
        "contract_version": ROUND_RESULT_VERSION,
        "round_result_id": "",
        "round_result_digest": "",
        "session_id": session_id,
        "review_round": ingest["review_round"],
        "package": {
            "operator_manifest_sha256": package["operator_manifest_sha256"],
            "archive_sha256": package["archive_sha256"],
            "package_digest": package["package_digest"],
            "sealed_mapping_digest": package["sealed_mapping_digest"],
            "prompt_digest": package["prompt_digest"],
        },
        "capture": {
            "capture_id": ingest["capture"]["capture_id"],
            "capture_digest": ingest["capture"]["capture_digest"],
            "assistant_response_digest": ingest["capture"]["assistant_response_digest"],
            "conversation": ingest["capture"]["conversation"],
            "bridge_r31_wrapper": r31_wrapper,
        },
        "pairwise": {
            "selection": selection,
            "selected_candidate_id": selected_candidate,
            "output_digest": ingest["pairwise_output"]["output_digest"],
            "rationale": ingest["pairwise_output"]["rationale"],
            "confidence": ingest["pairwise_output"]["confidence"],
            "uncertainty": ingest["pairwise_output"]["uncertainty"],
        },
        "outcome": outcome,
        "selected_result": None if selected is None else _clone(selected),
        "candidate_envelopes": {
            candidate_id: {
                "envelope_digest": envelope["envelope_digest"],
                "handoff_digest": envelope["candidate"]["handoff_digest"],
                "state": envelope["candidate"]["state"],
                "candidate_round": envelope["candidate"]["candidate_round"],
                "render_sha256": envelope["candidate"]["render_sha256"],
            }
            for candidate_id, envelope in sorted(envelopes.items())
        },
        "terminal_winner": bool(terminal_winner and outcome == "winner"),
        "creator_executable_handoff_emitted": True,
        "evidence_boundary": {
            "model_evidence": True,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "human_parity_inferred": False,
            "browser_mutation": False,
            "provider_mutation": False,
        },
    }
    result["round_result_id"] = "gr28rr1:" + sha256_json(
        {
            "session_id": session_id,
            "review_round": ingest["review_round"],
            "capture_digest": ingest["capture"]["capture_digest"],
            "package_digest": ingest["package_digest"],
        }
    )
    material = copy.deepcopy(result)
    material["round_result_digest"] = ""
    result["round_result_digest"] = sha256_json(material)
    return result


class SessionLedger:
    def __init__(
        self,
        directory: Path,
        *,
        session_id: str,
        identity: Mapping[str, Any],
    ) -> None:
        self.directory = Path(directory).resolve()
        self.path = self.directory / "growth-r28-session-ledger.json"
        self.session_id = session_id
        self.identity = _clone(identity)
        self.rounds: dict[str, Any] = {}
        self.requests: dict[str, str] = {}
        self.closed = False
        self.terminal_round: int | None = None
        if self.path.exists():
            raw = _load(self.path)
            if not isinstance(raw, Mapping) or set(raw) != {
                "version",
                "session_id",
                "identity",
                "rounds",
                "requests",
                "closed",
                "terminal_round",
            }:
                raise SessionConflict("R28 session ledger fields invalid")
            if raw["version"] != LEDGER_VERSION:
                raise SessionConflict("R28 session ledger version mismatch")
            if raw["session_id"] != session_id or raw["identity"] != self.identity:
                raise SessionConflict("R28 session identity/authority drift")
            self.rounds = dict(raw["rounds"])
            self.requests = dict(raw["requests"])
            self.closed = bool(raw["closed"])
            self.terminal_round = raw["terminal_round"]

    def _persist(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        body = {
            "version": LEDGER_VERSION,
            "session_id": self.session_id,
            "identity": self.identity,
            "rounds": self.rounds,
            "requests": self.requests,
            "closed": self.closed,
            "terminal_round": self.terminal_round,
        }
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(body, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    @property
    def next_round(self) -> int:
        if self.closed:
            return MAX_REVIEW_ROUND + 1
        completed = sorted(int(key) for key in self.rounds)
        if not completed:
            return 0
        last = completed[-1]
        return last + 1

    def apply(
        self,
        *,
        round_result: Mapping[str, Any],
        raw_fingerprint: str,
    ) -> bool:
        review_round = _round(round_result["review_round"], "round_result.review_round")
        key = str(review_round)
        capture = round_result["capture"]
        conversation = capture["conversation"]
        request_key = (
            conversation["conversation_id"]
            + "\n"
            + conversation["request_id"]
            + "\n"
            + str(review_round)
        )
        prior = self.rounds.get(key)
        if prior is not None:
            if prior["raw_fingerprint"] != raw_fingerprint:
                raise SessionConflict(
                    "same session review round changed capture/package/mapping/response bytes"
                )
            if prior["round_result"]["round_result_digest"] != round_result["round_result_digest"]:
                raise SessionConflict("exact session replay changed round result")
            return False
        if self.closed:
            raise RoundSequenceError("session already ended by terminal winner")
        expected = self.next_round
        if review_round != expected:
            raise RoundSequenceError(
                f"review round sequence violation: expected {expected}, got {review_round}"
            )
        if review_round > MAX_REVIEW_ROUND:
            raise RoundSequenceError("fourth review is forbidden")
        prior_request = self.requests.get(request_key)
        if prior_request is not None and prior_request != raw_fingerprint:
            raise SessionConflict("same session request identity has conflicting bytes")
        self.rounds[key] = {
            "raw_fingerprint": raw_fingerprint,
            "round_result": _clone(round_result),
        }
        self.requests[request_key] = raw_fingerprint
        if round_result["terminal_winner"]:
            self.closed = True
            self.terminal_round = review_round
        self._persist()
        return True


def _write_live_outputs(
    *,
    out_dir: Path,
    round_result: Mapping[str, Any],
    envelopes: Mapping[str, Mapping[str, Any]],
    effect: bool,
) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    round_no = round_result["review_round"]
    round_dir = out / f"round-{round_no}"
    if effect:
        round_dir.mkdir(parents=True, exist_ok=True)
        for candidate_id, envelope in sorted(envelopes.items()):
            slug = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:16]
            (round_dir / f"creator-envelope-{slug}.json").write_text(
                json.dumps(envelope, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        (round_dir / "round-result.json").write_text(
            json.dumps(round_result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    prior = round_dir / "round-result.json"
    if not prior.is_file():
        raise SessionConflict("exact replay output missing")
    if _load(prior).get("round_result_digest") != round_result["round_result_digest"]:
        raise SessionConflict("exact replay output digest drift")


def ingest_round(
    *,
    operator_dir: Path,
    capture_path: Path,
    authority_profile: Mapping[str, Any],
    session_dir: Path,
    out_dir: Path,
    conversation_id: str,
    growth_sha: str,
    growth_ci_run_id: int,
    terminal_winner: bool = False,
) -> dict[str, Any]:
    profile = validate_authority_profile(authority_profile)
    package = validate_operator_package(operator_dir, profile=profile)
    capture, r31_wrapper = load_live_capture(capture_path)
    if capture.get("conversationId") != conversation_id:
        raise SessionConflict("capture conversation differs from dedicated review conversation")
    validated = validate_capture_against_package(capture, package=package)
    normalized = validated["normalized_media_package"]
    parsed = validated["parsed_capture"]
    ingest = convert_dynamic_capture(
        media_package=normalized,
        parsed_capture=parsed,
    )
    if ingest.get("evidence_state") != "LIVE_REVIEW_INGESTED":
        raise NonLiveCapture("dynamic converter did not produce live review")
    authority_digest = authority_profile_digest(profile)
    session_id, identity = _session_identity(
        source=package["source"],
        brief_digest=package["brief_lineage_digest"],
        conversation_id=conversation_id,
        authority_digest=authority_digest,
    )
    ledger = SessionLedger(session_dir, session_id=session_id, identity=identity)

    envelopes = {
        candidate_id: build_creator_envelope(
            ingest_result=ingest,
            candidate_id=candidate_id,
            growth_producer_sha=r27.CREATOR_R29_GROWTH_R26_SHA,
            growth_ci_run_id=r27.CREATOR_R29_GROWTH_R26_CI,
        )
        for candidate_id in sorted(ingest["dynamic_handoffs"])
    }
    selected = _selected_result(ingest=ingest, envelopes=envelopes)
    result = _round_result(
        session_id=session_id,
        package=package,
        ingest=ingest,
        envelopes=envelopes,
        selected=selected,
        r31_wrapper=r31_wrapper,
        terminal_winner=terminal_winner,
    )
    fingerprint = sha256_json(
        {
            "capture_payload": capture,
            "r31_wrapper": r31_wrapper,
            "operator_manifest_sha256": package["operator_manifest_sha256"],
            "archive_sha256": package["archive_sha256"],
            "package_digest": package["package_digest"],
            "mapping_file_sha256": package["raw_mapping_file_sha256"],
            "sealed_mapping_digest": package["sealed_mapping_digest"],
            "response_digest": ingest["capture"]["assistant_response_digest"],
            "request_id": ingest["capture"]["conversation"]["request_id"],
            "operation_id": ingest["capture"]["conversation"]["operation_id"],
            "review_round": ingest["review_round"],
            "authority_profile_digest": authority_digest,
        }
    )
    effect = ledger.apply(round_result=result, raw_fingerprint=fingerprint)
    _write_live_outputs(
        out_dir=out_dir,
        round_result=result,
        envelopes=envelopes,
        effect=effect,
    )
    summary = {
        "report_version": REPORT_VERSION,
        "state": "LIVE_REVIEW_INGESTED",
        "session_id": session_id,
        "session_closed": ledger.closed,
        "terminal_round": ledger.terminal_round,
        "review_round": ingest["review_round"],
        "next_review_round": ledger.next_round,
        "new_ingest_effect": effect,
        "round_result_digest": result["round_result_digest"],
        "selected_result": result["selected_result"],
        "pairwise": result["pairwise"],
        "candidate_envelope_count": len(envelopes),
        "creator_executable_handoff_emitted": True,
        "fixture_only": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
        "growth_r28": {
            "producer_sha": _sha1(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "authority_profile_digest": authority_digest,
        },
    }
    summary["report_digest"] = sha256_json(summary)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "session-readiness.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _clone(summary)


def source_ready(
    *,
    operator_dir: Path,
    authority_profile: Mapping[str, Any],
    session_dir: Path,
    out_dir: Path,
    conversation_id: str,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    profile = validate_authority_profile(authority_profile)
    package = validate_operator_package(operator_dir, profile=profile)
    authority_digest = authority_profile_digest(profile)
    session_id, identity = _session_identity(
        source=package["source"],
        brief_digest=package["brief_lineage_digest"],
        conversation_id=conversation_id,
        authority_digest=authority_digest,
    )
    ledger = SessionLedger(session_dir, session_id=session_id, identity=identity)
    sequence_ready = (
        package["review_round"] == ledger.next_round
        or str(package["review_round"]) in ledger.rounds
    )
    report = {
        "report_version": REPORT_VERSION,
        "state": "SOURCE_READY",
        "live_capture_gate": "BLOCKED_WAITING_GENUINE_CAPTURE",
        "session_id": session_id,
        "session_closed": ledger.closed,
        "next_review_round": ledger.next_round,
        "package_review_round": package["review_round"],
        "package_sequence_ready": sequence_ready,
        "package_digest": package["package_digest"],
        "sealed_mapping_digest": package["sealed_mapping_digest"],
        "prompt_digest": package["prompt_digest"],
        "operator_manifest_sha256": package["operator_manifest_sha256"],
        "archive_sha256": package["archive_sha256"],
        "fixture_only": False,
        "live_review_ingested": False,
        "creator_executable_handoff_emitted": False,
        "media_r23_distinct_authority_available": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
        "growth_r28": {
            "producer_sha": _sha1(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "authority_profile_digest": authority_digest,
        },
    }
    report["report_digest"] = sha256_json(report)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "session-readiness.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _clone(report)


def rehearse_fixture(
    *,
    fixture: Mapping[str, Any],
    authority_profile: Mapping[str, Any],
    out_dir: Path,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    profile = validate_authority_profile(authority_profile)
    if not isinstance(fixture, Mapping) or fixture.get("contract_version") != FIXTURE_REHEARSAL_VERSION:
        raise R28Error("R28 fixture rehearsal contract mismatch")
    if fixture.get("fixture_only") is not True:
        raise R28Error("R28 rehearsal must be fixture_only=true")
    source = fixture.get("source")
    if not isinstance(source, Mapping) or set(source) != {"source_id", "sha256", "size"}:
        raise R28Error("R28 fixture source invalid")
    brief = _sha(fixture.get("brief_lineage_digest"), "fixture.brief_lineage_digest")
    conversation = _nonempty(fixture.get("conversation_id"), "fixture.conversation_id")
    rounds = fixture.get("rounds")
    if not isinstance(rounds, list) or [row.get("review_round") for row in rounds] != [0, 1, 2]:
        raise RoundSequenceError("fixture rehearsal must prove exact round sequence 0 -> 1 -> 2")
    allowed = {"winner", "tie", "insufficient_evidence", "human_review"}
    for index, row in enumerate(rounds):
        if row.get("fixture_only") is not True:
            raise NonLiveCapture(f"fixture round {index} missing fixture_only=true")
        if row.get("outcome") not in allowed:
            raise R28Error(f"fixture round {index} outcome invalid")
        _sha(row.get("response_digest"), f"fixture.rounds[{index}].response_digest")
        _sha(row.get("package_digest"), f"fixture.rounds[{index}].package_digest")
        _sha(row.get("mapping_digest"), f"fixture.rounds[{index}].mapping_digest")
    authority_digest = authority_profile_digest(profile)
    session_id, _ = _session_identity(
        source=source,
        brief_digest=brief,
        conversation_id=conversation,
        authority_digest=authority_digest,
    )
    report = {
        "report_version": REPORT_VERSION,
        "state": "SOURCE_READY",
        "rehearsal_contract": FIXTURE_REHEARSAL_VERSION,
        "session_id": session_id,
        "round_sequence_proven": [0, 1, 2],
        "fourth_review_rejected": True,
        "fixture_only": True,
        "fixture_capture_count": 3,
        "live_review_ingested": False,
        "creator_executable_handoff_emitted": False,
        "fixture_promoted": False,
        "media_r22_rounds_exactly_available": [0, 1],
        "round_2_live_package_policy": (
            "requires exact R22-materializer package or future explicitly frozen Media R23 authority"
        ),
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
        "growth_r28": {
            "producer_sha": _sha1(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "authority_profile_digest": authority_digest,
        },
    }
    report["report_digest"] = sha256_json(report)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "fixture-multiround-rehearsal.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _clone(report)


def _write_blocked(
    *,
    path: Path,
    state: str,
    exc: Exception,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    report = {
        "report_version": REPORT_VERSION,
        "state": state,
        "reason": type(exc).__name__,
        "detail": str(exc),
        "growth_r28": {
            "producer_sha": _sha1(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
        },
        "live_review_ingested": False,
        "creator_executable_handoff_emitted": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
    }
    report["report_digest"] = sha256_json(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-multiround-session-r28")
    sub = parser.add_subparsers(dest="command", required=True)

    ready = sub.add_parser("source-ready")
    ready.add_argument("--operator-dir", required=True)
    ready.add_argument("--authority-profile", required=True)
    ready.add_argument("--session-dir", required=True)
    ready.add_argument("--out-dir", required=True)
    ready.add_argument("--conversation-id", required=True)
    ready.add_argument("--growth-sha", required=True)
    ready.add_argument("--growth-ci-run-id", type=int, required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--operator-dir", required=True)
    ingest.add_argument("--capture", required=True)
    ingest.add_argument("--authority-profile", required=True)
    ingest.add_argument("--session-dir", required=True)
    ingest.add_argument("--out-dir", required=True)
    ingest.add_argument("--conversation-id", required=True)
    ingest.add_argument("--growth-sha", required=True)
    ingest.add_argument("--growth-ci-run-id", type=int, required=True)
    ingest.add_argument("--terminal-winner", action="store_true")

    rehearsal = sub.add_parser("rehearse-fixtures")
    rehearsal.add_argument("--fixture", required=True)
    rehearsal.add_argument("--authority-profile", required=True)
    rehearsal.add_argument("--out-dir", required=True)
    rehearsal.add_argument("--growth-sha", required=True)
    rehearsal.add_argument("--growth-ci-run-id", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    profile = _load(Path(args.authority_profile))
    growth_sha = args.growth_sha
    growth_ci = args.growth_ci_run_id
    try:
        if args.command == "source-ready":
            report = source_ready(
                operator_dir=Path(args.operator_dir),
                authority_profile=profile,
                session_dir=Path(args.session_dir),
                out_dir=Path(args.out_dir),
                conversation_id=args.conversation_id,
                growth_sha=growth_sha,
                growth_ci_run_id=growth_ci,
            )
        elif args.command == "rehearse-fixtures":
            report = rehearse_fixture(
                fixture=_load(Path(args.fixture)),
                authority_profile=profile,
                out_dir=Path(args.out_dir),
                growth_sha=growth_sha,
                growth_ci_run_id=growth_ci,
            )
        else:
            report = ingest_round(
                operator_dir=Path(args.operator_dir),
                capture_path=Path(args.capture),
                authority_profile=profile,
                session_dir=Path(args.session_dir),
                out_dir=Path(args.out_dir),
                conversation_id=args.conversation_id,
                growth_sha=growth_sha,
                growth_ci_run_id=growth_ci,
                terminal_winner=bool(args.terminal_winner),
            )
        print(json.dumps(report, sort_keys=True))
        return 0
    except MalformedModelResponse as exc:
        out = Path(args.out_dir)
        report = _write_blocked(
            path=out / "session-readiness.json",
            state="MALFORMED_MODEL_RESPONSE",
            exc=exc,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci,
        )
        print(json.dumps(report, sort_keys=True))
        return 3
    except ReconciliationRequired as exc:
        out = Path(args.out_dir)
        report = _write_blocked(
            path=out / "session-readiness.json",
            state="RECONCILIATION_REQUIRED",
            exc=exc,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci,
        )
        print(json.dumps(report, sort_keys=True))
        return 4
    except Exception as exc:
        out = Path(args.out_dir)
        report = _write_blocked(
            path=out / "session-readiness.json",
            state="BLOCKED_WAITING_GENUINE_CAPTURE",
            exc=exc,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci,
        )
        print(json.dumps(report, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
