from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .dynamic_review_capture_r26 import (
    CREATOR_ENVELOPE_VERSION,
    DYNAMIC_CAPTURE_VERSION,
    build_creator_envelope,
    convert_dynamic_capture,
    parse_dynamic_review_response,
)

OPERATOR_VERSION = "growth.live_ingest_operator.r27.v1"
INDEX_VERSION = "growth.live_ingest_operator_index.r27.v1"
READINESS_VERSION = "growth.live_ingest_operator_r27.readiness.v1"
MEDIA_AUTHORITY_VERSION = "growth.media_review_round_authority.r27.v1"
BRIDGE_AUTHORITY_VERSION = "growth.bridge_live_capture_authority.r27.v1"
LEDGER_VERSION = "growth.live_ingest_operator_ledger.r27.v1"

GROWTH_R26_SHA = "e844ed2daaaca9e9694fe1e0fb6b8b7bfac69cbc"
GROWTH_R26_CI_RUN_ID = 36994388154
MEDIA_R21_SHA = "d753e9e4c1f4448386608a1425232dbc1dba87ea"
MEDIA_R21_CI_RUN_ID = 36994000619
MEDIA_R21_ARTIFACT_ID = 11221240371
MEDIA_R21_ARTIFACT_NAME = "media-r21-round-pair-review"
MEDIA_R21_ARTIFACT_DIGEST = (
    "sha256:1036800923196882590ace62edbaa123ab4250b9d242e14adba909ba256ab022"
)

BRIDGE_R30_SHA = "ceaee873231a8552c5b7324083baa800eec566a8"
BRIDGE_R30_CI_RUN_ID = 36993885456
BRIDGE_DYNAMIC_CAPTURE_CONTRACT = "bridge.dynamic_existing_chat_video_review_capture.v1"
BRIDGE_R30_CAPTURE_SCHEMA = "bridge://bridge.dynamic_existing_chat_video_review_capture.v1"
BRIDGE_R30_CONTRACT_BLOB = "93968dc1fb65a334493acdb587b20753f0a8494a"
BRIDGE_R30_SCHEMA_BLOB = "2cbe22ad6c7fe877764bad8dcfc1496aef3f3737"
BRIDGE_R30_IMPLEMENTATION_BLOB = "c5bd2f95a6d58a86cddd9a6fdc127e68e3346c20"

BRIDGE_R31_SHA = "31cfef82663d72d53e69e6345b50073ffcd461ca"
BRIDGE_R31_CI_RUN_ID = 36997793086
BRIDGE_R31_NATIVE_AUTHORITY_CONTRACT = "bridge.r31_media_r21_authority_profile.v1"
BRIDGE_R31_LIVE_RESULT_CONTRACT = "bridge.r31_live_dynamic_operator_result.v1"
BRIDGE_R31_AUTHORITY_SCHEMA_BLOB = "25e2cbfe487ba88f70d233774e585691ad4f70c6"
BRIDGE_R31_MANIFEST_SCHEMA_BLOB = "f90cd2aa4bdc868af845bfba58c612ebef3f32ad"
BRIDGE_R31_RESULT_SCHEMA_BLOB = "455344dd091c552f12d91c6b8fb059ac9c70711e"
BRIDGE_R31_IMPLEMENTATION_BLOB = "38509af174fc25aa4229c084fc3cd9b2e35b539e"
BRIDGE_R31_FINALIZER_BLOB = "bb92f9cc87e557da3277983ba9c3970f4e9c6bfa"

CREATOR_R29_OBSERVED_SHA = "ab0809f902ab26990721502feda39e56753f56e8"
CREATOR_R29_OBSERVED_CI = 36998464412
CREATOR_R29_IMPLEMENTATION_BLOB = "d8e359c9c6880b0e571fa44badded079783271b0"

ALLOWED_MEDIA_ROUNDS = {"R21", "R22"}
ALLOWED_BRIDGE_ROUNDS = {"R30", "R31"}
ALLOWED_MEDIA_CONTRACTS = {
    "media.review_round_bundle.r21.v1",
    "media.review_round_bundle.r22.v1",
}

_FILE_KEYS = {
    "bundle",
    "evidence",
    "transport_handoff",
    "sealed_mapping",
    "prompt",
}


class LiveIngestOperatorError(ValueError):
    pass


class OperatorAuthorityError(LiveIngestOperatorError):
    pass


class OperatorLineageError(LiveIngestOperatorError):
    pass


class OperatorBoundaryError(LiveIngestOperatorError):
    pass


class OperatorReplayConflict(LiveIngestOperatorError):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiveIngestOperatorError(f"{field} must be non-empty string")
    return value


def _hex(value: Any, size: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != size
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise LiveIngestOperatorError(f"{field} must be lowercase {size}-hex")
    return value


def _sha256(value: Any, field: str) -> str:
    return _hex(value, 64, field)


def _sha1(value: Any, field: str) -> str:
    return _hex(value, 40, field)


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LiveIngestOperatorError(f"{field} must be integer >= 1")
    return value


def _round(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2:
        raise OperatorBoundaryError(f"{field} must be integer 0..2")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise OperatorAuthorityError(f"{field} must be sha256:<64-hex>")
    _sha256(value[7:], field)
    return value


def _safe_relative_file(root: Path, name: str, field: str) -> Path:
    rel = Path(_nonempty(name, field))
    if rel.is_absolute() or ".." in rel.parts:
        raise OperatorBoundaryError(f"{field} must stay inside package directory")
    root = Path(root).resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise OperatorBoundaryError(f"{field} escapes package directory") from exc
    if not target.is_file():
        raise OperatorLineageError(f"missing package file: {name}")
    return target


def parse_media_authority(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract_version",
        "producer_round",
        "repository",
        "producer_sha",
        "ci_run_id",
        "package_contract",
        "contract_blob_sha1",
        "schema_blob_sha1",
        "manifest_blob_sha1",
        "implementation_blob_sha1",
        "runner_blob_sha1",
        "artifact_id",
        "artifact_name",
        "artifact_digest",
        "files",
        "package_digest",
        "sealed_mapping_digest",
        "prompt_digest",
        "round_lineage_digest",
        "mode",
        "review_round",
        "brief_lineage_digest",
        "source",
        "attachments",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise OperatorAuthorityError("Media authority profile fields invalid")
    if payload["contract_version"] != MEDIA_AUTHORITY_VERSION:
        raise OperatorAuthorityError("Media authority profile contract mismatch")
    producer_round = payload["producer_round"]
    if producer_round not in ALLOWED_MEDIA_ROUNDS:
        raise OperatorAuthorityError("Media producer round must be R21 or R22")
    if payload["repository"] != "foto6/video2":
        raise OperatorAuthorityError("Media repository mismatch")
    package_contract = _nonempty(payload["package_contract"], "media.package_contract")
    if package_contract not in ALLOWED_MEDIA_CONTRACTS:
        raise OperatorAuthorityError("unsupported Media review-round contract")

    files = payload["files"]
    if not isinstance(files, Mapping) or set(files) != _FILE_KEYS:
        raise OperatorAuthorityError("Media authority files manifest invalid")
    normalized_files = {}
    for key in sorted(_FILE_KEYS):
        row = files[key]
        if not isinstance(row, Mapping) or set(row) != {"name", "sha256"}:
            raise OperatorAuthorityError(f"media.files.{key} fields invalid")
        normalized_files[key] = {
            "name": _nonempty(row["name"], f"media.files.{key}.name"),
            "sha256": _sha256(row["sha256"], f"media.files.{key}.sha256"),
        }

    source = payload["source"]
    if not isinstance(source, Mapping) or set(source) != {
        "source_id",
        "sha256",
        "size",
    }:
        raise OperatorAuthorityError("Media source authority fields invalid")
    normalized_source = {
        "source_id": _nonempty(source["source_id"], "media.source.source_id"),
        "sha256": _sha256(source["sha256"], "media.source.sha256"),
        "size": _positive_int(source["size"], "media.source.size"),
    }

    attachments = payload["attachments"]
    if not isinstance(attachments, list) or len(attachments) != 2:
        raise OperatorAuthorityError("Media authority requires exact A/B attachments")
    normalized_attachments = []
    seen = set()
    for index, row in enumerate(attachments):
        if not isinstance(row, Mapping) or set(row) != {
            "blind_label",
            "path",
            "sha256",
            "size",
            "mime_type",
        }:
            raise OperatorAuthorityError(f"media.attachments[{index}] fields invalid")
        label = row["blind_label"]
        if label not in {"A", "B"} or label in seen:
            raise OperatorAuthorityError("Media authority attachment labels must be unique A/B")
        seen.add(label)
        normalized_attachments.append(
            {
                "blind_label": label,
                "path": _nonempty(row["path"], f"media.attachments[{index}].path"),
                "sha256": _sha256(
                    row["sha256"], f"media.attachments[{index}].sha256"
                ),
                "size": _positive_int(
                    row["size"], f"media.attachments[{index}].size"
                ),
                "mime_type": _nonempty(
                    row["mime_type"], f"media.attachments[{index}].mime_type"
                ),
            }
        )

    mode = payload["mode"]
    if mode not in {"initial", "targeted_reedit"}:
        raise OperatorAuthorityError("Media mode invalid")
    normalized = {
        "contract_version": MEDIA_AUTHORITY_VERSION,
        "producer_round": producer_round,
        "repository": "foto6/video2",
        "producer_sha": _sha1(payload["producer_sha"], "media.producer_sha"),
        "ci_run_id": _positive_int(payload["ci_run_id"], "media.ci_run_id"),
        "package_contract": package_contract,
        "contract_blob_sha1": _sha1(
            payload["contract_blob_sha1"], "media.contract_blob_sha1"
        ),
        "schema_blob_sha1": _sha1(
            payload["schema_blob_sha1"], "media.schema_blob_sha1"
        ),
        "manifest_blob_sha1": _sha1(
            payload["manifest_blob_sha1"], "media.manifest_blob_sha1"
        ),
        "implementation_blob_sha1": _sha1(
            payload["implementation_blob_sha1"], "media.implementation_blob_sha1"
        ),
        "runner_blob_sha1": _sha1(
            payload["runner_blob_sha1"], "media.runner_blob_sha1"
        ),
        "artifact_id": _positive_int(payload["artifact_id"], "media.artifact_id"),
        "artifact_name": _nonempty(payload["artifact_name"], "media.artifact_name"),
        "artifact_digest": _artifact_digest(
            payload["artifact_digest"], "media.artifact_digest"
        ),
        "files": normalized_files,
        "package_digest": _sha256(payload["package_digest"], "media.package_digest"),
        "sealed_mapping_digest": _sha256(
            payload["sealed_mapping_digest"], "media.sealed_mapping_digest"
        ),
        "prompt_digest": _sha256(payload["prompt_digest"], "media.prompt_digest"),
        "round_lineage_digest": _sha256(
            payload["round_lineage_digest"], "media.round_lineage_digest"
        ),
        "mode": mode,
        "review_round": _round(payload["review_round"], "media.review_round"),
        "brief_lineage_digest": _sha256(
            payload["brief_lineage_digest"], "media.brief_lineage_digest"
        ),
        "source": normalized_source,
        "attachments": sorted(
            normalized_attachments, key=lambda row: row["blind_label"]
        ),
    }
    if producer_round == "R21":
        expected = {
            "producer_sha": MEDIA_R21_SHA,
            "ci_run_id": MEDIA_R21_CI_RUN_ID,
            "artifact_id": MEDIA_R21_ARTIFACT_ID,
            "artifact_name": MEDIA_R21_ARTIFACT_NAME,
            "artifact_digest": MEDIA_R21_ARTIFACT_DIGEST,
            "package_contract": "media.review_round_bundle.r21.v1",
            "contract_blob_sha1": "65358261775f0fcd2ab9e21f3f621aee977f29da",
            "schema_blob_sha1": "f04925e317d849434852e6b706533f909da47b22",
            "manifest_blob_sha1": "f76033375e7ee03b56491722e2e99e7334bd2cad",
            "implementation_blob_sha1": "c6f556b8a177b6182d787356625094cdcad5a58e",
            "runner_blob_sha1": "c93e9a69de31b66189031932ccfa7f2c83cf043c",
        }
        for key, value in expected.items():
            if normalized[key] != value:
                raise OperatorAuthorityError(f"exact Media R21 authority drift: {key}")
    return _clone(normalized)


def _creator_r29_media_authority(
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "contract_version": "growth.media_dynamic_review_authority.r26.v1",
        "repository": "foto6/video2",
        "producer_sha": MEDIA_R21_SHA,
        "ci_run_id": MEDIA_R21_CI_RUN_ID,
        "package_contract": "media.dynamic_review_package.r21.v1",
        "contract_blob_sha1": "65358261775f0fcd2ab9e21f3f621aee977f29da",
        "schema_blob_sha1": "f04925e317d849434852e6b706533f909da47b22",
        "implementation_blob_sha1": "c6f556b8a177b6182d787356625094cdcad5a58e",
        "artifact_id": MEDIA_R21_ARTIFACT_ID,
        "artifact_name": MEDIA_R21_ARTIFACT_NAME,
        "artifact_digest": MEDIA_R21_ARTIFACT_DIGEST,
        "package_digest": profile["package_digest"],
        "package_file_sha256": profile["files"]["bundle"]["sha256"],
        "evidence_file_sha256": profile["files"]["evidence"]["sha256"],
        "prompt_digest": profile["prompt_digest"],
        "prompt_file_sha256": profile["files"]["prompt"]["sha256"],
        "sealed_mapping_digest": profile["sealed_mapping_digest"],
        "sealed_mapping_file_sha256": profile["files"]["sealed_mapping"]["sha256"],
        "review_round": profile["review_round"],
        "source": _clone(profile["source"]),
        "attachments": [
            {
                "blind_label": row["blind_label"],
                "generic_file_name": row["path"],
                "sha256": row["sha256"],
                "size": row["size"],
                "mime_type": row["mime_type"],
            }
            for row in profile["attachments"]
        ],
    }


def _creator_r29_bridge_authority() -> dict[str, Any]:
    return {
        "contract_version": "growth.bridge_dynamic_capture_authority.r26.v1",
        "repository": "foto6/WebAIBridge",
        "producer_sha": BRIDGE_R30_SHA,
        "ci_run_id": BRIDGE_R30_CI_RUN_ID,
        "capture_contract": BRIDGE_DYNAMIC_CAPTURE_CONTRACT,
        "capture_schema_id": BRIDGE_R30_CAPTURE_SCHEMA,
        "contract_blob_sha1": BRIDGE_R30_CONTRACT_BLOB,
        "schema_blob_sha1": BRIDGE_R30_SCHEMA_BLOB,
        "implementation_blob_sha1": BRIDGE_R30_IMPLEMENTATION_BLOB,
    }


def parse_bridge_authority(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract_version",
        "producer_round",
        "repository",
        "producer_sha",
        "ci_run_id",
        "capture_contract",
        "capture_schema_id",
        "contract_blob_sha1",
        "schema_blob_sha1",
        "implementation_blob_sha1",
        "capture_file_sha256",
        "binding",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise OperatorAuthorityError("Bridge capture authority profile fields invalid")
    if payload["contract_version"] != BRIDGE_AUTHORITY_VERSION:
        raise OperatorAuthorityError("Bridge capture authority contract mismatch")
    producer_round = payload["producer_round"]
    if producer_round not in ALLOWED_BRIDGE_ROUNDS:
        raise OperatorAuthorityError("Bridge producer round must be R30 or R31")
    if payload["repository"] != "foto6/WebAIBridge":
        raise OperatorAuthorityError("Bridge repository mismatch")
    if payload["capture_contract"] != BRIDGE_DYNAMIC_CAPTURE_CONTRACT:
        raise OperatorAuthorityError("Bridge dynamic capture contract mismatch")

    binding = payload["binding"]
    required_binding = {
        "request_id",
        "operation_id",
        "conversation_id",
        "prompt_digest",
        "media_package_digest",
        "sealed_mapping_digest",
        "bridge_package_digest",
        "handoff_sha256",
        "source_binding_fingerprint",
        "assistant_response_digest",
    }
    if not isinstance(binding, Mapping) or set(binding) != required_binding:
        raise OperatorAuthorityError("Bridge capture binding fields invalid")
    normalized = {
        "contract_version": BRIDGE_AUTHORITY_VERSION,
        "producer_round": producer_round,
        "repository": "foto6/WebAIBridge",
        "producer_sha": _sha1(payload["producer_sha"], "bridge.producer_sha"),
        "ci_run_id": _positive_int(payload["ci_run_id"], "bridge.ci_run_id"),
        "capture_contract": BRIDGE_DYNAMIC_CAPTURE_CONTRACT,
        "capture_schema_id": _nonempty(
            payload["capture_schema_id"], "bridge.capture_schema_id"
        ),
        "contract_blob_sha1": _sha1(
            payload["contract_blob_sha1"], "bridge.contract_blob_sha1"
        ),
        "schema_blob_sha1": _sha1(
            payload["schema_blob_sha1"], "bridge.schema_blob_sha1"
        ),
        "implementation_blob_sha1": _sha1(
            payload["implementation_blob_sha1"], "bridge.implementation_blob_sha1"
        ),
        "capture_file_sha256": _sha256(
            payload["capture_file_sha256"], "bridge.capture_file_sha256"
        ),
        "binding": {
            "request_id": _nonempty(binding["request_id"], "bridge.binding.request_id"),
            "operation_id": _nonempty(
                binding["operation_id"], "bridge.binding.operation_id"
            ),
            "conversation_id": _nonempty(
                binding["conversation_id"], "bridge.binding.conversation_id"
            ),
            "prompt_digest": _sha256(
                binding["prompt_digest"], "bridge.binding.prompt_digest"
            ),
            "media_package_digest": _sha256(
                binding["media_package_digest"],
                "bridge.binding.media_package_digest",
            ),
            "sealed_mapping_digest": _sha256(
                binding["sealed_mapping_digest"],
                "bridge.binding.sealed_mapping_digest",
            ),
            "bridge_package_digest": _sha256(
                binding["bridge_package_digest"],
                "bridge.binding.bridge_package_digest",
            ),
            "handoff_sha256": _sha256(
                binding["handoff_sha256"], "bridge.binding.handoff_sha256"
            ),
            "source_binding_fingerprint": _sha256(
                binding["source_binding_fingerprint"],
                "bridge.binding.source_binding_fingerprint",
            ),
            "assistant_response_digest": _sha256(
                binding["assistant_response_digest"],
                "bridge.binding.assistant_response_digest",
            ),
        },
    }
    exact_by_round = {
        "R30": {
            "producer_sha": BRIDGE_R30_SHA,
            "ci_run_id": BRIDGE_R30_CI_RUN_ID,
            "capture_schema_id": BRIDGE_R30_CAPTURE_SCHEMA,
            "contract_blob_sha1": BRIDGE_R30_CONTRACT_BLOB,
            "schema_blob_sha1": BRIDGE_R30_SCHEMA_BLOB,
            "implementation_blob_sha1": BRIDGE_R30_IMPLEMENTATION_BLOB,
        },
        "R31": {
            "producer_sha": BRIDGE_R31_SHA,
            "ci_run_id": BRIDGE_R31_CI_RUN_ID,
            "capture_schema_id": BRIDGE_R30_CAPTURE_SCHEMA,
            "contract_blob_sha1": BRIDGE_R31_AUTHORITY_SCHEMA_BLOB,
            "schema_blob_sha1": BRIDGE_R31_RESULT_SCHEMA_BLOB,
            "implementation_blob_sha1": BRIDGE_R31_IMPLEMENTATION_BLOB,
        },
    }
    for key, value in exact_by_round[producer_round].items():
        if normalized[key] != value:
            raise OperatorAuthorityError(
                f"exact Bridge {producer_round} authority drift: {key}"
            )
    return _clone(normalized)


def _native_r31_media_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OperatorAuthorityError("R31 mediaAuthority must be object")
    required = {
        "repository", "sha", "ciRunId", "ciConclusion", "branch",
        "bundleContract", "handoffContract", "evidenceContract",
        "requiredState", "sourcePins",
    }
    if set(value) != required:
        raise OperatorAuthorityError("R31 mediaAuthority fields invalid")
    if (
        value["repository"] != "foto6/video2"
        or value["sha"] != MEDIA_R21_SHA
        or value["ciRunId"] != MEDIA_R21_CI_RUN_ID
        or value["ciConclusion"] != "success"
        or value["bundleContract"] != "media.review_round_bundle.r21.v1"
        or value["handoffContract"] != "media.review_round_transport_handoff.r21.v1"
        or value["evidenceContract"] != "media.review_round_bundle.r21.evidence.v1"
        or value["requiredState"] != "ROUND_PAIR_PACKAGE_READY"
    ):
        raise OperatorAuthorityError("R31 Media R21 authority drift")
    pins = value["sourcePins"]
    expected_pins = {
        "implementationGitBlob": "c6f556b8a177b6182d787356625094cdcad5a58e",
        "runnerGitBlob": "c93e9a69de31b66189031932ccfa7f2c83cf043c",
        "contractGitBlob": "65358261775f0fcd2ab9e21f3f621aee977f29da",
        "schemaGitBlob": "f04925e317d849434852e6b706533f909da47b22",
        "conformanceManifestGitBlob": "f76033375e7ee03b56491722e2e99e7334bd2cad",
    }
    if pins != expected_pins:
        raise OperatorAuthorityError("R31 Media R21 source blob drift")
    # The branch field is intentionally not compared; it is never authority.
    return _clone(value)


def _resolve_native_r31_authority(
    payload: Mapping[str, Any],
    *,
    media_package: Mapping[str, Any],
    capture: Mapping[str, Any],
    capture_file_sha256: str,
    live_result: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    required = {
        "contract", "state", "generatedAt", "mediaAuthority",
        "mediaEvidence", "bridgeTransport", "evidenceBoundary",
        "operatorManifestDigest",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise OperatorAuthorityError("native Bridge R31 authority profile fields invalid")
    if (
        payload["contract"] != BRIDGE_R31_NATIVE_AUTHORITY_CONTRACT
        or payload["state"] != "SOURCE_READY"
    ):
        raise OperatorAuthorityError("native Bridge R31 authority contract/state invalid")
    media_authority = _native_r31_media_authority(payload["mediaAuthority"])
    evidence = payload["mediaEvidence"]
    if not isinstance(evidence, Mapping):
        raise OperatorAuthorityError("R31 mediaEvidence must be object")
    for key, expected in (
        ("packageDigest", media_package["package_digest"]),
        ("promptDigest", media_package["prompt_digest"]),
        ("sealedMappingDigest", media_package["sealed_mapping_digest"]),
        ("mode", media_package["mode"]),
        ("reviewRound", media_package["review_round"]),
        ("roundLineageDigest", media_package["round_lineage_digest"]),
    ):
        if evidence.get(key) != expected:
            raise OperatorLineageError(f"R31 authority mediaEvidence drift: {key}")
    file_hashes = evidence.get("fileHashes")
    source_authority = media_package["source_authority"]
    if (
        isinstance(source_authority, Mapping)
        and source_authority.get("contract_version") == MEDIA_AUTHORITY_VERSION
    ):
        expected_files = {
            "bundleFileSha256": source_authority["files"]["bundle"]["sha256"],
            "handoffFileSha256": source_authority["files"]["transport_handoff"]["sha256"],
            "sealedMappingFileSha256": source_authority["files"]["sealed_mapping"]["sha256"],
            "promptFileSha256": source_authority["files"]["prompt"]["sha256"],
            "evidenceFileSha256": source_authority["files"]["evidence"]["sha256"],
        }
        if file_hashes != expected_files:
            raise OperatorLineageError("R31 authority exact Media file hashes drift")
    attachments = evidence.get("attachments")
    if not isinstance(attachments, list) or len(attachments) != 2:
        raise OperatorLineageError("R31 authority A/B attachment evidence missing")
    expected_attachments = media_package["attachments_by_label"]
    seen = set()
    for row in attachments:
        if not isinstance(row, Mapping):
            raise OperatorLineageError("R31 authority attachment evidence invalid")
        label = row.get("blindLabel")
        expected = expected_attachments.get(label)
        if expected is None or label in seen:
            raise OperatorLineageError("R31 authority attachment label drift")
        seen.add(label)
        if (
            row.get("name") != expected["generic_file_name"]
            or row.get("size") != expected["size"]
            or row.get("sha256") != expected["sha256"]
            or row.get("mime") != expected["mime_type"]
        ):
            raise OperatorLineageError("R31 authority attachment bytes/MIME drift")
    if seen != {"A", "B"}:
        raise OperatorLineageError("R31 authority must bind A and B")

    transport = payload["bridgeTransport"]
    if not isinstance(transport, Mapping) or set(transport) != {
        "inheritedR30Contract", "dynamicPackageDigest",
        "exactCandidateSha", "derivedHandoffSha256",
    }:
        raise OperatorAuthorityError("R31 bridgeTransport fields invalid")
    if (
        transport["inheritedR30Contract"] != "media.dynamic_review_handoff.v1"
        or transport["exactCandidateSha"] != BRIDGE_R31_SHA
    ):
        raise OperatorAuthorityError("R31 exact producer/transport authority drift")
    dynamic_package_digest = _sha256(
        transport["dynamicPackageDigest"], "r31.dynamicPackageDigest"
    )
    derived_handoff_sha = _sha256(
        transport["derivedHandoffSha256"], "r31.derivedHandoffSha256"
    )
    if payload["evidenceBoundary"] != {
        "browserMutationPerformed": False,
        "promptSent": False,
        "liveReviewPass": False,
        "model_evidence": False,
        "human_ground_truth": False,
    }:
        raise OperatorBoundaryError("R31 source authority evidence boundary drift")
    _sha256(payload["operatorManifestDigest"], "r31.operatorManifestDigest")

    if not isinstance(live_result, Mapping):
        raise OperatorBoundaryError(
            "native R31 live ingest requires r31-live-result.json"
        )
    if (
        live_result.get("contract") != BRIDGE_R31_LIVE_RESULT_CONTRACT
        or live_result.get("state") != "LIVE_REVIEW_PASS"
    ):
        raise OperatorBoundaryError("native R31 result is not LIVE_REVIEW_PASS")
    if (
        live_result.get("model_evidence") is not True
        or live_result.get("human_ground_truth") is not False
        or live_result.get("captureContract") != BRIDGE_DYNAMIC_CAPTURE_CONTRACT
        or live_result.get("currentLiveBridgeRestarted") is not False
        or live_result.get("currentLiveBridgeRepointed") is not False
        or live_result.get("currentLiveBridgeStateWritten") is not False
    ):
        raise OperatorBoundaryError("native R31 live-result evidence boundary invalid")
    if live_result.get("operatorManifestDigest") != payload["operatorManifestDigest"]:
        raise OperatorLineageError("R31 operator manifest digest drift")
    _native_r31_media_authority(live_result.get("mediaAuthority"))
    response_digest = _sha256(
        live_result.get("responseDigest"), "r31.live_result.responseDigest"
    )
    _sha256(live_result.get("captureDigest"), "r31.live_result.captureDigest")
    _sha256(
        live_result.get("responseFileSha256"),
        "r31.live_result.responseFileSha256",
    )
    request_id = _nonempty(live_result.get("requestId"), "r31.live_result.requestId")
    operation_id = _nonempty(
        live_result.get("operationId"), "r31.live_result.operationId"
    )
    conversation = live_result.get("conversation")
    if not isinstance(conversation, Mapping):
        raise OperatorLineageError("R31 live-result conversation binding missing")
    conversation_id = _nonempty(
        conversation.get("conversationId") or conversation.get("id"),
        "r31.live_result.conversationId",
    )

    dynamic = capture.get("dynamicPackage")
    if not isinstance(dynamic, Mapping):
        raise OperatorLineageError("R31 capture dynamicPackage binding missing")
    if dynamic.get("packageDigest") != dynamic_package_digest:
        raise OperatorLineageError("R31 dynamic package digest drift")
    if dynamic.get("handoffSha256") != derived_handoff_sha:
        raise OperatorLineageError("R31 derived handoff SHA drift")
    if dynamic.get("sealedMappingDigestRef") != media_package["sealed_mapping_digest"]:
        raise OperatorLineageError("R31 sealed mapping reference drift")

    profile = {
        "contract_version": BRIDGE_AUTHORITY_VERSION,
        "producer_round": "R31",
        "repository": "foto6/WebAIBridge",
        "producer_sha": BRIDGE_R31_SHA,
        "ci_run_id": BRIDGE_R31_CI_RUN_ID,
        "capture_contract": BRIDGE_DYNAMIC_CAPTURE_CONTRACT,
        "capture_schema_id": BRIDGE_R30_CAPTURE_SCHEMA,
        "contract_blob_sha1": BRIDGE_R31_AUTHORITY_SCHEMA_BLOB,
        "schema_blob_sha1": BRIDGE_R31_RESULT_SCHEMA_BLOB,
        "implementation_blob_sha1": BRIDGE_R31_IMPLEMENTATION_BLOB,
        "capture_file_sha256": _sha256(
            capture_file_sha256, "r31.capture_file_sha256"
        ),
        "binding": {
            "request_id": request_id,
            "operation_id": operation_id,
            "conversation_id": conversation_id,
            "prompt_digest": media_package["prompt_digest"],
            "media_package_digest": media_package["package_digest"],
            "sealed_mapping_digest": media_package["sealed_mapping_digest"],
            "bridge_package_digest": dynamic_package_digest,
            "handoff_sha256": derived_handoff_sha,
            "source_binding_fingerprint": _sha256(
                dynamic.get("sourceBindingFingerprint"),
                "r31.sourceBindingFingerprint",
            ),
            "assistant_response_digest": response_digest,
        },
    }
    source = {
        "contract": BRIDGE_R31_NATIVE_AUTHORITY_CONTRACT,
        "repository": "foto6/WebAIBridge",
        "producer_sha": BRIDGE_R31_SHA,
        "ci_run_id": BRIDGE_R31_CI_RUN_ID,
        "source_blobs": {
            "authority_schema_blob_sha1": BRIDGE_R31_AUTHORITY_SCHEMA_BLOB,
            "manifest_schema_blob_sha1": BRIDGE_R31_MANIFEST_SCHEMA_BLOB,
            "result_schema_blob_sha1": BRIDGE_R31_RESULT_SCHEMA_BLOB,
            "implementation_blob_sha1": BRIDGE_R31_IMPLEMENTATION_BLOB,
            "finalizer_blob_sha1": BRIDGE_R31_FINALIZER_BLOB,
        },
        "authority_profile_digest": sha256_json(payload),
        "live_result_digest": sha256_json(live_result),
        "media_authority": media_authority,
        "bridge_transport": _clone(transport),
    }
    return _clone(profile), _clone(source)


def _resolve_bridge_authority(
    payload: Mapping[str, Any],
    *,
    media_package: Mapping[str, Any],
    capture: Mapping[str, Any],
    capture_file_sha256: str,
    live_result: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(payload, Mapping) and payload.get("contract") == BRIDGE_R31_NATIVE_AUTHORITY_CONTRACT:
        return _resolve_native_r31_authority(
            payload,
            media_package=media_package,
            capture=capture,
            capture_file_sha256=capture_file_sha256,
            live_result=live_result,
        )
    profile = parse_bridge_authority(payload)
    if profile["capture_file_sha256"] != capture_file_sha256:
        raise OperatorLineageError("Bridge capture file bytes drift")
    return profile, _clone(profile)


def _read_bound_json(root: Path, row: Mapping[str, Any], field: str) -> dict[str, Any]:
    path = _safe_relative_file(root, row["name"], field)
    digest = _file_sha256(path)
    if digest != row["sha256"]:
        raise OperatorLineageError(f"{field} bytes drift")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OperatorLineageError(f"{field} must be JSON") from exc
    if not isinstance(value, Mapping):
        raise OperatorLineageError(f"{field} JSON must be object")
    return dict(value)


def _normalize_attachment(row: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    required = {
        "blindLabel",
        "path",
        "sha256",
        "size",
        "mimeType",
        "derivative_for_model_review",
    }
    if not isinstance(row, Mapping) or set(row) != required:
        raise OperatorLineageError(f"bundle.attachments[{index}] fields invalid")
    label = row["blindLabel"]
    if label not in {"A", "B"}:
        raise OperatorLineageError("bundle attachment blind label invalid")
    if row["mimeType"] != "video/mp4":
        raise OperatorBoundaryError("review attachment MIME must be video/mp4")
    if not isinstance(row["derivative_for_model_review"], bool):
        raise OperatorBoundaryError("attachment derivative flag must be boolean")
    return {
        "blind_label": label,
        "path": _nonempty(row["path"], f"bundle.attachments[{index}].path"),
        "sha256": _sha256(row["sha256"], f"bundle.attachments[{index}].sha256"),
        "size": _positive_int(row["size"], f"bundle.attachments[{index}].size"),
        "mime_type": "video/mp4",
        "derivative_for_model_review": row["derivative_for_model_review"],
    }


def _normalize_mapping_entry(
    row: Mapping[str, Any],
    *,
    index: int,
    source: Mapping[str, Any],
    producer_sha: str,
) -> dict[str, Any]:
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
        raise OperatorLineageError(f"sealedMapping.entries[{index}] fields invalid")
    label = row["blindLabel"]
    if label not in {"A", "B"}:
        raise OperatorLineageError("sealed mapping label invalid")
    entry_source = row["source"]
    if not isinstance(entry_source, Mapping):
        raise OperatorLineageError("sealed mapping source invalid")
    normalized_source = {
        "source_id": _nonempty(entry_source.get("sourceId"), "mapping.source.sourceId"),
        "sha256": _sha256(entry_source.get("sha256"), "mapping.source.sha256"),
        "size": _positive_int(entry_source.get("size"), "mapping.source.size"),
    }
    if normalized_source != source:
        raise OperatorLineageError("sealed mapping source lineage drift")
    render = row["render"]
    if not isinstance(render, Mapping):
        raise OperatorLineageError("sealed mapping render invalid")
    render_normalized = {
        "sha256": _sha256(render.get("sha256"), "mapping.render.sha256"),
        "size": _positive_int(render.get("size"), "mapping.render.size"),
    }
    export = row["renderExport"]
    if not isinstance(export, Mapping):
        raise OperatorLineageError("sealed mapping renderExport invalid")
    render_export = {
        "digest": _sha256(export.get("digest"), "mapping.renderExport.digest"),
        "file_sha256": _sha256(
            export.get("fileSha256"), "mapping.renderExport.fileSha256"
        ),
    }
    attachment = row["attachment"]
    if not isinstance(attachment, Mapping):
        raise OperatorLineageError("sealed mapping attachment invalid")
    attachment_normalized = {
        "sha256": _sha256(
            attachment.get("sha256"), "mapping.attachment.sha256"
        ),
        "size": _positive_int(
            attachment.get("size"), "mapping.attachment.size"
        ),
        "mime_type": _nonempty(
            attachment.get("mimeType"), "mapping.attachment.mimeType"
        ),
        "derivative_for_model_review": (
            attachment.get("derivative_for_model_review") is True
        ),
        "derivative": _clone(attachment.get("derivative")),
    }
    if attachment_normalized["mime_type"] != "video/mp4":
        raise OperatorBoundaryError("mapping attachment MIME must be video/mp4")
    if (
        not attachment_normalized["derivative_for_model_review"]
        and (
            render_normalized["sha256"] != attachment_normalized["sha256"]
            or render_normalized["size"] != attachment_normalized["size"]
        )
    ):
        raise OperatorLineageError(
            "non-derivative attachment must preserve exact render bytes"
        )
    render_producer = _sha1(
        row["renderProducerSha"], "mapping.renderProducerSha"
    )
    if render_producer != producer_sha:
        raise OperatorAuthorityError("mapping render producer drift")
    return {
        "blind_label": label,
        "candidate_id": _nonempty(row["candidateId"], "mapping.candidateId"),
        "role": _nonempty(row["role"], "mapping.role"),
        "generic_file_name": _nonempty(
            row["genericFileName"], "mapping.genericFileName"
        ),
        "candidate_round": _round(row["roundNumber"], "mapping.roundNumber"),
        "brief_lineage_digest": _sha256(
            row["briefLineageDigest"], "mapping.briefLineageDigest"
        ),
        "render_producer_sha": render_producer,
        "source": normalized_source,
        "render": render_normalized,
        "render_export": render_export,
        "attachment": attachment_normalized,
        "baseline_review_candidate_id": row["baselineReviewCandidateId"],
        "application_parent_candidate_id": row["applicationParentCandidateId"],
        "parent_render_sha256": row["parentRenderSha256"],
        "growth_handoff_digest": row["growthHandoffDigest"],
        "media_application_digest": row["mediaApplicationDigest"],
        "editorial_application": None,
    }


def load_media_package(
    package_dir: Path,
    *,
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    profile = parse_media_authority(authority)
    root = Path(package_dir).resolve()
    if not root.is_dir():
        raise OperatorLineageError("Media package directory missing")

    bundle = _read_bound_json(root, profile["files"]["bundle"], "media.bundle")
    evidence = _read_bound_json(
        root, profile["files"]["evidence"], "media.evidence"
    )
    handoff = _read_bound_json(
        root, profile["files"]["transport_handoff"], "media.transport_handoff"
    )
    mapping = _read_bound_json(
        root, profile["files"]["sealed_mapping"], "media.sealed_mapping"
    )
    prompt = _read_bound_json(root, profile["files"]["prompt"], "media.prompt")

    if bundle.get("contractVersion") != profile["package_contract"]:
        raise OperatorAuthorityError("Media package contract/profile mismatch")
    if bundle.get("state") != "ROUND_PAIR_PACKAGE_READY":
        raise OperatorBoundaryError("Media package state is not ROUND_PAIR_PACKAGE_READY")
    if bundle.get("producer") != {
        "repository": profile["repository"],
        "sha": profile["producer_sha"],
    }:
        raise OperatorAuthorityError("Media package producer drift")
    for key in ("modelReviewPerformed", "liveModelReviewed", "providerPublish", "humanQuality"):
        if bundle.get(key) is not False:
            raise OperatorBoundaryError(f"Media source package boundary invalid: {key}")

    core = {key: value for key, value in bundle.items() if key != "transportHandoff"}
    package_digest = sha256_json(core)
    if (
        package_digest != profile["package_digest"]
        or bundle.get("transportHandoff", {}).get("packageDigest") != package_digest
        or evidence.get("packageDigest") != package_digest
    ):
        raise OperatorLineageError("Media package digest drift")

    if not isinstance(mapping.get("entries"), list) or len(mapping["entries"]) != 2:
        raise OperatorLineageError("sealed mapping must contain exactly two entries")
    mapping_digest = sha256_json(mapping["entries"])
    if (
        mapping.get("digest") != mapping_digest
        or mapping_digest != profile["sealed_mapping_digest"]
        or bundle.get("sealedMapping") != mapping
        or bundle.get("transportHandoff", {}).get("sealedMappingDigest") != mapping_digest
        or evidence.get("sealedMappingDigest") != mapping_digest
    ):
        raise OperatorLineageError("sealed mapping digest/content drift")

    if set(prompt) != {"text", "digest", "bytes"}:
        raise OperatorLineageError("Media prompt file fields invalid")
    prompt_text = _nonempty(prompt["text"], "media.prompt.text")
    prompt_digest = _text_sha256(prompt_text)
    if (
        prompt_digest != profile["prompt_digest"]
        or prompt.get("digest") != prompt_digest
        or prompt.get("bytes") != len(prompt_text.encode("utf-8"))
        or bundle.get("prompt") != {"text": prompt_text, "digest": prompt_digest}
        or evidence.get("promptDigest") != prompt_digest
        or handoff.get("promptDigest") != prompt_digest
        or handoff.get("promptText") != prompt_text
    ):
        raise OperatorLineageError("Media prompt digest/content drift")

    source_raw = bundle.get("source")
    if not isinstance(source_raw, Mapping):
        raise OperatorLineageError("Media source missing")
    source = {
        "source_id": _nonempty(source_raw.get("sourceId"), "bundle.source.sourceId"),
        "sha256": _sha256(source_raw.get("sha256"), "bundle.source.sha256"),
        "size": _positive_int(source_raw.get("size"), "bundle.source.size"),
    }
    if source != profile["source"]:
        raise OperatorLineageError("Media source authority drift")
    if evidence.get("source") != source_raw:
        raise OperatorLineageError("Media evidence source drift")

    review_round = _round(bundle.get("reviewRound"), "bundle.reviewRound")
    mode = bundle.get("mode")
    if (
        review_round != profile["review_round"]
        or mode != profile["mode"]
        or bundle.get("briefLineageDigest") != profile["brief_lineage_digest"]
    ):
        raise OperatorLineageError("Media round/mode/brief lineage drift")
    round_material = {
        "source": source_raw,
        "briefLineageDigest": bundle["briefLineageDigest"],
        "reviewRound": review_round,
        "roundLineage": bundle.get("roundLineage"),
    }
    round_digest = sha256_json(round_material)
    if (
        round_digest != profile["round_lineage_digest"]
        or evidence.get("roundLineageDigest") != round_digest
        or handoff.get("roundLineage", {}).get("digest") != round_digest
    ):
        raise OperatorLineageError("Media round lineage digest drift")

    bundle_attachments = bundle.get("attachments")
    if not isinstance(bundle_attachments, list) or len(bundle_attachments) != 2:
        raise OperatorLineageError("Media bundle requires exact A/B attachments")
    normalized_attachments = [
        _normalize_attachment(row, index=index)
        for index, row in enumerate(bundle_attachments)
    ]
    by_label = {row["blind_label"]: row for row in normalized_attachments}
    if set(by_label) != {"A", "B"} or len(by_label) != 2:
        raise OperatorLineageError("Media bundle labels must be unique A/B")
    profile_attachments = {
        row["blind_label"]: row for row in profile["attachments"]
    }
    for label in ("A", "B"):
        observed = by_label[label]
        expected = profile_attachments[label]
        if {
            "blind_label": observed["blind_label"],
            "path": observed["path"],
            "sha256": observed["sha256"],
            "size": observed["size"],
            "mime_type": observed["mime_type"],
        } != expected:
            raise OperatorLineageError("Media attachment authority drift")
        file_path = _safe_relative_file(
            root, observed["path"], f"media.attachment.{label}.path"
        )
        if file_path.stat().st_size != observed["size"]:
            raise OperatorLineageError("Media attachment byte size drift")
        if _file_sha256(file_path) != observed["sha256"]:
            raise OperatorLineageError("Media attachment byte hash drift")

    entries = [
        _normalize_mapping_entry(
            row,
            index=index,
            source=source,
            producer_sha=profile["producer_sha"],
        )
        for index, row in enumerate(mapping["entries"])
    ]
    entry_by_label = {row["blind_label"]: row for row in entries}
    if set(entry_by_label) != {"A", "B"} or len(entry_by_label) != 2:
        raise OperatorLineageError("sealed mapping labels invalid")
    if len({row["candidate_id"] for row in entries}) != 2:
        raise OperatorLineageError("sealed mapping candidate IDs must be unique")
    if len({row["render"]["sha256"] for row in entries}) != 2:
        raise OperatorLineageError("sealed mapping render hashes must be unique")
    for label in ("A", "B"):
        entry = entry_by_label[label]
        attachment = by_label[label]
        if (
            entry["generic_file_name"] != attachment["path"]
            or entry["attachment"]["sha256"] != attachment["sha256"]
            or entry["attachment"]["size"] != attachment["size"]
            or entry["attachment"]["mime_type"] != attachment["mime_type"]
        ):
            raise OperatorLineageError("sealed mapping attachment identity drift")
        if entry["brief_lineage_digest"] != profile["brief_lineage_digest"]:
            raise OperatorLineageError("sealed mapping brief lineage drift")

    round_lineage = bundle.get("roundLineage")
    if mode == "initial":
        if review_round != 0 or round_lineage is not None:
            raise OperatorLineageError("initial Media package round lineage invalid")
        if any(row["candidate_round"] != 0 for row in entries):
            raise OperatorLineageError("initial candidates must both be round 0")
    else:
        if not isinstance(round_lineage, Mapping):
            raise OperatorLineageError("targeted package requires round lineage")
        baseline = next((row for row in entries if row["role"] == "baseline"), None)
        challenger = next((row for row in entries if row["role"] == "challenger"), None)
        if baseline is None or challenger is None:
            raise OperatorLineageError("targeted mapping requires baseline/challenger roles")
        if (
            challenger["candidate_round"] != baseline["candidate_round"] + 1
            or challenger["candidate_round"] != review_round
            or round_lineage.get("baselineReviewCandidateId") != baseline["candidate_id"]
            or round_lineage.get("childReviewCandidateId") != challenger["candidate_id"]
            or round_lineage.get("parentRenderSha256") != baseline["render"]["sha256"]
            or round_lineage.get("childRenderSha256") != challenger["render"]["sha256"]
            or round_lineage.get("parentRound") != baseline["candidate_round"]
            or round_lineage.get("childRound") != challenger["candidate_round"]
            or round_lineage.get("growthHandoffDigest")
            != challenger["growth_handoff_digest"]
            or round_lineage.get("mediaApplicationDigest")
            != challenger["media_application_digest"]
        ):
            raise OperatorLineageError("targeted parent/child round lineage drift")
        prior = round_lineage.get("priorReview")
        if not isinstance(prior, Mapping):
            raise OperatorLineageError("targeted prior review evidence missing")
        if (
            prior.get("selectedCandidateId") != baseline["candidate_id"]
            or prior.get("reviewRound") != baseline["candidate_round"]
        ):
            raise OperatorLineageError("targeted prior review baseline drift")

    if handoff != bundle.get("transportHandoff"):
        raise OperatorLineageError("transport handoff file differs from bundle binding")
    if handoff.get("state") != "ROUND_PAIR_PACKAGE_READY":
        raise OperatorBoundaryError("transport handoff is not source-ready")
    if handoff.get("sourceLineage") != {
        "sourceId": source["source_id"],
        "sha256": source["sha256"],
        "size": source["size"],
        "briefLineageDigest": profile["brief_lineage_digest"],
    }:
        raise OperatorLineageError("transport source lineage drift")
    if handoff.get("roundLineage") != {
        "mode": mode,
        "reviewRound": review_round,
        "digest": round_digest,
    }:
        raise OperatorLineageError("transport round lineage drift")
    if handoff.get("attachments") != bundle_attachments:
        raise OperatorLineageError("transport attachment lineage drift")
    for key in ("modelReviewPerformed", "liveModelReviewed", "providerPublish", "humanQuality"):
        if handoff.get(key) is not False or evidence.get(key) is not False:
            raise OperatorBoundaryError(f"Media evidence boundary violated: {key}")

    evidence_expected = {
        "producer": {"repository": profile["repository"], "sha": profile["producer_sha"]},
        "state": "ROUND_PAIR_PACKAGE_READY",
        "mode": mode,
        "reviewRound": review_round,
        "briefLineageDigest": profile["brief_lineage_digest"],
        "packageDigest": package_digest,
        "sealedMappingDigest": mapping_digest,
        "roundLineageDigest": round_digest,
        "promptDigest": prompt_digest,
    }
    for key, expected in evidence_expected.items():
        if evidence.get(key) != expected:
            raise OperatorLineageError(f"Media evidence drift: {key}")
    if evidence.get("attachments") != bundle_attachments:
        raise OperatorLineageError("Media evidence attachments drift")
    if evidence.get("roundLineage") != round_lineage:
        raise OperatorLineageError("Media evidence roundLineage drift")

    return _clone(
        {
            "authority": _creator_r29_media_authority(profile),
            "source_authority": profile,
            "package_dir": str(root),
            "package_digest": package_digest,
            "prompt_digest": prompt_digest,
            "sealed_mapping_digest": mapping_digest,
            "round_lineage_digest": round_digest,
            "source": source,
            "review_round": review_round,
            "intent": (
                "initial_candidate_review"
                if mode == "initial"
                else "targeted_reedit_review"
            ),
            "mode": mode,
            "brief_lineage_digest": profile["brief_lineage_digest"],
            "attachments_by_label": {
                label: {
                    "blind_label": label,
                    "generic_file_name": by_label[label]["path"],
                    "sha256": by_label[label]["sha256"],
                    "size": by_label[label]["size"],
                    "mime_type": by_label[label]["mime_type"],
                    "derivative_for_model_review": by_label[label][
                        "derivative_for_model_review"
                    ],
                }
                for label in ("A", "B")
            },
            "mapping_by_label": entry_by_label,
            "round_lineage": _clone(round_lineage),
        }
    )


def _capture_attachment(
    row: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    allowed = {"name", "sha256", "size", "blindLabel", "mimeType", "filePathHash"}
    if (
        not isinstance(row, Mapping)
        or not {"name", "sha256", "size"}.issubset(set(row))
        or not set(row).issubset(allowed)
    ):
        raise OperatorLineageError(f"capture.attachments[{index}] fields invalid")
    if row["name"] != expected["generic_file_name"]:
        raise OperatorLineageError("capture attachment name drift")
    if _sha256(row["sha256"], "capture.attachment.sha256") != expected["sha256"]:
        raise OperatorLineageError("capture attachment hash drift")
    if _positive_int(row["size"], "capture.attachment.size") != expected["size"]:
        raise OperatorLineageError("capture attachment size drift")
    if "blindLabel" in row and row["blindLabel"] != expected["blind_label"]:
        raise OperatorLineageError("capture attachment blind label drift")
    if "mimeType" in row and row["mimeType"] != expected["mime_type"]:
        raise OperatorLineageError("capture attachment MIME drift")
    if "filePathHash" in row:
        _sha256(row["filePathHash"], "capture.attachment.filePathHash")
    return {
        "blind_label": expected["blind_label"],
        "name": expected["generic_file_name"],
        "sha256": expected["sha256"],
        "size": expected["size"],
        "mime_type": expected["mime_type"],
    }


def parse_live_bridge_capture(
    payload: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
    media_package: Mapping[str, Any],
    capture_file_sha256: str,
) -> dict[str, Any]:
    profile = parse_bridge_authority(authority)
    if _sha256(capture_file_sha256, "capture_file_sha256") != profile[
        "capture_file_sha256"
    ]:
        raise OperatorLineageError("Bridge capture file bytes drift")
    if not isinstance(payload, Mapping):
        raise OperatorBoundaryError("Bridge live capture must be object")
    if payload.get("contract") != BRIDGE_DYNAMIC_CAPTURE_CONTRACT:
        raise OperatorAuthorityError("Bridge capture contract mismatch")
    if payload.get("capture_kind") not in {None, "bridge_existing_chat_capture"}:
        raise OperatorBoundaryError("fixture/non-live capture_kind rejected")
    if payload.get("fixture") is True or payload.get("fakeCdp") is True:
        raise OperatorBoundaryError("fixture/fake-CDP capture rejected")
    if payload.get("disposition") != "LIVE_REVIEW_PASS":
        raise OperatorBoundaryError("only LIVE_REVIEW_PASS can be live-ingested")
    if payload.get("model_evidence") is not True:
        raise OperatorBoundaryError("live capture requires model_evidence=true")
    if payload.get("human_ground_truth") is not False:
        raise OperatorBoundaryError("model evidence cannot become human ground truth")
    if payload.get("human_label", False) is not False:
        raise OperatorBoundaryError("human label evidence is forbidden")
    if payload.get("live_platform_evidence", False) is not False:
        raise OperatorBoundaryError("video review is not live platform evidence")
    live = payload.get("liveEvidence")
    if not isinstance(live, Mapping):
        raise OperatorBoundaryError("genuine liveEvidence is required")
    if live.get("realAttachment") is not True or live.get("realSendCaptured") is not True:
        raise OperatorBoundaryError("fixture/fake-CDP capture lacks real attachment+send")
    if live.get("fakeCdp") is True:
        raise OperatorBoundaryError("fake-CDP capture rejected")
    validation = payload.get("responseValidation")
    if not isinstance(validation, Mapping) or validation.get("strictJson") is not True:
        raise OperatorBoundaryError("live capture lacks strict JSON validation")

    binding = profile["binding"]
    ids = {
        "request_id": _nonempty(payload.get("requestId"), "capture.requestId"),
        "operation_id": _nonempty(payload.get("operationId"), "capture.operationId"),
        "conversation_id": _nonempty(
            payload.get("conversationId"), "capture.conversationId"
        ),
    }
    for key, observed in ids.items():
        if observed != binding[key]:
            raise OperatorLineageError(f"Bridge capture {key} drift")
    _nonempty(payload.get("conversationUrl"), "capture.conversationUrl")
    _nonempty(payload.get("profileId"), "capture.profileId")

    prompt_digest = _sha256(payload.get("promptDigest"), "capture.promptDigest")
    if (
        prompt_digest != binding["prompt_digest"]
        or prompt_digest != media_package["prompt_digest"]
    ):
        raise OperatorLineageError("Bridge capture prompt digest drift")

    attachments_raw = payload.get("attachments")
    if not isinstance(attachments_raw, list) or len(attachments_raw) != 2:
        raise OperatorLineageError("Bridge capture requires exact A/B attachments")
    expected_by_label = media_package["attachments_by_label"]
    normalized_attachments = []
    seen = set()
    for index, row in enumerate(attachments_raw):
        label = row.get("blindLabel") if isinstance(row, Mapping) else None
        if label not in {"A", "B"}:
            name = row.get("name") if isinstance(row, Mapping) else None
            matches = [
                value
                for value in expected_by_label.values()
                if value["generic_file_name"] == name
            ]
            if len(matches) != 1:
                raise OperatorLineageError("capture attachment cannot bind to A/B")
            label = matches[0]["blind_label"]
        if label in seen:
            raise OperatorLineageError("duplicate capture attachment label")
        seen.add(label)
        normalized_attachments.append(
            _capture_attachment(row, expected_by_label[label], index=index)
        )
    if seen != {"A", "B"}:
        raise OperatorLineageError("capture must bind both A and B")

    response_text = _nonempty(payload.get("responseText"), "capture.responseText")
    response_digest = _sha256(payload.get("responseDigest"), "capture.responseDigest")
    if (
        _text_sha256(response_text) != response_digest
        or response_digest != binding["assistant_response_digest"]
    ):
        raise OperatorLineageError("assistant-response digest drift")
    assistant_turn = payload.get("assistantTurn")
    if assistant_turn is not None and (
        not isinstance(assistant_turn, Mapping)
        or assistant_turn.get("responseDigest") != response_digest
    ):
        raise OperatorLineageError("assistant-turn digest binding drift")
    normalized_response = parse_dynamic_review_response(response_text)

    dynamic = payload.get("dynamicPackage")
    if not isinstance(dynamic, Mapping):
        raise OperatorLineageError("Bridge dynamicPackage binding required")
    required_dynamic = {
        "handoffContract",
        "handoffSha256",
        "packageDigest",
        "sealedMappingDigestRef",
        "producer",
        "sourceLineage",
        "sourceBindingFingerprint",
    }
    if not required_dynamic.issubset(set(dynamic)):
        raise OperatorLineageError("Bridge dynamicPackage fields invalid")
    if dynamic.get("handoffContract") != "media.dynamic_review_handoff.v1":
        raise OperatorLineageError("Bridge dynamic handoff contract mismatch")
    if (
        _sha256(dynamic.get("handoffSha256"), "dynamic.handoffSha256")
        != binding["handoff_sha256"]
        or _sha256(dynamic.get("packageDigest"), "dynamic.packageDigest")
        != binding["bridge_package_digest"]
        or _sha256(
            dynamic.get("sealedMappingDigestRef"),
            "dynamic.sealedMappingDigestRef",
        )
        != binding["sealed_mapping_digest"]
        or _sha256(
            dynamic.get("sourceBindingFingerprint"),
            "dynamic.sourceBindingFingerprint",
        )
        != binding["source_binding_fingerprint"]
    ):
        raise OperatorLineageError("Bridge dynamic package binding drift")
    if (
        binding["media_package_digest"] != media_package["package_digest"]
        or binding["sealed_mapping_digest"] != media_package["sealed_mapping_digest"]
    ):
        raise OperatorLineageError("Bridge authority is stale for Media package/mapping")

    producer = dynamic.get("producer")
    if not isinstance(producer, Mapping):
        raise OperatorLineageError("Bridge dynamic Media producer binding missing")
    if (
        producer.get("repository") != media_package["authority"]["repository"]
        or producer.get("sha") != media_package["authority"]["producer_sha"]
        or producer.get("round") != media_package["authority"]["producer_round"]
    ):
        raise OperatorAuthorityError("Bridge dynamic Media producer/round drift")

    source_lineage = dynamic.get("sourceLineage")
    if not isinstance(source_lineage, Mapping):
        raise OperatorLineageError("Bridge dynamic source lineage missing")
    source = media_package["source"]
    aliases = {
        "sourceId": source["source_id"],
        "source_id": source["source_id"],
        "sha256": source["sha256"],
        "sourceSha256": source["sha256"],
        "size": source["size"],
        "sourceSize": source["size"],
        "briefLineageDigest": media_package["brief_lineage_digest"],
        "reviewRound": media_package["review_round"],
    }
    for key, expected in aliases.items():
        if key in source_lineage and source_lineage[key] != expected:
            raise OperatorLineageError(f"Bridge dynamic source lineage drift: {key}")

    capture_id = _nonempty(
        payload.get("captureId") or payload.get("operationId"),
        "capture.captureId",
    )
    assistant_message_id = payload.get("assistantMessageId")
    if assistant_message_id is None and isinstance(assistant_turn, Mapping):
        assistant_message_id = assistant_turn.get("turnKey")
    if assistant_message_id is not None:
        assistant_message_id = _nonempty(
            assistant_message_id, "capture.assistantMessageId"
        )
    model_identity = _nonempty(
        payload.get("modelIdentity")
        or f"bridge-{profile['producer_round'].lower()}-captured-video-model",
        "capture.modelIdentity",
    )

    material = {
        "capture_file_sha256": profile["capture_file_sha256"],
        "bridge_authority": profile,
        "media_package_digest": media_package["package_digest"],
        "sealed_mapping_digest": media_package["sealed_mapping_digest"],
        "bridge_package_digest": binding["bridge_package_digest"],
        "capture_id": capture_id,
        "request_id": ids["request_id"],
        "operation_id": ids["operation_id"],
        "conversation_id": ids["conversation_id"],
        "prompt_digest": prompt_digest,
        "attachments": sorted(
            normalized_attachments, key=lambda row: row["blind_label"]
        ),
        "assistant_response_digest": response_digest,
        "assistant_message_id": assistant_message_id,
        "model_identity": model_identity,
    }
    return _clone(
        {
            "capture_id": capture_id,
            "capture_digest": sha256_json(material),
            "capture_file_sha256": profile["capture_file_sha256"],
            "bridge_authority": _creator_r29_bridge_authority(),
            "source_bridge_authority": profile,
            "conversation": {
                "conversation_id": ids["conversation_id"],
                "request_id": ids["request_id"],
                "operation_id": ids["operation_id"],
                "assistant_message_id": assistant_message_id,
            },
            "prompt_digest": prompt_digest,
            "attachments": sorted(
                normalized_attachments, key=lambda row: row["blind_label"]
            ),
            "assistant_response": {
                "raw_sha256": response_digest,
                "raw_content": response_text,
                "model_identity": model_identity,
                "normalized": normalized_response["normalized"],
                "response_shape": normalized_response["response_shape"],
                "normalization_generated_summary": normalized_response[
                    "normalization_generated_summary"
                ],
                "normalization_generated_pairwise_confidence": normalized_response[
                    "normalization_generated_pairwise_confidence"
                ],
            },
            "evidence_boundary": {
                "model_evidence": True,
                "human_ground_truth": False,
                "human_label": False,
                "human_parity_inferred": False,
                "live_platform_evidence": False,
            },
        }
    )


def _candidate_index(
    ingest: Mapping[str, Any],
    media_package: Mapping[str, Any],
    envelopes: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    label_by_candidate = {
        row["candidate_id"]: label
        for label, row in media_package["mapping_by_label"].items()
    }
    rows = []
    for candidate_id in sorted(ingest["dynamic_handoffs"]):
        envelope = envelopes[candidate_id]
        handoff = ingest["dynamic_handoffs"][candidate_id]
        rows.append(
            {
                "candidate_id": candidate_id,
                "model_facing_label": label_by_candidate[candidate_id],
                "state": handoff["state"],
                "candidate_round": handoff["binding"]["candidate_round"],
                "render_sha256": handoff["binding"]["render_sha256"],
                "attachment_sha256": handoff["binding"]["attachment_sha256"],
                "handoff_digest": handoff["handoff_digest"],
                "envelope_file": (
                    "creator-review-"
                    + hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:16]
                    + ".json"
                ),
                "envelope_digest": envelope["envelope_digest"],
            }
        )
    return rows


def build_coordinator_index(
    *,
    growth_producer_sha: str,
    growth_ci_run_id: int,
    media_package: Mapping[str, Any],
    ingest: Mapping[str, Any] | None,
    envelopes: Mapping[str, Mapping[str, Any]] | None,
    bridge_authority: Mapping[str, Any] | None,
) -> dict[str, Any]:
    growth_sha = _sha1(growth_producer_sha, "growth_producer_sha")
    ci_run_id = _positive_int(growth_ci_run_id, "growth_ci_run_id")
    live = ingest is not None
    rows = [] if not live else _candidate_index(
        ingest, media_package, envelopes or {}
    )
    selected = None
    pairwise = None
    capture = None
    if live:
        selected_id = ingest["unblinding"]["selected_candidate_id"]
        if selected_id is not None:
            selected = next(
                (
                    {
                        "candidate_id": row["candidate_id"],
                        "state": row["state"],
                        "candidate_round": row["candidate_round"],
                        "render_sha256": row["render_sha256"],
                        "handoff_digest": row["handoff_digest"],
                        "envelope_file": row["envelope_file"],
                        "envelope_digest": row["envelope_digest"],
                    }
                    for row in rows
                    if row["candidate_id"] == selected_id
                ),
                None,
            )
            if selected is None:
                raise OperatorLineageError("selected candidate missing from envelope index")
        pairwise_output = ingest["pairwise_output"]
        pairwise = {
            "model_facing_selection": ingest["unblinding"]["model_facing_selection"],
            "selected_candidate_id": selected_id,
            "output_digest": pairwise_output["output_digest"],
            "rationale": pairwise_output["rationale"],
            "confidence": pairwise_output["confidence"],
            "uncertainty": pairwise_output["uncertainty"],
            "candidate_results": rows,
        }
        capture = {
            "capture_id": ingest["capture"]["capture_id"],
            "capture_digest": ingest["capture"]["capture_digest"],
            "assistant_response_digest": ingest["capture"][
                "assistant_response_digest"
            ],
            "conversation": ingest["capture"]["conversation"],
        }
    index = {
        "contract_version": INDEX_VERSION,
        "index_id": "",
        "index_digest": "",
        "state": "LIVE_REVIEW_INGESTED" if live else "SOURCE_READY",
        "live_capture_gate": (
            "SATISFIED_DYNAMIC_CAPTURE_CONSUMED"
            if live
            else "BLOCKED_WAITING_DYNAMIC_CAPTURE"
        ),
        "operator": {
            "contract_version": OPERATOR_VERSION,
            "repository": "foto6/video3",
            "producer_sha": growth_sha,
            "ci_run_id": ci_run_id,
            "starting_r26_sha": GROWTH_R26_SHA,
            "provider_mutation": False,
            "browser_mutation": False,
            "creator_mutation": False,
            "media_mutation": False,
        },
        "creator_target": {
            "repository": "foto6/video1",
            "observed_r29_sha": CREATOR_R29_OBSERVED_SHA,
            "observed_r29_ci_run_id": CREATOR_R29_OBSERVED_CI,
            "envelope_contract": CREATOR_ENVELOPE_VERSION,
            "manual_envelope_edit_required": False,
        },
        "media": {
            "authority": media_package["source_authority"],
            "creator_r29_compat_authority": media_package["authority"],
            "package_digest": media_package["package_digest"],
            "sealed_mapping_digest": media_package["sealed_mapping_digest"],
            "round_lineage_digest": media_package["round_lineage_digest"],
            "review_round": media_package["review_round"],
            "mode": media_package["mode"],
            "source": media_package["source"],
        },
        "bridge": None if bridge_authority is None else _clone(bridge_authority),
        "creator_r29_bridge_authority": (
            None if bridge_authority is None else _creator_r29_bridge_authority()
        ),
        "capture": capture,
        "pairwise": pairwise,
        "selected_result": selected,
        "candidate_results": rows,
        "evidence_boundary": {
            "model_evidence": live,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "human_parity_inferred": False,
            "live_platform_evidence": False,
        },
    }
    index["index_id"] = "gr27idx1:" + sha256_json(
        {
            "growth_producer_sha": growth_sha,
            "media_package_digest": media_package["package_digest"],
            "sealed_mapping_digest": media_package["sealed_mapping_digest"],
            "capture_digest": None if capture is None else capture["capture_digest"],
        }
    )
    material = dict(index)
    material["index_digest"] = ""
    index["index_digest"] = sha256_json(material)
    return _clone(index)


class OperatorLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.records: dict[str, dict[str, Any]] = {}
        self.requests: dict[str, str] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or set(raw) != {
                "version",
                "records",
                "requests",
            }:
                raise OperatorReplayConflict("operator ledger fields invalid")
            if raw["version"] != LEDGER_VERSION:
                raise OperatorReplayConflict("operator ledger version mismatch")
            self.records = dict(raw["records"])
            self.requests = dict(raw["requests"])

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(
                {
                    "version": LEDGER_VERSION,
                    "records": self.records,
                    "requests": self.requests,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def lookup(
        self,
        *,
        capture_id: str,
        request_key: str,
        fingerprint: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        prior = self.records.get(capture_id)
        if prior is not None:
            if prior["fingerprint"] != fingerprint:
                raise OperatorReplayConflict(
                    "same capture identity changed response/package/mapping bytes"
                )
            return _clone(prior["payload"]), False
        prior_request = self.requests.get(request_key)
        if prior_request is not None and prior_request != fingerprint:
            raise OperatorReplayConflict(
                "same conversation/request changed capture/package/mapping"
            )
        return None, True

    def commit(
        self,
        *,
        capture_id: str,
        request_key: str,
        fingerprint: str,
        payload: Mapping[str, Any],
    ) -> None:
        self.records[capture_id] = {
            "fingerprint": fingerprint,
            "payload": _clone(payload),
        }
        self.requests[request_key] = fingerprint
        self._persist()


def _write_outputs(
    *,
    out_dir: Path,
    ingest: Mapping[str, Any] | None,
    envelopes: Mapping[str, Mapping[str, Any]] | None,
    index: Mapping[str, Any],
    effect: bool,
) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if ingest is not None:
        (out_dir / "growth.dynamic_live_review_capture.r26.v1.json").write_text(
            json.dumps(ingest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for candidate_id, envelope in sorted((envelopes or {}).items()):
            name = (
                "creator-review-"
                + hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:16]
                + ".json"
            )
            (out_dir / name).write_text(
                json.dumps(envelope, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    (out_dir / "growth.live_ingest_operator_index.r27.v1.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "operator-effect.json").write_text(
        json.dumps(
            {
                "state": index["state"],
                "index_digest": index["index_digest"],
                "new_handoff_effect": effect,
                "provider_mutation": False,
                "browser_mutation": False,
                "creator_mutation": False,
                "media_mutation": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _raw_replay_identity(
    *,
    package_dir: Path,
    media_authority: Mapping[str, Any],
    capture_path: Path,
    bridge_authority: Mapping[str, Any],
    bridge_live_result_path: Path | None = None,
) -> tuple[str, str, str]:
    package_root = Path(package_dir).resolve()
    if not package_root.is_dir():
        raise OperatorLineageError("Media package directory missing")
    capture_path = Path(capture_path)
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    capture_id = _nonempty(
        capture.get("captureId") or capture.get("operationId"),
        "capture.captureId",
    )
    conversation_id = _nonempty(
        capture.get("conversationId"), "capture.conversationId"
    )
    request_id = _nonempty(capture.get("requestId"), "capture.requestId")
    raw_files: dict[str, dict[str, Any]] = {}
    for file_path in sorted(
        (path for path in package_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(package_root).as_posix(),
    ):
        relative = file_path.relative_to(package_root).as_posix()
        raw_files[relative] = {
            "sha256": _file_sha256(file_path),
            "size": file_path.stat().st_size,
        }
    if not raw_files:
        raise OperatorLineageError("Media package directory contains no files")
    live_result_identity = None
    if bridge_live_result_path is not None:
        result_path = Path(bridge_live_result_path)
        if not result_path.is_file():
            raise OperatorLineageError("Bridge live-result file missing")
        live_result_identity = {
            "sha256": _file_sha256(result_path),
            "size": result_path.stat().st_size,
        }
    fingerprint = sha256_json(
        {
            "capture_id": capture_id,
            "conversation_id": conversation_id,
            "request_id": request_id,
            "capture_file_sha256": _file_sha256(capture_path),
            "capture_response_digest_field": capture.get("responseDigest"),
            "media_authority_digest": sha256_json(media_authority),
            "bridge_authority_digest": sha256_json(bridge_authority),
            "bridge_live_result": live_result_identity,
            "raw_package_files": raw_files,
        }
    )
    return capture_id, conversation_id + "\n" + request_id, fingerprint


def run_operator(
    *,
    media_package_dir: Path,
    media_authority: Mapping[str, Any],
    ledger_path: Path,
    out_dir: Path,
    growth_producer_sha: str,
    growth_ci_run_id: int,
    bridge_capture_path: Path | None = None,
    bridge_authority: Mapping[str, Any] | None = None,
    bridge_live_result_path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    if (bridge_capture_path is None) != (bridge_authority is None):
        raise OperatorBoundaryError(
            "Bridge capture and Bridge authority must be supplied together"
        )

    effective_live_result_path = bridge_live_result_path
    if (
        bridge_capture_path is not None
        and effective_live_result_path is None
        and isinstance(bridge_authority, Mapping)
        and bridge_authority.get("contract") == BRIDGE_R31_NATIVE_AUTHORITY_CONTRACT
    ):
        sibling = Path(bridge_capture_path).parent / "r31-live-result.json"
        if sibling.is_file():
            effective_live_result_path = sibling

    ledger = OperatorLedger(ledger_path)
    capture_id = None
    request_key = None
    fingerprint = None
    if bridge_capture_path is not None:
        capture_id, request_key, fingerprint = _raw_replay_identity(
            package_dir=media_package_dir,
            media_authority=media_authority,
            capture_path=Path(bridge_capture_path),
            bridge_authority=bridge_authority,
            bridge_live_result_path=effective_live_result_path,
        )
        prior, is_new = ledger.lookup(
            capture_id=capture_id,
            request_key=request_key,
            fingerprint=fingerprint,
        )
        if prior is not None:
            ingest = prior["ingest"]
            envelopes = prior["envelopes"]
            index = prior["index"]
            _write_outputs(
                out_dir=out_dir,
                ingest=ingest,
                envelopes=envelopes,
                index=index,
                effect=False,
            )
            return index, False

    media = load_media_package(
        media_package_dir,
        authority=media_authority,
    )
    if bridge_capture_path is None:
        index = build_coordinator_index(
            growth_producer_sha=growth_producer_sha,
            growth_ci_run_id=growth_ci_run_id,
            media_package=media,
            ingest=None,
            envelopes=None,
            bridge_authority=None,
        )
        _write_outputs(
            out_dir=out_dir,
            ingest=None,
            envelopes=None,
            index=index,
            effect=False,
        )
        return index, False

    capture_path = Path(bridge_capture_path)
    capture_bytes_sha = _file_sha256(capture_path)
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    live_result = (
        None
        if effective_live_result_path is None
        else json.loads(
            Path(effective_live_result_path).read_text(encoding="utf-8")
        )
    )
    bridge_profile, bridge_source_authority = _resolve_bridge_authority(
        bridge_authority,
        media_package=media,
        capture=capture,
        capture_file_sha256=capture_bytes_sha,
        live_result=live_result,
    )
    parsed_capture = parse_live_bridge_capture(
        capture,
        authority=bridge_profile,
        media_package=media,
        capture_file_sha256=capture_bytes_sha,
    )
    if parsed_capture["capture_id"] != capture_id:
        raise OperatorReplayConflict("capture identity changed during validation")
    validated_request_key = (
        parsed_capture["conversation"]["conversation_id"]
        + "\n"
        + parsed_capture["conversation"]["request_id"]
    )
    if validated_request_key != request_key:
        raise OperatorReplayConflict("request identity changed during validation")
    is_new = True

    ingest = convert_dynamic_capture(
        media_package=media,
        parsed_capture=parsed_capture,
    )
    envelopes = {
        candidate_id: build_creator_envelope(
            ingest_result=ingest,
            candidate_id=candidate_id,
            growth_producer_sha=GROWTH_R26_SHA,
            growth_ci_run_id=GROWTH_R26_CI_RUN_ID,
        )
        for candidate_id in sorted(ingest["dynamic_handoffs"])
    }
    index = build_coordinator_index(
        growth_producer_sha=growth_producer_sha,
        growth_ci_run_id=growth_ci_run_id,
        media_package=media,
        ingest=ingest,
        envelopes=envelopes,
        bridge_authority=bridge_source_authority,
    )
    payload = {
        "ingest": ingest,
        "envelopes": envelopes,
        "index": index,
    }
    ledger.commit(
        capture_id=capture_id,
        request_key=request_key,
        fingerprint=fingerprint,
        payload=payload,
    )
    _write_outputs(
        out_dir=out_dir,
        ingest=ingest,
        envelopes=envelopes,
        index=index,
        effect=is_new,
    )
    return index, is_new


def readiness_report(index: Mapping[str, Any]) -> dict[str, Any]:
    live = index.get("state") == "LIVE_REVIEW_INGESTED"
    report = {
        "report_version": READINESS_VERSION,
        "state": index["state"],
        "live_capture_gate": index["live_capture_gate"],
        "operator": index["operator"],
        "creator_target": index["creator_target"],
        "media": index["media"],
        "bridge": index["bridge"],
        "capture": index["capture"],
        "selected_result": index["selected_result"],
        "index_digest": index["index_digest"],
        "invariants": {
            "source_ready": True,
            "live_review_ingested": live,
            "unblind_after_capture_validation_only": True,
            "canonical_creator_envelope_contract": CREATOR_ENVELOPE_VERSION,
            "exact_replay_noop": True,
            "conflicting_replay_rejected": True,
            "fixture_fake_cdp_rejected": True,
            "malformed_or_blocked_rejected": True,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "provider_mutation": False,
            "browser_mutation": False,
            "creator_mutation": False,
            "media_mutation": False,
        },
    }
    report["report_digest"] = sha256_json(report)
    return _clone(report)


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="growth-live-ingest-r27")
    parser.add_argument("--media-package-dir", required=True)
    parser.add_argument("--media-authority", required=True)
    parser.add_argument("--bridge-capture")
    parser.add_argument("--bridge-authority")
    parser.add_argument("--bridge-live-result")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--growth-sha", required=True)
    parser.add_argument("--growth-ci-run-id", type=int, required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)

    if bool(args.bridge_capture) != bool(args.bridge_authority):
        parser.error("--bridge-capture and --bridge-authority must be supplied together")
    index, _ = run_operator(
        media_package_dir=Path(args.media_package_dir),
        media_authority=json.loads(Path(args.media_authority).read_text(encoding="utf-8")),
        bridge_capture_path=(
            None if not args.bridge_capture else Path(args.bridge_capture)
        ),
        bridge_authority=(
            None
            if not args.bridge_authority
            else json.loads(Path(args.bridge_authority).read_text(encoding="utf-8"))
        ),
        bridge_live_result_path=(
            None if not args.bridge_live_result else Path(args.bridge_live_result)
        ),
        ledger_path=Path(args.ledger),
        out_dir=Path(args.out_dir),
        growth_producer_sha=args.growth_sha,
        growth_ci_run_id=args.growth_ci_run_id,
    )
    report = readiness_report(index)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
