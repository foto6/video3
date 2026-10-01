from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .gemini_transport import (
    GEMINI_PROVIDER_NAME,
    GeminiNativeVideoConfig,
    GeminiNativeVideoError,
    GeminiNativeVideoMalformedOutput,
    GeminiNativeVideoTransportError,
    GeminiRuntime,
    GeminiTransport,
    ProbeRunner,
    ffprobe_duration_seconds,
    runtime_for_fake,
    runtime_from_environment,
    video_part,
)
from .visual_critic import (
    CRITIC_DIMENSIONS,
    HUMAN_LEVEL_UNPROVEN,
    build_vlm_observation,
    critique_candidate,
    parse_visual_critic_input,
)


GEMINI_NATIVE_VIDEO_CRITIC_VERSION = (
    "growth.visual_critic_gemini_native_video.r15b.v1"
)


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fence = chr(96) * 3
    if stripped.startswith(fence):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == fence:
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise GeminiNativeVideoMalformedOutput(
            "Gemini output is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise GeminiNativeVideoMalformedOutput(
            "Gemini output must be a JSON object"
        )
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        raise GeminiNativeVideoMalformedOutput(
            f"{field} must be numeric"
        )
    parsed = float(value)
    if parsed < minimum or parsed > maximum:
        raise GeminiNativeVideoMalformedOutput(
            f"{field} outside [{minimum},{maximum}]"
        )
    return parsed


def compact_critic_context(
    critic_input: Mapping[str, Any],
    *,
    blind: bool,
) -> dict[str, Any]:
    parsed = parse_visual_critic_input(critic_input)
    context: dict[str, Any] = {
        "duration_seconds": parsed["render"]["duration_seconds"],
        "render_geometry": {
            "width": parsed["render"]["width"],
            "height": parsed["render"]["height"],
            "fps": parsed["render"]["fps"],
        },
        "audio_probe": parsed["audio_probe"],
        "captions": parsed["captions"],
        "overlays": parsed["overlays"],
        "cuts": parsed["cuts"],
        "source_semantic_timeline":
            parsed["source_semantic_timeline"],
        "media_qa": {
            "contract_version":
                parsed["media_qa"]["contract_version"],
            "technical_pass":
                parsed["media_qa"]["technical_pass"],
            "creative_pass":
                parsed["media_qa"]["creative_pass"],
            "hard_failures":
                parsed["media_qa"]["hard_failures"],
            "warnings": parsed["media_qa"]["warnings"],
        },
    }
    if not blind:
        context["candidate_id"] = parsed["candidate_id"]
        context["render_sha256"] = (
            parsed["render"]["artifact_sha256"]
        )
    return context


def _single_prompt(context: Mapping[str, Any]) -> str:
    return (
        "You are an advisory short-form video editor critic. "
        "The native MP4 immediately before this prompt is the primary "
        "evidence. Inspect its visual AND audio content directly. Use the "
        "compact source-semantic/edit/QA context only as grounding. "
        "Do not claim human preference or causal truth. Return JSON only. "
        "Return exactly one observation for every required dimension. "
        "Every observation must include a concrete timestamp_seconds "
        "within the video duration, judgment in "
        "positive|negative|neutral|uncertain, confidence in [0,1], and "
        "a concise evidence-backed note. Required dimensions: "
        + canonical_json(list(CRITIC_DIMENSIONS))
        + ". Output schema exactly: "
        '{"observations":[{"dimension":"...","timestamp_seconds":0.0,'
        '"judgment":"positive","confidence":0.0,"note":"..."}]}. '
        "Compact context: "
        + canonical_json(context)
    )


def _parse_single_response(
    text: str,
    *,
    duration_seconds: float,
) -> list[dict[str, Any]]:
    payload = _json_object(text)
    if set(payload) != {"observations"}:
        raise GeminiNativeVideoMalformedOutput(
            "single-video Gemini output fields invalid"
        )
    observations = payload["observations"]
    if not isinstance(observations, list):
        raise GeminiNativeVideoMalformedOutput(
            "Gemini observations must be an array"
        )
    by_dimension: dict[str, dict[str, Any]] = {}
    required_fields = {
        "dimension",
        "timestamp_seconds",
        "judgment",
        "confidence",
        "note",
    }
    for raw in observations:
        if not isinstance(raw, Mapping) or set(raw) != required_fields:
            raise GeminiNativeVideoMalformedOutput(
                "Gemini observation fields invalid"
            )
        dimension = raw["dimension"]
        if dimension not in CRITIC_DIMENSIONS:
            raise GeminiNativeVideoMalformedOutput(
                "Gemini observation dimension unsupported"
            )
        if dimension in by_dimension:
            raise GeminiNativeVideoMalformedOutput(
                "Gemini returned duplicate critic dimension"
            )
        timestamp = _number(
            raw["timestamp_seconds"],
            "timestamp_seconds",
            minimum=0,
            maximum=duration_seconds,
        )
        judgment = raw["judgment"]
        if judgment not in {
            "positive",
            "negative",
            "neutral",
            "uncertain",
        }:
            raise GeminiNativeVideoMalformedOutput(
                "Gemini observation judgment unsupported"
            )
        confidence = _number(
            raw["confidence"],
            "confidence",
            minimum=0,
            maximum=1,
        )
        note = raw["note"]
        if not isinstance(note, str) or not note.strip():
            raise GeminiNativeVideoMalformedOutput(
                "Gemini observation note must be non-empty"
            )
        by_dimension[dimension] = {
            "dimension": dimension,
            "timestamp_seconds": round(timestamp, 3),
            "judgment": judgment,
            "confidence": round(confidence, 6),
            "note": note.strip(),
        }
    if set(by_dimension) != set(CRITIC_DIMENSIONS):
        missing = sorted(
            set(CRITIC_DIMENSIONS) - set(by_dimension)
        )
        raise GeminiNativeVideoMalformedOutput(
            f"Gemini must return all critic dimensions; missing={missing}"
        )
    return [
        by_dimension[dimension]
        for dimension in CRITIC_DIMENSIONS
    ]


class GeminiNativeVideoCriticAdapter:
    provider_name = GEMINI_PROVIDER_NAME

    def __init__(
        self,
        *,
        video_path: Path,
        runtime: GeminiRuntime,
    ) -> None:
        self.video_path = Path(video_path)
        self._runtime = runtime
        self.model_name = runtime.config.model
        self.last_request_metadata: dict[str, Any] | None = None

    @classmethod
    def from_environment(
        cls,
        *,
        video_path: str | Path,
        env: Mapping[str, str] | None = None,
        transport: GeminiTransport | None = None,
        probe_runner: ProbeRunner = ffprobe_duration_seconds,
    ) -> "GeminiNativeVideoCriticAdapter | None":
        runtime = runtime_from_environment(
            env=env,
            transport=transport,
            probe_runner=probe_runner,
        )
        if runtime is None:
            return None
        return cls(
            video_path=Path(video_path),
            runtime=runtime,
        )

    @classmethod
    def for_fake_transport(
        cls,
        *,
        video_path: str | Path,
        transport: GeminiTransport,
        probe_runner: ProbeRunner,
        config: GeminiNativeVideoConfig | None = None,
    ) -> "GeminiNativeVideoCriticAdapter":
        return cls(
            video_path=Path(video_path),
            runtime=runtime_for_fake(
                transport=transport,
                probe_runner=probe_runner,
                config=config,
            ),
        )

    def observe(
        self,
        critic_input: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]:
        parsed = parse_visual_critic_input(critic_input)
        local = self._runtime.validate_local_video(
            self.video_path,
            expected_sha256=
                parsed["render"]["artifact_sha256"],
            expected_duration_seconds=
                parsed["render"]["duration_seconds"],
        )
        context = compact_critic_context(
            parsed,
            blind=False,
        )
        prompt = _single_prompt(context)
        request_digest = sha256_json({
            "provider": GEMINI_PROVIDER_NAME,
            "model": self._runtime.config.model,
            "mode": "native_video",
            "processing_mode":
                self._runtime.config.processing_mode,
            "static_fps": self._runtime.config.static_fps,
            "input_digest": parsed["input_digest"],
            "video_sha256": local["video_sha256"],
            "prompt": prompt,
        })
        uploaded_name: str | None = None
        try:
            uploaded = self._runtime.upload_active(
                self.video_path
            )
            uploaded_name_raw = uploaded.get("name")
            uri = uploaded.get("uri")
            mime_type = (
                uploaded.get("mimeType")
                or uploaded.get("mime_type")
            )
            if (
                not isinstance(uploaded_name_raw, str)
                or not uploaded_name_raw
                or not isinstance(uri, str)
                or not uri
            ):
                raise GeminiNativeVideoTransportError(
                    "active Gemini file lacks name/uri"
                )
            uploaded_name = uploaded_name_raw
            if mime_type not in {None, "video/mp4"}:
                raise GeminiNativeVideoTransportError(
                    "Gemini upload mime type differs from video/mp4"
                )
            response = self._runtime.interact([
                video_part(
                    uri=uri,
                    config=self._runtime.config,
                ),
                {"type": "text", "text": prompt},
            ])
            structured = _parse_single_response(
                response,
                duration_seconds=
                    local["ffprobe_duration_seconds"],
            )
        finally:
            if uploaded_name is not None:
                self._runtime.cleanup(uploaded_name)

        observations = [
            build_vlm_observation(
                input_digest=parsed["input_digest"],
                provider_name=self.provider_name,
                model_name=self.model_name,
                dimension=item["dimension"],
                time=item["timestamp_seconds"],
                judgment=item["judgment"],
                confidence=item["confidence"],
                note=(
                    "Gemini native-video opinion "
                    f"[mode={self._runtime.config.processing_mode}]: "
                    + item["note"]
                ),
                request_digest=request_digest,
            )
            for item in structured
        ]
        self.last_request_metadata = {
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "mode": "native_video",
            "processing_mode":
                self._runtime.config.processing_mode,
            "static_fps": self._runtime.config.static_fps,
            "request_digest": request_digest,
            "video_sha256": local["video_sha256"],
            "video_size_bytes": local["video_size_bytes"],
            "ffprobe_duration_seconds":
                local["ffprobe_duration_seconds"],
            "observation_count": len(observations),
            "human_ground_truth": False,
        }
        return observations


def gemini_provider_from_environment(
    *,
    video_path: str | Path,
    env: Mapping[str, str] | None = None,
    transport: GeminiTransport | None = None,
    probe_runner: ProbeRunner = ffprobe_duration_seconds,
) -> GeminiNativeVideoCriticAdapter | None:
    return GeminiNativeVideoCriticAdapter.from_environment(
        video_path=video_path,
        env=env,
        transport=transport,
        probe_runner=probe_runner,
    )


def critique_candidate_with_gemini_native_video(
    critic_input: Mapping[str, Any],
    *,
    adapter: GeminiNativeVideoCriticAdapter,
) -> dict[str, Any]:
    base = critique_candidate(
        critic_input,
        vlm_provider=adapter,
    )
    metadata = adapter.last_request_metadata
    if metadata is None:
        raise GeminiNativeVideoError(
            "Gemini adapter did not record request metadata"
        )
    wrapper = {
        "contract_version":
            GEMINI_NATIVE_VIDEO_CRITIC_VERSION,
        "report_digest": "",
        "base_critic_report": base,
        "provider_evidence": metadata,
        "human_benchmark_readiness": HUMAN_LEVEL_UNPROVEN,
        "interpretation": (
            "Gemini native-video observations are VLM opinions with "
            "provider/model/mode/request provenance and confidence. "
            "They are not human preference ground truth."
        ),
        "authority": {
            "advisory_only": True,
            "publish_authorized": False,
            "provider_mutation": False,
            "media_mutation": False,
            "creator_mutation": False,
        },
    }
    material = dict(wrapper)
    material["report_digest"] = ""
    wrapper["report_digest"] = sha256_json(material)
    return json.loads(canonical_json(wrapper))
