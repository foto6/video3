from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .critic_reedit_adapter import (
    DEFECT_TO_OPERATION,
    SUPPORTED_EDIT_OPERATIONS,
    build_creator_reedit_handoff,
    expected_binding_from,
)
from .web_video_critic import (
    DEFECT_CATEGORIES,
    PAIRWISE_SELECTIONS,
    SEVERITIES,
    SUMMARY_ASSESSMENTS,
    WEB_VIDEO_ATTACHED_MODE,
    build_web_video_critic_input,
    build_web_video_critic_output,
    build_web_video_observation,
    build_web_video_pairwise_input,
    build_web_video_pairwise_output,
)

LIVE_REVIEW_CAPTURE_VERSION = "growth.live_video_review_capture.v1"
LIVE_REVIEW_RESPONSE_VERSION = "growth.live_video_review_response.v1"
LIVE_REVIEW_INGEST_VERSION = "growth.live_video_review_ingest.v1"
CAPTURE_KINDS = ("bridge_existing_chat_capture", "fixture")

GROWTH_R23_START_SHA = "26f769abceb43a63677ea8f7ba028369db371696"
BRIDGE_R26_REPOSITORY = "foto6/WebAIBridge"
BRIDGE_R26_SHA = "73c13f9eed2a2cbcea881dd8c5452d054bfef940"
BRIDGE_R26_ATTACHMENT_CONTRACT = "bridge.chat_file_attachment.v1"
MEDIA_R18_REPOSITORY = "foto6/video2"
MEDIA_R18_SHA = "2c41f084e000eca5efd9a51d2d3752bec1bd1311"
MEDIA_R18_CI_RUN_ID = 36967381891
MEDIA_R18_ARTIFACT_ID = 11210373001
MEDIA_R18_ARTIFACT_NAME = "media-r18-direct-model-review"
MEDIA_R18_ARTIFACT_DIGEST = "sha256:1f2ac715ec787be14564201445737f19825f56267a7cab6fb99553aaaff013eb"
MEDIA_R18_PACKAGE_DIGEST = "c7af4d86acddb2b1bc67135449610386188fead05e0801dbb32e8857afe4945d"
MEDIA_R18_PACKAGE_FILE_SHA256 = "23b36200e0023913f5435b99748f3ded92cb9ed86a9f769d7d64bb0d6743771e"
MEDIA_R18_PROMPT_CONTRACT = "media.direct_model_review_prompt.v1"
MEDIA_R18_PROMPT_SHA256 = "98e377f73f26d9960acc34703797f962ef75472597acdbefc6dfdc82636b06b6"
MEDIA_R18_PROMPT_TEXT_SHA256 = "2f79d24571f0e9d0fb051702678125d6b50308323aab0923b154ee7e16b3c9d2"
REVIEW_BUNDLE_VERSION = "growth.real_artifact_decision_pack.v1"
REVIEW_BUNDLE_DIGEST = "d415659c4a24dfd4198c6d08bb0fecf524086b0c2031d68395b739bf0dd631d3"
MEDIA_RENDER_PRODUCER_SHA = "231a0680c8939cfec77aaa283e507e93f383ad73"
SOURCE = {
    "source_id": "r16-demo-source",
    "sha256": "7b484abef5de1569e1b7f91a5d780f17c6d687ef68b3c42c9e54375f4e5e434b",
    "size": 763377,
}
ATTACHMENTS = (
    {
        "blind_label": "A",
        "generic_file_name": "review-A.mp4",
        "candidate_id": "candidate-1",
        "render_sha256": "3bd12d999264cb932cb3ef15dfbd96e002ede517f2273795b593553b9642d864",
        "render_size": 575465,
        "render_export_sha256": "2b5177c00bb054184a239c7eddb1383ad253922e8eb686f5aa2e56c91a1335ac",
        "attachment_sha256": "3bd12d999264cb932cb3ef15dfbd96e002ede517f2273795b593553b9642d864",
        "attachment_size": 575465,
        "mime_type": "video/mp4",
    },
    {
        "blind_label": "B",
        "generic_file_name": "review-B.mp4",
        "candidate_id": "candidate-2",
        "render_sha256": "cdae63d5ccce67332e607af7fd87b18c31da8dc77c2f0ce5182550321767286c",
        "render_size": 576763,
        "render_export_sha256": "b349b897f8f86bc9257e2622f564d6430f3864ff605ae4b82ba0cb09c59298f6",
        "attachment_sha256": "cdae63d5ccce67332e607af7fd87b18c31da8dc77c2f0ce5182550321767286c",
        "attachment_size": 576763,
        "mime_type": "video/mp4",
    },
)

REVIEW_GOAL = "Identify bounded editorial defects that could justify a targeted re-edit."
REVIEW_PLATFORM = "short_form_vertical"
REVIEW_FOCUS = (
    "hook clarity in first 1-3s",
    "pacing coherence",
    "semantic cut correctness",
    "framing/crop",
    "caption readability",
    "audio balance",
    "payoff/CTA/loop",
)
REVIEW_CONSTRAINTS = (
    "Do not claim every frame was inspected.",
    "Do not infer human preference.",
    "Do not treat model opinion as live platform evidence.",
    "Treat the captured assistant response as model evidence only.",
)


class LiveReviewIngestError(ValueError):
    pass


class LiveReviewLineageError(LiveReviewIngestError):
    pass


class LiveReviewBoundaryError(LiveReviewIngestError):
    pass


class LiveReviewReplayConflict(LiveReviewIngestError):
    pass


class LiveReviewUnsupportedEdit(LiveReviewIngestError):
    pass


def _exact_keys(value: Any, expected: set[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise LiveReviewIngestError(f"{field} fields invalid")
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiveReviewIngestError(f"{field} must be non-empty string")
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise LiveReviewIngestError(f"{field} must be lowercase SHA-256")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LiveReviewIngestError(f"{field} must be integer >= 1")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LiveReviewIngestError(f"{field} must be integer >= 0")
    return value


def _confidence(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveReviewIngestError(f"{field} must be numeric in [0,1]")
    result = float(value)
    if result < 0.0 or result > 1.0:
        raise LiveReviewIngestError(f"{field} must be numeric in [0,1]")
    return round(result, 6)


def expected_request_binding() -> dict[str, Any]:
    return {
        "prompt": {
            "contract_version": MEDIA_R18_PROMPT_CONTRACT,
            "sha256": MEDIA_R18_PROMPT_SHA256,
            "prompt_text_sha256": MEDIA_R18_PROMPT_TEXT_SHA256,
        },
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
        "bridge_r26": {
            "repository": BRIDGE_R26_REPOSITORY,
            "source_sha": BRIDGE_R26_SHA,
            "attachment_contract": BRIDGE_R26_ATTACHMENT_CONTRACT,
        },
        "source": dict(SOURCE),
        "media": {
            "repository": MEDIA_R18_REPOSITORY,
            "producer_sha": MEDIA_RENDER_PRODUCER_SHA,
        },
        "review_bundle": {
            "contract_version": REVIEW_BUNDLE_VERSION,
            "digest": REVIEW_BUNDLE_DIGEST,
        },
        "attachments": [dict(row) for row in ATTACHMENTS],
    }


def _capture_identity_material(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "capture_kind": payload["capture_kind"],
        "conversation": payload["conversation"],
        "request_binding": payload["request_binding"],
        "assistant_response_sha256": payload["assistant_response"]["raw_sha256"],
    }


def _response_bytes_sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_range(raw: Any, field: str) -> dict[str, Any]:
    row = _exact_keys(raw, {"start_ms", "end_ms", "kind"}, field)
    start = _nonnegative_int(row["start_ms"], f"{field}.start_ms")
    end = _positive_int(row["end_ms"], f"{field}.end_ms")
    if end <= start:
        raise LiveReviewBoundaryError(f"{field} has contradictory timestamps")
    if row["kind"] not in {"continuous_review", "sampled_review", "targeted_recheck"}:
        raise LiveReviewIngestError(f"{field}.kind invalid")
    return {"start_ms": start, "end_ms": end, "kind": row["kind"]}


def _range_covers(start: int, end: int, ranges: Sequence[Mapping[str, Any]]) -> bool:
    return any(row["start_ms"] <= start and end <= row["end_ms"] for row in ranges)


def parse_live_review_response(raw_content: str) -> dict[str, Any]:
    _nonempty(raw_content, "assistant_response.raw_content")
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise LiveReviewBoundaryError(
            "assistant response must be strict JSON; free-form-only output rejected"
        ) from exc
    root = _exact_keys(
        payload,
        {"contract_version", "coverage", "observations", "summaries", "pairwise"},
        "assistant review response",
    )
    if root["contract_version"] != LIVE_REVIEW_RESPONSE_VERSION:
        raise LiveReviewIngestError("assistant review response contract_version invalid")

    labels = {row["blind_label"] for row in ATTACHMENTS}
    coverage_raw = root["coverage"]
    if not isinstance(coverage_raw, list) or len(coverage_raw) != len(labels):
        raise LiveReviewBoundaryError(
            "coverage must contain exactly one row per blind attachment"
        )
    coverage: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(coverage_raw):
        row = _exact_keys(
            item,
            {
                "attachment_label",
                "inspected_ranges",
                "notes",
                "uninspected_possible",
                "every_frame_inspected",
            },
            f"coverage[{index}]",
        )
        label = _nonempty(row["attachment_label"], f"coverage[{index}].attachment_label")
        if label not in labels:
            raise LiveReviewLineageError("unknown candidate/attachment label")
        if label in coverage:
            raise LiveReviewReplayConflict("duplicate coverage label")
        if (
            row["uninspected_possible"] is not True
            or row["every_frame_inspected"] is not False
        ):
            raise LiveReviewBoundaryError("coverage uncertainty must remain explicit")
        ranges_raw = row["inspected_ranges"]
        if not isinstance(ranges_raw, list) or not ranges_raw:
            raise LiveReviewBoundaryError(
                "real attached review requires non-empty inspected ranges per attachment"
            )
        ranges = [
            _validate_range(value, f"coverage[{index}].inspected_ranges[{j}]")
            for j, value in enumerate(ranges_raw)
        ]
        coverage[label] = {
            "attachment_label": label,
            "inspected_ranges": ranges,
            "notes": _nonempty(row["notes"], f"coverage[{index}].notes"),
            "uninspected_possible": True,
            "every_frame_inspected": False,
        }

    observations_raw = root["observations"]
    if not isinstance(observations_raw, list):
        raise LiveReviewIngestError("observations must be array")
    observations: list[dict[str, Any]] = []
    seen_observations: set[str] = set()
    for index, item in enumerate(observations_raw):
        row = _exact_keys(
            item,
            {
                "attachment_label",
                "start_ms",
                "end_ms",
                "defect_category",
                "severity",
                "evidence",
                "description",
                "proposed_edit",
                "confidence",
                "uncertainty",
            },
            f"observations[{index}]",
        )
        label = _nonempty(
            row["attachment_label"], f"observations[{index}].attachment_label"
        )
        if label not in labels:
            raise LiveReviewLineageError("unknown candidate/attachment label")
        start = _nonnegative_int(row["start_ms"], f"observations[{index}].start_ms")
        end = _positive_int(row["end_ms"], f"observations[{index}].end_ms")
        if end <= start:
            raise LiveReviewBoundaryError("contradictory observation timestamps")
        if not _range_covers(start, end, coverage[label]["inspected_ranges"]):
            raise LiveReviewBoundaryError(
                "observation timestamp is outside inspected coverage"
            )
        category = row["defect_category"]
        if category not in DEFECT_CATEGORIES:
            raise LiveReviewUnsupportedEdit("unsupported defect category")
        operation = DEFECT_TO_OPERATION.get(category)
        if operation not in SUPPORTED_EDIT_OPERATIONS:
            raise LiveReviewUnsupportedEdit(
                "defect cannot map to a supported Creator edit operation"
            )
        if row["severity"] not in SEVERITIES:
            raise LiveReviewIngestError("unsupported severity")
        normalized = {
            "attachment_label": label,
            "start_ms": start,
            "end_ms": end,
            "defect_category": category,
            "severity": row["severity"],
            "evidence": _nonempty(
                row["evidence"], f"observations[{index}].evidence"
            ),
            "description": _nonempty(
                row["description"], f"observations[{index}].description"
            ),
            "proposed_edit": _nonempty(
                row["proposed_edit"], f"observations[{index}].proposed_edit"
            ),
            "confidence": _confidence(
                row["confidence"], f"observations[{index}].confidence"
            ),
            "uncertainty": _nonempty(
                row["uncertainty"], f"observations[{index}].uncertainty"
            ),
        }
        signature = sha256_json(normalized)
        if signature in seen_observations:
            raise LiveReviewReplayConflict("duplicate actionable observation")
        seen_observations.add(signature)
        observations.append(normalized)

    summaries_raw = root["summaries"]
    if not isinstance(summaries_raw, list) or len(summaries_raw) != len(labels):
        raise LiveReviewBoundaryError(
            "summaries must contain exactly one row per blind attachment"
        )
    summaries: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(summaries_raw):
        row = _exact_keys(
            item,
            {"attachment_label", "assessment", "summary", "confidence", "uncertainty"},
            f"summaries[{index}]",
        )
        label = _nonempty(
            row["attachment_label"], f"summaries[{index}].attachment_label"
        )
        if label not in labels:
            raise LiveReviewLineageError("unknown candidate/attachment label")
        if label in summaries:
            raise LiveReviewReplayConflict("duplicate summary label")
        if row["assessment"] not in SUMMARY_ASSESSMENTS:
            raise LiveReviewIngestError("summary assessment invalid")
        summaries[label] = {
            "attachment_label": label,
            "assessment": row["assessment"],
            "summary": _nonempty(row["summary"], f"summaries[{index}].summary"),
            "confidence": _confidence(
                row["confidence"], f"summaries[{index}].confidence"
            ),
            "uncertainty": _nonempty(
                row["uncertainty"], f"summaries[{index}].uncertainty"
            ),
        }

    pairwise = _exact_keys(
        root["pairwise"],
        {"selection", "rationale", "confidence", "uncertainty"},
        "pairwise",
    )
    if pairwise["selection"] not in PAIRWISE_SELECTIONS:
        raise LiveReviewBoundaryError(
            "pairwise verdict must be A/B/tie/insufficient_evidence"
        )
    parsed_pairwise = {
        "selection": pairwise["selection"],
        "rationale": _nonempty(pairwise["rationale"], "pairwise.rationale"),
        "confidence": _confidence(pairwise["confidence"], "pairwise.confidence"),
        "uncertainty": _nonempty(pairwise["uncertainty"], "pairwise.uncertainty"),
    }
    return json.loads(
        canonical_json(
            {
                "contract_version": LIVE_REVIEW_RESPONSE_VERSION,
                "coverage": [coverage[label] for label in sorted(coverage)],
                "observations": observations,
                "summaries": [summaries[label] for label in sorted(summaries)],
                "pairwise": parsed_pairwise,
            }
        )
    )


def parse_live_review_capture(payload: Mapping[str, Any]) -> dict[str, Any]:
    root = _exact_keys(
        payload,
        {
            "contract_version",
            "capture_id",
            "capture_digest",
            "capture_kind",
            "conversation",
            "request_binding",
            "assistant_response",
            "evidence_boundary",
        },
        "live review capture",
    )
    if root["contract_version"] != LIVE_REVIEW_CAPTURE_VERSION:
        raise LiveReviewIngestError("capture contract_version invalid")
    if root["capture_kind"] not in CAPTURE_KINDS:
        raise LiveReviewBoundaryError("capture_kind invalid")
    conversation = _exact_keys(
        root["conversation"],
        {"conversation_id", "review_request_id", "assistant_message_id"},
        "conversation",
    )
    for field in ("conversation_id", "review_request_id", "assistant_message_id"):
        _nonempty(conversation[field], f"conversation.{field}")

    if root["request_binding"] != expected_request_binding():
        raise LiveReviewLineageError(
            "stale/wrong hashes, prompt drift, or Media/Bridge provenance drift"
        )

    response = _exact_keys(
        root["assistant_response"],
        {"model_identity", "raw_content", "raw_sha256"},
        "assistant_response",
    )
    _nonempty(response["model_identity"], "assistant_response.model_identity")
    raw_content = _nonempty(response["raw_content"], "assistant_response.raw_content")
    raw_sha = _sha256(response["raw_sha256"], "assistant_response.raw_sha256")
    if raw_sha != _response_bytes_sha(raw_content):
        raise LiveReviewLineageError("captured assistant response hash mismatch")
    parsed_response = parse_live_review_response(raw_content)

    if root["evidence_boundary"] != {
        "human_ground_truth": False,
        "human_label": False,
        "live_platform_evidence": False,
        "human_parity_gate_eligible": False,
    }:
        raise LiveReviewBoundaryError(
            "model capture cannot become human ground truth or human parity evidence"
        )

    expected_id = "glvrc1:" + sha256_json(_capture_identity_material(root))
    if root["capture_id"] != expected_id:
        raise LiveReviewLineageError("capture identity mismatch")
    _sha256(root["capture_digest"], "capture_digest")
    digest_material = dict(root)
    digest_material["capture_digest"] = ""
    expected_digest = sha256_json(digest_material)
    if root["capture_digest"] != expected_digest:
        raise LiveReviewLineageError("capture digest mismatch")

    normalized = dict(root)
    normalized["assistant_response"] = dict(response)
    normalized["parsed_response"] = parsed_response
    return json.loads(canonical_json(normalized))


def build_capture(
    *,
    capture_kind: str,
    conversation_id: str,
    review_request_id: str,
    assistant_message_id: str,
    model_identity: str,
    raw_content: str,
) -> dict[str, Any]:
    payload = {
        "contract_version": LIVE_REVIEW_CAPTURE_VERSION,
        "capture_id": "",
        "capture_digest": "",
        "capture_kind": capture_kind,
        "conversation": {
            "conversation_id": conversation_id,
            "review_request_id": review_request_id,
            "assistant_message_id": assistant_message_id,
        },
        "request_binding": expected_request_binding(),
        "assistant_response": {
            "model_identity": model_identity,
            "raw_content": raw_content,
            "raw_sha256": _response_bytes_sha(raw_content),
        },
        "evidence_boundary": {
            "human_ground_truth": False,
            "human_label": False,
            "live_platform_evidence": False,
            "human_parity_gate_eligible": False,
        },
    }
    payload["capture_id"] = "glvrc1:" + sha256_json(_capture_identity_material(payload))
    material = dict(payload)
    material["capture_digest"] = ""
    payload["capture_digest"] = sha256_json(material)
    parse_live_review_capture(payload)
    return json.loads(canonical_json(payload))


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
        attachment_sha256=attachment["attachment_sha256"],
        attachment_size=attachment["attachment_size"],
        attachment_mime_type="video/mp4",
        review_goal=REVIEW_GOAL,
        platform=REVIEW_PLATFORM,
        requested_focus=REVIEW_FOCUS,
        constraints=REVIEW_CONSTRAINTS,
    )


def _convert_real_capture(
    parsed_capture: Mapping[str, Any], *, reedit_round: int
) -> dict[str, Any]:
    if parsed_capture["capture_kind"] != "bridge_existing_chat_capture":
        raise LiveReviewBoundaryError(
            "fixture capture cannot be promoted to REAL_ATTACHED_VIDEO_REVIEW_INGESTED"
        )
    response = parsed_capture["parsed_response"]
    capture_digest = parsed_capture["capture_digest"]
    model_identity = parsed_capture["assistant_response"]["model_identity"]
    by_label = {row["blind_label"]: row for row in ATTACHMENTS}
    coverage = {row["attachment_label"]: row for row in response["coverage"]}
    summaries = {row["attachment_label"]: row for row in response["summaries"]}
    raw_observations = {label: [] for label in by_label}
    for row in response["observations"]:
        raw_observations[row["attachment_label"]].append(row)

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
            for row in raw_observations[label]
        ]
        summary = summaries[label]
        cov = coverage[label]
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
            "Choose between the two blinded Media R18 candidates using only "
            "captured model review evidence."
        ),
        producer_identity_blinded=True,
    )
    captured_selection = response["pairwise"]["selection"]
    if captured_selection in {"A", "B"}:
        selected_candidate = by_label[captured_selection]["candidate_id"]
        r22_label_by_candidate = {
            row["candidate_id"]: row["blind_label"]
            for row in pairwise_input["review_presentation"]
        }
        r22_selection = r22_label_by_candidate[selected_candidate]
    else:
        r22_selection = captured_selection
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
        "contract_version": LIVE_REVIEW_INGEST_VERSION,
        "ingest_id": "",
        "ingest_digest": "",
        "evidence_state": "REAL_ATTACHED_VIDEO_REVIEW_INGESTED",
        "capture_id": parsed_capture["capture_id"],
        "capture_digest": capture_digest,
        "conversation": parsed_capture["conversation"],
        "prompt_sha256": MEDIA_R18_PROMPT_SHA256,
        "human_ground_truth": False,
        "human_parity_inferred": False,
        "captured_pairwise_selection": captured_selection,
        "r22_pairwise_selection": pairwise_output["selection"],
        "critic_inputs": critic_inputs,
        "critic_outputs": critic_outputs,
        "pairwise_input": pairwise_input,
        "pairwise_output": pairwise_output,
        "creator_reedit_handoffs": handoffs,
        "authority": {
            "provider_mutation": False,
            "upload_performed": False,
            "creator_effect_applied": False,
            "media_effect_applied": False,
        },
    }
    result["ingest_id"] = "glvri1:" + sha256_json(
        {"capture_digest": capture_digest, "reedit_round": reedit_round}
    )
    material = dict(result)
    material["ingest_digest"] = ""
    result["ingest_digest"] = sha256_json(material)
    return json.loads(canonical_json(result))


def ingest_capture(
    payload: Mapping[str, Any], *, reedit_round: int = 0
) -> dict[str, Any]:
    parsed = parse_live_review_capture(payload)
    return _convert_real_capture(parsed, reedit_round=reedit_round)


class LiveReviewIngestLedger:
    def __init__(self) -> None:
        self._capture_digests: dict[str, str] = {}
        self._request_digests: dict[str, str] = {}
        self._results: dict[str, dict[str, Any]] = {}

    def ingest(
        self, payload: Mapping[str, Any], *, reedit_round: int = 0
    ) -> tuple[dict[str, Any], bool]:
        parsed = parse_live_review_capture(payload)
        capture_id = parsed["capture_id"]
        capture_digest = parsed["capture_digest"]
        request_key = (
            parsed["conversation"]["conversation_id"]
            + "\n"
            + parsed["conversation"]["review_request_id"]
        )
        existing_capture = self._capture_digests.get(capture_id)
        if existing_capture is not None:
            if existing_capture != capture_digest:
                raise LiveReviewReplayConflict(
                    "capture_id reused with conflicting captured response"
                )
            return json.loads(canonical_json(self._results[capture_id])), False
        existing_request = self._request_digests.get(request_key)
        if existing_request is not None and existing_request != capture_digest:
            raise LiveReviewReplayConflict(
                "review request already has a conflicting captured assistant response"
            )
        result = _convert_real_capture(parsed, reedit_round=reedit_round)
        self._capture_digests[capture_id] = capture_digest
        self._request_digests[request_key] = capture_digest
        self._results[capture_id] = result
        return json.loads(canonical_json(result)), True


def validate_fixture(payload: Mapping[str, Any]) -> dict[str, Any]:
    parsed = parse_live_review_capture(payload)
    if parsed["capture_kind"] != "fixture":
        raise LiveReviewBoundaryError(
            "fixture validator accepts capture_kind=fixture only"
        )
    return {
        "evidence_state": "FIXTURE_VALIDATED",
        "capture_id": parsed["capture_id"],
        "capture_digest": parsed["capture_digest"],
        "response_contract": parsed["parsed_response"]["contract_version"],
        "human_ground_truth": False,
        "real_attached_video_review_ingested": False,
        "creator_handoff_effect_emitted": False,
    }


def readiness_report(
    *,
    growth_sha: str,
    run_id: str,
    fixture_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fixture_validated = (
        fixture_result is not None
        and fixture_result.get("evidence_state") == "FIXTURE_VALIDATED"
    )
    return {
        "report_version": "growth.live_review_ingest_r24.readiness.v1",
        "growth_repository": "foto6/video3",
        "growth_source_sha": growth_sha,
        "workflow_run_id": run_id,
        "starting_r23_sha": GROWTH_R23_START_SHA,
        "ingest_contract": LIVE_REVIEW_CAPTURE_VERSION,
        "response_contract": LIVE_REVIEW_RESPONSE_VERSION,
        "ingest_result_contract": LIVE_REVIEW_INGEST_VERSION,
        "bridge_r26": {
            "repository": BRIDGE_R26_REPOSITORY,
            "source_sha": BRIDGE_R26_SHA,
            "attachment_contract": BRIDGE_R26_ATTACHMENT_CONTRACT,
        },
        "media_r18": expected_request_binding()["media_r18"],
        "prompt_sha256": MEDIA_R18_PROMPT_SHA256,
        "fixture_status": "FIXTURE_VALIDATED" if fixture_validated else "NOT_VALIDATED",
        "real_review_status": "NOT_AVAILABLE",
        "final_live_evidence_gate": "BLOCKED_REAL_ATTACHED_VIDEO_REVIEW_CAPTURE_REQUIRED",
        "invariants": {
            "conversation_and_review_request_bound": True,
            "blind_attachment_hash_size_bound": True,
            "media_source_render_lineage_bound": True,
            "exact_prompt_digest_required": True,
            "free_form_only_rejected": True,
            "unknown_label_rejected": True,
            "stale_hash_rejected": True,
            "contradictory_timestamp_rejected": True,
            "unsupported_edit_rejected": True,
            "duplicate_conflict_rejected": True,
            "exact_duplicate_idempotent": True,
            "coverage_uncertainty_preserved": True,
            "human_ground_truth": False,
            "human_parity_inferred": False,
        },
        "provider_mutation": False,
        "upload_performed": False,
    }


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture")
    parser.add_argument("--fixture")
    parser.add_argument("--reedit-round", type=int, default=0)
    parser.add_argument("--output")
    parser.add_argument("--report")
    parser.add_argument("--growth-sha", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if bool(args.capture) == bool(args.fixture):
        parser.error("provide exactly one of --capture or --fixture")

    path = Path(args.capture or args.fixture)
    payload = json.loads(path.read_text(encoding="utf-8"))
    fixture_result = None
    if args.fixture:
        fixture_result = validate_fixture(payload)
        result = fixture_result
    else:
        result = ingest_capture(payload, reedit_round=args.reedit_round)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    report = readiness_report(
        growth_sha=args.growth_sha, run_id=args.run_id, fixture_result=fixture_result
    )
    if args.capture:
        report["real_review_status"] = result["evidence_state"]
        report["final_live_evidence_gate"] = (
            "SATISFIED_REAL_ATTACHED_VIDEO_REVIEW_INGESTED"
        )
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps({"result": result, "readiness": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
