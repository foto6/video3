from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .autonomous_reels import canonical_json, sha256_json
from .gemini_transport import (
    GEMINI_PROVIDER_NAME,
    GeminiNativeVideoConfig,
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
from .gemini_visual_critic import compact_critic_context
from .visual_critic import (
    CRITIC_DIMENSIONS,
    HUMAN_LEVEL_UNPROVEN,
    StructuralCriticPolicy,
    compare_candidates,
    critique_candidate,
    parse_pairwise_comparison,
    parse_visual_critic_input,
)


GEMINI_NATIVE_VIDEO_PAIRWISE_VERSION = (
    "growth.visual_critic_gemini_native_video_pairwise.r15b.v1"
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
            "Gemini pairwise output is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise GeminiNativeVideoMalformedOutput(
            "Gemini pairwise output must be object"
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


def _pair_prompt(
    context_1: Mapping[str, Any],
    context_2: Mapping[str, Any],
) -> str:
    return (
        "Compare two finished short-form edits. The two native MP4 inputs "
        "are presented only as candidate_1 then candidate_2. Do not infer, "
        "request, or mention original identities. Judge the actual native "
        "video and audio first; contexts are grounding only. Prefer one "
        "candidate only for concrete editorial evidence. Use tie when "
        "effectively equal and insufficient_evidence when evidence is weak. "
        "Never claim human preference ground truth. Return JSON only. "
        "Schema exactly: "
        '{"selection":"candidate_1|candidate_2|tie|insufficient_evidence",'
        '"confidence":0.0,"reason":"...",'
        '"evidence":[{"dimension":"...","candidate_1_time_seconds":0.0,'
        '"candidate_2_time_seconds":0.0,"note":"..."}]}. '
        "Allowed dimensions: "
        + canonical_json(list(CRITIC_DIMENSIONS))
        + ". candidate_1 context: "
        + canonical_json(context_1)
        + ". candidate_2 context: "
        + canonical_json(context_2)
    )


def _parse_pair_response(
    text: str,
    *,
    duration_1: float,
    duration_2: float,
) -> dict[str, Any]:
    payload = _json_object(text)
    if set(payload) != {
        "selection",
        "confidence",
        "reason",
        "evidence",
    }:
        raise GeminiNativeVideoMalformedOutput(
            "Gemini pairwise output fields invalid"
        )
    selection = payload["selection"]
    if selection not in {
        "candidate_1",
        "candidate_2",
        "tie",
        "insufficient_evidence",
    }:
        raise GeminiNativeVideoMalformedOutput(
            "Gemini pairwise selection invalid"
        )
    confidence = _number(
        payload["confidence"],
        "pairwise confidence",
        minimum=0,
        maximum=1,
    )
    reason = payload["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise GeminiNativeVideoMalformedOutput(
            "Gemini pairwise reason must be non-empty"
        )
    evidence = payload["evidence"]
    if not isinstance(evidence, list):
        raise GeminiNativeVideoMalformedOutput(
            "Gemini pairwise evidence must be array"
        )
    normalized = []
    required = {
        "dimension",
        "candidate_1_time_seconds",
        "candidate_2_time_seconds",
        "note",
    }
    for raw in evidence:
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise GeminiNativeVideoMalformedOutput(
                "Gemini pairwise evidence fields invalid"
            )
        dimension = raw["dimension"]
        if dimension not in CRITIC_DIMENSIONS:
            raise GeminiNativeVideoMalformedOutput(
                "Gemini pairwise evidence dimension invalid"
            )
        time_1 = _number(
            raw["candidate_1_time_seconds"],
            "candidate_1_time_seconds",
            minimum=0,
            maximum=duration_1,
        )
        time_2 = _number(
            raw["candidate_2_time_seconds"],
            "candidate_2_time_seconds",
            minimum=0,
            maximum=duration_2,
        )
        note = raw["note"]
        if not isinstance(note, str) or not note.strip():
            raise GeminiNativeVideoMalformedOutput(
                "Gemini pairwise evidence note invalid"
            )
        normalized.append({
            "dimension": dimension,
            "candidate_1_time_seconds": round(time_1, 3),
            "candidate_2_time_seconds": round(time_2, 3),
            "note": note.strip(),
        })
    if selection in {"candidate_1", "candidate_2"} and not normalized:
        raise GeminiNativeVideoMalformedOutput(
            "candidate preference requires evidence"
        )
    return {
        "selection": selection,
        "confidence": round(confidence, 6),
        "reason": reason.strip(),
        "evidence": normalized,
    }


class GeminiNativeVideoPairwiseEvaluator:
    def __init__(self, *, runtime: GeminiRuntime) -> None:
        self._runtime = runtime

    @classmethod
    def from_environment(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        transport: GeminiTransport | None = None,
        probe_runner: ProbeRunner = ffprobe_duration_seconds,
    ) -> "GeminiNativeVideoPairwiseEvaluator | None":
        runtime = runtime_from_environment(
            env=env,
            transport=transport,
            probe_runner=probe_runner,
        )
        return None if runtime is None else cls(runtime=runtime)

    @classmethod
    def for_fake_transport(
        cls,
        *,
        transport: GeminiTransport,
        probe_runner: ProbeRunner,
        config: GeminiNativeVideoConfig | None = None,
    ) -> "GeminiNativeVideoPairwiseEvaluator":
        return cls(
            runtime=runtime_for_fake(
                transport=transport,
                probe_runner=probe_runner,
                config=config,
            )
        )

    @staticmethod
    def blind_swap(comparison_digest: str) -> bool:
        first = hashlib.sha256(
            comparison_digest.encode("utf-8")
        ).digest()[0]
        return bool(first & 1)

    def compare(
        self,
        *,
        critic_input_a: Mapping[str, Any],
        video_path_a: str | Path,
        critic_input_b: Mapping[str, Any],
        video_path_b: str | Path,
        structural_policy: StructuralCriticPolicy | None = None,
    ) -> dict[str, Any]:
        input_a = parse_visual_critic_input(critic_input_a)
        input_b = parse_visual_critic_input(critic_input_b)
        structural = parse_pairwise_comparison(
            compare_candidates(
                critique_candidate(input_a),
                critique_candidate(input_b),
                policy=structural_policy,
            )
        )
        local_a = self._runtime.validate_local_video(
            Path(video_path_a),
            expected_sha256=
                input_a["render"]["artifact_sha256"],
            expected_duration_seconds=
                input_a["render"]["duration_seconds"],
        )
        local_b = self._runtime.validate_local_video(
            Path(video_path_b),
            expected_sha256=
                input_b["render"]["artifact_sha256"],
            expected_duration_seconds=
                input_b["render"]["duration_seconds"],
        )
        swapped = self.blind_swap(
            structural["comparison_digest"]
        )
        ordered = (
            [
                ("B", input_b, Path(video_path_b), local_b),
                ("A", input_a, Path(video_path_a), local_a),
            ]
            if swapped
            else [
                ("A", input_a, Path(video_path_a), local_a),
                ("B", input_b, Path(video_path_b), local_b),
            ]
        )
        context_1 = compact_critic_context(
            ordered[0][1],
            blind=True,
        )
        context_2 = compact_critic_context(
            ordered[1][1],
            blind=True,
        )
        prompt = _pair_prompt(context_1, context_2)
        presentation_digest = sha256_json({
            "comparison_digest":
                structural["comparison_digest"],
            "candidate_1_video_sha256":
                ordered[0][3]["video_sha256"],
            "candidate_2_video_sha256":
                ordered[1][3]["video_sha256"],
            "prompt": prompt,
        })
        request_digest = sha256_json({
            "provider": GEMINI_PROVIDER_NAME,
            "model": self._runtime.config.model,
            "mode": "native_video_pairwise_blinded",
            "processing_mode":
                self._runtime.config.processing_mode,
            "static_fps": self._runtime.config.static_fps,
            "presentation_digest": presentation_digest,
        })

        uploaded_names: list[str] = []
        try:
            file_1 = self._runtime.upload_active(
                ordered[0][2]
            )
            name_1 = file_1.get("name")
            uri_1 = file_1.get("uri")
            if (
                not isinstance(name_1, str)
                or not name_1
                or not isinstance(uri_1, str)
                or not uri_1
            ):
                raise GeminiNativeVideoTransportError(
                    "candidate_1 upload lacks name/uri"
                )
            uploaded_names.append(name_1)
            file_2 = self._runtime.upload_active(
                ordered[1][2]
            )
            name_2 = file_2.get("name")
            uri_2 = file_2.get("uri")
            if (
                not isinstance(name_2, str)
                or not name_2
                or not isinstance(uri_2, str)
                or not uri_2
            ):
                raise GeminiNativeVideoTransportError(
                    "candidate_2 upload lacks name/uri"
                )
            uploaded_names.append(name_2)
            response = self._runtime.interact([
                video_part(
                    uri=uri_1,
                    config=self._runtime.config,
                ),
                {
                    "type": "text",
                    "text": (
                        "candidate_1 compact context: "
                        + canonical_json(context_1)
                    ),
                },
                video_part(
                    uri=uri_2,
                    config=self._runtime.config,
                ),
                {
                    "type": "text",
                    "text": (
                        "candidate_2 compact context: "
                        + canonical_json(context_2)
                    ),
                },
                {"type": "text", "text": prompt},
            ])
            provider = _parse_pair_response(
                response,
                duration_1=
                    ordered[0][3]["ffprobe_duration_seconds"],
                duration_2=
                    ordered[1][3]["ffprobe_duration_seconds"],
            )
        finally:
            cleanup_error: BaseException | None = None
            for name in reversed(uploaded_names):
                try:
                    self._runtime.cleanup(name)
                except BaseException as exc:
                    cleanup_error = exc
            if cleanup_error is not None:
                raise cleanup_error

        blind_selection = provider["selection"]
        if blind_selection == "candidate_1":
            mapped = ordered[0][0]
        elif blind_selection == "candidate_2":
            mapped = ordered[1][0]
        else:
            mapped = blind_selection

        strong = (
            provider["confidence"]
            >= self._runtime.config.pairwise_min_confidence
            and (
                mapped in {"tie", "insufficient_evidence"}
                or bool(provider["evidence"])
            )
        )
        if strong:
            final_selection = mapped
            final_reason = "gemini_native_video_pairwise_opinion"
        else:
            final_selection = structural["selection"]
            final_reason = (
                "structural_result_preserved_weak_gemini_evidence"
            )

        mapped_evidence = []
        for item in provider["evidence"]:
            if swapped:
                a_time = item["candidate_2_time_seconds"]
                b_time = item["candidate_1_time_seconds"]
            else:
                a_time = item["candidate_1_time_seconds"]
                b_time = item["candidate_2_time_seconds"]
            mapped_evidence.append({
                "dimension": item["dimension"],
                "candidate_a_time_seconds": a_time,
                "candidate_b_time_seconds": b_time,
                "note": item["note"],
            })

        result = {
            "contract_version":
                GEMINI_NATIVE_VIDEO_PAIRWISE_VERSION,
            "result_digest": "",
            "structural_comparison": structural,
            "presentation": {
                "blinded": True,
                "deterministic_order_from":
                    "sha256(structural_comparison_digest)",
                "presentation_digest": presentation_digest,
                "mapped_after_scoring": True,
            },
            "gemini_opinion": {
                "provider_name": GEMINI_PROVIDER_NAME,
                "model_name": self._runtime.config.model,
                "mode": "native_video_pairwise_blinded",
                "processing_mode":
                    self._runtime.config.processing_mode,
                "request_digest": request_digest,
                "blind_selection": blind_selection,
                "mapped_selection": mapped,
                "confidence": provider["confidence"],
                "reason": provider["reason"],
                "evidence": mapped_evidence,
                "human_ground_truth": False,
                "evidence_strength": (
                    "sufficient" if strong else "weak"
                ),
            },
            "final_selection": final_selection,
            "final_reason": final_reason,
            "human_benchmark_readiness": HUMAN_LEVEL_UNPROVEN,
            "authority": {
                "advisory_only": True,
                "publish_authorized": False,
                "provider_mutation": False,
                "media_mutation": False,
                "creator_mutation": False,
                "tournament_selection_input": True,
            },
        }
        material = dict(result)
        material["result_digest"] = ""
        result["result_digest"] = sha256_json(material)
        return json.loads(canonical_json(result))
