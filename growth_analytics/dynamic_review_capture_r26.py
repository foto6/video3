from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .critic_reedit_adapter import (
    DEFECT_TO_OPERATION,
    MAX_REEDIT_ROUNDS,
    SUPPORTED_EDIT_OPERATIONS,
)
from .live_review_ingest import parse_live_review_response
from .web_video_critic import (
    PAIRWISE_SELECTIONS,
    WEB_VIDEO_ATTACHED_MODE,
    build_web_video_critic_input,
    build_web_video_critic_output,
    build_web_video_observation,
    build_web_video_pairwise_input,
    build_web_video_pairwise_output,
)

DYNAMIC_CAPTURE_VERSION = "growth.dynamic_live_review_capture.r26.v1"
MEDIA_AUTHORITY_VERSION = "growth.media_dynamic_review_authority.r26.v1"
BRIDGE_AUTHORITY_VERSION = "growth.bridge_dynamic_capture_authority.r26.v1"
DYNAMIC_HANDOFF_VERSION = "growth.dynamic_creator_reedit_handoff.r26.v1"
CREATOR_ENVELOPE_VERSION = "growth.dynamic_creator_external_review_envelope.r26.v1"
CREATOR_EVENT_VERSION = "creator.dynamic_external_review_event.r26.v1"
LEDGER_VERSION = "growth.dynamic_live_review_capture_ledger.r26.v1"

GROWTH_R25_SHA = "2c441ebaa017c7da72461316401aeaf445e3d6e5"
MEDIA_R20_SHA = "b22174db3c772a49a21fb9f8b1d40828bf258005"
MEDIA_R20_CI_RUN_ID = 36988788032
MEDIA_R20_ARTIFACT_ID = 11218642168
MEDIA_R20_ARTIFACT_NAME = "media-r20-dynamic-review"
MEDIA_R20_ARTIFACT_DIGEST = (
    "sha256:430b64164487d19ec66e76f70edeab640410f88565ed8ae854b8bbc28bd2f410"
)
BRIDGE_R29_SHA = "ed9a35290f94607d7577f1ee9301de1bb44334f2"
BRIDGE_R29_CI_RUN_ID = 36989658042
BRIDGE_R29_CAPTURE_CONTRACT = "bridge.existing_chat_video_review_capture.v1"

_MEDIA_PACKAGE_REQUIRED = {
    "contractVersion",
    "state",
    "packageProducer",
    "bridgeAuthority",
    "source",
    "reviewContext",
    "attachments",
    "promptManifest",
    "promptDigest",
    "sealedMapping",
    "bridgeHandoff",
    "modelReview",
    "humanQuality",
}
_NATIVE_RESPONSE_ALLOWED = {
    "observations",
    "coverage",
    "pairwise",
    "human_ground_truth",
    "human_label",
    "live_platform_evidence",
}


class DynamicReviewError(ValueError):
    pass


class DynamicAuthorityError(DynamicReviewError):
    pass


class DynamicLineageError(DynamicReviewError):
    pass


class DynamicBoundaryError(DynamicReviewError):
    pass


class DynamicReplayConflict(DynamicReviewError):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DynamicReviewError(f"{field} must be non-empty string")
    return value


def _hex(value: Any, size: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != size
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise DynamicReviewError(f"{field} must be lowercase {size}-hex")
    return value


def _sha256(value: Any, field: str) -> str:
    return _hex(value, 64, field)


def _sha1(value: Any, field: str) -> str:
    return _hex(value, 40, field)


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DynamicReviewError(f"{field} must be integer >= 1")
    return value


def _round(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > MAX_REEDIT_ROUNDS
    ):
        raise DynamicBoundaryError(f"{field} must be integer 0..{MAX_REEDIT_ROUNDS}")
    return value


def _confidence(value: Any, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or value > 1
    ):
        raise DynamicBoundaryError(f"{field} must be number 0..1")
    return float(value)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact_digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
    ):
        raise DynamicAuthorityError(f"{field} must be sha256:<64-hex>")
    _sha256(value[7:], field)
    return value


def parse_media_authority(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract_version",
        "repository",
        "producer_sha",
        "ci_run_id",
        "package_contract",
        "contract_blob_sha1",
        "schema_blob_sha1",
        "implementation_blob_sha1",
        "artifact_id",
        "artifact_name",
        "artifact_digest",
        "package_digest",
        "package_file_sha256",
        "evidence_file_sha256",
        "prompt_digest",
        "prompt_file_sha256",
        "sealed_mapping_digest",
        "sealed_mapping_file_sha256",
        "review_round",
        "source",
        "attachments",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise DynamicAuthorityError("Media dynamic authority profile fields invalid")
    if payload["contract_version"] != MEDIA_AUTHORITY_VERSION:
        raise DynamicAuthorityError("Media dynamic authority contract mismatch")
    if payload["repository"] != "foto6/video2":
        raise DynamicAuthorityError("Media repository mismatch")
    producer_sha = _sha1(payload["producer_sha"], "media.producer_sha")
    package_contract = _nonempty(payload["package_contract"], "media.package_contract")
    if package_contract not in {
        "media.dynamic_review_package.r20.v1",
        "media.dynamic_review_package.r21.v1",
    }:
        raise DynamicAuthorityError("unsupported Media R20/R21 dynamic package contract")
    source = payload["source"]
    if not isinstance(source, Mapping) or set(source) != {
        "source_id",
        "sha256",
        "size",
    }:
        raise DynamicAuthorityError("media.source authority fields invalid")
    normalized_source = {
        "source_id": _nonempty(source["source_id"], "media.source.source_id"),
        "sha256": _sha256(source["sha256"], "media.source.sha256"),
        "size": _positive_int(source["size"], "media.source.size"),
    }
    attachments_raw = payload["attachments"]
    if not isinstance(attachments_raw, list) or len(attachments_raw) != 2:
        raise DynamicAuthorityError("media.attachments must contain exact A/B")
    attachments = []
    seen = set()
    for index, row in enumerate(attachments_raw):
        if not isinstance(row, Mapping) or set(row) != {
            "blind_label",
            "generic_file_name",
            "sha256",
            "size",
            "mime_type",
        }:
            raise DynamicAuthorityError(f"media.attachments[{index}] fields invalid")
        label = row["blind_label"]
        if label not in {"A", "B"} or label in seen:
            raise DynamicAuthorityError("media attachment blind labels must be unique A/B")
        seen.add(label)
        attachments.append(
            {
                "blind_label": label,
                "generic_file_name": _nonempty(
                    row["generic_file_name"], f"media.attachments[{index}].generic_file_name"
                ),
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
    if {row["blind_label"] for row in attachments} != {"A", "B"}:
        raise DynamicAuthorityError("media authority requires A and B attachments")
    normalized = {
        "contract_version": MEDIA_AUTHORITY_VERSION,
        "repository": "foto6/video2",
        "producer_sha": producer_sha,
        "ci_run_id": _positive_int(payload["ci_run_id"], "media.ci_run_id"),
        "package_contract": package_contract,
        "contract_blob_sha1": _sha1(
            payload["contract_blob_sha1"], "media.contract_blob_sha1"
        ),
        "schema_blob_sha1": _sha1(
            payload["schema_blob_sha1"], "media.schema_blob_sha1"
        ),
        "implementation_blob_sha1": _sha1(
            payload["implementation_blob_sha1"], "media.implementation_blob_sha1"
        ),
        "artifact_id": _positive_int(payload["artifact_id"], "media.artifact_id"),
        "artifact_name": _nonempty(payload["artifact_name"], "media.artifact_name"),
        "artifact_digest": _artifact_digest(
            payload["artifact_digest"], "media.artifact_digest"
        ),
        "package_digest": _sha256(payload["package_digest"], "media.package_digest"),
        "package_file_sha256": _sha256(
            payload["package_file_sha256"], "media.package_file_sha256"
        ),
        "evidence_file_sha256": _sha256(
            payload["evidence_file_sha256"], "media.evidence_file_sha256"
        ),
        "prompt_digest": _sha256(payload["prompt_digest"], "media.prompt_digest"),
        "prompt_file_sha256": _sha256(
            payload["prompt_file_sha256"], "media.prompt_file_sha256"
        ),
        "sealed_mapping_digest": _sha256(
            payload["sealed_mapping_digest"], "media.sealed_mapping_digest"
        ),
        "sealed_mapping_file_sha256": _sha256(
            payload["sealed_mapping_file_sha256"],
            "media.sealed_mapping_file_sha256",
        ),
        "review_round": _round(payload["review_round"], "media.review_round"),
        "source": normalized_source,
        "attachments": sorted(attachments, key=lambda row: row["blind_label"]),
    }
    return _clone(normalized)


def parse_bridge_authority(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract_version",
        "repository",
        "producer_sha",
        "ci_run_id",
        "capture_contract",
        "capture_schema_id",
        "contract_blob_sha1",
        "schema_blob_sha1",
        "implementation_blob_sha1",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise DynamicAuthorityError("Bridge dynamic authority profile fields invalid")
    if payload["contract_version"] != BRIDGE_AUTHORITY_VERSION:
        raise DynamicAuthorityError("Bridge dynamic authority contract mismatch")
    if payload["repository"] != "foto6/WebAIBridge":
        raise DynamicAuthorityError("Bridge repository mismatch")
    capture_contract = _nonempty(
        payload["capture_contract"], "bridge.capture_contract"
    )
    if capture_contract != BRIDGE_R29_CAPTURE_CONTRACT:
        raise DynamicAuthorityError("Bridge capture contract mismatch")
    return {
        "contract_version": BRIDGE_AUTHORITY_VERSION,
        "repository": "foto6/WebAIBridge",
        "producer_sha": _sha1(payload["producer_sha"], "bridge.producer_sha"),
        "ci_run_id": _positive_int(payload["ci_run_id"], "bridge.ci_run_id"),
        "capture_contract": capture_contract,
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
    }


def _normalize_package_attachment(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(row, Mapping) or set(row) != {
        "blindLabel",
        "genericFileName",
        "mimeType",
        "file",
        "derivative",
    }:
        raise DynamicLineageError(f"package.attachments[{index}] fields invalid")
    label = row["blindLabel"]
    if label not in {"A", "B"}:
        raise DynamicLineageError("package attachment blind label must be A/B")
    file_row = row["file"]
    if not isinstance(file_row, Mapping) or set(file_row) != {
        "path",
        "sha256",
        "size",
    }:
        raise DynamicLineageError("package attachment file binding invalid")
    return {
        "blind_label": label,
        "generic_file_name": _nonempty(
            row["genericFileName"], f"package.attachments[{index}].genericFileName"
        ),
        "mime_type": _nonempty(
            row["mimeType"], f"package.attachments[{index}].mimeType"
        ),
        "sha256": _sha256(
            file_row["sha256"], f"package.attachments[{index}].file.sha256"
        ),
        "size": _positive_int(
            file_row["size"], f"package.attachments[{index}].file.size"
        ),
        "derivative": _clone(row["derivative"]),
    }


def _normalize_mapping_entry(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    required = {
        "attachment",
        "blindLabel",
        "candidateId",
        "editorialApplication",
        "genericFileName",
        "render",
        "renderExport",
        "renderProducerSha",
        "roundNumber",
        "source",
    }
    if not isinstance(row, Mapping) or set(row) != required:
        raise DynamicLineageError(f"sealedMapping.entries[{index}] fields invalid")
    label = row["blindLabel"]
    if label not in {"A", "B"}:
        raise DynamicLineageError("sealed mapping blind label invalid")
    attachment = row["attachment"]
    if not isinstance(attachment, Mapping) or set(attachment) != {
        "derivative",
        "derivative_for_model_review",
        "mimeType",
        "sha256",
        "size",
    }:
        raise DynamicLineageError("sealed mapping attachment fields invalid")
    render = row["render"]
    if not isinstance(render, Mapping) or set(render) != {
        "artifactPath",
        "sha256",
        "size",
    }:
        raise DynamicLineageError("sealed mapping render fields invalid")
    render_export = row["renderExport"]
    if not isinstance(render_export, Mapping) or set(render_export) != {
        "artifactPath",
        "digest",
        "fileSha256",
    }:
        raise DynamicLineageError("sealed mapping renderExport fields invalid")
    source = row["source"]
    if not isinstance(source, Mapping) or set(source) != {
        "artifactPath",
        "sha256",
        "size",
        "sourceId",
    }:
        raise DynamicLineageError("sealed mapping source fields invalid")
    return {
        "blind_label": label,
        "candidate_id": _nonempty(row["candidateId"], "mapping.candidateId"),
        "generic_file_name": _nonempty(
            row["genericFileName"], "mapping.genericFileName"
        ),
        "candidate_round": _round(row["roundNumber"], "mapping.roundNumber"),
        "render_producer_sha": _sha1(
            row["renderProducerSha"], "mapping.renderProducerSha"
        ),
        "source": {
            "source_id": _nonempty(source["sourceId"], "mapping.source.sourceId"),
            "sha256": _sha256(source["sha256"], "mapping.source.sha256"),
            "size": _positive_int(source["size"], "mapping.source.size"),
        },
        "render": {
            "sha256": _sha256(render["sha256"], "mapping.render.sha256"),
            "size": _positive_int(render["size"], "mapping.render.size"),
        },
        "render_export": {
            "digest": _sha256(
                render_export["digest"], "mapping.renderExport.digest"
            ),
            "file_sha256": _sha256(
                render_export["fileSha256"], "mapping.renderExport.fileSha256"
            ),
        },
        "attachment": {
            "sha256": _sha256(
                attachment["sha256"], "mapping.attachment.sha256"
            ),
            "size": _positive_int(
                attachment["size"], "mapping.attachment.size"
            ),
            "mime_type": _nonempty(
                attachment["mimeType"], "mapping.attachment.mimeType"
            ),
            "derivative_for_model_review": (
                attachment["derivative_for_model_review"] is True
            ),
            "derivative": _clone(attachment["derivative"]),
        },
        "editorial_application": _clone(row["editorialApplication"]),
    }


def parse_media_dynamic_package(
    package: Mapping[str, Any],
    evidence: Mapping[str, Any],
    sealed_mapping: Mapping[str, Any],
    prompt_manifest: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
    package_file_sha256: str,
    evidence_file_sha256: str,
    mapping_file_sha256: str,
    prompt_file_sha256: str,
) -> dict[str, Any]:
    auth = parse_media_authority(authority)
    if not isinstance(package, Mapping) or set(package) != _MEDIA_PACKAGE_REQUIRED:
        raise DynamicLineageError("dynamic Media package fields invalid")
    if package["contractVersion"] != auth["package_contract"]:
        raise DynamicAuthorityError("Media package contract/profile mismatch")
    if package["state"] != "DYNAMIC_REVIEW_PACKAGE_READY":
        raise DynamicBoundaryError("Media package is not source-ready")
    if package["humanQuality"] is not False:
        raise DynamicBoundaryError("human quality evidence is forbidden")
    producer = package["packageProducer"]
    if producer != {
        "repository": auth["repository"],
        "sha": auth["producer_sha"],
    }:
        raise DynamicAuthorityError("wrong Media package producer")
    if _sha256(package_file_sha256, "package_file_sha256") != auth["package_file_sha256"]:
        raise DynamicLineageError("Media package file bytes drift")
    if _sha256(evidence_file_sha256, "evidence_file_sha256") != auth["evidence_file_sha256"]:
        raise DynamicLineageError("Media evidence file bytes drift")
    if _sha256(mapping_file_sha256, "mapping_file_sha256") != auth["sealed_mapping_file_sha256"]:
        raise DynamicLineageError("sealed mapping file bytes drift")
    if _sha256(prompt_file_sha256, "prompt_file_sha256") != auth["prompt_file_sha256"]:
        raise DynamicLineageError("prompt manifest file bytes drift")
    if sha256_json(package) != auth["package_digest"]:
        raise DynamicLineageError("Media package digest drift")
    if prompt_manifest != package["promptManifest"]:
        raise DynamicLineageError("prompt manifest differs from package binding")
    prompt_text = _nonempty(
        prompt_manifest.get("promptText"), "promptManifest.promptText"
    )
    prompt_digest = _text_sha256(prompt_text)
    if (
        package["promptDigest"] != auth["prompt_digest"]
        or prompt_digest != auth["prompt_digest"]
    ):
        raise DynamicLineageError("stale Media prompt digest")

    if not isinstance(evidence, Mapping):
        raise DynamicLineageError("Media package evidence must be object")
    for key, expected in (
        ("packageDigest", auth["package_digest"]),
        ("packageFileSha256", auth["package_file_sha256"]),
        ("promptDigest", auth["prompt_digest"]),
        ("promptFileSha256", auth["prompt_file_sha256"]),
        ("sealedMappingDigest", auth["sealed_mapping_digest"]),
        ("sealedMappingFileSha256", auth["sealed_mapping_file_sha256"]),
    ):
        if evidence.get(key) != expected:
            raise DynamicLineageError(f"Media evidence {key} drift")
    if evidence.get("producer") != {
        "repository": auth["repository"],
        "sha": auth["producer_sha"],
    }:
        raise DynamicAuthorityError("Media evidence producer drift")
    if evidence.get("modelReviewPerformed") is not False:
        raise DynamicBoundaryError("source package cannot claim model review performed")
    if evidence.get("humanQuality") is not False:
        raise DynamicBoundaryError("Media evidence cannot claim human quality")

    mapping_required = {"digest", "entries"}
    if not isinstance(sealed_mapping, Mapping) or set(sealed_mapping) != mapping_required:
        raise DynamicLineageError("sealed mapping fields invalid")
    entries_raw = sealed_mapping["entries"]
    if not isinstance(entries_raw, list) or len(entries_raw) != 2:
        raise DynamicLineageError("sealed mapping requires exactly two entries")
    if sha256_json(entries_raw) != sealed_mapping["digest"]:
        raise DynamicLineageError("sealed mapping content digest mismatch")
    if sealed_mapping["digest"] != auth["sealed_mapping_digest"]:
        raise DynamicLineageError("sealed mapping authority digest mismatch")
    if package["sealedMapping"] != sealed_mapping:
        raise DynamicLineageError("swapped or stale sealed mapping")
    if package["bridgeHandoff"].get("sealedMappingDigest") != sealed_mapping["digest"]:
        raise DynamicLineageError("Bridge handoff sealed mapping digest drift")

    package_attachments = [
        _normalize_package_attachment(row, index)
        for index, row in enumerate(package["attachments"])
    ]
    by_label = {row["blind_label"]: row for row in package_attachments}
    if set(by_label) != {"A", "B"} or len(package_attachments) != 2:
        raise DynamicLineageError("Media package attachment labels invalid")
    auth_attachments = {
        row["blind_label"]: row for row in auth["attachments"]
    }
    if by_label != auth_attachments:
        raise DynamicLineageError("stale Media package attachment authority")

    entries = [
        _normalize_mapping_entry(row, index)
        for index, row in enumerate(entries_raw)
    ]
    entry_by_label = {row["blind_label"]: row for row in entries}
    if set(entry_by_label) != {"A", "B"} or len(entry_by_label) != 2:
        raise DynamicLineageError("sealed mapping labels invalid")
    if len({row["candidate_id"] for row in entries}) != 2:
        raise DynamicLineageError("sealed mapping candidate IDs must be unique")
    if len({row["render"]["sha256"] for row in entries}) != 2:
        raise DynamicLineageError("sealed mapping render hashes must be unique")

    source = package["source"]
    normalized_source = {
        "source_id": _nonempty(source.get("sourceId"), "package.source.sourceId"),
        "sha256": _sha256(source.get("sha256"), "package.source.sha256"),
        "size": _positive_int(source.get("size"), "package.source.size"),
    }
    if normalized_source != auth["source"]:
        raise DynamicLineageError("Media source authority drift")
    for label in ("A", "B"):
        entry = entry_by_label[label]
        attachment = by_label[label]
        if entry["source"] != normalized_source:
            raise DynamicLineageError("sealed mapping source lineage drift")
        if entry["render_producer_sha"] != auth["producer_sha"]:
            raise DynamicAuthorityError("sealed mapping render producer drift")
        if (
            entry["generic_file_name"] != attachment["generic_file_name"]
            or entry["attachment"]["sha256"] != attachment["sha256"]
            or entry["attachment"]["size"] != attachment["size"]
            or entry["attachment"]["mime_type"] != attachment["mime_type"]
        ):
            raise DynamicLineageError("sealed mapping attachment lineage drift")
        derivative = entry["attachment"]["derivative"]
        if derivative is None:
            if (
                entry["render"]["sha256"] != entry["attachment"]["sha256"]
                or entry["render"]["size"] != entry["attachment"]["size"]
            ):
                raise DynamicLineageError(
                    "non-derivative review attachment must preserve render bytes"
                )
        else:
            if not entry["attachment"]["derivative_for_model_review"]:
                raise DynamicLineageError("derivative mapping boundary invalid")

    review_context = package["reviewContext"]
    if not isinstance(review_context, Mapping) or set(review_context) != {
        "reviewRound",
        "intent",
    }:
        raise DynamicLineageError("Media reviewContext fields invalid")
    review_round = _round(review_context["reviewRound"], "package.reviewRound")
    rounds = [row["candidate_round"] for row in entries]
    if review_round != max(rounds):
        raise DynamicLineageError("wrong dynamic review round")
    if max(rounds) - min(rounds) > 1:
        raise DynamicLineageError("dynamic review round gap invalid")
    expected_intent = (
        "initial_candidate_review"
        if review_round == 0
        else "targeted_reedit_review"
    )
    if review_context["intent"] != expected_intent:
        raise DynamicLineageError("dynamic review intent/round mismatch")
    if review_round != auth["review_round"]:
        raise DynamicLineageError("Media authority review round drift")
    if evidence.get("reviewContext") != review_context:
        raise DynamicLineageError("Media evidence reviewContext drift")
    evidence_source = evidence.get("source")
    if not isinstance(evidence_source, Mapping) or {
        "source_id": evidence_source.get("sourceId"),
        "sha256": evidence_source.get("sha256"),
        "size": evidence_source.get("size"),
    } != normalized_source:
        raise DynamicLineageError("Media evidence source drift")

    handoff = package["bridgeHandoff"]
    if handoff.get("promptDigest") != auth["prompt_digest"]:
        raise DynamicLineageError("Bridge handoff prompt digest drift")
    if handoff.get("liveExecutionPerformed") is not False:
        raise DynamicBoundaryError("Media package cannot claim Bridge live execution")
    handoff_attachments = handoff.get("attachments")
    if not isinstance(handoff_attachments, list) or len(handoff_attachments) != 2:
        raise DynamicLineageError("Media Bridge handoff attachments invalid")
    for row in handoff_attachments:
        label = row.get("blindLabel")
        expected = by_label.get(label)
        if expected is None:
            raise DynamicLineageError("unknown Bridge handoff blind label")
        if (
            row.get("genericFileName") != expected["generic_file_name"]
            or row.get("sha256") != expected["sha256"]
            or row.get("size") != expected["size"]
            or row.get("mimeType") != expected["mime_type"]
        ):
            raise DynamicLineageError("Bridge handoff attachment drift")

    model_review = package["modelReview"]
    if model_review != {
        "performed": False,
        "state": "DYNAMIC_REVIEW_PACKAGE_READY",
        "nextState": "LIVE_MODEL_REVIEWED",
        "capture": None,
    }:
        raise DynamicBoundaryError("Media model review boundary drift")

    return _clone(
        {
            "authority": auth,
            "package_digest": auth["package_digest"],
            "prompt_digest": auth["prompt_digest"],
            "sealed_mapping_digest": auth["sealed_mapping_digest"],
            "source": normalized_source,
            "review_round": review_round,
            "intent": expected_intent,
            "request_id": _nonempty(handoff.get("requestId"), "bridgeHandoff.requestId"),
            "idempotency_key": _nonempty(
                handoff.get("idempotencyKey"), "bridgeHandoff.idempotencyKey"
            ),
            "attachments_by_label": by_label,
            "mapping_by_label": entry_by_label,
            "prompt_manifest": _clone(prompt_manifest),
        }
    )


def load_media_dynamic_package(
    *,
    package_path: Path,
    evidence_path: Path,
    mapping_path: Path,
    prompt_path: Path,
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    package_path = Path(package_path)
    evidence_path = Path(evidence_path)
    mapping_path = Path(mapping_path)
    prompt_path = Path(prompt_path)
    package = json.loads(package_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
    return parse_media_dynamic_package(
        package,
        evidence,
        mapping,
        prompt,
        authority=authority,
        package_file_sha256=_file_sha256(package_path),
        evidence_file_sha256=_file_sha256(evidence_path),
        mapping_file_sha256=_file_sha256(mapping_path),
        prompt_file_sha256=_file_sha256(prompt_path),
    )


def _native_dynamic_response(raw_content: str) -> dict[str, Any]:
    try:
        root = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise DynamicBoundaryError("assistant response must be strict JSON") from exc
    if not isinstance(root, Mapping) or not set(root).issubset(_NATIVE_RESPONSE_ALLOWED):
        raise DynamicBoundaryError("dynamic assistant response fields invalid")
    if not {"observations", "coverage", "pairwise"}.issubset(set(root)):
        raise DynamicBoundaryError("dynamic assistant response required fields missing")
    for flag in ("human_ground_truth", "human_label", "live_platform_evidence"):
        if flag in root and root[flag] is not False:
            raise DynamicBoundaryError(f"{flag} must remain false")

    coverage = root["coverage"]
    if not isinstance(coverage, Mapping) or set(coverage) != {
        "inspected_ranges",
        "notes",
        "uninspected_possible",
        "every_frame_inspected",
    }:
        raise DynamicBoundaryError("dynamic coverage fields invalid")
    if (
        coverage["uninspected_possible"] is not True
        or coverage["every_frame_inspected"] is not False
    ):
        raise DynamicBoundaryError("coverage uncertainty must remain explicit")
    notes = _nonempty(coverage["notes"], "coverage.notes")
    ranges = coverage["inspected_ranges"]
    if not isinstance(ranges, list) or not ranges:
        raise DynamicBoundaryError("dynamic coverage inspected_ranges required")
    grouped: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    for index, row in enumerate(ranges):
        if not isinstance(row, Mapping) or not set(row).issubset(
            {"attachment_label", "start_ms", "end_ms", "kind"}
        ):
            raise DynamicBoundaryError(f"coverage.inspected_ranges[{index}] invalid")
        if not {"attachment_label", "start_ms", "end_ms"}.issubset(set(row)):
            raise DynamicBoundaryError("coverage range required fields missing")
        label = row["attachment_label"]
        if label not in grouped:
            raise DynamicLineageError("unknown dynamic coverage blind label")
        start = row["start_ms"]
        end = row["end_ms"]
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
        ):
            raise DynamicBoundaryError("contradictory dynamic coverage timestamps")
        grouped[label].append(
            {
                "start_ms": start,
                "end_ms": end,
                "kind": _nonempty(row.get("kind") or "sampled_review", "coverage.kind"),
            }
        )
    if not all(grouped.values()):
        raise DynamicBoundaryError("dynamic coverage requires inspected range for A and B")

    observations = root["observations"]
    if not isinstance(observations, list):
        raise DynamicBoundaryError("dynamic observations must be array")
    pairwise = root["pairwise"]
    if not isinstance(pairwise, Mapping) or not set(pairwise).issubset(
        {"selection", "rationale", "confidence", "uncertainty"}
    ):
        raise DynamicBoundaryError("dynamic pairwise fields invalid")
    if not {"selection", "rationale", "uncertainty"}.issubset(set(pairwise)):
        raise DynamicBoundaryError("dynamic pairwise required fields missing")
    if pairwise["selection"] not in PAIRWISE_SELECTIONS:
        raise DynamicBoundaryError(
            "pairwise verdict must be A/B/tie/insufficient_evidence"
        )
    pairwise_confidence = (
        _confidence(pairwise["confidence"], "pairwise.confidence")
        if "confidence" in pairwise
        else 0.0
    )
    pairwise_uncertainty = _nonempty(
        pairwise["uncertainty"], "pairwise.uncertainty"
    )
    if "confidence" not in pairwise:
        pairwise_uncertainty = (
            "Model did not supply pairwise confidence; normalized confidence=0. "
            + pairwise_uncertainty
        )

    counts = {"A": 0, "B": 0}
    for row in observations:
        if isinstance(row, Mapping) and row.get("attachment_label") in counts:
            counts[row["attachment_label"]] += 1
    summaries = []
    for label in ("A", "B"):
        summaries.append(
            {
                "attachment_label": label,
                "assessment": (
                    "actionable_findings"
                    if counts[label]
                    else "insufficient_evidence"
                ),
                "summary": (
                    f"Bridge dynamic capture supplied {counts[label]} actionable "
                    f"observation(s) for attachment {label}; no separate model "
                    "whole-video summary field was supplied."
                ),
                "confidence": 0.0,
                "uncertainty": (
                    "Generated transport-normalization summary only; not a "
                    f"human or model whole-video rating. Coverage note: {notes}"
                ),
            }
        )
    canonical = {
        "contract_version": "growth.live_video_review_response.v1",
        "coverage": [
            {
                "attachment_label": label,
                "inspected_ranges": grouped[label],
                "notes": notes,
                "uninspected_possible": True,
                "every_frame_inspected": False,
            }
            for label in ("A", "B")
        ],
        "observations": observations,
        "summaries": summaries,
        "pairwise": {
            "selection": pairwise["selection"],
            "rationale": _nonempty(pairwise["rationale"], "pairwise.rationale"),
            "confidence": pairwise_confidence,
            "uncertainty": pairwise_uncertainty,
        },
    }
    return {
        "normalized": parse_live_review_response(canonical_json(canonical)),
        "response_shape": "bridge_r29_native_strict",
        "normalization_generated_summary": True,
        "normalization_generated_pairwise_confidence": "confidence" not in pairwise,
    }


def parse_dynamic_review_response(raw_content: str) -> dict[str, Any]:
    _nonempty(raw_content, "assistant_response")
    try:
        parsed = parse_live_review_response(raw_content)
        return {
            "normalized": parsed,
            "response_shape": "growth.live_video_review_response.v1",
            "normalization_generated_summary": False,
            "normalization_generated_pairwise_confidence": False,
        }
    except Exception:
        return _native_dynamic_response(raw_content)


def _capture_attachment(
    row: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise DynamicLineageError(f"capture.attachments[{index}] invalid")
    allowed = {"name", "sha256", "size", "blindLabel", "mimeType", "filePathHash"}
    if not {"name", "sha256", "size"}.issubset(set(row)) or not set(row).issubset(
        allowed
    ):
        raise DynamicLineageError(f"capture.attachments[{index}] fields invalid")
    if row["name"] != expected["generic_file_name"]:
        raise DynamicLineageError("stale dynamic capture attachment name")
    if _sha256(row["sha256"], "capture.attachment.sha256") != expected["sha256"]:
        raise DynamicLineageError("stale dynamic capture attachment hash")
    if _positive_int(row["size"], "capture.attachment.size") != expected["size"]:
        raise DynamicLineageError("stale dynamic capture attachment size")
    if "blindLabel" in row and row["blindLabel"] != expected["blind_label"]:
        raise DynamicLineageError("capture blind label/name mismatch")
    if "mimeType" in row and row["mimeType"] != expected["mime_type"]:
        raise DynamicLineageError("stale dynamic capture attachment MIME")
    if "filePathHash" in row:
        _sha256(row["filePathHash"], "capture.attachment.filePathHash")
    return {
        "blind_label": expected["blind_label"],
        "name": expected["generic_file_name"],
        "sha256": expected["sha256"],
        "size": expected["size"],
        "mime_type": expected["mime_type"],
    }


def parse_dynamic_bridge_capture(
    capture: Mapping[str, Any],
    *,
    media_package: Mapping[str, Any],
    bridge_authority: Mapping[str, Any],
) -> dict[str, Any]:
    authority = parse_bridge_authority(bridge_authority)
    if not isinstance(capture, Mapping):
        raise DynamicReviewError("Bridge dynamic capture must be object")
    allowed = {
        "contract",
        "capture_kind",
        "captureId",
        "requestId",
        "operationId",
        "conversationId",
        "conversationUrl",
        "profileId",
        "promptDigest",
        "promptFileSha256",
        "packageDigest",
        "sealedMappingDigest",
        "attachments",
        "userTurn",
        "assistantTurn",
        "assistantMessageId",
        "modelIdentity",
        "responseText",
        "responseDigest",
        "responseValidation",
        "requestedAt",
        "promptSentAt",
        "completedAt",
        "disposition",
        "model_evidence",
        "human_ground_truth",
        "human_label",
        "live_platform_evidence",
        "liveEvidence",
        "mediaProducer",
    }
    required = {
        "contract",
        "requestId",
        "operationId",
        "conversationId",
        "conversationUrl",
        "profileId",
        "promptDigest",
        "attachments",
        "responseText",
        "responseDigest",
        "model_evidence",
        "human_ground_truth",
    }
    if not required.issubset(set(capture)) or not set(capture).issubset(allowed):
        raise DynamicBoundaryError("Bridge dynamic capture fields invalid")
    if capture["contract"] != authority["capture_contract"]:
        raise DynamicAuthorityError("Bridge capture contract/profile mismatch")
    if "capture_kind" in capture and capture["capture_kind"] != "bridge_existing_chat_capture":
        raise DynamicBoundaryError("capture_kind must be bridge_existing_chat_capture")
    if capture["model_evidence"] is not True:
        raise DynamicBoundaryError("dynamic capture requires model_evidence=true")
    if capture["human_ground_truth"] is not False:
        raise DynamicBoundaryError("dynamic capture cannot become human ground truth")
    if capture.get("human_label", False) is not False:
        raise DynamicBoundaryError("dynamic capture cannot become a human label")
    if capture.get("live_platform_evidence", False) is not False:
        raise DynamicBoundaryError("dynamic review is not live platform evidence")

    live = capture.get("liveEvidence")
    if not isinstance(live, Mapping) or (
        live.get("realAttachment") is not True
        or live.get("realSendCaptured") is not True
    ):
        raise DynamicBoundaryError("genuine dynamic capture requires real attachment+send evidence")
    if capture.get("disposition") != "LIVE_REVIEW_PASS":
        raise DynamicBoundaryError("dynamic capture disposition is not LIVE_REVIEW_PASS")
    validation = capture.get("responseValidation")
    if (
        not isinstance(validation, Mapping)
        or validation.get("strictJson") is not True
    ):
        raise DynamicBoundaryError("dynamic capture lacks strict JSON validation evidence")

    request_id = _nonempty(capture["requestId"], "capture.requestId")
    if request_id != media_package["request_id"]:
        raise DynamicLineageError("dynamic capture request does not match Media package")
    prompt_digest = _sha256(capture["promptDigest"], "capture.promptDigest")
    if prompt_digest != media_package["prompt_digest"]:
        raise DynamicLineageError("stale dynamic capture prompt")
    if (
        "promptFileSha256" in capture
        and capture["promptFileSha256"]
        != media_package["authority"]["prompt_file_sha256"]
    ):
        raise DynamicLineageError("stale dynamic capture prompt file")
    if (
        "packageDigest" in capture
        and capture["packageDigest"] != media_package["package_digest"]
    ):
        raise DynamicLineageError("dynamic capture package digest drift")
    if (
        "sealedMappingDigest" in capture
        and capture["sealedMappingDigest"] != media_package["sealed_mapping_digest"]
    ):
        raise DynamicLineageError("dynamic capture sealed mapping digest drift")
    user_turn = capture.get("userTurn")
    if user_turn is not None:
        if not isinstance(user_turn, Mapping) or user_turn.get("promptDigest") != prompt_digest:
            raise DynamicLineageError("dynamic capture user-turn prompt binding drift")

    attachments = capture["attachments"]
    if not isinstance(attachments, list) or len(attachments) != 2:
        raise DynamicLineageError("dynamic capture requires exact A/B attachments")
    expected_by_label = media_package["attachments_by_label"]
    normalized = []
    seen = set()
    for index, row in enumerate(attachments):
        label = row.get("blindLabel")
        if label not in {"A", "B"}:
            name = row.get("name")
            matches = [
                item
                for item in expected_by_label.values()
                if item["generic_file_name"] == name
            ]
            if len(matches) != 1:
                raise DynamicLineageError("capture attachment cannot be bound to blind label")
            label = matches[0]["blind_label"]
        if label in seen:
            raise DynamicLineageError("duplicate dynamic capture attachment label")
        seen.add(label)
        normalized.append(
            _capture_attachment(
                row,
                expected=expected_by_label[label],
                index=index,
            )
        )
    if seen != {"A", "B"}:
        raise DynamicLineageError("dynamic capture must contain blind labels A and B")

    response_text = _nonempty(capture["responseText"], "capture.responseText")
    response_digest = _sha256(capture["responseDigest"], "capture.responseDigest")
    if _text_sha256(response_text) != response_digest:
        raise DynamicLineageError("assistant response digest mismatch")
    assistant_turn = capture.get("assistantTurn")
    if assistant_turn is not None:
        if (
            not isinstance(assistant_turn, Mapping)
            or assistant_turn.get("responseDigest") != response_digest
        ):
            raise DynamicLineageError("assistant-turn response binding drift")
    response = parse_dynamic_review_response(response_text)

    capture_id = _nonempty(
        capture.get("captureId") or capture["operationId"], "capture.captureId"
    )
    conversation_id = _nonempty(
        capture["conversationId"], "capture.conversationId"
    )
    operation_id = _nonempty(capture["operationId"], "capture.operationId")
    _nonempty(capture["conversationUrl"], "capture.conversationUrl")
    _nonempty(capture["profileId"], "capture.profileId")
    assistant_message_id = capture.get("assistantMessageId")
    if assistant_message_id is None and isinstance(assistant_turn, Mapping):
        assistant_message_id = assistant_turn.get("turnKey")
    if assistant_message_id is not None:
        assistant_message_id = _nonempty(
            assistant_message_id, "capture.assistantMessageId"
        )
    model_identity = capture.get("modelIdentity")
    if model_identity is None:
        model_identity = (
            "bridge-captured-web-chat-video-model@"
            + authority["producer_sha"][:12]
        )
    model_identity = _nonempty(model_identity, "capture.modelIdentity")

    material = {
        "capture_id": capture_id,
        "bridge_authority": authority,
        "package_digest": media_package["package_digest"],
        "sealed_mapping_digest": media_package["sealed_mapping_digest"],
        "conversation_id": conversation_id,
        "request_id": request_id,
        "operation_id": operation_id,
        "prompt_digest": prompt_digest,
        "attachments": sorted(normalized, key=lambda row: row["blind_label"]),
        "response_digest": response_digest,
        "assistant_message_id": assistant_message_id,
        "model_identity": model_identity,
    }
    return _clone(
        {
            "capture_id": capture_id,
            "capture_digest": sha256_json(material),
            "bridge_authority": authority,
            "conversation": {
                "conversation_id": conversation_id,
                "request_id": request_id,
                "operation_id": operation_id,
                "assistant_message_id": assistant_message_id,
            },
            "prompt_digest": prompt_digest,
            "attachments": sorted(normalized, key=lambda row: row["blind_label"]),
            "assistant_response": {
                "raw_sha256": response_digest,
                "raw_content": response_text,
                "model_identity": model_identity,
                "normalized": response["normalized"],
                "response_shape": response["response_shape"],
                "normalization_generated_summary": response[
                    "normalization_generated_summary"
                ],
                "normalization_generated_pairwise_confidence": response[
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


def _critic_input(
    *,
    media_package: Mapping[str, Any],
    mapping: Mapping[str, Any],
) -> dict[str, Any]:
    source = mapping["source"]
    return build_web_video_critic_input(
        source_id=source["source_id"],
        source_sha256=source["sha256"],
        source_size=source["size"],
        media_repository=media_package["authority"]["repository"],
        media_producer_sha=mapping["render_producer_sha"],
        candidate_id=mapping["candidate_id"],
        render_sha256=mapping["render"]["sha256"],
        render_size=mapping["render"]["size"],
        render_export_sha256=mapping["render_export"]["file_sha256"],
        review_bundle_digest=media_package["package_digest"],
        attachment_sha256=mapping["attachment"]["sha256"],
        attachment_size=mapping["attachment"]["size"],
        attachment_mime_type=mapping["attachment"]["mime_type"],
        review_goal="Dynamic blinded pairwise editorial review",
        platform="short_form_vertical_video",
        requested_focus=[
            "hook",
            "pacing",
            "framing",
            "subtitles",
            "audio",
            "cta",
        ],
        constraints=[
            "preserve explicit coverage uncertainty",
            "model evidence only; no human rating",
            "free-form proposed edits are audit-only",
        ],
    )


def _dynamic_binding(
    *,
    media_package: Mapping[str, Any],
    mapping: Mapping[str, Any],
    critic_input: Mapping[str, Any],
    critic_output: Mapping[str, Any],
    capture_digest: str,
) -> dict[str, Any]:
    return {
        "source_id": mapping["source"]["source_id"],
        "source_sha256": mapping["source"]["sha256"],
        "source_size": mapping["source"]["size"],
        "media_repository": media_package["authority"]["repository"],
        "media_producer_sha": media_package["authority"]["producer_sha"],
        "package_digest": media_package["package_digest"],
        "sealed_mapping_digest": media_package["sealed_mapping_digest"],
        "review_round": media_package["review_round"],
        "candidate_id": mapping["candidate_id"],
        "candidate_round": mapping["candidate_round"],
        "render_sha256": mapping["render"]["sha256"],
        "render_size": mapping["render"]["size"],
        "render_export_sha256": mapping["render_export"]["file_sha256"],
        "attachment_sha256": mapping["attachment"]["sha256"],
        "attachment_size": mapping["attachment"]["size"],
        "attachment_mime_type": mapping["attachment"]["mime_type"],
        "critic_input_digest": critic_input["input_digest"],
        "critic_output_digest": critic_output["output_digest"],
        "capture_digest": capture_digest,
    }


def _dynamic_state(
    *,
    candidate_id: str,
    selected_candidate_id: str | None,
    model_selection: str,
    directive_count: int,
    review_round: int,
) -> str:
    if model_selection == "tie":
        return "tie"
    if model_selection == "insufficient_evidence":
        return "insufficient_evidence"
    if selected_candidate_id == candidate_id:
        return "winner"
    if directive_count and review_round < MAX_REEDIT_ROUNDS:
        return "targeted_reedit"
    if review_round >= MAX_REEDIT_ROUNDS:
        return "reedit_limit_reached"
    return "insufficient_evidence"


def _dynamic_handoff(
    *,
    media_package: Mapping[str, Any],
    parsed_capture: Mapping[str, Any],
    mapping: Mapping[str, Any],
    critic_input: Mapping[str, Any],
    critic_output: Mapping[str, Any],
    pairwise_output: Mapping[str, Any],
    model_selection: str,
    selected_candidate_id: str | None,
) -> dict[str, Any]:
    binding = _dynamic_binding(
        media_package=media_package,
        mapping=mapping,
        critic_input=critic_input,
        critic_output=critic_output,
        capture_digest=parsed_capture["capture_digest"],
    )
    directives = []
    for observation in critic_output["observations"]:
        if observation["scope"] != "local" or observation["severity"] == "info":
            continue
        operation = DEFECT_TO_OPERATION.get(observation["defect_category"])
        if operation not in SUPPORTED_EDIT_OPERATIONS:
            raise DynamicBoundaryError("unsupported executable edit operation")
        directive = {
            "operation": operation,
            "start_ms": observation["start_ms"],
            "end_ms": observation["end_ms"],
            "defect_category": observation["defect_category"],
            "severity": observation["severity"],
            "source_observation_id": observation["observation_id"],
            "evidence": observation["evidence"],
            "confidence": observation["confidence"],
            "uncertainty": observation["uncertainty"],
            "upstream_proposed_edit": observation["proposed_edit"],
            "upstream_proposed_edit_executable": False,
            "binding": binding,
        }
        directive["directive_id"] = "gdr26d1:" + sha256_json(directive)
        directives.append(directive)
    state = _dynamic_state(
        candidate_id=mapping["candidate_id"],
        selected_candidate_id=selected_candidate_id,
        model_selection=model_selection,
        directive_count=len(directives),
        review_round=media_package["review_round"],
    )
    if state != "targeted_reedit":
        directives = []
    handoff = {
        "contract_version": DYNAMIC_HANDOFF_VERSION,
        "handoff_id": "",
        "handoff_digest": "",
        "state": state,
        "review_round": media_package["review_round"],
        "max_reedit_rounds": MAX_REEDIT_ROUNDS,
        "binding": binding,
        "pairwise": {
            "model_facing_selection": model_selection,
            "selected_candidate_id": selected_candidate_id,
            "output_digest": pairwise_output["output_digest"],
        },
        "coverage": {
            **critic_output["coverage"],
            "coverage_uncertainty_preserved": True,
        },
        "summary_uncertainty": critic_output["whole_video_summary"]["uncertainty"],
        "directives": directives,
        "evidence_boundary": {
            "model_review_only": True,
            "human_ground_truth": False,
            "human_label": False,
            "human_rating_evidence": False,
            "live_platform_evidence": False,
            "free_form_proposed_edit_executable": False,
        },
        "authority": {
            "advisory_only": True,
            "creator_mutation": False,
            "media_mutation": False,
            "provider_mutation": False,
            "publish_authorized": False,
            "release_authorized": False,
        },
    }
    handoff["handoff_id"] = "gdr26h1:" + sha256_json(
        {
            "capture_digest": parsed_capture["capture_digest"],
            "package_digest": media_package["package_digest"],
            "candidate_id": mapping["candidate_id"],
            "critic_output_digest": critic_output["output_digest"],
            "pairwise_output_digest": pairwise_output["output_digest"],
            "review_round": media_package["review_round"],
        }
    )
    material = dict(handoff)
    material["handoff_digest"] = ""
    handoff["handoff_digest"] = sha256_json(material)
    return _clone(handoff)


def convert_dynamic_capture(
    *,
    media_package: Mapping[str, Any],
    parsed_capture: Mapping[str, Any],
) -> dict[str, Any]:
    response = parsed_capture["assistant_response"]["normalized"]
    mappings = media_package["mapping_by_label"]
    coverage = {row["attachment_label"]: row for row in response["coverage"]}
    summaries = {row["attachment_label"]: row for row in response["summaries"]}
    observations_by_label: dict[str, list[Mapping[str, Any]]] = {"A": [], "B": []}
    for row in response["observations"]:
        observations_by_label[row["attachment_label"]].append(row)

    critic_inputs: dict[str, dict[str, Any]] = {}
    critic_outputs: dict[str, dict[str, Any]] = {}
    label_by_candidate: dict[str, str] = {}
    for label in ("A", "B"):
        mapping = mappings[label]
        candidate_id = mapping["candidate_id"]
        label_by_candidate[candidate_id] = label
        critic_input = _critic_input(
            media_package=media_package,
            mapping=mapping,
        )
        critic_inputs[candidate_id] = critic_input
        observations = [
            build_web_video_observation(
                input_digest=critic_input["input_digest"],
                scope="local",
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
                defect_category=row["defect_category"],
                severity=row["severity"],
                evidence=row["evidence"],
                description=row["description"],
                proposed_edit=row["proposed_edit"],
                confidence=row["confidence"],
                uncertainty=row["uncertainty"],
            )
            for row in observations_by_label[label]
        ]
        cov = coverage[label]
        summary = summaries[label]
        critic_outputs[candidate_id] = build_web_video_critic_output(
            critic_input=critic_input,
            model_identity=parsed_capture["assistant_response"]["model_identity"],
            execution_mode=WEB_VIDEO_ATTACHED_MODE,
            inspected_ranges=cov["inspected_ranges"],
            coverage_notes=cov["notes"],
            observations=observations,
            assessment=summary["assessment"],
            whole_video_summary=summary["summary"],
            summary_confidence=summary["confidence"],
            summary_uncertainty=summary["uncertainty"],
            transport_evidence_digest=parsed_capture["capture_digest"],
            verified_transport_evidence_digest=parsed_capture["capture_digest"],
        )

    pairwise_input = build_web_video_pairwise_input(
        candidate_inputs=list(critic_inputs.values()),
        comparison_goal=(
            "Compare the exact dynamic Media package candidates using only "
            "the verified Bridge captured model evidence."
        ),
        producer_identity_blinded=True,
    )
    model_selection = response["pairwise"]["selection"]
    if model_selection not in PAIRWISE_SELECTIONS:
        raise DynamicBoundaryError("invalid dynamic pairwise selection")
    selected_candidate_id = (
        mappings[model_selection]["candidate_id"]
        if model_selection in {"A", "B"}
        else None
    )
    if selected_candidate_id is not None:
        r22_label_by_candidate = {
            row["candidate_id"]: row["blind_label"]
            for row in pairwise_input["review_presentation"]
        }
        r22_selection = r22_label_by_candidate[selected_candidate_id]
    else:
        r22_selection = model_selection
    evidence_ids = [
        observation["observation_id"]
        for output in critic_outputs.values()
        for observation in output["observations"]
    ]
    pairwise_output = build_web_video_pairwise_output(
        pairwise_input=pairwise_input,
        selection=r22_selection,
        rationale=response["pairwise"]["rationale"],
        evidence_observation_ids=evidence_ids,
        confidence=response["pairwise"]["confidence"],
        uncertainty=response["pairwise"]["uncertainty"],
    )
    if pairwise_output.get("mapped_candidate_id") != selected_candidate_id:
        raise DynamicLineageError("pairwise unblinding candidate identity mismatch")

    handoffs = {}
    for candidate_id, critic_input in critic_inputs.items():
        label = label_by_candidate[candidate_id]
        handoffs[candidate_id] = _dynamic_handoff(
            media_package=media_package,
            parsed_capture=parsed_capture,
            mapping=mappings[label],
            critic_input=critic_input,
            critic_output=critic_outputs[candidate_id],
            pairwise_output=pairwise_output,
            model_selection=model_selection,
            selected_candidate_id=selected_candidate_id,
        )

    result = {
        "contract_version": DYNAMIC_CAPTURE_VERSION,
        "ingest_id": "",
        "ingest_digest": "",
        "evidence_state": "LIVE_REVIEW_INGESTED",
        "media_authority": media_package["authority"],
        "bridge_authority": parsed_capture["bridge_authority"],
        "package_digest": media_package["package_digest"],
        "sealed_mapping_digest": media_package["sealed_mapping_digest"],
        "review_round": media_package["review_round"],
        "source": media_package["source"],
        "capture": {
            "capture_id": parsed_capture["capture_id"],
            "capture_digest": parsed_capture["capture_digest"],
            "assistant_response_digest": parsed_capture["assistant_response"][
                "raw_sha256"
            ],
            "conversation": parsed_capture["conversation"],
            "response_shape": parsed_capture["assistant_response"]["response_shape"],
            "normalization_generated_summary": parsed_capture[
                "assistant_response"
            ]["normalization_generated_summary"],
            "normalization_generated_pairwise_confidence": parsed_capture[
                "assistant_response"
            ]["normalization_generated_pairwise_confidence"],
        },
        "unblinding": {
            "performed_after_capture_validation": True,
            "sealed_mapping_digest": media_package["sealed_mapping_digest"],
            "model_facing_selection": model_selection,
            "selected_candidate_id": selected_candidate_id,
        },
        "critic_inputs": critic_inputs,
        "critic_outputs": critic_outputs,
        "pairwise_input": pairwise_input,
        "pairwise_output": pairwise_output,
        "dynamic_handoffs": handoffs,
        "evidence_boundary": parsed_capture["evidence_boundary"],
        "authority": {
            "provider_mutation": False,
            "upload_performed": False,
            "prompt_sent_by_growth": False,
            "creator_effect_applied": False,
            "media_effect_applied": False,
            "human_rating_evidence": False,
        },
    }
    result["ingest_id"] = "gdr26i1:" + sha256_json(
        {
            "capture_digest": parsed_capture["capture_digest"],
            "package_digest": media_package["package_digest"],
            "sealed_mapping_digest": media_package["sealed_mapping_digest"],
        }
    )
    material = dict(result)
    material["ingest_digest"] = ""
    result["ingest_digest"] = sha256_json(material)
    return _clone(result)


def build_creator_envelope(
    *,
    ingest_result: Mapping[str, Any],
    candidate_id: str,
    growth_producer_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    if ingest_result.get("contract_version") != DYNAMIC_CAPTURE_VERSION:
        raise DynamicLineageError("dynamic ingest result contract mismatch")
    if candidate_id not in ingest_result["dynamic_handoffs"]:
        raise DynamicLineageError("unknown dynamic candidate ID")
    handoff = ingest_result["dynamic_handoffs"][candidate_id]
    producer_sha = _sha1(growth_producer_sha, "growth_producer_sha")
    ci_run_id = _positive_int(growth_ci_run_id, "growth_ci_run_id")
    binding = handoff["binding"]
    event = {
        "contractVersion": CREATOR_EVENT_VERSION,
        "producer": {
            "repository": "foto6/video3",
            "sha": producer_sha,
            "ciRunId": ci_run_id,
            "contract": DYNAMIC_HANDOFF_VERSION,
        },
        "captureMode": "external_live_review",
        "reviewIdentity": handoff["handoff_id"],
        "handoff": handoff,
    }
    envelope = {
        "contract_version": CREATOR_ENVELOPE_VERSION,
        "envelope_id": "",
        "envelope_digest": "",
        "creator_event": event,
        "growth_r26": {
            "repository": "foto6/video3",
            "producer_sha": producer_sha,
            "ci_run_id": ci_run_id,
            "starting_r25_sha": GROWTH_R25_SHA,
            "ingest_contract": DYNAMIC_CAPTURE_VERSION,
        },
        "media_authority": ingest_result["media_authority"],
        "bridge_authority": ingest_result["bridge_authority"],
        "capture": ingest_result["capture"],
        "review": {
            "package_digest": ingest_result["package_digest"],
            "sealed_mapping_digest": ingest_result["sealed_mapping_digest"],
            "review_round": ingest_result["review_round"],
            "model_facing_selection": ingest_result["unblinding"][
                "model_facing_selection"
            ],
            "selected_candidate_id": ingest_result["unblinding"][
                "selected_candidate_id"
            ],
        },
        "candidate": {
            "candidate_id": candidate_id,
            "candidate_round": binding["candidate_round"],
            "source_id": binding["source_id"],
            "source_sha256": binding["source_sha256"],
            "render_sha256": binding["render_sha256"],
            "render_size": binding["render_size"],
            "attachment_sha256": binding["attachment_sha256"],
            "attachment_size": binding["attachment_size"],
            "attachment_mime_type": binding["attachment_mime_type"],
            "handoff_digest": handoff["handoff_digest"],
            "state": handoff["state"],
        },
        "evidence_boundary": {
            "model_evidence": True,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "human_parity_inferred": False,
            "provider_mutation": False,
        },
    }
    envelope["envelope_id"] = "gdr26ce1:" + sha256_json(
        {
            "growth_producer_sha": producer_sha,
            "capture_digest": ingest_result["capture"]["capture_digest"],
            "package_digest": ingest_result["package_digest"],
            "candidate_id": candidate_id,
            "handoff_digest": handoff["handoff_digest"],
            "review_round": ingest_result["review_round"],
        }
    )
    material = dict(envelope)
    material["envelope_digest"] = ""
    envelope["envelope_digest"] = sha256_json(material)
    return _clone(envelope)


class DynamicReplayLedger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = None if path is None else Path(path)
        self.records: dict[str, dict[str, Any]] = {}
        self.requests: dict[str, str] = {}
        if self.path is not None and self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or set(raw) != {
                "version",
                "records",
                "requests",
            }:
                raise DynamicReplayConflict("dynamic replay ledger fields invalid")
            if raw["version"] != LEDGER_VERSION:
                raise DynamicReplayConflict("dynamic replay ledger version mismatch")
            self.records = dict(raw["records"])
            self.requests = dict(raw["requests"])

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "version": LEDGER_VERSION,
            "records": self.records,
            "requests": self.requests,
        }
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(body, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def ingest(
        self,
        *,
        media_package: Mapping[str, Any],
        capture: Mapping[str, Any],
        bridge_authority: Mapping[str, Any],
        raw_mapping_file_sha256: str,
    ) -> tuple[dict[str, Any], bool]:
        capture_id = _nonempty(
            capture.get("captureId") or capture.get("operationId"),
            "capture identity",
        )
        request_id = _nonempty(capture.get("requestId"), "capture.requestId")
        conversation_id = _nonempty(
            capture.get("conversationId"), "capture.conversationId"
        )
        package_digest = media_package["package_digest"]
        response_digest = capture.get("responseDigest")
        if not isinstance(response_digest, str):
            response_digest = _text_sha256(str(capture.get("responseText", "")))
        raw_fingerprint = sha256_json(
            {
                "capture_id": capture_id,
                "request_id": request_id,
                "conversation_id": conversation_id,
                "package_digest": package_digest,
                "mapping_file_sha256": _sha256(
                    raw_mapping_file_sha256, "raw_mapping_file_sha256"
                ),
                "response_digest": response_digest,
                "capture_payload_digest": sha256_json(capture),
            }
        )
        key = capture_id + "\n" + package_digest
        request_key = conversation_id + "\n" + request_id + "\n" + package_digest
        prior = self.records.get(key)
        if prior is not None:
            if prior["raw_fingerprint"] != raw_fingerprint:
                raise DynamicReplayConflict(
                    "same capture/package changed bytes, response, or mapping"
                )
            return _clone(prior["result"]), False
        prior_request = self.requests.get(request_key)
        if prior_request is not None and prior_request != raw_fingerprint:
            raise DynamicReplayConflict(
                "same conversation/request/package has conflicting capture"
            )

        parsed_capture = parse_dynamic_bridge_capture(
            capture,
            media_package=media_package,
            bridge_authority=bridge_authority,
        )
        result = convert_dynamic_capture(
            media_package=media_package,
            parsed_capture=parsed_capture,
        )
        self.records[key] = {
            "raw_fingerprint": raw_fingerprint,
            "capture_digest": parsed_capture["capture_digest"],
            "result": result,
        }
        self.requests[request_key] = raw_fingerprint
        self._persist()
        return _clone(result), True


def readiness_report(
    *,
    growth_sha: str,
    growth_ci_run_id: int,
    media_package: Mapping[str, Any] | None,
    observed_bridge_sha: str | None,
    consumed: Mapping[str, Any] | None = None,
    creator_envelopes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_ready = media_package is not None
    live = (
        consumed is not None
        and consumed.get("evidence_state") == "LIVE_REVIEW_INGESTED"
    )
    report = {
        "report_version": "growth.dynamic_live_review_capture.r26.readiness.v1",
        "growth_repository": "foto6/video3",
        "growth_source_sha": _sha1(growth_sha, "growth_sha"),
        "growth_ci_run_id": _positive_int(
            growth_ci_run_id, "growth_ci_run_id"
        ),
        "starting_r25_sha": GROWTH_R25_SHA,
        "state": "LIVE_REVIEW_INGESTED" if live else "SOURCE_READY",
        "live_capture_gate": (
            "SATISFIED_DYNAMIC_CAPTURE_CONSUMED"
            if live
            else "BLOCKED_WAITING_DYNAMIC_CAPTURE"
        ),
        "media_source_ready": source_ready,
        "media": (
            None
            if media_package is None
            else {
                "authority": media_package["authority"],
                "package_digest": media_package["package_digest"],
                "sealed_mapping_digest": media_package["sealed_mapping_digest"],
                "review_round": media_package["review_round"],
                "source": media_package["source"],
            }
        ),
        "bridge_expected": {
            "repository": "foto6/WebAIBridge",
            "r29_exact_green_sha": BRIDGE_R29_SHA,
            "r29_exact_green_ci_run_id": BRIDGE_R29_CI_RUN_ID,
            "observed_bridge_sha_not_branch_authority": observed_bridge_sha,
            "authority_contract": BRIDGE_AUTHORITY_VERSION,
            "capture_contract": BRIDGE_R29_CAPTURE_CONTRACT,
            "exact_producer_and_blob_profile_required": True,
            "branch_names_are_authority": False,
            "current_r29_dynamic_capture_available": False,
        },
        "frozen_paths": {
            "r24_r26_fixture_static_path_preserved": True,
            "r25_r18_real_capture_path_preserved": True,
            "dynamic_path_contract": DYNAMIC_CAPTURE_VERSION,
        },
        "invariants": {
            "sealed_mapping_unblind_after_capture_validation_only": True,
            "model_facing_labels_are_candidate_identity": False,
            "coverage_uncertainty_preserved": True,
            "free_form_proposed_edit_executable": False,
            "executable_operations_allowlisted": True,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "provider_mutation": False,
            "exact_replay_idempotent": True,
            "conflicting_replay_rejected": True,
        },
        "live_capture": None,
        "creator_envelopes": None,
    }
    if live:
        report["live_capture"] = {
            "bridge_producer_sha": consumed["bridge_authority"]["producer_sha"],
            "capture_id": consumed["capture"]["capture_id"],
            "capture_digest": consumed["capture"]["capture_digest"],
            "assistant_response_digest": consumed["capture"][
                "assistant_response_digest"
            ],
            "package_digest": consumed["package_digest"],
            "sealed_mapping_digest": consumed["sealed_mapping_digest"],
            "review_round": consumed["review_round"],
        }
        report["creator_envelopes"] = {
            candidate_id: {
                "envelope_digest": envelope["envelope_digest"],
                "handoff_digest": envelope["candidate"]["handoff_digest"],
                "state": envelope["candidate"]["state"],
                "candidate_round": envelope["candidate"]["candidate_round"],
            }
            for candidate_id, envelope in (creator_envelopes or {}).items()
        }
    report["report_digest"] = sha256_json(report)
    return _clone(report)


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="growth-dynamic-review-r26")
    parser.add_argument("--media-package", required=True)
    parser.add_argument("--media-evidence", required=True)
    parser.add_argument("--sealed-mapping", required=True)
    parser.add_argument("--prompt-manifest", required=True)
    parser.add_argument("--media-authority", required=True)
    parser.add_argument("--capture")
    parser.add_argument("--bridge-authority")
    parser.add_argument("--ledger")
    parser.add_argument("--growth-sha", required=True)
    parser.add_argument("--growth-ci-run-id", type=int, required=True)
    parser.add_argument("--observed-bridge-sha")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)

    media_authority = _load_json(args.media_authority)
    media_package = load_media_dynamic_package(
        package_path=Path(args.media_package),
        evidence_path=Path(args.media_evidence),
        mapping_path=Path(args.sealed_mapping),
        prompt_path=Path(args.prompt_manifest),
        authority=media_authority,
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    consumed = None
    envelopes = None

    if bool(args.capture) != bool(args.bridge_authority):
        parser.error("--capture and --bridge-authority must be supplied together")
    if args.capture:
        capture = _load_json(args.capture)
        bridge_authority = _load_json(args.bridge_authority)
        ledger = DynamicReplayLedger(
            None if not args.ledger else Path(args.ledger)
        )
        consumed, effect = ledger.ingest(
            media_package=media_package,
            capture=capture,
            bridge_authority=bridge_authority,
            raw_mapping_file_sha256=_file_sha256(Path(args.sealed_mapping)),
        )
        envelopes = {
            candidate_id: build_creator_envelope(
                ingest_result=consumed,
                candidate_id=candidate_id,
                growth_producer_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            for candidate_id in sorted(consumed["dynamic_handoffs"])
        }
        (out_dir / "growth.dynamic_live_review_capture.r26.v1.json").write_text(
            json.dumps(consumed, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for candidate_id, envelope in envelopes.items():
            safe = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:16]
            (out_dir / f"creator-dynamic-review-{safe}.json").write_text(
                json.dumps(envelope, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        (out_dir / "ingest-effect.json").write_text(
            json.dumps(
                {
                    "capture_id": consumed["capture"]["capture_id"],
                    "capture_digest": consumed["capture"]["capture_digest"],
                    "package_digest": consumed["package_digest"],
                    "new_handoff_effect": effect,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    report = readiness_report(
        growth_sha=args.growth_sha,
        growth_ci_run_id=args.growth_ci_run_id,
        media_package=media_package,
        observed_bridge_sha=args.observed_bridge_sha,
        consumed=consumed,
        creator_envelopes=envelopes,
    )
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
