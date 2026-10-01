from __future__ import annotations

import json
import math
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Mapping, Protocol, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .event_stream import parse_timestamp


VISUAL_CRITIC_VERSION = "growth.visual_critic.r15.v1"
PAIRWISE_CRITIC_VERSION = "growth.visual_critic_pairwise.r15.v1"
HUMAN_LABEL_VERSION = "growth.visual_critic_human_pairwise_label.v1"
CALIBRATION_VERSION = "growth.visual_critic_calibration.r15.v1"
VLM_OBSERVATION_VERSION = "growth.visual_critic_vlm_observation.v1"
HUMAN_LEVEL_UNPROVEN = "HUMAN_LEVEL_UNPROVEN"
HUMAN_BENCHMARK_CORPUS_READY = "HUMAN_BENCHMARK_CORPUS_READY"

CRITIC_DIMENSIONS = (
    "hook_clarity_first_1_3s",
    "pacing_coherence",
    "semantic_cut_correctness",
    "subject_framing_crop_quality",
    "broll_relevance",
    "caption_readability_emphasis_relevance",
    "visual_continuity",
    "motion_zoom_appropriateness",
    "audio_voice_music_balance",
    "payoff_cta_loop_coherence",
    "awkward_dead_moments",
)

_ALLOWED_NOTE_SOURCES = frozenset({
    "objective_hard_failure",
    "structural_heuristic",
    "vlm_opinion",
})
_FORBIDDEN_SECRET_KEYS = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "credential",
    "credentials",
})


class VisualCriticError(ValueError):
    pass


class VisualCriticProviderError(VisualCriticError):
    pass


@dataclass(frozen=True)
class StructuralCriticPolicy:
    min_pairwise_dimensions: int = 6
    tie_margin: float = 0.04
    min_dimension_confidence: float = 0.35
    min_human_labels: int = 30

    def __post_init__(self) -> None:
        if (
            isinstance(self.min_pairwise_dimensions, bool)
            or not isinstance(self.min_pairwise_dimensions, int)
            or self.min_pairwise_dimensions < 1
        ):
            raise VisualCriticError(
                "min_pairwise_dimensions must be positive integer"
            )
        if (
            isinstance(self.min_human_labels, bool)
            or not isinstance(self.min_human_labels, int)
            or self.min_human_labels < 1
        ):
            raise VisualCriticError(
                "min_human_labels must be positive integer"
            )
        for value, field in (
            (self.tie_margin, "tie_margin"),
            (
                self.min_dimension_confidence,
                "min_dimension_confidence",
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= float(value) <= 1
            ):
                raise VisualCriticError(
                    f"{field} must be in [0,1]"
                )


class VLMVisualCriticAdapter(Protocol):
    provider_name: str
    model_name: str

    def observe(
        self,
        critic_input: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]:
        ...


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise VisualCriticError(
            f"{field} must be a non-empty string"
        )
    return value


def _digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise VisualCriticError(
            f"{field} must be lowercase SHA-256 hex"
        )
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise VisualCriticError(f"{field} must be finite numeric")
    parsed = float(value)
    if minimum is not None and parsed < minimum:
        raise VisualCriticError(f"{field} below minimum")
    if maximum is not None and parsed > maximum:
        raise VisualCriticError(f"{field} above maximum")
    return parsed


def _optional_number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if value is None:
        return None
    return _number(
        value,
        field,
        minimum=minimum,
        maximum=maximum,
    )


def _secret_free(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN_SECRET_KEYS:
                raise VisualCriticProviderError(
                    f"credential-like field forbidden at {path}.{key}"
                )
            _secret_free(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _secret_free(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if value.startswith("Bearer ") or value.startswith("Basic "):
            raise VisualCriticProviderError(
                f"credential-like value forbidden at {path}"
            )


def _time(value: Any, field: str, duration: float) -> float:
    return _number(
        value,
        field,
        minimum=0.0,
        maximum=duration,
    )


def _validate_bbox(raw: Any, field: str) -> dict[str, float] | None:
    if raw is None:
        return None
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"x", "y", "w", "h"}
    ):
        raise VisualCriticError(f"{field} bbox fields invalid")
    result = {
        key: _number(
            raw[key],
            f"{field}.{key}",
            minimum=0.0,
            maximum=1.0,
        )
        for key in ("x", "y", "w", "h")
    }
    if (
        result["w"] <= 0
        or result["h"] <= 0
        or result["x"] + result["w"] > 1.000001
        or result["y"] + result["h"] > 1.000001
    ):
        raise VisualCriticError(f"{field} bbox out of bounds")
    return result


def _validate_timeline_interval(
    raw: Mapping[str, Any],
    *,
    duration: float,
    fields: set[str],
    name: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise VisualCriticError(f"{name} fields invalid")
    start = _time(raw["start"], f"{name}.start", duration)
    end = _time(raw["end"], f"{name}.end", duration)
    if end <= start:
        raise VisualCriticError(f"{name} end must exceed start")
    result = dict(raw)
    result["start"] = start
    result["end"] = end
    return result


def parse_visual_critic_input(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "input_version",
        "input_digest",
        "candidate_id",
        "render",
        "frames",
        "contact_sheet",
        "audio_probe",
        "captions",
        "overlays",
        "cuts",
        "source_semantic_timeline",
        "media_qa",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise VisualCriticError(
            "visual critic input fields must match r15 exactly"
        )
    if payload["input_version"] != VISUAL_CRITIC_VERSION:
        raise VisualCriticError(
            "unsupported visual critic input version"
        )
    _nonempty(payload["candidate_id"], "candidate_id")
    render = payload["render"]
    if (
        not isinstance(render, Mapping)
        or set(render)
        != {
            "artifact_id",
            "artifact_sha256",
            "duration_seconds",
            "width",
            "height",
            "fps",
        }
    ):
        raise VisualCriticError("render fields invalid")
    _nonempty(render["artifact_id"], "render.artifact_id")
    _digest(
        render["artifact_sha256"],
        "render.artifact_sha256",
    )
    duration = _number(
        render["duration_seconds"],
        "render.duration_seconds",
        minimum=0.1,
        maximum=600.0,
    )
    for field in ("width", "height"):
        value = render[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1
        ):
            raise VisualCriticError(
                f"render.{field} must be positive integer"
            )
    _number(render["fps"], "render.fps", minimum=1, maximum=240)

    frames = payload["frames"]
    if not isinstance(frames, list) or not frames:
        raise VisualCriticError("frames must be a non-empty array")
    parsed_frames: list[dict[str, Any]] = []
    previous_time = -1.0
    for index, raw in enumerate(frames):
        if (
            not isinstance(raw, Mapping)
            or set(raw)
            != {
                "time",
                "frame_sha256",
                "shot_id",
                "subject_bbox",
                "subject_visible",
                "frame_role",
                "semantic_match",
                "motion_score",
                "zoom_factor",
                "continuity_break",
            }
        ):
            raise VisualCriticError("frame fields invalid")
        time = _time(
            raw["time"],
            f"frames[{index}].time",
            duration,
        )
        if time < previous_time:
            raise VisualCriticError(
                "frames must be time ordered"
            )
        previous_time = time
        role = raw["frame_role"]
        if role not in {"primary", "broll", "graphic"}:
            raise VisualCriticError("unsupported frame_role")
        semantic_match = _optional_number(
            raw["semantic_match"],
            f"frames[{index}].semantic_match",
            minimum=0,
            maximum=1,
        )
        parsed_frames.append({
            "time": time,
            "frame_sha256": _digest(
                raw["frame_sha256"],
                f"frames[{index}].frame_sha256",
            ),
            "shot_id": _nonempty(
                raw["shot_id"],
                f"frames[{index}].shot_id",
            ),
            "subject_bbox": _validate_bbox(
                raw["subject_bbox"],
                f"frames[{index}].subject_bbox",
            ),
            "subject_visible": bool(raw["subject_visible"]),
            "frame_role": role,
            "semantic_match": semantic_match,
            "motion_score": _number(
                raw["motion_score"],
                f"frames[{index}].motion_score",
                minimum=0,
                maximum=1,
            ),
            "zoom_factor": _number(
                raw["zoom_factor"],
                f"frames[{index}].zoom_factor",
                minimum=0.5,
                maximum=3.0,
            ),
            "continuity_break": bool(
                raw["continuity_break"]
            ),
        })

    contact = payload["contact_sheet"]
    if (
        not isinstance(contact, Mapping)
        or set(contact) != {"sha256", "frame_count"}
    ):
        raise VisualCriticError(
            "contact_sheet fields invalid"
        )
    _digest(contact["sha256"], "contact_sheet.sha256")
    if contact["frame_count"] != len(parsed_frames):
        raise VisualCriticError(
            "contact sheet frame count mismatch"
        )

    audio = payload["audio_probe"]
    if (
        not isinstance(audio, Mapping)
        or set(audio)
        != {
            "integrated_lufs",
            "true_peak_dbfs",
            "voice_lufs",
            "music_lufs",
            "speech_coverage",
            "silence_intervals",
        }
    ):
        raise VisualCriticError("audio_probe fields invalid")
    parsed_audio = {
        "integrated_lufs": _optional_number(
            audio["integrated_lufs"],
            "audio_probe.integrated_lufs",
        ),
        "true_peak_dbfs": _optional_number(
            audio["true_peak_dbfs"],
            "audio_probe.true_peak_dbfs",
        ),
        "voice_lufs": _optional_number(
            audio["voice_lufs"],
            "audio_probe.voice_lufs",
        ),
        "music_lufs": _optional_number(
            audio["music_lufs"],
            "audio_probe.music_lufs",
        ),
        "speech_coverage": _optional_number(
            audio["speech_coverage"],
            "audio_probe.speech_coverage",
            minimum=0,
            maximum=1,
        ),
        "silence_intervals": [],
    }
    for index, raw in enumerate(audio["silence_intervals"]):
        parsed_audio["silence_intervals"].append(
            _validate_timeline_interval(
                raw,
                duration=duration,
                fields={"start", "end"},
                name=f"silence_intervals[{index}]",
            )
        )

    captions = payload["captions"]
    if not isinstance(captions, list):
        raise VisualCriticError("captions must be array")
    parsed_captions: list[dict[str, Any]] = []
    for index, raw in enumerate(captions):
        item = _validate_timeline_interval(
            raw,
            duration=duration,
            fields={
                "start",
                "end",
                "text",
                "chars_per_second",
                "contrast_ratio",
                "collision",
                "emphasis_relevant",
            },
            name=f"captions[{index}]",
        )
        _nonempty(item["text"], f"captions[{index}].text")
        item["chars_per_second"] = _number(
            item["chars_per_second"],
            f"captions[{index}].chars_per_second",
            minimum=0,
            maximum=100,
        )
        item["contrast_ratio"] = _number(
            item["contrast_ratio"],
            f"captions[{index}].contrast_ratio",
            minimum=0,
            maximum=30,
        )
        item["collision"] = bool(item["collision"])
        if item["emphasis_relevant"] not in {
            True,
            False,
            None,
        }:
            raise VisualCriticError(
                "caption emphasis_relevant must be boolean or null"
            )
        parsed_captions.append(item)

    overlays = payload["overlays"]
    if not isinstance(overlays, list):
        raise VisualCriticError("overlays must be array")
    parsed_overlays = []
    for index, raw in enumerate(overlays):
        item = _validate_timeline_interval(
            raw,
            duration=duration,
            fields={
                "start",
                "end",
                "kind",
                "text",
                "semantic_relevance",
                "collision",
            },
            name=f"overlays[{index}]",
        )
        _nonempty(item["kind"], f"overlays[{index}].kind")
        if item["text"] is not None:
            _nonempty(
                item["text"],
                f"overlays[{index}].text",
            )
        item["semantic_relevance"] = _optional_number(
            item["semantic_relevance"],
            f"overlays[{index}].semantic_relevance",
            minimum=0,
            maximum=1,
        )
        item["collision"] = bool(item["collision"])
        parsed_overlays.append(item)

    cuts = payload["cuts"]
    if not isinstance(cuts, list):
        raise VisualCriticError("cuts must be array")
    parsed_cuts = []
    previous_cut = -1.0
    for index, raw in enumerate(cuts):
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {
                "time",
                "source_boundary_distance_seconds",
                "semantic_safe",
            }
        ):
            raise VisualCriticError("cut fields invalid")
        time = _time(
            raw["time"],
            f"cuts[{index}].time",
            duration,
        )
        if time < previous_cut:
            raise VisualCriticError("cuts must be time ordered")
        previous_cut = time
        parsed_cuts.append({
            "time": time,
            "source_boundary_distance_seconds":
                _optional_number(
                    raw["source_boundary_distance_seconds"],
                    "source_boundary_distance_seconds",
                    minimum=0,
                    maximum=60,
                ),
            "semantic_safe": raw["semantic_safe"],
        })
        if raw["semantic_safe"] not in {True, False, None}:
            raise VisualCriticError(
                "cut semantic_safe must be boolean or null"
            )

    semantic = payload["source_semantic_timeline"]
    if semantic is not None:
        if not isinstance(semantic, list):
            raise VisualCriticError(
                "source_semantic_timeline must be array or null"
            )
        parsed_semantic = []
        for index, raw in enumerate(semantic):
            item = _validate_timeline_interval(
                raw,
                duration=duration,
                fields={
                    "start",
                    "end",
                    "label",
                    "importance",
                    "source_ref",
                },
                name=f"semantic[{index}]",
            )
            _nonempty(item["label"], f"semantic[{index}].label")
            item["importance"] = _number(
                item["importance"],
                f"semantic[{index}].importance",
                minimum=0,
                maximum=1,
            )
            _nonempty(
                item["source_ref"],
                f"semantic[{index}].source_ref",
            )
            parsed_semantic.append(item)
    else:
        parsed_semantic = None

    media_qa = payload["media_qa"]
    if (
        not isinstance(media_qa, Mapping)
        or set(media_qa)
        != {
            "contract_version",
            "artifact_sha256",
            "technical_pass",
            "creative_pass",
            "hard_failures",
            "warnings",
        }
    ):
        raise VisualCriticError("media_qa fields invalid")
    _nonempty(
        media_qa["contract_version"],
        "media_qa.contract_version",
    )
    if (
        _digest(
            media_qa["artifact_sha256"],
            "media_qa.artifact_sha256",
        )
        != render["artifact_sha256"]
    ):
        raise VisualCriticError(
            "media QA artifact digest mismatch"
        )
    if not isinstance(media_qa["technical_pass"], bool):
        raise VisualCriticError(
            "media_qa.technical_pass must be boolean"
        )
    if media_qa["creative_pass"] not in {True, False, None}:
        raise VisualCriticError(
            "media_qa.creative_pass must be boolean or null"
        )
    for field in ("hard_failures", "warnings"):
        if (
            not isinstance(media_qa[field], list)
            or any(
                not isinstance(item, str) or not item
                for item in media_qa[field]
            )
        ):
            raise VisualCriticError(
                f"media_qa.{field} must be string array"
            )

    rebuilt = {
        "input_version": VISUAL_CRITIC_VERSION,
        "candidate_id": payload["candidate_id"],
        "render": {
            "artifact_id": render["artifact_id"],
            "artifact_sha256": render["artifact_sha256"],
            "duration_seconds": duration,
            "width": render["width"],
            "height": render["height"],
            "fps": float(render["fps"]),
        },
        "frames": parsed_frames,
        "contact_sheet": {
            "sha256": contact["sha256"],
            "frame_count": contact["frame_count"],
        },
        "audio_probe": parsed_audio,
        "captions": parsed_captions,
        "overlays": parsed_overlays,
        "cuts": parsed_cuts,
        "source_semantic_timeline": parsed_semantic,
        "media_qa": {
            "contract_version": media_qa["contract_version"],
            "artifact_sha256": media_qa["artifact_sha256"],
            "technical_pass": media_qa["technical_pass"],
            "creative_pass": media_qa["creative_pass"],
            "hard_failures": list(media_qa["hard_failures"]),
            "warnings": list(media_qa["warnings"]),
        },
    }
    rebuilt["input_digest"] = sha256_json(rebuilt)
    if rebuilt != payload:
        raise VisualCriticError(
            "visual critic input digest or normalization mismatch"
        )
    return json.loads(canonical_json(rebuilt))


def build_visual_critic_input(
    *,
    candidate_id: str,
    render: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
    contact_sheet: Mapping[str, Any],
    audio_probe: Mapping[str, Any],
    captions: Sequence[Mapping[str, Any]],
    overlays: Sequence[Mapping[str, Any]],
    cuts: Sequence[Mapping[str, Any]],
    source_semantic_timeline: Sequence[Mapping[str, Any]] | None,
    media_qa: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "input_version": VISUAL_CRITIC_VERSION,
        "input_digest": "",
        "candidate_id": candidate_id,
        "render": dict(render),
        "frames": [dict(item) for item in frames],
        "contact_sheet": dict(contact_sheet),
        "audio_probe": dict(audio_probe),
        "captions": [dict(item) for item in captions],
        "overlays": [dict(item) for item in overlays],
        "cuts": [dict(item) for item in cuts],
        "source_semantic_timeline": (
            None
            if source_semantic_timeline is None
            else [dict(item) for item in source_semantic_timeline]
        ),
        "media_qa": dict(media_qa),
    }
    normalized = json.loads(canonical_json(payload))
    normalized["input_digest"] = sha256_json({
        key: value
        for key, value in normalized.items()
        if key != "input_digest"
    })
    return parse_visual_critic_input(normalized)


def parse_vlm_observation(
    payload: Mapping[str, Any],
    *,
    input_digest: str,
) -> dict[str, Any]:
    expected = {
        "observation_version",
        "observation_id",
        "input_digest",
        "provider_name",
        "model_name",
        "dimension",
        "time",
        "judgment",
        "confidence",
        "note",
        "request_digest",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise VisualCriticProviderError(
            "VLM observation fields invalid"
        )
    _secret_free(payload)
    if payload["observation_version"] != VLM_OBSERVATION_VERSION:
        raise VisualCriticProviderError(
            "unsupported VLM observation version"
        )
    if payload["input_digest"] != input_digest:
        raise VisualCriticProviderError(
            "VLM observation input digest mismatch"
        )
    _nonempty(payload["provider_name"], "provider_name")
    _nonempty(payload["model_name"], "model_name")
    if payload["dimension"] not in CRITIC_DIMENSIONS:
        raise VisualCriticProviderError(
            "unsupported VLM observation dimension"
        )
    if payload["time"] is not None:
        _number(payload["time"], "VLM observation time", minimum=0)
    if payload["judgment"] not in {
        "positive",
        "negative",
        "neutral",
        "uncertain",
    }:
        raise VisualCriticProviderError(
            "unsupported VLM judgment"
        )
    _number(
        payload["confidence"],
        "VLM confidence",
        minimum=0,
        maximum=1,
    )
    _nonempty(payload["note"], "VLM note")
    _digest(payload["request_digest"], "VLM request_digest")
    identity = {
        "input_digest": input_digest,
        "provider_name": payload["provider_name"],
        "model_name": payload["model_name"],
        "dimension": payload["dimension"],
        "time": payload["time"],
        "judgment": payload["judgment"],
        "confidence": payload["confidence"],
        "note": payload["note"],
        "request_digest": payload["request_digest"],
    }
    expected_id = "gvo1:" + sha256_json(identity)
    if payload["observation_id"] != expected_id:
        raise VisualCriticProviderError(
            "VLM observation identity mismatch"
        )
    return json.loads(canonical_json(dict(payload)))


def build_vlm_observation(
    *,
    input_digest: str,
    provider_name: str,
    model_name: str,
    dimension: str,
    time: float | None,
    judgment: str,
    confidence: float,
    note: str,
    request_digest: str,
) -> dict[str, Any]:
    material = {
        "observation_version": VLM_OBSERVATION_VERSION,
        "input_digest": input_digest,
        "provider_name": provider_name,
        "model_name": model_name,
        "dimension": dimension,
        "time": time,
        "judgment": judgment,
        "confidence": confidence,
        "note": note,
        "request_digest": request_digest,
    }
    material["observation_id"] = "gvo1:" + sha256_json({
        key: value
        for key, value in material.items()
        if key != "observation_version"
    })
    return parse_vlm_observation(
        material,
        input_digest=input_digest,
    )


def _score(
    value: float | None,
    confidence: float,
    evidence: Sequence[str],
    limitation: str | None = None,
) -> dict[str, Any]:
    if value is not None:
        value = round(max(0.0, min(1.0, float(value))), 6)
    return {
        "score": value,
        "confidence": round(
            max(0.0, min(1.0, float(confidence))),
            6,
        ),
        "evidence": list(evidence),
        "limitation": limitation,
    }


def _note(
    *,
    time: float | None,
    dimension: str,
    severity: str,
    source: str,
    message: str,
) -> dict[str, Any]:
    if dimension not in CRITIC_DIMENSIONS:
        raise VisualCriticError("note dimension invalid")
    if severity not in {"info", "warning", "hard_failure"}:
        raise VisualCriticError("note severity invalid")
    if source not in _ALLOWED_NOTE_SOURCES:
        raise VisualCriticError("note source invalid")
    return {
        "time": None if time is None else round(float(time), 3),
        "dimension": dimension,
        "severity": severity,
        "source": source,
        "message": message,
    }


def _structural_evaluation(
    critic_input: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    data = parse_visual_critic_input(critic_input)
    duration = data["render"]["duration_seconds"]
    frames = data["frames"]
    captions = data["captions"]
    overlays = data["overlays"]
    cuts = data["cuts"]
    semantic = data["source_semantic_timeline"]
    audio = data["audio_probe"]
    notes: list[dict[str, Any]] = []
    hard_failures: list[dict[str, Any]] = []

    if data["media_qa"]["technical_pass"] is False:
        for failure in data["media_qa"]["hard_failures"]:
            hard_failures.append({
                "code": "MEDIA_TECHNICAL_QA_FAILURE",
                "time": None,
                "message": failure,
                "source": "media_qa",
            })
    for caption in captions:
        if caption["collision"]:
            hard_failures.append({
                "code": "CAPTION_COLLISION",
                "time": round(caption["start"], 3),
                "message": "caption collision reported by source timeline",
                "source": "caption_timeline",
            })
            notes.append(_note(
                time=caption["start"],
                dimension="caption_readability_emphasis_relevance",
                severity="hard_failure",
                source="objective_hard_failure",
                message="Caption collision overlaps another protected region.",
            ))
    for overlay in overlays:
        if overlay["collision"]:
            hard_failures.append({
                "code": "OVERLAY_COLLISION",
                "time": round(overlay["start"], 3),
                "message": "overlay collision reported by source timeline",
                "source": "overlay_timeline",
            })

    early_frames = [f for f in frames if f["time"] <= 3.0]
    early_subject = [
        1.0 if f["subject_visible"] else 0.0
        for f in early_frames
        if f["frame_role"] == "primary"
    ]
    early_caption = [
        c for c in captions if c["start"] < 3.0
    ]
    hook_evidence = len(early_frames) + len(early_caption)
    if hook_evidence:
        visible = fmean(early_subject) if early_subject else 0.5
        readable = (
            fmean(
                min(1.0, c["contrast_ratio"] / 4.5)
                * max(
                    0.0,
                    1.0 - max(
                        0.0,
                        c["chars_per_second"] - 18.0,
                    ) / 25.0,
                )
                for c in early_caption
            )
            if early_caption
            else 0.55
        )
        hook_score = 0.55 * visible + 0.45 * readable
        hook_conf = min(0.85, 0.35 + 0.08 * hook_evidence)
        if hook_score < 0.5:
            notes.append(_note(
                time=0.0,
                dimension="hook_clarity_first_1_3s",
                severity="warning",
                source="structural_heuristic",
                message="First 1-3s has weak subject/caption structural clarity.",
            ))
        hook = _score(
            hook_score,
            hook_conf,
            ["sampled_frames:first_3s", "captions:first_3s"],
        )
    else:
        hook = _score(
            None,
            0.0,
            [],
            "No sampled frame or caption evidence in first 3s.",
        )

    shot_times: dict[str, list[float]] = {}
    for frame in frames:
        shot_times.setdefault(frame["shot_id"], []).append(
            frame["time"]
        )
    shot_change_count = sum(
        1
        for left, right in zip(frames, frames[1:])
        if left["shot_id"] != right["shot_id"]
    )
    if len(frames) >= 3:
        cuts_per_10s = shot_change_count / max(duration, 0.1) * 10.0
        pace_penalty = min(
            1.0,
            abs(cuts_per_10s - 3.0) / 6.0,
        )
        pacing = _score(
            1.0 - pace_penalty,
            min(0.85, 0.4 + len(frames) * 0.04),
            ["sampled_frames:shot_ids"],
        )
        if cuts_per_10s < 0.8:
            notes.append(_note(
                time=frames[1]["time"],
                dimension="pacing_coherence",
                severity="warning",
                source="structural_heuristic",
                message="Low shot-change density may feel static for short-form pacing.",
            ))
    else:
        pacing = _score(
            None,
            0.1,
            ["sampled_frames:shot_ids"],
            "Too few sampled frames for pacing inference.",
        )

    safe_values = [
        cut["semantic_safe"]
        for cut in cuts
        if cut["semantic_safe"] is not None
    ]
    if safe_values:
        semantic_score = fmean(
            1.0 if value else 0.0
            for value in safe_values
        )
        semantic_conf = min(
            0.9,
            0.45 + 0.07 * len(safe_values),
        )
        for cut in cuts:
            if cut["semantic_safe"] is False:
                notes.append(_note(
                    time=cut["time"],
                    dimension="semantic_cut_correctness",
                    severity="warning",
                    source="structural_heuristic",
                    message="Cut is marked semantically unsafe against source timing.",
                ))
        semantic_cut = _score(
            semantic_score,
            semantic_conf,
            ["cuts:semantic_safe"],
        )
    elif semantic is None:
        semantic_cut = _score(
            None,
            0.0,
            [],
            "No source semantic timeline or semantic cut labels supplied.",
        )
    else:
        semantic_cut = _score(
            None,
            0.2,
            ["source_semantic_timeline"],
            "Semantic timeline exists but cuts have no semantic-safe labels.",
        )

    subject_frames = [
        frame for frame in frames
        if frame["frame_role"] == "primary"
        and frame["subject_visible"]
    ]
    if subject_frames:
        frame_scores = []
        for frame in subject_frames:
            bbox = frame["subject_bbox"]
            if bbox is None:
                continue
            center_x = bbox["x"] + bbox["w"] / 2.0
            center_y = bbox["y"] + bbox["h"] / 2.0
            centered = 1.0 - min(
                1.0,
                abs(center_x - 0.5) * 1.4
                + abs(center_y - 0.42) * 1.1,
            )
            size_ok = 1.0 - min(
                1.0,
                abs(bbox["h"] - 0.55) / 0.55,
            )
            score = 0.65 * centered + 0.35 * size_ok
            frame_scores.append(score)
            if score < 0.45:
                notes.append(_note(
                    time=frame["time"],
                    dimension="subject_framing_crop_quality",
                    severity="warning",
                    source="structural_heuristic",
                    message="Subject framing/crop is structurally off-center or poorly sized.",
                ))
        framing = (
            _score(
                fmean(frame_scores),
                min(0.9, 0.45 + 0.06 * len(frame_scores)),
                ["sampled_frames:subject_bbox"],
            )
            if frame_scores
            else _score(
                None,
                0.1,
                ["sampled_frames:subject_visible"],
                "Subject is visible but no subject bounding boxes were supplied.",
            )
        )
    else:
        framing = _score(
            None,
            0.0,
            [],
            "No visible primary subject samples supplied.",
        )

    broll_frames = [
        frame for frame in frames
        if frame["frame_role"] == "broll"
    ]
    broll_matches = [
        frame["semantic_match"]
        for frame in broll_frames
        if frame["semantic_match"] is not None
    ]
    if broll_matches:
        broll_score = fmean(broll_matches)
        for frame in broll_frames:
            if (
                frame["semantic_match"] is not None
                and frame["semantic_match"] < 0.4
            ):
                notes.append(_note(
                    time=frame["time"],
                    dimension="broll_relevance",
                    severity="warning",
                    source="structural_heuristic",
                    message="B-roll has low semantic match to supplied source context.",
                ))
        broll = _score(
            broll_score,
            min(0.85, 0.45 + 0.08 * len(broll_matches)),
            ["sampled_frames:broll_semantic_match"],
        )
    elif not broll_frames:
        broll = _score(
            None,
            0.25,
            ["sampled_frames:frame_role"],
            "No B-roll samples supplied; relevance is not applicable.",
        )
    else:
        broll = _score(
            None,
            0.15,
            ["sampled_frames:frame_role"],
            "B-roll exists but no semantic-match observations were supplied.",
        )

    if captions:
        caption_scores = []
        for caption in captions:
            speed = max(
                0.0,
                1.0 - max(
                    0.0,
                    caption["chars_per_second"] - 18.0,
                ) / 25.0,
            )
            contrast = min(
                1.0,
                caption["contrast_ratio"] / 4.5,
            )
            emphasis = (
                0.5
                if caption["emphasis_relevant"] is None
                else (
                    1.0
                    if caption["emphasis_relevant"]
                    else 0.0
                )
            )
            collision = 0.0 if caption["collision"] else 1.0
            score = (
                0.3 * speed
                + 0.3 * contrast
                + 0.2 * emphasis
                + 0.2 * collision
            )
            caption_scores.append(score)
            if caption["chars_per_second"] > 24:
                notes.append(_note(
                    time=caption["start"],
                    dimension="caption_readability_emphasis_relevance",
                    severity="warning",
                    source="structural_heuristic",
                    message="Caption reading speed is high for short-form viewing.",
                ))
        caption_quality = _score(
            fmean(caption_scores),
            min(0.9, 0.45 + 0.05 * len(captions)),
            [
                "captions:reading_speed",
                "captions:contrast",
                "captions:collision",
                "captions:emphasis",
            ],
        )
    else:
        caption_quality = _score(
            None,
            0.0,
            [],
            "No caption timeline supplied.",
        )

    continuity_breaks = [
        frame for frame in frames if frame["continuity_break"]
    ]
    continuity = _score(
        1.0 - min(1.0, len(continuity_breaks) / max(1, len(frames) / 3.0)),
        min(0.82, 0.4 + 0.03 * len(frames)),
        ["sampled_frames:continuity_break"],
    )
    for frame in continuity_breaks:
        notes.append(_note(
            time=frame["time"],
            dimension="visual_continuity",
            severity="warning",
            source="structural_heuristic",
            message="Sample is marked as a visual continuity break.",
        ))

    motion_scores = [frame["motion_score"] for frame in frames]
    zoom_scores = [abs(frame["zoom_factor"] - 1.0) for frame in frames]
    if motion_scores:
        excessive_motion = sum(
            1
            for motion, zoom in zip(motion_scores, zoom_scores)
            if motion > 0.9 or zoom > 0.6
        )
        motion_quality = _score(
            1.0 - excessive_motion / len(motion_scores),
            min(0.8, 0.4 + 0.03 * len(frames)),
            ["sampled_frames:motion_score", "sampled_frames:zoom_factor"],
        )
        for frame in frames:
            if (
                frame["motion_score"] > 0.9
                or abs(frame["zoom_factor"] - 1.0) > 0.6
            ):
                notes.append(_note(
                    time=frame["time"],
                    dimension="motion_zoom_appropriateness",
                    severity="warning",
                    source="structural_heuristic",
                    message="Motion/zoom intensity is structurally extreme.",
                ))
    else:
        motion_quality = _score(None, 0.0, [], "No motion samples.")

    audio_evidence = []
    audio_components = []
    if audio["true_peak_dbfs"] is not None:
        audio_evidence.append("audio_probe:true_peak_dbfs")
        peak = audio["true_peak_dbfs"]
        audio_components.append(
            1.0 if peak <= -1.0 else max(0.0, 1.0 - (peak + 1.0))
        )
        if peak > -0.2:
            hard_failures.append({
                "code": "AUDIO_TRUE_PEAK_TOO_HIGH",
                "time": None,
                "message": "true peak exceeds structural clipping threshold",
                "source": "audio_probe",
            })
    if (
        audio["voice_lufs"] is not None
        and audio["music_lufs"] is not None
    ):
        audio_evidence.extend([
            "audio_probe:voice_lufs",
            "audio_probe:music_lufs",
        ])
        separation = audio["voice_lufs"] - audio["music_lufs"]
        audio_components.append(
            max(0.0, min(1.0, (separation + 2.0) / 10.0))
        )
        if separation < 2.0:
            notes.append(_note(
                time=None,
                dimension="audio_voice_music_balance",
                severity="warning",
                source="structural_heuristic",
                message="Music level is close to or above voice level.",
            ))
    if audio_components:
        audio_quality = _score(
            fmean(audio_components),
            min(0.85, 0.45 + 0.15 * len(audio_components)),
            audio_evidence,
        )
    else:
        audio_quality = _score(
            None,
            0.0,
            [],
            "Insufficient audio probe fields for balance assessment.",
        )

    end_window = max(0.0, duration - 4.0)
    end_semantics = (
        []
        if semantic is None
        else [
            item for item in semantic
            if item["end"] >= end_window
        ]
    )
    end_text = " ".join(
        caption["text"].lower()
        for caption in captions
        if caption["end"] >= end_window
    )
    semantic_labels = {
        item["label"].lower() for item in end_semantics
    }
    has_payoff = any(
        "payoff" in label or "result" in label
        for label in semantic_labels
    )
    has_cta = (
        "cta" in semantic_labels
        or any(
            token in end_text
            for token in ("follow", "save", "comment", "link")
        )
    )
    has_loop = any(
        "loop" in label for label in semantic_labels
    )
    end_evidence_count = len(end_semantics) + (
        1 if end_text else 0
    )
    if end_evidence_count:
        payoff_score = (
            0.5 * (1.0 if has_payoff else 0.0)
            + 0.3 * (1.0 if has_cta else 0.0)
            + 0.2 * (1.0 if has_loop else 0.0)
        )
        payoff = _score(
            payoff_score,
            min(0.78, 0.35 + 0.08 * end_evidence_count),
            ["source_semantic_timeline:end", "captions:end"],
        )
        if payoff_score < 0.5:
            notes.append(_note(
                time=end_window,
                dimension="payoff_cta_loop_coherence",
                severity="warning",
                source="structural_heuristic",
                message="Ending lacks strong structural payoff/CTA/loop evidence.",
            ))
    else:
        payoff = _score(
            None,
            0.0,
            [],
            "No source semantic or caption evidence near ending.",
        )

    dead_intervals = []
    for interval in audio["silence_intervals"]:
        if interval["end"] - interval["start"] >= 1.2:
            nearby = [
                frame for frame in frames
                if interval["start"] <= frame["time"] <= interval["end"]
            ]
            if nearby and fmean(
                frame["motion_score"] for frame in nearby
            ) < 0.12:
                dead_intervals.append(interval)
    dead_score = (
        1.0
        if not dead_intervals
        else max(
            0.0,
            1.0 - sum(
                item["end"] - item["start"]
                for item in dead_intervals
            ) / duration * 2.5,
        )
    )
    awkward = _score(
        dead_score,
        0.72 if audio["silence_intervals"] else 0.45,
        ["audio_probe:silence_intervals", "sampled_frames:motion_score"],
    )
    for interval in dead_intervals:
        notes.append(_note(
            time=interval["start"],
            dimension="awkward_dead_moments",
            severity="warning",
            source="structural_heuristic",
            message="Extended silence plus low motion indicates an awkward/dead moment.",
        ))

    dimensions = {
        "hook_clarity_first_1_3s": hook,
        "pacing_coherence": pacing,
        "semantic_cut_correctness": semantic_cut,
        "subject_framing_crop_quality": framing,
        "broll_relevance": broll,
        "caption_readability_emphasis_relevance":
            caption_quality,
        "visual_continuity": continuity,
        "motion_zoom_appropriateness": motion_quality,
        "audio_voice_music_balance": audio_quality,
        "payoff_cta_loop_coherence": payoff,
        "awkward_dead_moments": awkward,
    }
    return dimensions, hard_failures, notes


def _provider_observations(
    critic_input: Mapping[str, Any],
    provider: VLMVisualCriticAdapter | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    input_digest = critic_input["input_digest"]
    if provider is None:
        return (
            {
                "state": "missing_provider",
                "provider_name": None,
                "model_name": None,
                "observation_count": 0,
                "limitation": (
                    "No VLM provider supplied; report is deterministic "
                    "structural critique only and does not directly inspect pixels."
                ),
            },
            [],
            [],
        )
    _nonempty(provider.provider_name, "provider.provider_name")
    _nonempty(provider.model_name, "provider.model_name")
    raw = provider.observe(critic_input)
    if not isinstance(raw, Sequence):
        raise VisualCriticProviderError(
            "VLM provider observations must be a sequence"
        )
    observations = [
        parse_vlm_observation(
            item,
            input_digest=input_digest,
        )
        for item in raw
    ]
    if any(
        item["provider_name"] != provider.provider_name
        or item["model_name"] != provider.model_name
        for item in observations
    ):
        raise VisualCriticProviderError(
            "VLM observation provider identity mismatch"
        )
    by_id: dict[str, dict[str, Any]] = {}
    for item in observations:
        previous = by_id.get(item["observation_id"])
        if previous is not None and previous != item:
            raise VisualCriticProviderError(
                "conflicting duplicate VLM observation"
            )
        by_id[item["observation_id"]] = item
    canonical = [by_id[key] for key in sorted(by_id)]
    notes = [
        _note(
            time=item["time"],
            dimension=item["dimension"],
            severity=(
                "warning"
                if item["judgment"] == "negative"
                else "info"
            ),
            source="vlm_opinion",
            message=(
                f"VLM opinion ({item['provider_name']}/"
                f"{item['model_name']}, confidence="
                f"{item['confidence']:.2f}): {item['note']}"
            ),
        )
        for item in canonical
    ]
    return (
        {
            "state": "observations_present",
            "provider_name": provider.provider_name,
            "model_name": provider.model_name,
            "observation_count": len(canonical),
            "limitation": (
                "VLM observations are model opinions with provenance/confidence; "
                "they are not human preference labels or ground truth."
            ),
        },
        canonical,
        notes,
    )


def critique_candidate(
    critic_input: Mapping[str, Any],
    *,
    vlm_provider: VLMVisualCriticAdapter | None = None,
) -> dict[str, Any]:
    parsed = parse_visual_critic_input(critic_input)
    dimensions, hard_failures, notes = _structural_evaluation(parsed)
    provider_state, vlm_observations, vlm_notes = (
        _provider_observations(parsed, vlm_provider)
    )
    notes.extend(vlm_notes)
    notes.sort(
        key=lambda item: (
            float("inf")
            if item["time"] is None
            else item["time"],
            item["dimension"],
            item["source"],
            item["message"],
        )
    )
    report = {
        "contract_version": VISUAL_CRITIC_VERSION,
        "critic_report_id": "",
        "critic_report_digest": "",
        "candidate_id": parsed["candidate_id"],
        "input_digest": parsed["input_digest"],
        "render": {
            "artifact_id": parsed["render"]["artifact_id"],
            "artifact_sha256":
                parsed["render"]["artifact_sha256"],
        },
        "objective_hard_failures": hard_failures,
        "heuristic_editorial_judgments": {
            dimension: dimensions[dimension]
            for dimension in CRITIC_DIMENSIONS
        },
        "vlm_provider": provider_state,
        "vlm_observations": vlm_observations,
        "human_labels": {
            "present": False,
            "count": 0,
            "note": (
                "Candidate critique does not contain human labels. "
                "Human preference is evaluated only by the calibration harness."
            ),
        },
        "actionable_notes": notes,
        "limitations": [
            "Structural heuristic scores are not ground-truth human preference.",
            (
                "Scores remain dimension-specific; uncertainty is retained "
                "per dimension and is not collapsed into a single quality score."
            ),
            provider_state["limitation"],
        ],
        "authority": {
            "advisory_only": True,
            "publish_authorized": False,
            "provider_mutation": False,
            "media_mutation": False,
            "creator_mutation": False,
        },
    }
    report["critic_report_id"] = "gvcr15:" + sha256_json({
        "candidate_id": parsed["candidate_id"],
        "input_digest": parsed["input_digest"],
        "artifact_sha256":
            parsed["render"]["artifact_sha256"],
    })
    material = dict(report)
    material["critic_report_digest"] = ""
    report["critic_report_digest"] = sha256_json(material)
    return parse_visual_critic_report(report)


def parse_visual_critic_report(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "contract_version",
        "critic_report_id",
        "critic_report_digest",
        "candidate_id",
        "input_digest",
        "render",
        "objective_hard_failures",
        "heuristic_editorial_judgments",
        "vlm_provider",
        "vlm_observations",
        "human_labels",
        "actionable_notes",
        "limitations",
        "authority",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise VisualCriticError(
            "visual critic report fields must match r15 exactly"
        )
    if payload["contract_version"] != VISUAL_CRITIC_VERSION:
        raise VisualCriticError(
            "unsupported visual critic report version"
        )
    _nonempty(payload["candidate_id"], "candidate_id")
    _digest(payload["input_digest"], "input_digest")
    render = payload["render"]
    if (
        not isinstance(render, Mapping)
        or set(render) != {"artifact_id", "artifact_sha256"}
    ):
        raise VisualCriticError("critic render fields invalid")
    _nonempty(render["artifact_id"], "artifact_id")
    _digest(render["artifact_sha256"], "artifact_sha256")
    failures = payload["objective_hard_failures"]
    if not isinstance(failures, list):
        raise VisualCriticError(
            "objective_hard_failures must be array"
        )
    for failure in failures:
        if (
            not isinstance(failure, Mapping)
            or set(failure)
            != {"code", "time", "message", "source"}
        ):
            raise VisualCriticError(
                "objective hard failure fields invalid"
            )
        _nonempty(failure["code"], "hard failure code")
        _nonempty(failure["message"], "hard failure message")
        _nonempty(failure["source"], "hard failure source")
        if failure["time"] is not None:
            _number(failure["time"], "hard failure time", minimum=0)
    judgments = payload["heuristic_editorial_judgments"]
    if (
        not isinstance(judgments, Mapping)
        or set(judgments) != set(CRITIC_DIMENSIONS)
    ):
        raise VisualCriticError(
            "heuristic dimension set invalid"
        )
    for dimension in CRITIC_DIMENSIONS:
        item = judgments[dimension]
        if (
            not isinstance(item, Mapping)
            or set(item)
            != {"score", "confidence", "evidence", "limitation"}
        ):
            raise VisualCriticError(
                "dimension judgment fields invalid"
            )
        if item["score"] is not None:
            _number(
                item["score"],
                f"{dimension}.score",
                minimum=0,
                maximum=1,
            )
        _number(
            item["confidence"],
            f"{dimension}.confidence",
            minimum=0,
            maximum=1,
        )
        if (
            not isinstance(item["evidence"], list)
            or any(
                not isinstance(value, str) or not value
                for value in item["evidence"]
            )
        ):
            raise VisualCriticError(
                "dimension evidence must be string array"
            )
        if (
            item["limitation"] is not None
            and (
                not isinstance(item["limitation"], str)
                or not item["limitation"]
            )
        ):
            raise VisualCriticError(
                "dimension limitation invalid"
            )
    provider = payload["vlm_provider"]
    if (
        not isinstance(provider, Mapping)
        or set(provider)
        != {
            "state",
            "provider_name",
            "model_name",
            "observation_count",
            "limitation",
        }
    ):
        raise VisualCriticError("VLM provider state fields invalid")
    if provider["state"] not in {
        "missing_provider",
        "observations_present",
    }:
        raise VisualCriticError("unsupported VLM provider state")
    _nonempty(provider["limitation"], "VLM limitation")
    observations = payload["vlm_observations"]
    if not isinstance(observations, list):
        raise VisualCriticError("VLM observations must be array")
    if provider["observation_count"] != len(observations):
        raise VisualCriticError(
            "VLM observation count mismatch"
        )
    for observation in observations:
        parse_vlm_observation(
            observation,
            input_digest=payload["input_digest"],
        )
    human = payload["human_labels"]
    if human != {
        "present": False,
        "count": 0,
        "note": (
            "Candidate critique does not contain human labels. "
            "Human preference is evaluated only by the calibration harness."
        ),
    }:
        raise VisualCriticError(
            "candidate critic report cannot claim human labels"
        )
    notes = payload["actionable_notes"]
    if not isinstance(notes, list):
        raise VisualCriticError("actionable_notes must be array")
    for note in notes:
        if (
            not isinstance(note, Mapping)
            or set(note)
            != {
                "time",
                "dimension",
                "severity",
                "source",
                "message",
            }
        ):
            raise VisualCriticError("actionable note fields invalid")
        if note["dimension"] not in CRITIC_DIMENSIONS:
            raise VisualCriticError(
                "actionable note dimension invalid"
            )
        if note["severity"] not in {
            "info",
            "warning",
            "hard_failure",
        }:
            raise VisualCriticError(
                "actionable note severity invalid"
            )
        if note["source"] not in _ALLOWED_NOTE_SOURCES:
            raise VisualCriticError(
                "actionable note source invalid"
            )
        _nonempty(note["message"], "actionable note message")
        if note["time"] is not None:
            _number(note["time"], "actionable note time", minimum=0)
    if (
        not isinstance(payload["limitations"], list)
        or len(payload["limitations"]) < 2
        or any(
            not isinstance(item, str) or not item
            for item in payload["limitations"]
        )
    ):
        raise VisualCriticError("limitations must be non-empty string array")
    if payload["authority"] != {
        "advisory_only": True,
        "publish_authorized": False,
        "provider_mutation": False,
        "media_mutation": False,
        "creator_mutation": False,
    }:
        raise VisualCriticError(
            "visual critic authority boundary invalid"
        )
    expected_id = "gvcr15:" + sha256_json({
        "candidate_id": payload["candidate_id"],
        "input_digest": payload["input_digest"],
        "artifact_sha256": render["artifact_sha256"],
    })
    if payload["critic_report_id"] != expected_id:
        raise VisualCriticError("critic report identity mismatch")
    _digest(
        payload["critic_report_digest"],
        "critic_report_digest",
    )
    material = dict(payload)
    material["critic_report_digest"] = ""
    if sha256_json(material) != payload["critic_report_digest"]:
        raise VisualCriticError("critic report digest mismatch")
    return json.loads(canonical_json(dict(payload)))


def compare_candidates(
    report_a: Mapping[str, Any],
    report_b: Mapping[str, Any],
    *,
    policy: StructuralCriticPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or StructuralCriticPolicy()
    a = parse_visual_critic_report(report_a)
    b = parse_visual_critic_report(report_b)
    if a["render"]["artifact_sha256"] == b["render"]["artifact_sha256"]:
        raise VisualCriticError(
            "pairwise candidates must be distinct render artifacts"
        )
    hard_a = len(a["objective_hard_failures"])
    hard_b = len(b["objective_hard_failures"])
    comparable: list[dict[str, Any]] = []
    for dimension in CRITIC_DIMENSIONS:
        left = a["heuristic_editorial_judgments"][dimension]
        right = b["heuristic_editorial_judgments"][dimension]
        if (
            left["score"] is None
            or right["score"] is None
            or left["confidence"] < policy.min_dimension_confidence
            or right["confidence"] < policy.min_dimension_confidence
        ):
            continue
        delta = round(left["score"] - right["score"], 6)
        comparable.append({
            "dimension": dimension,
            "a_score": left["score"],
            "b_score": right["score"],
            "delta_a_minus_b": delta,
            "a_confidence": left["confidence"],
            "b_confidence": right["confidence"],
        })

    reason: str
    selection: str
    if hard_a != hard_b:
        selection = "A" if hard_a < hard_b else "B"
        reason = "fewer_objective_hard_failures"
    elif len(comparable) < policy.min_pairwise_dimensions:
        selection = "insufficient_evidence"
        reason = "too_few_comparable_structural_dimensions"
    else:
        weighted = []
        for item in comparable:
            confidence = min(
                item["a_confidence"],
                item["b_confidence"],
            )
            weighted.append(
                item["delta_a_minus_b"] * confidence
            )
        delta = fmean(weighted) if weighted else 0.0
        if abs(delta) <= policy.tie_margin:
            selection = "tie"
            reason = "dimension_specific_difference_within_tie_margin"
        else:
            selection = "A" if delta > 0 else "B"
            reason = "structural_heuristic_pairwise_preference"

    comparison = {
        "contract_version": PAIRWISE_CRITIC_VERSION,
        "comparison_id": "",
        "comparison_digest": "",
        "candidate_a": {
            "candidate_id": a["candidate_id"],
            "critic_report_digest": a["critic_report_digest"],
            "artifact_sha256": a["render"]["artifact_sha256"],
        },
        "candidate_b": {
            "candidate_id": b["candidate_id"],
            "critic_report_digest": b["critic_report_digest"],
            "artifact_sha256": b["render"]["artifact_sha256"],
        },
        "selection": selection,
        "reason": reason,
        "objective_hard_failure_counts": {
            "A": hard_a,
            "B": hard_b,
        },
        "comparable_dimensions": comparable,
        "uncertainty": {
            "tie_margin": policy.tie_margin,
            "min_dimension_confidence":
                policy.min_dimension_confidence,
            "min_pairwise_dimensions":
                policy.min_pairwise_dimensions,
            "human_preference_ground_truth": False,
            "note": (
                "Pairwise selection is advisory structural/model evidence, "
                "not ground-truth human preference."
            ),
        },
        "authority": {
            "advisory_only": True,
            "publish_authorized": False,
            "tournament_selection_input": True,
        },
    }
    comparison["comparison_id"] = "gvcp15:" + sha256_json({
        "a": a["critic_report_digest"],
        "b": b["critic_report_digest"],
    })
    material = dict(comparison)
    material["comparison_digest"] = ""
    comparison["comparison_digest"] = sha256_json(material)
    return parse_pairwise_comparison(comparison)


def parse_pairwise_comparison(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "contract_version",
        "comparison_id",
        "comparison_digest",
        "candidate_a",
        "candidate_b",
        "selection",
        "reason",
        "objective_hard_failure_counts",
        "comparable_dimensions",
        "uncertainty",
        "authority",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise VisualCriticError(
            "pairwise comparison fields must match r15 exactly"
        )
    if payload["contract_version"] != PAIRWISE_CRITIC_VERSION:
        raise VisualCriticError(
            "unsupported pairwise critic version"
        )
    for side in ("candidate_a", "candidate_b"):
        candidate = payload[side]
        if (
            not isinstance(candidate, Mapping)
            or set(candidate)
            != {
                "candidate_id",
                "critic_report_digest",
                "artifact_sha256",
            }
        ):
            raise VisualCriticError(
                "pairwise candidate fields invalid"
            )
        _nonempty(candidate["candidate_id"], "candidate_id")
        _digest(
            candidate["critic_report_digest"],
            "critic_report_digest",
        )
        _digest(
            candidate["artifact_sha256"],
            "artifact_sha256",
        )
    if payload["selection"] not in {
        "A",
        "B",
        "tie",
        "insufficient_evidence",
    }:
        raise VisualCriticError(
            "unsupported pairwise selection"
        )
    _nonempty(payload["reason"], "pairwise reason")
    counts = payload["objective_hard_failure_counts"]
    if (
        not isinstance(counts, Mapping)
        or set(counts) != {"A", "B"}
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counts.values()
        )
    ):
        raise VisualCriticError(
            "objective hard failure counts invalid"
        )
    if not isinstance(payload["comparable_dimensions"], list):
        raise VisualCriticError(
            "comparable_dimensions must be array"
        )
    seen: set[str] = set()
    for row in payload["comparable_dimensions"]:
        if (
            not isinstance(row, Mapping)
            or set(row)
            != {
                "dimension",
                "a_score",
                "b_score",
                "delta_a_minus_b",
                "a_confidence",
                "b_confidence",
            }
        ):
            raise VisualCriticError(
                "comparable dimension row fields invalid"
            )
        if row["dimension"] not in CRITIC_DIMENSIONS:
            raise VisualCriticError(
                "unsupported comparable dimension"
            )
        if row["dimension"] in seen:
            raise VisualCriticError(
                "duplicate comparable dimension"
            )
        seen.add(row["dimension"])
        for field in (
            "a_score",
            "b_score",
            "a_confidence",
            "b_confidence",
        ):
            _number(
                row[field],
                field,
                minimum=0,
                maximum=1,
            )
        _number(
            row["delta_a_minus_b"],
            "delta_a_minus_b",
            minimum=-1,
            maximum=1,
        )
    uncertainty = payload["uncertainty"]
    if (
        not isinstance(uncertainty, Mapping)
        or set(uncertainty)
        != {
            "tie_margin",
            "min_dimension_confidence",
            "min_pairwise_dimensions",
            "human_preference_ground_truth",
            "note",
        }
    ):
        raise VisualCriticError(
            "pairwise uncertainty fields invalid"
        )
    if uncertainty["human_preference_ground_truth"] is not False:
        raise VisualCriticError(
            "pairwise critic cannot claim human ground truth"
        )
    _nonempty(uncertainty["note"], "uncertainty note")
    if payload["authority"] != {
        "advisory_only": True,
        "publish_authorized": False,
        "tournament_selection_input": True,
    }:
        raise VisualCriticError(
            "pairwise authority boundary invalid"
        )
    expected_id = "gvcp15:" + sha256_json({
        "a": payload["candidate_a"]["critic_report_digest"],
        "b": payload["candidate_b"]["critic_report_digest"],
    })
    if payload["comparison_id"] != expected_id:
        raise VisualCriticError(
            "pairwise comparison identity mismatch"
        )
    _digest(payload["comparison_digest"], "comparison_digest")
    material = dict(payload)
    material["comparison_digest"] = ""
    if sha256_json(material) != payload["comparison_digest"]:
        raise VisualCriticError(
            "pairwise comparison digest mismatch"
        )
    return json.loads(canonical_json(dict(payload)))


def build_human_pairwise_label(
    *,
    comparison: Mapping[str, Any],
    preference: str,
    annotator_ref: str,
    observed_at: str,
    provenance_digest: str,
) -> dict[str, Any]:
    pair = parse_pairwise_comparison(comparison)
    if preference not in {"A", "B", "tie"}:
        raise VisualCriticError(
            "human preference must be A, B, or tie"
        )
    _nonempty(annotator_ref, "annotator_ref")
    parse_timestamp(observed_at)
    _digest(provenance_digest, "provenance_digest")
    material = {
        "label_version": HUMAN_LABEL_VERSION,
        "label_id": "",
        "source_kind": "human_provided",
        "comparison_id": pair["comparison_id"],
        "comparison_digest": pair["comparison_digest"],
        "candidate_a_artifact_sha256":
            pair["candidate_a"]["artifact_sha256"],
        "candidate_b_artifact_sha256":
            pair["candidate_b"]["artifact_sha256"],
        "preference": preference,
        "annotator_ref": annotator_ref,
        "observed_at": observed_at,
        "provenance_digest": provenance_digest,
    }
    material["label_id"] = "gvhl15:" + sha256_json({
        key: value
        for key, value in material.items()
        if key not in {"label_version", "label_id"}
    })
    return parse_human_pairwise_label(
        material,
        comparison=pair,
    )


def parse_human_pairwise_label(
    payload: Mapping[str, Any],
    *,
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    pair = parse_pairwise_comparison(comparison)
    expected = {
        "label_version",
        "label_id",
        "source_kind",
        "comparison_id",
        "comparison_digest",
        "candidate_a_artifact_sha256",
        "candidate_b_artifact_sha256",
        "preference",
        "annotator_ref",
        "observed_at",
        "provenance_digest",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise VisualCriticError(
            "human pairwise label fields invalid"
        )
    if payload["label_version"] != HUMAN_LABEL_VERSION:
        raise VisualCriticError(
            "unsupported human label version"
        )
    if payload["source_kind"] != "human_provided":
        raise VisualCriticError(
            "only actual human-provided labels are accepted"
        )
    if (
        payload["comparison_id"] != pair["comparison_id"]
        or payload["comparison_digest"]
        != pair["comparison_digest"]
        or payload["candidate_a_artifact_sha256"]
        != pair["candidate_a"]["artifact_sha256"]
        or payload["candidate_b_artifact_sha256"]
        != pair["candidate_b"]["artifact_sha256"]
    ):
        raise VisualCriticError(
            "human label does not bind exact pairwise comparison"
        )
    if payload["preference"] not in {"A", "B", "tie"}:
        raise VisualCriticError(
            "unsupported human preference"
        )
    _nonempty(payload["annotator_ref"], "annotator_ref")
    parse_timestamp(payload["observed_at"])
    _digest(
        payload["provenance_digest"],
        "provenance_digest",
    )
    expected_id = "gvhl15:" + sha256_json({
        key: value
        for key, value in payload.items()
        if key not in {"label_version", "label_id"}
    })
    if payload["label_id"] != expected_id:
        raise VisualCriticError(
            "human label identity mismatch"
        )
    return json.loads(canonical_json(dict(payload)))


def build_calibration_report(
    *,
    comparisons: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
    min_real_labels: int = 30,
) -> dict[str, Any]:
    if (
        isinstance(min_real_labels, bool)
        or not isinstance(min_real_labels, int)
        or min_real_labels < 1
    ):
        raise VisualCriticError(
            "min_real_labels must be positive integer"
        )
    pairs = {
        item["comparison_id"]: parse_pairwise_comparison(item)
        for item in comparisons
    }
    if len(pairs) != len(comparisons):
        raise VisualCriticError(
            "duplicate pairwise comparisons supplied"
        )
    parsed_labels: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    seen_comparisons: set[str] = set()
    for raw in labels:
        comparison_id = raw.get("comparison_id")
        pair = pairs.get(comparison_id)
        if pair is None:
            raise VisualCriticError(
                "human label references unknown comparison"
            )
        label = parse_human_pairwise_label(
            raw,
            comparison=pair,
        )
        if label["label_id"] in seen_labels:
            raise VisualCriticError(
                "duplicate human label"
            )
        if label["comparison_id"] in seen_comparisons:
            raise VisualCriticError(
                "multiple human labels per comparison unsupported in r15"
            )
        seen_labels.add(label["label_id"])
        seen_comparisons.add(label["comparison_id"])
        parsed_labels.append(label)

    evaluated = []
    for label in parsed_labels:
        pair = pairs[label["comparison_id"]]
        critic = pair["selection"]
        human = label["preference"]
        evaluated.append({
            "comparison_id": label["comparison_id"],
            "critic_selection": critic,
            "human_preference": human,
            "agree": critic == human,
            "critic_abstained":
                critic == "insufficient_evidence",
        })
    real_count = len(parsed_labels)
    non_abstain = [
        item for item in evaluated
        if not item["critic_abstained"]
    ]
    agreement = (
        None
        if not non_abstain
        else round(
            sum(1 for item in non_abstain if item["agree"])
            / len(non_abstain),
            6,
        )
    )
    abstention_rate = (
        None
        if real_count == 0
        else round(
            sum(
                1 for item in evaluated
                if item["critic_abstained"]
            ) / real_count,
            6,
        )
    )
    readiness = (
        HUMAN_BENCHMARK_CORPUS_READY
        if real_count >= min_real_labels
        else HUMAN_LEVEL_UNPROVEN
    )
    report = {
        "contract_version": CALIBRATION_VERSION,
        "report_digest": "",
        "human_benchmark_readiness": readiness,
        "minimum_real_labels": min_real_labels,
        "actual_human_labels": real_count,
        "agreement_metrics": (
            None
            if real_count == 0
            else {
                "non_abstaining_agreement": agreement,
                "abstention_rate": abstention_rate,
                "evaluated_pairs": real_count,
                "non_abstaining_pairs": len(non_abstain),
            }
        ),
        "evaluated": evaluated,
        "interpretation": (
            "Metrics, when present, summarize agreement with supplied "
            "human pairwise labels only. They do not establish that the "
            "critic matches human-level editorial judgment."
        ),
        "authority": {
            "advisory_only": True,
            "publish_authorized": False,
        },
    }
    material = dict(report)
    material["report_digest"] = ""
    report["report_digest"] = sha256_json(material)
    return json.loads(canonical_json(report))
