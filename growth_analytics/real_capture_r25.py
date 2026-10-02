from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .critic_reedit_adapter import (
    CRITIC_REEDIT_ADAPTER_VERSION,
    CREATOR_REEDIT_HANDOFF_VERSION,
    build_creator_reedit_handoff,
    expected_binding_from,
)
from .live_review_ingest import (
    ATTACHMENTS,
    MEDIA_R18_ARTIFACT_DIGEST,
    MEDIA_R18_ARTIFACT_ID,
    MEDIA_R18_ARTIFACT_NAME,
    MEDIA_R18_CI_RUN_ID,
    MEDIA_R18_PACKAGE_DIGEST,
    MEDIA_R18_PACKAGE_FILE_SHA256,
    MEDIA_R18_PROMPT_SHA256,
    MEDIA_R18_PROMPT_TEXT_SHA256,
    MEDIA_R18_REPOSITORY,
    MEDIA_R18_SHA,
    MEDIA_RENDER_PRODUCER_SHA,
    REVIEW_BUNDLE_DIGEST,
    REVIEW_CONSTRAINTS,
    REVIEW_FOCUS,
    REVIEW_GOAL,
    REVIEW_PLATFORM,
    SOURCE,
    parse_live_review_response,
)
from .web_video_critic import (
    PAIRWISE_SELECTIONS,
    WEB_VIDEO_ATTACHED_MODE,
    build_web_video_critic_input,
    build_web_video_critic_output,
    build_web_video_observation,
    build_web_video_pairwise_input,
    build_web_video_pairwise_output,
)

BRIDGE_R29_AUTHORITY_VERSION = "growth.bridge_r29_capture_authority.v1"
R25_INGEST_VERSION = "growth.real_capture_ingest.r25.v1"
R25_CREATOR_ENVELOPE_VERSION = "growth.creator_external_review_envelope.r25.v1"
R29_CAPTURE_CONTRACT = "bridge.existing_chat_video_review_capture.v1"
CREATOR_EXTERNAL_REVIEW_EVENT_VERSION = "creator.external_real_review_event.r26.v1"
GROWTH_R24_SHA = "dc0741d5b11c1f7464ac9a6c5db80c0a4535df08"
R29_REPOSITORY = "foto6/WebAIBridge"
R29_BRANCH_ADVISORY = "agent/bridge-r29-isolated-live-video-review-20261002"

# The R23 contract/schema/adapter blobs remain the Creator R26/R27 handoff surface.
GROWTH_HANDOFF_CONTRACT_BLOB = "ced853aad722aad4c7a88e9a41756baa1b2892a6"
GROWTH_HANDOFF_SCHEMA_BLOB = "ba9ada04488760147136dcaaf012206405c04653"
GROWTH_HANDOFF_ADAPTER_BLOB = "a4f5b1219c874ae13c05651e584dd7fbf5d4449a"

_CAPTURE_REQUIRED = {
    "contract",
    "capture_kind",
    "provenance",
    "requestId",
    "operationId",
    "conversationId",
    "conversationUrl",
    "profileId",
    "promptDigest",
    "attachments",
    "responseText",
    "responseDigest",
    "requestedAt",
    "promptSentAt",
    "completedAt",
    "modelIdentity",
    "model_evidence",
    "human_ground_truth",
}
_CAPTURE_OPTIONAL = {"captureId", "assistantMessageId"}


class R25RealCaptureError(ValueError):
    pass


class R25AuthorityError(R25RealCaptureError):
    pass


class R25LineageError(R25RealCaptureError):
    pass


class R25BoundaryError(R25RealCaptureError):
    pass


class R25ReplayConflict(R25RealCaptureError):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise R25RealCaptureError(f"{field} must be non-empty string")
    return value


def _hex(value: Any, size: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != size
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R25RealCaptureError(f"{field} must be lowercase {size}-hex")
    return value


def _sha256(value: Any, field: str) -> str:
    return _hex(value, 64, field)


def _sha1(value: Any, field: str) -> str:
    return _hex(value, 40, field)


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise R25RealCaptureError(f"{field} must be integer >= 1")
    return value


def parse_bridge_r29_authority(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract_version",
        "repository",
        "producer_sha",
        "capture_contract",
        "contract_blob_sha1",
        "implementation_blob_sha1",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise R25AuthorityError("Bridge R29 authority profile fields invalid")
    if payload["contract_version"] != BRIDGE_R29_AUTHORITY_VERSION:
        raise R25AuthorityError("Bridge R29 authority profile contract mismatch")
    if payload["repository"] != R29_REPOSITORY:
        raise R25AuthorityError("Bridge R29 repository mismatch")
    producer_sha = _sha1(payload["producer_sha"], "bridge_r29.producer_sha")
    contract_blob = _sha1(
        payload["contract_blob_sha1"], "bridge_r29.contract_blob_sha1"
    )
    implementation_blob = _sha1(
        payload["implementation_blob_sha1"],
        "bridge_r29.implementation_blob_sha1",
    )
    if payload["capture_contract"] != R29_CAPTURE_CONTRACT:
        raise R25AuthorityError("Bridge R29 capture contract mismatch")
    # Deliberately no branch/ref field: moving refs are never authority.
    return {
        "contract_version": BRIDGE_R29_AUTHORITY_VERSION,
        "repository": R29_REPOSITORY,
        "producer_sha": producer_sha,
        "capture_contract": R29_CAPTURE_CONTRACT,
        "contract_blob_sha1": contract_blob,
        "implementation_blob_sha1": implementation_blob,
    }


def _expected_attachment_by_name() -> dict[str, dict[str, Any]]:
    return {row["generic_file_name"]: dict(row) for row in ATTACHMENTS}


def _normalize_bridge_attachment(
    raw: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    allowed = {"name", "sha256", "size", "filePathHash", "blindLabel"}
    required = {"name", "sha256", "size", "filePathHash"}
    if (
        not isinstance(raw, Mapping)
        or not required.issubset(set(raw))
        or not set(raw).issubset(allowed)
    ):
        raise R25LineageError(f"attachments[{index}] fields invalid")
    name = _nonempty(raw["name"], f"attachments[{index}].name")
    expected = _expected_attachment_by_name().get(name)
    if expected is None:
        raise R25LineageError("unknown blinded attachment name")
    expected_label = expected["blind_label"]
    if "blindLabel" in raw and raw["blindLabel"] != expected_label:
        raise R25LineageError("blind attachment label/name mismatch")
    sha = _sha256(raw["sha256"], f"attachments[{index}].sha256")
    size = _positive_int(raw["size"], f"attachments[{index}].size")
    path_hash = _sha256(
        raw["filePathHash"], f"attachments[{index}].filePathHash"
    )
    if sha != expected["attachment_sha256"] or size != expected["attachment_size"]:
        raise R25LineageError("stale or wrong attachment SHA/size")
    if (
        expected["attachment_sha256"] != expected["render_sha256"]
        or expected["attachment_size"] != expected["render_size"]
    ):
        raise R25LineageError("Media R18 render/attachment lineage mismatch")
    return {
        "blind_label": expected_label,
        "name": name,
        "sha256": sha,
        "size": size,
        "file_path_hash": path_hash,
        "candidate_id": expected["candidate_id"],
        "render_sha256": expected["render_sha256"],
        "render_size": expected["render_size"],
        "render_export_sha256": expected["render_export_sha256"],
    }


def _capture_material(
    *,
    capture_id: str,
    capture: Mapping[str, Any],
    authority: Mapping[str, Any],
    attachments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "capture_id": capture_id,
        "authority": dict(authority),
        "conversation_id": capture["conversationId"],
        "request_id": capture["requestId"],
        "operation_id": capture["operationId"],
        "prompt_digest": capture["promptDigest"],
        "attachments": list(attachments),
        "response_digest": capture["responseDigest"],
        "assistant_message_id": capture.get("assistantMessageId"),
        "model_identity": capture["modelIdentity"],
        "completed_at": capture["completedAt"],
    }


def parse_bridge_r29_capture(
    payload: Mapping[str, Any],
    *,
    expected_authority: Mapping[str, Any],
) -> dict[str, Any]:
    authority = parse_bridge_r29_authority(expected_authority)
    if not isinstance(payload, Mapping):
        raise R25RealCaptureError("Bridge R29 capture must be object")
    keys = set(payload)
    if not _CAPTURE_REQUIRED.issubset(keys) or not keys.issubset(
        _CAPTURE_REQUIRED | _CAPTURE_OPTIONAL
    ):
        raise R25RealCaptureError("Bridge R29 capture fields invalid")
    if payload["contract"] != R29_CAPTURE_CONTRACT:
        raise R25AuthorityError("Bridge R29 capture contract mismatch")
    if payload["capture_kind"] != "bridge_existing_chat_capture":
        raise R25BoundaryError(
            "R25 accepts only capture_kind=bridge_existing_chat_capture"
        )
    observed_authority = parse_bridge_r29_authority(payload["provenance"])
    if observed_authority != authority:
        raise R25AuthorityError(
            "Bridge R29 producer/contract/blob authority drift"
        )
    if payload["model_evidence"] is not True:
        raise R25BoundaryError("Bridge R29 capture must assert model_evidence=true")
    if payload["human_ground_truth"] is not False:
        raise R25BoundaryError(
            "Bridge R29 model evidence cannot become human ground truth"
        )

    conversation_id = _nonempty(payload["conversationId"], "conversationId")
    request_id = _nonempty(payload["requestId"], "requestId")
    operation_id = _nonempty(payload["operationId"], "operationId")
    _nonempty(payload["conversationUrl"], "conversationUrl")
    _nonempty(payload["profileId"], "profileId")
    _nonempty(payload["requestedAt"], "requestedAt")
    _nonempty(payload["promptSentAt"], "promptSentAt")
    _nonempty(payload["completedAt"], "completedAt")
    model_identity = _nonempty(payload["modelIdentity"], "modelIdentity")
    prompt_digest = _sha256(payload["promptDigest"], "promptDigest")
    if prompt_digest != MEDIA_R18_PROMPT_TEXT_SHA256:
        raise R25LineageError("Media R18 prompt digest drift")

    attachments_raw = payload["attachments"]
    if not isinstance(attachments_raw, list) or len(attachments_raw) != 2:
        raise R25LineageError("Bridge R29 capture requires exact A/B attachments")
    attachments = [
        _normalize_bridge_attachment(row, index=index)
        for index, row in enumerate(attachments_raw)
    ]
    attachments = sorted(attachments, key=lambda row: row["blind_label"])
    if [row["blind_label"] for row in attachments] != ["A", "B"]:
        raise R25LineageError("Bridge R29 capture must bind blind labels A and B")

    response_text = _nonempty(payload["responseText"], "responseText")
    response_digest = _sha256(payload["responseDigest"], "responseDigest")
    expected_response_digest = __import__("hashlib").sha256(
        response_text.encode("utf-8")
    ).hexdigest()
    if response_digest != expected_response_digest:
        raise R25LineageError("assistant response digest mismatch")
    parsed_response = parse_live_review_response(response_text)

    assistant_message_id = payload.get("assistantMessageId")
    if assistant_message_id is not None:
        assistant_message_id = _nonempty(
            assistant_message_id, "assistantMessageId"
        )
    raw_capture_id = payload.get("captureId")
    if raw_capture_id is not None:
        raw_capture_id = _nonempty(raw_capture_id, "captureId")
    capture_id = raw_capture_id or operation_id

    material = _capture_material(
        capture_id=capture_id,
        capture=payload,
        authority=authority,
        attachments=attachments,
    )
    capture_digest = sha256_json(material)
    return _clone(
        {
            "capture_id": capture_id,
            "capture_digest": capture_digest,
            "bridge_authority": authority,
            "conversation": {
                "conversation_id": conversation_id,
                "request_id": request_id,
                "operation_id": operation_id,
                "assistant_message_id": assistant_message_id,
            },
            "prompt": {
                "prompt_text_sha256": prompt_digest,
                "prompt_file_sha256": MEDIA_R18_PROMPT_SHA256,
            },
            "attachments": attachments,
            "assistant_response": {
                "model_identity": model_identity,
                "raw_sha256": response_digest,
                "raw_content": response_text,
                "parsed": parsed_response,
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


def _critic_input_for(attachment: Mapping[str, Any]) -> dict[str, Any]:
    return build_web_video_critic_input(
        source_id=SOURCE["source_id"],
        source_sha256=SOURCE["sha256"],
        source_size=SOURCE["size"],
        media_repository=MEDIA_R18_REPOSITORY,
        media_producer_sha=MEDIA_RENDER_PRODUCER_SHA,
        candidate_id=attachment["candidate_id"],
        render_sha256=attachment["render_sha256"],
        render_size=attachment["render_size"],
        render_export_sha256=attachment["render_export_sha256"],
        review_bundle_digest=REVIEW_BUNDLE_DIGEST,
        attachment_sha256=attachment["sha256"],
        attachment_size=attachment["size"],
        attachment_mime_type="video/mp4",
        review_goal=REVIEW_GOAL,
        platform=REVIEW_PLATFORM,
        requested_focus=REVIEW_FOCUS,
        constraints=REVIEW_CONSTRAINTS,
    )


def _convert_capture(
    parsed_capture: Mapping[str, Any],
    *,
    reedit_round: int,
) -> dict[str, Any]:
    response = parsed_capture["assistant_response"]["parsed"]
    capture_digest = parsed_capture["capture_digest"]
    model_identity = parsed_capture["assistant_response"]["model_identity"]
    by_label = {
        row["blind_label"]: row for row in parsed_capture["attachments"]
    }
    coverage = {
        row["attachment_label"]: row for row in response["coverage"]
    }
    summaries = {
        row["attachment_label"]: row for row in response["summaries"]
    }
    observations_by_label: dict[str, list[Mapping[str, Any]]] = {
        "A": [],
        "B": [],
    }
    for row in response["observations"]:
        observations_by_label[row["attachment_label"]].append(row)

    critic_inputs: dict[str, dict[str, Any]] = {}
    critic_outputs: dict[str, dict[str, Any]] = {}
    for label in ("A", "B"):
        critic_input = _critic_input_for(by_label[label])
        critic_inputs[label] = critic_input
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
        critic_outputs[label] = build_web_video_critic_output(
            critic_input=critic_input,
            model_identity=model_identity,
            execution_mode=WEB_VIDEO_ATTACHED_MODE,
            inspected_ranges=cov["inspected_ranges"],
            coverage_notes=cov["notes"],
            observations=observations,
            assessment=summary["assessment"],
            whole_video_summary=summary["summary"],
            summary_confidence=summary["confidence"],
            summary_uncertainty=summary["uncertainty"],
            transport_evidence_digest=capture_digest,
            verified_transport_evidence_digest=capture_digest,
        )

    pairwise_input = build_web_video_pairwise_input(
        candidate_inputs=[critic_inputs["A"], critic_inputs["B"]],
        comparison_goal=(
            "Choose between the exact blinded Media R18 candidates using only "
            "the verified Bridge R29 captured model evidence."
        ),
        producer_identity_blinded=True,
    )
    selection = response["pairwise"]["selection"]
    if selection not in PAIRWISE_SELECTIONS:
        raise R25BoundaryError("pairwise selection invalid")
    if selection in {"A", "B"}:
        selected_candidate = by_label[selection]["candidate_id"]
        label_by_candidate = {
            row["candidate_id"]: row["blind_label"]
            for row in pairwise_input["review_presentation"]
        }
        r22_selection = label_by_candidate[selected_candidate]
    else:
        r22_selection = selection
    evidence_ids = [
        observation["observation_id"]
        for label in ("A", "B")
        for observation in critic_outputs[label]["observations"]
    ]
    pairwise_output = build_web_video_pairwise_output(
        pairwise_input=pairwise_input,
        selection=r22_selection,
        rationale=response["pairwise"]["rationale"],
        evidence_observation_ids=evidence_ids,
        confidence=response["pairwise"]["confidence"],
        uncertainty=response["pairwise"]["uncertainty"],
    )

    handoffs: dict[str, dict[str, Any]] = {}
    for label in ("A", "B"):
        critic_input = critic_inputs[label]
        critic_output = critic_outputs[label]
        handoffs[label] = build_creator_reedit_handoff(
            critic_input=critic_input,
            critic_output=critic_output,
            expected_binding=expected_binding_from(
                critic_input,
                critic_output,
                verified_transport_evidence_digest=capture_digest,
            ),
            reedit_round=reedit_round,
            pairwise_input=pairwise_input,
            pairwise_output=pairwise_output,
            verified_transport_evidence_digest=capture_digest,
        )

    result = {
        "contract_version": R25_INGEST_VERSION,
        "ingest_id": "",
        "ingest_digest": "",
        "evidence_state": "REAL_ATTACHED_VIDEO_REVIEW_INGESTED",
        "capture_id": parsed_capture["capture_id"],
        "capture_digest": capture_digest,
        "assistant_response_digest": parsed_capture["assistant_response"][
            "raw_sha256"
        ],
        "bridge_r29_authority": parsed_capture["bridge_authority"],
        "conversation": parsed_capture["conversation"],
        "prompt": parsed_capture["prompt"],
        "media_r18": {
            "repository": MEDIA_R18_REPOSITORY,
            "source_sha": MEDIA_R18_SHA,
            "ci_run_id": MEDIA_R18_CI_RUN_ID,
            "artifact_id": MEDIA_R18_ARTIFACT_ID,
            "artifact_name": MEDIA_R18_ARTIFACT_NAME,
            "artifact_digest": MEDIA_R18_ARTIFACT_DIGEST,
            "package_digest": MEDIA_R18_PACKAGE_DIGEST,
            "package_file_sha256": MEDIA_R18_PACKAGE_FILE_SHA256,
        },
        "critic_inputs": critic_inputs,
        "critic_outputs": critic_outputs,
        "pairwise_input": pairwise_input,
        "pairwise_output": pairwise_output,
        "creator_reedit_handoffs": handoffs,
        "evidence_boundary": parsed_capture["evidence_boundary"],
        "authority": {
            "provider_mutation": False,
            "upload_performed": False,
            "prompt_sent_by_growth": False,
            "creator_effect_applied": False,
            "media_effect_applied": False,
        },
    }
    result["ingest_id"] = "gr25i1:" + sha256_json(
        {"capture_digest": capture_digest, "reedit_round": reedit_round}
    )
    material = dict(result)
    material["ingest_digest"] = ""
    result["ingest_digest"] = sha256_json(material)
    return _clone(result)


def _growth_producer_descriptor(
    *,
    growth_producer_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    return {
        "repository": "foto6/video3",
        "sha": _sha1(growth_producer_sha, "growth_producer_sha"),
        "ciRunId": _positive_int(growth_ci_run_id, "growth_ci_run_id"),
        "contract": CREATOR_REEDIT_HANDOFF_VERSION,
        "adapterContract": CRITIC_REEDIT_ADAPTER_VERSION,
        "contractBlobSha1": GROWTH_HANDOFF_CONTRACT_BLOB,
        "schemaBlobSha1": GROWTH_HANDOFF_SCHEMA_BLOB,
        "adapterBlobSha1": GROWTH_HANDOFF_ADAPTER_BLOB,
    }


def build_creator_external_review_envelope(
    *,
    ingest_result: Mapping[str, Any],
    candidate_label: str,
    growth_producer_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    if candidate_label not in {"A", "B"}:
        raise R25RealCaptureError("candidate_label must be A or B")
    if ingest_result.get("contract_version") != R25_INGEST_VERSION:
        raise R25LineageError("R25 ingest result contract mismatch")
    handoff = ingest_result["creator_reedit_handoffs"][candidate_label]
    producer = _growth_producer_descriptor(
        growth_producer_sha=growth_producer_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    creator_event = {
        "contractVersion": CREATOR_EXTERNAL_REVIEW_EVENT_VERSION,
        "producer": producer,
        "captureMode": "external_live_review",
        "reviewIdentity": handoff["handoff_id"],
        "handoff": handoff,
    }
    binding = handoff["binding"]
    envelope = {
        "contract_version": R25_CREATOR_ENVELOPE_VERSION,
        "envelope_id": "",
        "envelope_digest": "",
        "creator_event": creator_event,
        "growth_r25": {
            "repository": "foto6/video3",
            "producer_sha": producer["sha"],
            "ci_run_id": producer["ciRunId"],
            "starting_r24_sha": GROWTH_R24_SHA,
            "ingest_contract": R25_INGEST_VERSION,
        },
        "bridge_r29": ingest_result["bridge_r29_authority"],
        "capture": {
            "capture_id": ingest_result["capture_id"],
            "capture_digest": ingest_result["capture_digest"],
            "assistant_response_digest": ingest_result[
                "assistant_response_digest"
            ],
            "conversation_id": ingest_result["conversation"]["conversation_id"],
            "request_id": ingest_result["conversation"]["request_id"],
        },
        "handoff": {
            "candidate_label": candidate_label,
            "handoff_id": handoff["handoff_id"],
            "handoff_digest": handoff["handoff_digest"],
            "state": handoff["state"],
            "reedit_round": handoff["reedit_round"],
            "source_id": binding["source_id"],
            "source_sha256": binding["source_sha256"],
            "render_sha256": binding["render_sha256"],
            "attachment_sha256": binding["attachment_sha256"],
            "attachment_size": binding["attachment_size"],
        },
        "evidence_boundary": {
            "model_evidence": True,
            "human_ground_truth": False,
            "human_parity_inferred": False,
            "provider_mutation": False,
        },
    }
    envelope["envelope_id"] = "gr25ce1:" + sha256_json(
        {
            "growth_producer_sha": producer["sha"],
            "capture_digest": ingest_result["capture_digest"],
            "handoff_digest": handoff["handoff_digest"],
            "reedit_round": handoff["reedit_round"],
        }
    )
    material = dict(envelope)
    material["envelope_digest"] = ""
    envelope["envelope_digest"] = sha256_json(material)
    return parse_creator_external_review_envelope(envelope)


def parse_creator_external_review_envelope(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "contract_version",
        "envelope_id",
        "envelope_digest",
        "creator_event",
        "growth_r25",
        "bridge_r29",
        "capture",
        "handoff",
        "evidence_boundary",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise R25RealCaptureError("Creator external review envelope fields invalid")
    if payload["contract_version"] != R25_CREATOR_ENVELOPE_VERSION:
        raise R25RealCaptureError("Creator envelope contract mismatch")
    parse_bridge_r29_authority(payload["bridge_r29"])
    growth = payload["growth_r25"]
    if not isinstance(growth, Mapping) or set(growth) != {
        "repository",
        "producer_sha",
        "ci_run_id",
        "starting_r24_sha",
        "ingest_contract",
    }:
        raise R25LineageError("Growth R25 producer fields invalid")
    if (
        growth["repository"] != "foto6/video3"
        or growth["starting_r24_sha"] != GROWTH_R24_SHA
        or growth["ingest_contract"] != R25_INGEST_VERSION
    ):
        raise R25LineageError("Growth R25 producer provenance drift")
    _sha1(growth["producer_sha"], "growth_r25.producer_sha")
    _positive_int(growth["ci_run_id"], "growth_r25.ci_run_id")

    event = payload["creator_event"]
    if not isinstance(event, Mapping) or set(event) != {
        "contractVersion",
        "producer",
        "captureMode",
        "reviewIdentity",
        "handoff",
    }:
        raise R25LineageError("Creator R26/R27 event shape mismatch")
    if (
        event["contractVersion"] != CREATOR_EXTERNAL_REVIEW_EVENT_VERSION
        or event["captureMode"] != "external_live_review"
    ):
        raise R25BoundaryError("Creator external review event boundary invalid")
    producer = event["producer"]
    if producer != _growth_producer_descriptor(
        growth_producer_sha=growth["producer_sha"],
        growth_ci_run_id=growth["ci_run_id"],
    ):
        raise R25LineageError("Creator event Growth R25 producer mismatch")
    handoff = event["handoff"]
    if handoff["handoff_id"] != event["reviewIdentity"]:
        raise R25LineageError("Creator review identity mismatch")
    summary = payload["handoff"]
    if (
        summary["handoff_id"] != handoff["handoff_id"]
        or summary["handoff_digest"] != handoff["handoff_digest"]
        or summary["reedit_round"] != handoff["reedit_round"]
        or summary["source_sha256"] != handoff["binding"]["source_sha256"]
        or summary["render_sha256"] != handoff["binding"]["render_sha256"]
        or summary["attachment_sha256"]
        != handoff["binding"]["attachment_sha256"]
        or summary["attachment_size"] != handoff["binding"]["attachment_size"]
    ):
        raise R25LineageError("Creator envelope handoff/source/render binding drift")
    capture = payload["capture"]
    if not isinstance(capture, Mapping) or set(capture) != {
        "capture_id",
        "capture_digest",
        "assistant_response_digest",
        "conversation_id",
        "request_id",
    }:
        raise R25LineageError("Creator envelope capture fields invalid")
    _nonempty(capture["capture_id"], "capture.capture_id")
    _sha256(capture["capture_digest"], "capture.capture_digest")
    _sha256(
        capture["assistant_response_digest"],
        "capture.assistant_response_digest",
    )
    _nonempty(capture["conversation_id"], "capture.conversation_id")
    _nonempty(capture["request_id"], "capture.request_id")
    if payload["evidence_boundary"] != {
        "model_evidence": True,
        "human_ground_truth": False,
        "human_parity_inferred": False,
        "provider_mutation": False,
    }:
        raise R25BoundaryError("Creator envelope evidence boundary invalid")
    _nonempty(payload["envelope_id"], "envelope_id")
    _sha256(payload["envelope_digest"], "envelope_digest")
    expected_id = "gr25ce1:" + sha256_json(
        {
            "growth_producer_sha": growth["producer_sha"],
            "capture_digest": capture["capture_digest"],
            "handoff_digest": summary["handoff_digest"],
            "reedit_round": summary["reedit_round"],
        }
    )
    if payload["envelope_id"] != expected_id:
        raise R25LineageError("Creator envelope identity mismatch")
    material = dict(payload)
    material["envelope_digest"] = ""
    if payload["envelope_digest"] != sha256_json(material):
        raise R25LineageError("Creator envelope digest mismatch")
    return _clone(payload)


class R25ReplayLedger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = None if path is None else Path(path)
        self.by_capture_id: dict[str, dict[str, Any]] = {}
        self.by_request: dict[str, str] = {}
        if self.path is not None and self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or set(raw) != {
                "version",
                "captures",
                "requests",
            }:
                raise R25ReplayConflict("R25 replay ledger fields invalid")
            if raw["version"] != "growth.real_capture_ingest_ledger.r25.v1":
                raise R25ReplayConflict("R25 replay ledger version mismatch")
            self.by_capture_id = dict(raw["captures"])
            self.by_request = dict(raw["requests"])

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "version": "growth.real_capture_ingest_ledger.r25.v1",
            "captures": self.by_capture_id,
            "requests": self.by_request,
        }
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(body, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def ingest(
        self,
        payload: Mapping[str, Any],
        *,
        expected_authority: Mapping[str, Any],
        reedit_round: int,
    ) -> tuple[dict[str, Any], bool]:
        parsed = parse_bridge_r29_capture(
            payload, expected_authority=expected_authority
        )
        capture_id = parsed["capture_id"]
        capture_digest = parsed["capture_digest"]
        request_key = (
            parsed["conversation"]["conversation_id"]
            + "\n"
            + parsed["conversation"]["request_id"]
        )
        prior = self.by_capture_id.get(capture_id)
        if prior is not None:
            if prior["capture_digest"] != capture_digest:
                raise R25ReplayConflict(
                    "capture ID reused with changed response/bytes"
                )
            return _clone(prior["result"]), False
        prior_request_digest = self.by_request.get(request_key)
        if (
            prior_request_digest is not None
            and prior_request_digest != capture_digest
        ):
            raise R25ReplayConflict(
                "same conversation/request has conflicting capture"
            )
        result = _convert_capture(parsed, reedit_round=reedit_round)
        self.by_capture_id[capture_id] = {
            "capture_digest": capture_digest,
            "assistant_response_digest": parsed["assistant_response"]["raw_sha256"],
            "result": result,
        }
        self.by_request[request_key] = capture_digest
        self._persist()
        return _clone(result), True


def readiness_report(
    *,
    growth_sha: str,
    growth_ci_run_id: int,
    observed_r29_branch_head: str | None = None,
    consumed: Mapping[str, Any] | None = None,
    creator_envelopes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _sha1(growth_sha, "growth_sha")
    _positive_int(growth_ci_run_id, "growth_ci_run_id")
    consumed_real = (
        consumed is not None
        and consumed.get("evidence_state")
        == "REAL_ATTACHED_VIDEO_REVIEW_INGESTED"
    )
    report = {
        "report_version": "growth.real_capture_ingest_r25.readiness.v1",
        "growth_repository": "foto6/video3",
        "growth_source_sha": growth_sha,
        "growth_ci_run_id": growth_ci_run_id,
        "starting_r24_sha": GROWTH_R24_SHA,
        "state": (
            "REAL_ATTACHED_VIDEO_REVIEW_INGESTED"
            if consumed_real
            else "SOURCE_READY"
        ),
        "live_capture_gate": (
            "SATISFIED_R29_CAPTURE_CONSUMED"
            if consumed_real
            else "BLOCKED_WAITING_R29_CAPTURE"
        ),
        "bridge_r29_expected": {
            "repository": R29_REPOSITORY,
            "branch_advisory_only": R29_BRANCH_ADVISORY,
            "capture_contract": R29_CAPTURE_CONTRACT,
            "authority_contract": BRIDGE_R29_AUTHORITY_VERSION,
            "exact_producer_sha_required_from_capture_provenance": True,
            "exact_contract_blob_sha1_required_from_capture_provenance": True,
            "exact_implementation_blob_sha1_required_from_capture_provenance": True,
            "moving_branch_ref_is_authority": False,
            "observed_branch_head_not_authority": observed_r29_branch_head,
        },
        "media_r18": {
            "repository": MEDIA_R18_REPOSITORY,
            "source_sha": MEDIA_R18_SHA,
            "ci_run_id": MEDIA_R18_CI_RUN_ID,
            "artifact_id": MEDIA_R18_ARTIFACT_ID,
            "artifact_name": MEDIA_R18_ARTIFACT_NAME,
            "artifact_digest": MEDIA_R18_ARTIFACT_DIGEST,
            "prompt_file_sha256": MEDIA_R18_PROMPT_SHA256,
            "prompt_text_sha256": MEDIA_R18_PROMPT_TEXT_SHA256,
            "attachments": [dict(row) for row in ATTACHMENTS],
        },
        "creator_compatibility": {
            "event_contract": CREATOR_EXTERNAL_REVIEW_EVENT_VERSION,
            "growth_r25_producer_bound": True,
            "current_creator_r26_pin_note": (
                "Creator R26 exact parser pins Growth R23; R25 emits the same "
                "event shape with exact R25 producer identity for Creator R27 "
                "or a pin update by the Creator owner."
            ),
        },
        "invariants": {
            "capture_kind_bridge_existing_chat_only": True,
            "model_evidence_true_required": True,
            "human_ground_truth": False,
            "strict_json_review_required": True,
            "coverage_uncertainty_preserved": True,
            "timestamp_and_blind_label_validation": True,
            "pairwise_semantics_preserved": True,
            "exact_replay_idempotent": True,
            "conflicting_replay_rejected": True,
            "stale_bridge_media_prompt_attachment_rejected": True,
            "provider_mutation": False,
        },
        "real_capture": None,
        "creator_envelopes": None,
    }
    if consumed_real:
        report["real_capture"] = {
            "bridge_r29_sha": consumed["bridge_r29_authority"]["producer_sha"],
            "capture_id": consumed["capture_id"],
            "capture_digest": consumed["capture_digest"],
            "assistant_response_digest": consumed[
                "assistant_response_digest"
            ],
        }
        report["creator_envelopes"] = {
            label: {
                "envelope_digest": envelope["envelope_digest"],
                "handoff_digest": envelope["handoff"]["handoff_digest"],
                "state": envelope["handoff"]["state"],
                "reedit_round": envelope["handoff"]["reedit_round"],
            }
            for label, envelope in (creator_envelopes or {}).items()
        }
    report["report_digest"] = sha256_json(report)
    return _clone(report)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _main() -> int:
    parser = argparse.ArgumentParser(prog="growth-real-capture-r25")
    parser.add_argument("--capture")
    parser.add_argument("--bridge-authority")
    parser.add_argument("--reedit-round", type=int, default=0)
    parser.add_argument("--growth-sha", required=True)
    parser.add_argument("--growth-ci-run-id", type=int, required=True)
    parser.add_argument("--ledger")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--observed-r29-branch-head")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    consumed = None
    envelopes = None

    if bool(args.capture) != bool(args.bridge_authority):
        parser.error("--capture and --bridge-authority must be supplied together")
    if args.capture:
        capture = _load_json(Path(args.capture))
        authority = _load_json(Path(args.bridge_authority))
        ledger = R25ReplayLedger(
            None if not args.ledger else Path(args.ledger)
        )
        consumed, effect = ledger.ingest(
            capture,
            expected_authority=authority,
            reedit_round=args.reedit_round,
        )
        envelopes = {
            label: build_creator_external_review_envelope(
                ingest_result=consumed,
                candidate_label=label,
                growth_producer_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
            for label in ("A", "B")
        }
        (out_dir / "growth.real_capture_ingest.r25.v1.json").write_text(
            json.dumps(consumed, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for label, envelope in envelopes.items():
            (out_dir / f"creator-external-review-{label}.json").write_text(
                json.dumps(envelope, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        (out_dir / "ingest-effect.json").write_text(
            json.dumps(
                {
                    "capture_id": consumed["capture_id"],
                    "capture_digest": consumed["capture_digest"],
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
        observed_r29_branch_head=args.observed_r29_branch_head,
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
