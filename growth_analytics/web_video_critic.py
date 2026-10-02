from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json


WEB_VIDEO_CRITIC_VERSION = "growth.web_video_critic.v1"
WEB_VIDEO_PAIRWISE_VERSION = "growth.web_video_critic_pairwise.v1"
WEB_VIDEO_REVIEW_BUNDLE_VERSION = "growth.real_artifact_decision_pack.v1"
WEB_VIDEO_TRANSPORT_BINDING_VERSION = (
    "growth.web_video_attachment_transport_binding.r22.v1"
)
BRIDGE_R25_REPOSITORY = "foto6/WebAIBridge"
BRIDGE_R25_BRANCH = "agent/bridge-r25-file-attachment-20261001"
BRIDGE_R25_SHA = "bfe6b043460b6c0c3d712cbcc7e0c9772d6bd3af"
BRIDGE_R25_CI_RUN_ID = 36888741188
BRIDGE_R25_CONTRACT = "bridge.chat_file_attachment.v1"
BRIDGE_R25_MAX_FILE_BYTES = 500000000
BRIDGE_R25_DISPOSITION = "READY_FOR_EXPLICIT_LIVE_REHEARSAL"
BRIDGE_R25_LIVE_PASS = False
BRIDGE_R25_READINESS_ARTIFACTS = (
    {
        "runner": "ubuntu-latest",
        "artifact_id": 11176350316,
        "artifact_name": "r25-readiness-ubuntu-latest",
        "artifact_digest":
            "sha256:0c9fcd14e9eae8cc7e19375ddd753e6bc91f0ad71829ad06dde7301690fcfbf6",
    },
    {
        "runner": "windows-latest",
        "artifact_id": 11176260440,
        "artifact_name": "r25-readiness-windows-latest",
        "artifact_digest":
            "sha256:b28c110abef306c6479043a671f3a4e6ebb875de463f4e59e709f8a3e5746a78",
    },
)
WEB_VIDEO_INTEGRATION_STATE = BRIDGE_R25_DISPOSITION
WEB_VIDEO_FIXTURE_MODE = "fixture_validation"
WEB_VIDEO_ATTACHED_MODE = "web_chat_attached_video"

DEFECT_CATEGORIES = (
    "hook_clarity",
    "pacing_coherence",
    "semantic_cut_correctness",
    "subject_framing_crop_quality",
    "broll_relevance",
    "caption_readability_emphasis_relevance",
    "visual_continuity",
    "motion_zoom_appropriateness",
    "audio_voice_music_balance",
    "payoff_cta_loop_coherence",
    "awkward_dead_moment",
)
SEVERITIES = (
    "info",
    "minor",
    "major",
    "hard_failure",
)
SUMMARY_ASSESSMENTS = (
    "actionable_findings",
    "no_material_defect_observed",
    "insufficient_evidence",
)
PAIRWISE_SELECTIONS = (
    "A",
    "B",
    "tie",
    "insufficient_evidence",
)
_SHA256_HEX = set("0123456789abcdef")


class WebVideoCriticError(ValueError):
    pass


class WebVideoCriticBoundaryError(
    WebVideoCriticError
):
    pass


class WebVideoCriticLineageError(
    WebVideoCriticError
):
    pass


def _nonempty(
    value: Any,
    field: str,
) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
    ):
        raise WebVideoCriticError(
            f"{field} must be non-empty string"
        )
    return value


def _sha256(
    value: Any,
    field: str,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(
            char not in _SHA256_HEX
            for char in value
        )
    ):
        raise WebVideoCriticError(
            f"{field} must be lowercase SHA-256"
        )
    return value


def _sha1(
    value: Any,
    field: str,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(
            char not in _SHA256_HEX
            for char in value
        )
    ):
        raise WebVideoCriticError(
            f"{field} must be lowercase Git SHA-1"
        )
    return value


def _positive_int(
    value: Any,
    field: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise WebVideoCriticError(
            f"{field} must be integer >= 1"
        )
    return value


def _nonnegative_int(
    value: Any,
    field: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise WebVideoCriticError(
            f"{field} must be integer >= 0"
        )
    return value


def _confidence(
    value: Any,
    field: str,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(
            value,
            (int, float),
        )
        or not math.isfinite(
            float(value)
        )
        or float(value) < 0.0
        or float(value) > 1.0
    ):
        raise WebVideoCriticError(
            f"{field} must be finite in [0,1]"
        )
    return round(
        float(value),
        6,
    )


def _string_array(
    value: Any,
    field: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if (
        not isinstance(value, list)
        or (
            not allow_empty
            and not value
        )
        or any(
            not isinstance(item, str)
            or not item.strip()
            for item in value
        )
    ):
        raise WebVideoCriticError(
            f"{field} must be string array"
        )
    return list(value)


def build_bridge_r25_transport_binding() -> dict[str, Any]:
    material = {
        "contract_version":
            WEB_VIDEO_TRANSPORT_BINDING_VERSION,
        "repository":
            BRIDGE_R25_REPOSITORY,
        "branch":
            BRIDGE_R25_BRANCH,
        "source_sha":
            BRIDGE_R25_SHA,
        "ci_run_id":
            BRIDGE_R25_CI_RUN_ID,
        "bridge_contract":
            BRIDGE_R25_CONTRACT,
        "max_file_bytes":
            BRIDGE_R25_MAX_FILE_BYTES,
        "disposition":
            BRIDGE_R25_DISPOSITION,
        "live_pass":
            BRIDGE_R25_LIVE_PASS,
        "no_live_deploy":
            True,
        "no_cutover":
            True,
        "real_user_chat_upload_performed":
            False,
        "readiness_artifacts": [
            dict(row)
            for row in BRIDGE_R25_READINESS_ARTIFACTS
        ],
        "binding_digest": "",
    }
    digest_material = dict(material)
    digest_material["binding_digest"] = ""
    material["binding_digest"] = sha256_json(
        digest_material
    )
    return json.loads(
        canonical_json(material)
    )


def _validate_bridge_r25_transport_binding(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    expected = build_bridge_r25_transport_binding()
    if not isinstance(raw, Mapping):
        raise WebVideoCriticBoundaryError(
            "attachment transport binding must be object"
        )
    if raw.get("live_pass") is not False:
        raise WebVideoCriticBoundaryError(
            "Bridge R25 livePass must remain false for R22 rehearsal readiness"
        )
    if raw.get("disposition") != (
        "READY_FOR_EXPLICIT_LIVE_REHEARSAL"
    ):
        raise WebVideoCriticBoundaryError(
            "Bridge R25 disposition is not rehearsal-ready"
        )
    if dict(raw) != expected:
        raise WebVideoCriticLineageError(
            "attachment transport does not match exact-green Bridge R25 authority"
        )
    return json.loads(
        canonical_json(dict(raw))
    )


def _input_id_material(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "source": payload["source"],
        "media": payload["media"],
        "candidate": payload["candidate"],
        "review_bundle": payload[
            "review_bundle"
        ],
        "attachment": payload[
            "attachment"
        ],
        "attachment_transport": payload[
            "attachment_transport"
        ],
        "brief": payload["brief"],
        "integration_state": payload[
            "integration_state"
        ],
    }


def build_web_video_critic_input(
    *,
    source_id: str,
    source_sha256: str,
    source_size: int,
    media_repository: str,
    media_producer_sha: str,
    candidate_id: str,
    render_sha256: str,
    render_size: int,
    render_export_sha256: str,
    review_bundle_digest: str,
    attachment_sha256: str,
    attachment_size: int,
    attachment_mime_type: str,
    review_goal: str,
    platform: str,
    requested_focus: Sequence[str],
    constraints: Sequence[str],
) -> dict[str, Any]:
    payload = {
        "contract_version":
            WEB_VIDEO_CRITIC_VERSION,
        "message_type": "input",
        "input_id": "",
        "input_digest": "",
        "integration_state":
            WEB_VIDEO_INTEGRATION_STATE,
        "source": {
            "source_id":
                _nonempty(
                    source_id,
                    "source_id",
                ),
            "sha256":
                _sha256(
                    source_sha256,
                    "source_sha256",
                ),
            "size":
                _positive_int(
                    source_size,
                    "source_size",
                ),
        },
        "media": {
            "repository":
                _nonempty(
                    media_repository,
                    "media_repository",
                ),
            "producer_sha":
                _sha1(
                    media_producer_sha,
                    "media_producer_sha",
                ),
        },
        "candidate": {
            "candidate_id":
                _nonempty(
                    candidate_id,
                    "candidate_id",
                ),
            "render_sha256":
                _sha256(
                    render_sha256,
                    "render_sha256",
                ),
            "render_size":
                _positive_int(
                    render_size,
                    "render_size",
                ),
            "render_export_sha256":
                _sha256(
                    render_export_sha256,
                    "render_export_sha256",
                ),
        },
        "review_bundle": {
            "contract_version":
                WEB_VIDEO_REVIEW_BUNDLE_VERSION,
            "digest":
                _sha256(
                    review_bundle_digest,
                    "review_bundle_digest",
                ),
        },
        "attachment": {
            "sha256":
                _sha256(
                    attachment_sha256,
                    "attachment_sha256",
                ),
            "size":
                _positive_int(
                    attachment_size,
                    "attachment_size",
                ),
            "mime_type":
                _nonempty(
                    attachment_mime_type,
                    "attachment_mime_type",
                ),
            "attachment_identity": "",
        },
        "attachment_transport":
            build_bridge_r25_transport_binding(),
        "brief": {
            "review_goal":
                _nonempty(
                    review_goal,
                    "review_goal",
                ),
            "platform":
                _nonempty(
                    platform,
                    "platform",
                ),
            "requested_focus":
                list(requested_focus),
            "constraints":
                list(constraints),
        },
        "evidence_boundary": {
            "model_review_only": True,
            "human_ground_truth": False,
            "human_label": False,
            "live_platform_evidence": False,
            "human_parity_gate_eligible": False,
        },
    }
    payload["attachment"][
        "attachment_identity"
    ] = (
        "gvwa1:"
        + sha256_json({
            "candidate_id":
                payload[
                    "candidate"
                ]["candidate_id"],
            "sha256":
                payload[
                    "attachment"
                ]["sha256"],
            "size":
                payload[
                    "attachment"
                ]["size"],
            "mime_type":
                payload[
                    "attachment"
                ]["mime_type"],
        })
    )
    payload["input_id"] = (
        "gvwi1:"
        + sha256_json(
            _input_id_material(
                payload
            )
        )
    )
    material = dict(payload)
    material[
        "input_digest"
    ] = ""
    payload["input_digest"] = (
        sha256_json(material)
    )
    return parse_web_video_critic_input(
        payload
    )


def parse_web_video_critic_input(
    payload: Mapping[str, Any],
    *,
    expected_source_sha256: str | None = None,
    expected_media_producer_sha: str | None = None,
    expected_review_bundle_digest: str | None = None,
) -> dict[str, Any]:
    required = {
        "contract_version",
        "message_type",
        "input_id",
        "input_digest",
        "integration_state",
        "source",
        "media",
        "candidate",
        "review_bundle",
        "attachment",
        "attachment_transport",
        "brief",
        "evidence_boundary",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != required
    ):
        raise WebVideoCriticError(
            "web-video critic input fields invalid"
        )
    if (
        payload["contract_version"]
        != WEB_VIDEO_CRITIC_VERSION
        or payload["message_type"]
        != "input"
    ):
        raise WebVideoCriticError(
            "unsupported web-video critic input"
        )
    if (
        payload["integration_state"]
        != WEB_VIDEO_INTEGRATION_STATE
    ):
        raise WebVideoCriticBoundaryError(
            "attachment transport state must match READY_FOR_EXPLICIT_LIVE_REHEARSAL"
        )
    _validate_bridge_r25_transport_binding(
        payload["attachment_transport"]
    )

    source = payload["source"]
    if (
        not isinstance(source, Mapping)
        or set(source)
        != {
            "source_id",
            "sha256",
            "size",
        }
    ):
        raise WebVideoCriticError(
            "source fields invalid"
        )
    _nonempty(
        source["source_id"],
        "source.source_id",
    )
    source_sha = _sha256(
        source["sha256"],
        "source.sha256",
    )
    _positive_int(
        source["size"],
        "source.size",
    )
    if (
        expected_source_sha256
        is not None
        and source_sha
        != _sha256(
            expected_source_sha256,
            "expected_source_sha256",
        )
    ):
        raise WebVideoCriticLineageError(
            "stale or wrong source SHA"
        )

    media = payload["media"]
    if (
        not isinstance(media, Mapping)
        or set(media)
        != {
            "repository",
            "producer_sha",
        }
    ):
        raise WebVideoCriticError(
            "media fields invalid"
        )
    _nonempty(
        media["repository"],
        "media.repository",
    )
    producer_sha = _sha1(
        media["producer_sha"],
        "media.producer_sha",
    )
    if (
        expected_media_producer_sha
        is not None
        and producer_sha
        != _sha1(
            expected_media_producer_sha,
            "expected_media_producer_sha",
        )
    ):
        raise WebVideoCriticLineageError(
            "stale or wrong Media producer SHA"
        )

    candidate = payload["candidate"]
    if (
        not isinstance(
            candidate,
            Mapping,
        )
        or set(candidate)
        != {
            "candidate_id",
            "render_sha256",
            "render_size",
            "render_export_sha256",
        }
    ):
        raise WebVideoCriticError(
            "candidate fields invalid"
        )
    _nonempty(
        candidate[
            "candidate_id"
        ],
        "candidate.candidate_id",
    )
    render_sha = _sha256(
        candidate[
            "render_sha256"
        ],
        "candidate.render_sha256",
    )
    render_size = _positive_int(
        candidate[
            "render_size"
        ],
        "candidate.render_size",
    )
    _sha256(
        candidate[
            "render_export_sha256"
        ],
        "candidate.render_export_sha256",
    )

    review_bundle = payload[
        "review_bundle"
    ]
    if (
        not isinstance(
            review_bundle,
            Mapping,
        )
        or set(
            review_bundle
        )
        != {
            "contract_version",
            "digest",
        }
        or review_bundle[
            "contract_version"
        ]
        != WEB_VIDEO_REVIEW_BUNDLE_VERSION
    ):
        raise WebVideoCriticError(
            "review_bundle fields invalid"
        )
    bundle_digest = _sha256(
        review_bundle["digest"],
        "review_bundle.digest",
    )
    if (
        expected_review_bundle_digest
        is not None
        and bundle_digest
        != _sha256(
            expected_review_bundle_digest,
            "expected_review_bundle_digest",
        )
    ):
        raise WebVideoCriticLineageError(
            "stale or wrong review-bundle digest"
        )

    attachment = payload[
        "attachment"
    ]
    if (
        not isinstance(
            attachment,
            Mapping,
        )
        or set(
            attachment
        )
        != {
            "sha256",
            "size",
            "mime_type",
            "attachment_identity",
        }
    ):
        raise WebVideoCriticError(
            "attachment fields invalid"
        )
    attachment_sha = _sha256(
        attachment["sha256"],
        "attachment.sha256",
    )
    attachment_size = _positive_int(
        attachment["size"],
        "attachment.size",
    )
    if (
        attachment[
            "mime_type"
        ] != "video/mp4"
    ):
        raise WebVideoCriticBoundaryError(
            "direct-video attachment must be video/mp4"
        )
    if (
        attachment_sha
        != render_sha
        or attachment_size
        != render_size
    ):
        raise WebVideoCriticLineageError(
            "attached MP4 identity does not match candidate render"
        )
    if (
        attachment_size
        > BRIDGE_R25_MAX_FILE_BYTES
    ):
        raise WebVideoCriticBoundaryError(
            "candidate exceeds exact Bridge R25 per-file cap"
        )
    expected_attachment_identity = (
        "gvwa1:"
        + sha256_json({
            "candidate_id":
                candidate[
                    "candidate_id"
                ],
            "sha256":
                attachment_sha,
            "size":
                attachment_size,
            "mime_type":
                attachment[
                    "mime_type"
                ],
        })
    )
    if (
        attachment[
            "attachment_identity"
        ]
        != expected_attachment_identity
    ):
        raise WebVideoCriticLineageError(
            "attachment identity mismatch"
        )

    brief = payload["brief"]
    if (
        not isinstance(brief, Mapping)
        or set(brief)
        != {
            "review_goal",
            "platform",
            "requested_focus",
            "constraints",
        }
    ):
        raise WebVideoCriticError(
            "brief fields invalid"
        )
    _nonempty(
        brief["review_goal"],
        "brief.review_goal",
    )
    _nonempty(
        brief["platform"],
        "brief.platform",
    )
    _string_array(
        brief["requested_focus"],
        "brief.requested_focus",
    )
    _string_array(
        brief["constraints"],
        "brief.constraints",
        allow_empty=True,
    )

    if payload[
        "evidence_boundary"
    ] != {
        "model_review_only": True,
        "human_ground_truth": False,
        "human_label": False,
        "live_platform_evidence": False,
        "human_parity_gate_eligible": False,
    }:
        raise WebVideoCriticBoundaryError(
            "web-video critic evidence boundary invalid"
        )

    expected_input_id = (
        "gvwi1:"
        + sha256_json(
            _input_id_material(
                payload
            )
        )
    )
    if (
        payload["input_id"]
        != expected_input_id
    ):
        raise WebVideoCriticLineageError(
            "web-video input identity mismatch"
        )
    _sha256(
        payload["input_digest"],
        "input_digest",
    )
    material = dict(payload)
    material[
        "input_digest"
    ] = ""
    if (
        sha256_json(material)
        != payload[
            "input_digest"
        ]
    ):
        raise WebVideoCriticLineageError(
            "web-video input digest mismatch"
        )
    return json.loads(
        canonical_json(
            dict(payload)
        )
    )


def _validate_range(
    raw: Mapping[str, Any],
    *,
    field: str,
) -> dict[str, Any]:
    if (
        not isinstance(raw, Mapping)
        or set(raw)
        != {
            "start_ms",
            "end_ms",
            "kind",
        }
    ):
        raise WebVideoCriticError(
            f"{field} fields invalid"
        )
    start = _nonnegative_int(
        raw["start_ms"],
        f"{field}.start_ms",
    )
    end = _positive_int(
        raw["end_ms"],
        f"{field}.end_ms",
    )
    if end <= start:
        raise WebVideoCriticError(
            f"{field} end_ms must exceed start_ms"
        )
    if raw["kind"] not in {
        "continuous_review",
        "sampled_review",
        "targeted_recheck",
    }:
        raise WebVideoCriticError(
            f"{field}.kind invalid"
        )
    return {
        "start_ms": start,
        "end_ms": end,
        "kind": raw["kind"],
    }


def _observation_identity(
    *,
    input_digest: str,
    observation: Mapping[str, Any],
) -> str:
    material = {
        key: value
        for key, value
        in observation.items()
        if key != "observation_id"
    }
    return (
        "gvwo1:"
        + sha256_json({
            "input_digest":
                input_digest,
            "observation":
                material,
        })
    )


def build_web_video_observation(
    *,
    input_digest: str,
    scope: str,
    start_ms: int | None,
    end_ms: int | None,
    defect_category: str,
    severity: str,
    evidence: str,
    description: str,
    proposed_edit: str,
    confidence: float,
    uncertainty: str,
) -> dict[str, Any]:
    _sha256(
        input_digest,
        "input_digest",
    )
    observation = {
        "observation_id": "",
        "scope": scope,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "defect_category":
            defect_category,
        "severity": severity,
        "evidence": evidence,
        "description": description,
        "proposed_edit":
            proposed_edit,
        "confidence": confidence,
        "uncertainty":
            uncertainty,
        "evidence_kind":
            "direct_video_model_opinion",
        "human_ground_truth":
            False,
        "human_label": False,
        "live_platform_evidence":
            False,
    }
    observation[
        "observation_id"
    ] = _observation_identity(
        input_digest=input_digest,
        observation=observation,
    )
    return _validate_observation(
        observation,
        input_digest=input_digest,
    )


def _validate_observation(
    raw: Mapping[str, Any],
    *,
    input_digest: str,
) -> dict[str, Any]:
    required = {
        "observation_id",
        "scope",
        "start_ms",
        "end_ms",
        "defect_category",
        "severity",
        "evidence",
        "description",
        "proposed_edit",
        "confidence",
        "uncertainty",
        "evidence_kind",
        "human_ground_truth",
        "human_label",
        "live_platform_evidence",
    }
    if (
        not isinstance(raw, Mapping)
        or set(raw) != required
    ):
        raise WebVideoCriticError(
            "web-video observation fields invalid"
        )
    scope = raw["scope"]
    if scope not in {
        "local",
        "whole_video",
    }:
        raise WebVideoCriticError(
            "observation scope invalid"
        )
    if scope == "local":
        if (
            raw["start_ms"] is None
            or raw["end_ms"] is None
        ):
            raise WebVideoCriticBoundaryError(
                "local defects require start_ms and end_ms"
            )
        start = _nonnegative_int(
            raw["start_ms"],
            "observation.start_ms",
        )
        end = _positive_int(
            raw["end_ms"],
            "observation.end_ms",
        )
        if end <= start:
            raise WebVideoCriticError(
                "local defect end_ms must exceed start_ms"
            )
    else:
        if (
            raw["start_ms"] is not None
            or raw["end_ms"] is not None
        ):
            raise WebVideoCriticBoundaryError(
                "whole-video observation must not invent local timestamps"
            )
        start = None
        end = None
    if (
        raw["defect_category"]
        not in DEFECT_CATEGORIES
    ):
        raise WebVideoCriticError(
            "unsupported defect_category"
        )
    if raw["severity"] not in SEVERITIES:
        raise WebVideoCriticError(
            "unsupported severity"
        )
    for field in (
        "evidence",
        "description",
        "proposed_edit",
        "uncertainty",
    ):
        _nonempty(
            raw[field],
            f"observation.{field}",
        )
    confidence = _confidence(
        raw["confidence"],
        "observation.confidence",
    )
    if (
        raw["evidence_kind"]
        != "direct_video_model_opinion"
        or raw[
            "human_ground_truth"
        ] is not False
        or raw[
            "human_label"
        ] is not False
        or raw[
            "live_platform_evidence"
        ] is not False
    ):
        raise WebVideoCriticBoundaryError(
            "model observation cannot become human/live evidence"
        )
    expected_id = (
        _observation_identity(
            input_digest=
                input_digest,
            observation=raw,
        )
    )
    if (
        raw["observation_id"]
        != expected_id
    ):
        raise WebVideoCriticLineageError(
            "observation identity mismatch"
        )
    result = dict(raw)
    result["start_ms"] = start
    result["end_ms"] = end
    result["confidence"] = confidence
    return json.loads(
        canonical_json(result)
    )


def _creator_directives(
    observations: Sequence[
        Mapping[str, Any]
    ],
) -> list[dict[str, Any]]:
    directives = []
    for observation in observations:
        if (
            observation["scope"]
            != "local"
            or observation[
                "severity"
            ] == "info"
        ):
            continue
        directives.append({
            "directive_id": (
                "gvwd1:"
                + sha256_json({
                    "observation_id":
                        observation[
                            "observation_id"
                        ],
                    "proposed_edit":
                        observation[
                            "proposed_edit"
                        ],
                })
            ),
            "observation_id":
                observation[
                    "observation_id"
                ],
            "start_ms":
                observation[
                    "start_ms"
                ],
            "end_ms":
                observation[
                    "end_ms"
                ],
            "defect_category":
                observation[
                    "defect_category"
                ],
            "severity":
                observation[
                    "severity"
                ],
            "directive":
                observation[
                    "proposed_edit"
                ],
            "evidence":
                observation[
                    "evidence"
                ],
            "confidence":
                observation[
                    "confidence"
                ],
            "uncertainty":
                observation[
                    "uncertainty"
                ],
        })
    return directives


def build_web_video_critic_output(
    *,
    critic_input: Mapping[str, Any],
    model_identity: str,
    execution_mode: str,
    inspected_ranges: Sequence[
        Mapping[str, Any]
    ],
    coverage_notes: str,
    observations: Sequence[
        Mapping[str, Any]
    ],
    assessment: str,
    whole_video_summary: str,
    summary_confidence: float,
    summary_uncertainty: str,
    transport_evidence_digest: str | None = None,
    verified_transport_evidence_digest: str | None = None,
) -> dict[str, Any]:
    parsed_input = (
        parse_web_video_critic_input(
            critic_input
        )
    )
    parsed_observations = [
        _validate_observation(
            observation,
            input_digest=
                parsed_input[
                    "input_digest"
                ],
        )
        for observation
        in observations
    ]
    output = {
        "contract_version":
            WEB_VIDEO_CRITIC_VERSION,
        "message_type": "output",
        "output_id": "",
        "output_digest": "",
        "input_id":
            parsed_input[
                "input_id"
            ],
        "input_digest":
            parsed_input[
                "input_digest"
            ],
        "review_provenance": {
            "reviewer_kind":
                "web_chat_video_model",
            "model_identity":
                _nonempty(
                    model_identity,
                    "model_identity",
                ),
            "execution_mode":
                execution_mode,
            "integration_state":
                WEB_VIDEO_INTEGRATION_STATE,
            "attachment_identity":
                parsed_input[
                    "attachment"
                ][
                    "attachment_identity"
                ],
            "render_sha256":
                parsed_input[
                    "candidate"
                ][
                    "render_sha256"
                ],
            "render_size":
                parsed_input[
                    "candidate"
                ][
                    "render_size"
                ],
            "source_sha256":
                parsed_input[
                    "source"
                ]["sha256"],
            "media_producer_sha":
                parsed_input[
                    "media"
                ][
                    "producer_sha"
                ],
            "review_bundle_digest":
                parsed_input[
                    "review_bundle"
                ]["digest"],
            "attachment_transport_digest":
                parsed_input[
                    "attachment_transport"
                ][
                    "binding_digest"
                ],
            "bridge_live_pass":
                parsed_input[
                    "attachment_transport"
                ]["live_pass"],
            "direct_video_primary":
                execution_mode
                == WEB_VIDEO_ATTACHED_MODE,
            "transport_evidence_digest":
                transport_evidence_digest,
        },
        "coverage": {
            "method": (
                "direct_video_model_review"
                if execution_mode
                == WEB_VIDEO_ATTACHED_MODE
                else "fixture_schema_validation"
            ),
            "inspected_ranges": [
                dict(row)
                for row
                in inspected_ranges
            ],
            "uninspected_possible": True,
            "every_frame_inspected": False,
            "notes":
                _nonempty(
                    coverage_notes,
                    "coverage_notes",
                ),
        },
        "observations":
            parsed_observations,
        "whole_video_summary": {
            "assessment":
                assessment,
            "summary":
                _nonempty(
                    whole_video_summary,
                    "whole_video_summary",
                ),
            "confidence":
                summary_confidence,
            "uncertainty":
                _nonempty(
                    summary_uncertainty,
                    "summary_uncertainty",
                ),
        },
        "creator_reedit_directives":
            _creator_directives(
                parsed_observations
            ),
        "evidence_boundary": {
            "model_opinion": True,
            "human_ground_truth": False,
            "human_label": False,
            "live_platform_metrics": False,
            "human_parity_gate_eligible": False,
        },
        "authority": {
            "advisory_only": True,
            "publish_authorized": False,
            "provider_mutation": False,
            "creator_mutation": False,
            "media_mutation": False,
            "release_authorized": False,
        },
    }
    output["output_id"] = (
        "gvwco1:"
        + sha256_json({
            "input_digest":
                output[
                    "input_digest"
                ],
            "model_identity":
                output[
                    "review_provenance"
                ][
                    "model_identity"
                ],
            "execution_mode":
                execution_mode,
        })
    )
    material = dict(output)
    material[
        "output_digest"
    ] = ""
    output["output_digest"] = (
        sha256_json(material)
    )
    return parse_web_video_critic_output(
        output,
        critic_input=parsed_input,
        verified_transport_evidence_digest=verified_transport_evidence_digest,
    )


def parse_web_video_critic_output(
    payload: Mapping[str, Any],
    *,
    critic_input: Mapping[str, Any],
    verified_transport_evidence_digest: str | None = None,
) -> dict[str, Any]:
    parsed_input = (
        parse_web_video_critic_input(
            critic_input
        )
    )
    required = {
        "contract_version",
        "message_type",
        "output_id",
        "output_digest",
        "input_id",
        "input_digest",
        "review_provenance",
        "coverage",
        "observations",
        "whole_video_summary",
        "creator_reedit_directives",
        "evidence_boundary",
        "authority",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != required
    ):
        raise WebVideoCriticError(
            "web-video critic output fields invalid"
        )
    if (
        payload["contract_version"]
        != WEB_VIDEO_CRITIC_VERSION
        or payload["message_type"]
        != "output"
    ):
        raise WebVideoCriticError(
            "unsupported web-video critic output"
        )
    if (
        payload["input_id"]
        != parsed_input["input_id"]
        or payload[
            "input_digest"
        ]
        != parsed_input[
            "input_digest"
        ]
    ):
        raise WebVideoCriticLineageError(
            "critic output does not bind exact input"
        )

    provenance = payload[
        "review_provenance"
    ]
    if (
        not isinstance(
            provenance,
            Mapping,
        )
        or set(provenance)
        != {
            "reviewer_kind",
            "model_identity",
            "execution_mode",
            "integration_state",
            "attachment_identity",
            "render_sha256",
            "render_size",
            "source_sha256",
            "media_producer_sha",
            "review_bundle_digest",
            "attachment_transport_digest",
            "bridge_live_pass",
            "direct_video_primary",
            "transport_evidence_digest",
        }
    ):
        raise WebVideoCriticError(
            "review_provenance fields invalid"
        )
    if (
        provenance[
            "reviewer_kind"
        ]
        != "web_chat_video_model"
        or provenance[
            "integration_state"
        ]
        != WEB_VIDEO_INTEGRATION_STATE
    ):
        raise WebVideoCriticBoundaryError(
            "web-chat review provenance invalid"
        )
    _nonempty(
        provenance[
            "model_identity"
        ],
        "review_provenance.model_identity",
    )
    mode = provenance[
        "execution_mode"
    ]
    if mode not in {
        WEB_VIDEO_FIXTURE_MODE,
        WEB_VIDEO_ATTACHED_MODE,
    }:
        raise WebVideoCriticError(
            "unsupported web-video execution mode"
        )
    if mode == WEB_VIDEO_FIXTURE_MODE:
        if verified_transport_evidence_digest is not None:
            raise WebVideoCriticBoundaryError(
                "fixture mode cannot consume verified attached-video capture evidence"
            )
        if (
            provenance[
                "direct_video_primary"
            ] is not False
            or provenance[
                "transport_evidence_digest"
            ] is not None
        ):
            raise WebVideoCriticBoundaryError(
                "fixture mode cannot claim direct attachment transport evidence"
            )
    else:
        if (
            provenance[
                "direct_video_primary"
            ] is not True
            or provenance[
                "transport_evidence_digest"
            ] is None
        ):
            raise WebVideoCriticBoundaryError(
                "attached-video mode requires transport evidence"
            )
        transport_digest = _sha256(
            provenance[
                "transport_evidence_digest"
            ],
            "transport_evidence_digest",
        )
        if parsed_input[
            "attachment_transport"
        ]["live_pass"] is not True:
            if verified_transport_evidence_digest is None:
                raise WebVideoCriticBoundaryError(
                    "attached-video review requires an externally verified capture while exact Bridge R25 livePass=false"
                )
            verified_digest = _sha256(
                verified_transport_evidence_digest,
                "verified_transport_evidence_digest",
            )
            if transport_digest != verified_digest:
                raise WebVideoCriticLineageError(
                    "attached-video transport evidence does not match verified capture"
                )

    exact_bindings = {
        "attachment_identity":
            parsed_input[
                "attachment"
            ][
                "attachment_identity"
            ],
        "render_sha256":
            parsed_input[
                "candidate"
            ][
                "render_sha256"
            ],
        "render_size":
            parsed_input[
                "candidate"
            ][
                "render_size"
            ],
        "source_sha256":
            parsed_input[
                "source"
            ]["sha256"],
        "media_producer_sha":
            parsed_input[
                "media"
            ][
                "producer_sha"
            ],
        "review_bundle_digest":
            parsed_input[
                "review_bundle"
            ]["digest"],
        "attachment_transport_digest":
            parsed_input[
                "attachment_transport"
            ][
                "binding_digest"
            ],
        "bridge_live_pass":
            parsed_input[
                "attachment_transport"
            ]["live_pass"],
    }
    if any(
        provenance[key] != value
        for key, value
        in exact_bindings.items()
    ):
        raise WebVideoCriticLineageError(
            "review provenance lineage mismatch"
        )

    coverage = payload[
        "coverage"
    ]
    if (
        not isinstance(
            coverage,
            Mapping,
        )
        or set(coverage)
        != {
            "method",
            "inspected_ranges",
            "uninspected_possible",
            "every_frame_inspected",
            "notes",
        }
    ):
        raise WebVideoCriticError(
            "coverage fields invalid"
        )
    expected_method = (
        "direct_video_model_review"
        if mode
        == WEB_VIDEO_ATTACHED_MODE
        else "fixture_schema_validation"
    )
    if (
        coverage["method"]
        != expected_method
        or coverage[
            "uninspected_possible"
        ] is not True
        or coverage[
            "every_frame_inspected"
        ] is not False
    ):
        raise WebVideoCriticBoundaryError(
            "coverage must preserve inspection uncertainty and never claim every frame"
        )
    _nonempty(
        coverage["notes"],
        "coverage.notes",
    )
    if not isinstance(
        coverage[
            "inspected_ranges"
        ],
        list,
    ):
        raise WebVideoCriticError(
            "inspected_ranges must be array"
        )
    [
        _validate_range(
            row,
            field=f"coverage.inspected_ranges[{index}]",
        )
        for index, row
        in enumerate(
            coverage[
                "inspected_ranges"
            ]
        )
    ]

    observations = payload[
        "observations"
    ]
    if not isinstance(
        observations,
        list,
    ):
        raise WebVideoCriticError(
            "observations must be array"
        )
    parsed_observations = [
        _validate_observation(
            row,
            input_digest=
                parsed_input[
                    "input_digest"
                ],
        )
        for row in observations
    ]
    observation_ids = [
        row["observation_id"]
        for row
        in parsed_observations
    ]
    if (
        len(
            observation_ids
        )
        != len(
            set(
                observation_ids
            )
        )
    ):
        raise WebVideoCriticError(
            "duplicate critic observation"
        )

    summary = payload[
        "whole_video_summary"
    ]
    if (
        not isinstance(
            summary,
            Mapping,
        )
        or set(summary)
        != {
            "assessment",
            "summary",
            "confidence",
            "uncertainty",
        }
        or summary[
            "assessment"
        ]
        not in SUMMARY_ASSESSMENTS
    ):
        raise WebVideoCriticError(
            "whole_video_summary invalid"
        )
    _nonempty(
        summary["summary"],
        "whole_video_summary.summary",
    )
    _confidence(
        summary[
            "confidence"
        ],
        "whole_video_summary.confidence",
    )
    _nonempty(
        summary[
            "uncertainty"
        ],
        "whole_video_summary.uncertainty",
    )

    expected_directives = (
        _creator_directives(
            parsed_observations
        )
    )
    if (
        payload[
            "creator_reedit_directives"
        ]
        != expected_directives
    ):
        raise WebVideoCriticBoundaryError(
            "Creator re-edit directives must be deterministic projections of time-coded model observations"
        )

    if payload[
        "evidence_boundary"
    ] != {
        "model_opinion": True,
        "human_ground_truth": False,
        "human_label": False,
        "live_platform_metrics": False,
        "human_parity_gate_eligible": False,
    }:
        raise WebVideoCriticBoundaryError(
            "model review cannot become human/live evidence or advance human parity"
        )
    if payload["authority"] != {
        "advisory_only": True,
        "publish_authorized": False,
        "provider_mutation": False,
        "creator_mutation": False,
        "media_mutation": False,
        "release_authorized": False,
    }:
        raise WebVideoCriticBoundaryError(
            "web-video critic authority invalid"
        )

    expected_output_id = (
        "gvwco1:"
        + sha256_json({
            "input_digest":
                payload[
                    "input_digest"
                ],
            "model_identity":
                provenance[
                    "model_identity"
                ],
            "execution_mode":
                mode,
        })
    )
    if (
        payload["output_id"]
        != expected_output_id
    ):
        raise WebVideoCriticLineageError(
            "critic output identity mismatch"
        )
    _sha256(
        payload["output_digest"],
        "output_digest",
    )
    material = dict(payload)
    material[
        "output_digest"
    ] = ""
    if (
        sha256_json(material)
        != payload[
            "output_digest"
        ]
    ):
        raise WebVideoCriticLineageError(
            "critic output digest mismatch"
        )
    return json.loads(
        canonical_json(
            dict(payload)
        )
    )


def _pair_binding(
    critic_input: Mapping[str, Any],
) -> dict[str, Any]:
    parsed = (
        parse_web_video_critic_input(
            critic_input
        )
    )
    return {
        "candidate_id":
            parsed[
                "candidate"
            ][
                "candidate_id"
            ],
        "source_sha256":
            parsed[
                "source"
            ]["sha256"],
        "media_producer_sha":
            parsed[
                "media"
            ][
                "producer_sha"
            ],
        "render_sha256":
            parsed[
                "candidate"
            ][
                "render_sha256"
            ],
        "render_size":
            parsed[
                "candidate"
            ][
                "render_size"
            ],
        "attachment_identity":
            parsed[
                "attachment"
            ][
                "attachment_identity"
            ],
        "review_bundle_digest":
            parsed[
                "review_bundle"
            ]["digest"],
        "attachment_transport_digest":
            parsed[
                "attachment_transport"
            ][
                "binding_digest"
            ],
        "critic_input_digest":
            parsed[
                "input_digest"
            ],
    }


def _pair_binding_digest(
    binding: Mapping[str, Any],
) -> str:
    return sha256_json(
        dict(binding)
    )


def _pair_presentation(
    bindings: Sequence[
        Mapping[str, Any]
    ],
    comparison_digest: str,
    *,
    producer_identity_blinded: bool,
) -> list[dict[str, Any]]:
    ordered = sorted(
        bindings,
        key=lambda binding:
            sha256_json({
                "comparison_digest":
                    comparison_digest,
                "binding_digest":
                    _pair_binding_digest(
                        binding
                    ),
            }),
    )
    result = []
    for label, binding in zip(
        ("A", "B"),
        ordered,
    ):
        row = {
            "blind_label": label,
            "candidate_id":
                binding[
                    "candidate_id"
                ],
            "render_sha256":
                binding[
                    "render_sha256"
                ],
            "render_size":
                binding[
                    "render_size"
                ],
            "attachment_identity":
                binding[
                    "attachment_identity"
                ],
            "source_sha256":
                binding[
                    "source_sha256"
                ],
            "review_bundle_digest":
                binding[
                    "review_bundle_digest"
                ],
            "producer_identity":
                (
                    None
                    if producer_identity_blinded
                    else binding[
                        "media_producer_sha"
                    ]
                ),
        }
        result.append(row)
    return result


def build_web_video_pairwise_input(
    *,
    candidate_inputs: Sequence[
        Mapping[str, Any]
    ],
    comparison_goal: str,
    producer_identity_blinded: bool = True,
) -> dict[str, Any]:
    if len(candidate_inputs) != 2:
        raise WebVideoCriticError(
            "pairwise input requires exactly two candidates"
        )
    bindings = [
        _pair_binding(item)
        for item
        in candidate_inputs
    ]
    if (
        bindings[0][
            "render_sha256"
        ]
        == bindings[1][
            "render_sha256"
        ]
    ):
        raise WebVideoCriticLineageError(
            "pairwise candidate MP4 bytes must be distinct"
        )
    if (
        bindings[0][
            "source_sha256"
        ]
        != bindings[1][
            "source_sha256"
        ]
        or bindings[0][
            "review_bundle_digest"
        ]
        != bindings[1][
            "review_bundle_digest"
        ]
    ):
        raise WebVideoCriticLineageError(
            "pairwise candidates must share exact source/review bundle lineage"
        )
    sorted_digests = sorted(
        _pair_binding_digest(
            binding
        )
        for binding
        in bindings
    )
    comparison_digest = (
        sha256_json({
            "candidate_binding_digests":
                sorted_digests,
            "comparison_goal":
                _nonempty(
                    comparison_goal,
                    "comparison_goal",
                ),
            "producer_identity_blinded":
                bool(
                    producer_identity_blinded
                ),
        })
    )
    payload = {
        "contract_version":
            WEB_VIDEO_PAIRWISE_VERSION,
        "message_type": "input",
        "comparison_id": (
            "gvwp1:"
            + comparison_digest
        ),
        "comparison_digest":
            comparison_digest,
        "input_digest": "",
        "integration_state":
            WEB_VIDEO_INTEGRATION_STATE,
        "comparison_goal":
            comparison_goal,
        "producer_identity_blinded":
            bool(
                producer_identity_blinded
            ),
        "candidate_bindings":
            sorted(
                bindings,
                key=lambda item:
                    item[
                        "candidate_id"
                    ],
            ),
        "review_presentation":
            _pair_presentation(
                bindings,
                comparison_digest,
                producer_identity_blinded=
                    bool(
                        producer_identity_blinded
                    ),
            ),
        "evidence_boundary": {
            "model_pairwise_only": True,
            "human_ground_truth": False,
            "human_label": False,
            "live_platform_evidence": False,
            "human_parity_gate_eligible": False,
        },
    }
    material = dict(payload)
    material[
        "input_digest"
    ] = ""
    payload["input_digest"] = (
        sha256_json(material)
    )
    return parse_web_video_pairwise_input(
        payload
    )


def parse_web_video_pairwise_input(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "contract_version",
        "message_type",
        "comparison_id",
        "comparison_digest",
        "input_digest",
        "integration_state",
        "comparison_goal",
        "producer_identity_blinded",
        "candidate_bindings",
        "review_presentation",
        "evidence_boundary",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != required
    ):
        raise WebVideoCriticError(
            "pairwise input fields invalid"
        )
    if (
        payload[
            "contract_version"
        ]
        != WEB_VIDEO_PAIRWISE_VERSION
        or payload[
            "message_type"
        ]
        != "input"
        or payload[
            "integration_state"
        ]
        != WEB_VIDEO_INTEGRATION_STATE
    ):
        raise WebVideoCriticBoundaryError(
            "pairwise contract/integration state invalid"
        )
    _nonempty(
        payload[
            "comparison_goal"
        ],
        "comparison_goal",
    )
    if not isinstance(
        payload[
            "producer_identity_blinded"
        ],
        bool,
    ):
        raise WebVideoCriticError(
            "producer_identity_blinded must be boolean"
        )
    bindings = payload[
        "candidate_bindings"
    ]
    if (
        not isinstance(bindings, list)
        or len(bindings) != 2
    ):
        raise WebVideoCriticError(
            "pairwise candidate_bindings must contain two rows"
        )
    candidate_ids = set()
    render_hashes = set()
    sources = set()
    bundles = set()
    transports = set()
    for index, binding in enumerate(
        bindings
    ):
        if (
            not isinstance(
                binding,
                Mapping,
            )
            or set(binding)
            != {
                "candidate_id",
                "source_sha256",
                "media_producer_sha",
                "render_sha256",
                "render_size",
                "attachment_identity",
                "review_bundle_digest",
                "attachment_transport_digest",
                "critic_input_digest",
            }
        ):
            raise WebVideoCriticError(
                "pairwise candidate binding fields invalid"
            )
        candidate_ids.add(
            _nonempty(
                binding[
                    "candidate_id"
                ],
                f"candidate_bindings[{index}].candidate_id",
            )
        )
        render_hashes.add(
            _sha256(
                binding[
                    "render_sha256"
                ],
                f"candidate_bindings[{index}].render_sha256",
            )
        )
        sources.add(
            _sha256(
                binding[
                    "source_sha256"
                ],
                f"candidate_bindings[{index}].source_sha256",
            )
        )
        bundles.add(
            _sha256(
                binding[
                    "review_bundle_digest"
                ],
                f"candidate_bindings[{index}].review_bundle_digest",
            )
        )
        transports.add(
            _sha256(
                binding[
                    "attachment_transport_digest"
                ],
                f"candidate_bindings[{index}].attachment_transport_digest",
            )
        )
        _sha1(
            binding[
                "media_producer_sha"
            ],
            f"candidate_bindings[{index}].media_producer_sha",
        )
        _positive_int(
            binding[
                "render_size"
            ],
            f"candidate_bindings[{index}].render_size",
        )
        _nonempty(
            binding[
                "attachment_identity"
            ],
            f"candidate_bindings[{index}].attachment_identity",
        )
        _sha256(
            binding[
                "attachment_transport_digest"
            ],
            f"candidate_bindings[{index}].attachment_transport_digest",
        )
        _sha256(
            binding[
                "critic_input_digest"
            ],
            f"candidate_bindings[{index}].critic_input_digest",
        )
    if (
        len(candidate_ids) != 2
        or len(render_hashes) != 2
        or len(sources) != 1
        or len(bundles) != 1
        or len(transports) != 1
        or next(iter(transports))
        != build_bridge_r25_transport_binding()[
            "binding_digest"
        ]
    ):
        raise WebVideoCriticLineageError(
            "pairwise candidate lineage must be two distinct renders of one source/review bundle and exact Bridge R25 transport"
        )

    sorted_digests = sorted(
        _pair_binding_digest(
            binding
        )
        for binding
        in bindings
    )
    expected_comparison_digest = (
        sha256_json({
            "candidate_binding_digests":
                sorted_digests,
            "comparison_goal":
                payload[
                    "comparison_goal"
                ],
            "producer_identity_blinded":
                payload[
                    "producer_identity_blinded"
                ],
        })
    )
    if (
        payload[
            "comparison_digest"
        ]
        != expected_comparison_digest
        or payload[
            "comparison_id"
        ]
        != (
            "gvwp1:"
            + expected_comparison_digest
        )
    ):
        raise WebVideoCriticLineageError(
            "pairwise comparison identity mismatch"
        )
    expected_presentation = (
        _pair_presentation(
            bindings,
            expected_comparison_digest,
            producer_identity_blinded=
                payload[
                    "producer_identity_blinded"
                ],
        )
    )
    if (
        payload[
            "review_presentation"
        ]
        != expected_presentation
    ):
        raise WebVideoCriticLineageError(
            "pairwise presentation order or blinding mismatch"
        )
    if payload[
        "producer_identity_blinded"
    ] and any(
        row[
            "producer_identity"
        ] is not None
        for row in payload[
            "review_presentation"
        ]
    ):
        raise WebVideoCriticBoundaryError(
            "producer identity leaked into blinded presentation"
        )
    if payload[
        "evidence_boundary"
    ] != {
        "model_pairwise_only": True,
        "human_ground_truth": False,
        "human_label": False,
        "live_platform_evidence": False,
        "human_parity_gate_eligible": False,
    }:
        raise WebVideoCriticBoundaryError(
            "pairwise evidence boundary invalid"
        )
    _sha256(
        payload["input_digest"],
        "pairwise input_digest",
    )
    material = dict(payload)
    material[
        "input_digest"
    ] = ""
    if (
        sha256_json(material)
        != payload[
            "input_digest"
        ]
    ):
        raise WebVideoCriticLineageError(
            "pairwise input digest mismatch"
        )
    return json.loads(
        canonical_json(
            dict(payload)
        )
    )


def build_web_video_pairwise_output(
    *,
    pairwise_input: Mapping[str, Any],
    selection: str,
    rationale: str,
    evidence_observation_ids: Sequence[str],
    confidence: float,
    uncertainty: str,
) -> dict[str, Any]:
    parsed = (
        parse_web_video_pairwise_input(
            pairwise_input
        )
    )
    if selection not in PAIRWISE_SELECTIONS:
        raise WebVideoCriticError(
            "pairwise selection invalid"
        )
    label_to_candidate = {
        row["blind_label"]:
            row["candidate_id"]
        for row in parsed[
            "review_presentation"
        ]
    }
    mapped = (
        label_to_candidate[
            selection
        ]
        if selection in {
            "A",
            "B",
        }
        else None
    )
    output = {
        "contract_version":
            WEB_VIDEO_PAIRWISE_VERSION,
        "message_type": "output",
        "output_id": "",
        "output_digest": "",
        "comparison_id":
            parsed[
                "comparison_id"
            ],
        "comparison_digest":
            parsed[
                "comparison_digest"
            ],
        "input_digest":
            parsed[
                "input_digest"
            ],
        "selection": selection,
        "mapped_candidate_id":
            mapped,
        "rationale":
            _nonempty(
                rationale,
                "rationale",
            ),
        "evidence_observation_ids":
            list(
                evidence_observation_ids
            ),
        "confidence":
            confidence,
        "uncertainty":
            _nonempty(
                uncertainty,
                "uncertainty",
            ),
        "producer_identity_blinded":
            parsed[
                "producer_identity_blinded"
            ],
        "human_ground_truth":
            False,
        "human_label": False,
        "live_platform_evidence":
            False,
        "human_parity_gate_eligible":
            False,
        "authority": {
            "advisory_only": True,
            "publish_authorized": False,
            "creator_mutation": False,
            "media_mutation": False,
        },
    }
    output["output_id"] = (
        "gvwpo1:"
        + sha256_json({
            "comparison_digest":
                output[
                    "comparison_digest"
                ],
            "selection":
                selection,
            "mapped_candidate_id":
                mapped,
        })
    )
    material = dict(output)
    material[
        "output_digest"
    ] = ""
    output["output_digest"] = (
        sha256_json(material)
    )
    return parse_web_video_pairwise_output(
        output,
        pairwise_input=parsed,
    )


def parse_web_video_pairwise_output(
    payload: Mapping[str, Any],
    *,
    pairwise_input: Mapping[str, Any],
) -> dict[str, Any]:
    parsed = (
        parse_web_video_pairwise_input(
            pairwise_input
        )
    )
    required = {
        "contract_version",
        "message_type",
        "output_id",
        "output_digest",
        "comparison_id",
        "comparison_digest",
        "input_digest",
        "selection",
        "mapped_candidate_id",
        "rationale",
        "evidence_observation_ids",
        "confidence",
        "uncertainty",
        "producer_identity_blinded",
        "human_ground_truth",
        "human_label",
        "live_platform_evidence",
        "human_parity_gate_eligible",
        "authority",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != required
    ):
        raise WebVideoCriticError(
            "pairwise output fields invalid"
        )
    if (
        payload[
            "contract_version"
        ]
        != WEB_VIDEO_PAIRWISE_VERSION
        or payload[
            "message_type"
        ]
        != "output"
        or payload[
            "comparison_id"
        ]
        != parsed[
            "comparison_id"
        ]
        or payload[
            "comparison_digest"
        ]
        != parsed[
            "comparison_digest"
        ]
        or payload[
            "input_digest"
        ]
        != parsed[
            "input_digest"
        ]
    ):
        raise WebVideoCriticLineageError(
            "pairwise output lineage mismatch"
        )
    selection = payload[
        "selection"
    ]
    if selection not in PAIRWISE_SELECTIONS:
        raise WebVideoCriticError(
            "pairwise selection invalid"
        )
    label_to_candidate = {
        row["blind_label"]:
            row["candidate_id"]
        for row in parsed[
            "review_presentation"
        ]
    }
    expected_mapped = (
        label_to_candidate[
            selection
        ]
        if selection in {
            "A",
            "B",
        }
        else None
    )
    if (
        payload[
            "mapped_candidate_id"
        ]
        != expected_mapped
    ):
        raise WebVideoCriticLineageError(
            "pairwise result mapping mismatch"
        )
    _nonempty(
        payload["rationale"],
        "pairwise rationale",
    )
    _string_array(
        payload[
            "evidence_observation_ids"
        ],
        "evidence_observation_ids",
        allow_empty=selection
        in {
            "tie",
            "insufficient_evidence",
        },
    )
    _confidence(
        payload[
            "confidence"
        ],
        "pairwise confidence",
    )
    _nonempty(
        payload[
            "uncertainty"
        ],
        "pairwise uncertainty",
    )
    if (
        payload[
            "producer_identity_blinded"
        ]
        != parsed[
            "producer_identity_blinded"
        ]
        or payload[
            "human_ground_truth"
        ] is not False
        or payload[
            "human_label"
        ] is not False
        or payload[
            "live_platform_evidence"
        ] is not False
        or payload[
            "human_parity_gate_eligible"
        ] is not False
    ):
        raise WebVideoCriticBoundaryError(
            "pairwise model output cannot become human/live evidence"
        )
    if payload["authority"] != {
        "advisory_only": True,
        "publish_authorized": False,
        "creator_mutation": False,
        "media_mutation": False,
    }:
        raise WebVideoCriticBoundaryError(
            "pairwise authority invalid"
        )
    expected_output_id = (
        "gvwpo1:"
        + sha256_json({
            "comparison_digest":
                payload[
                    "comparison_digest"
                ],
            "selection":
                selection,
            "mapped_candidate_id":
                expected_mapped,
        })
    )
    if (
        payload["output_id"]
        != expected_output_id
    ):
        raise WebVideoCriticLineageError(
            "pairwise output identity mismatch"
        )
    _sha256(
        payload["output_digest"],
        "pairwise output_digest",
    )
    material = dict(payload)
    material[
        "output_digest"
    ] = ""
    if (
        sha256_json(material)
        != payload[
            "output_digest"
        ]
    ):
        raise WebVideoCriticLineageError(
            "pairwise output digest mismatch"
        )
    return json.loads(
        canonical_json(
            dict(payload)
        )
    )
