from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .autonomous_reels import canonical_json
from .gemini_pairwise import GEMINI_NATIVE_VIDEO_PAIRWISE_VERSION
from .gemini_visual_critic import GEMINI_NATIVE_VIDEO_CRITIC_VERSION
from .visual_critic import (
    CRITIC_DIMENSIONS,
    PAIRWISE_CRITIC_VERSION,
    VISUAL_CRITIC_VERSION,
    VisualCriticError,
    parse_pairwise_comparison,
    parse_visual_critic_report,
)


CRITIC_EXPORT_VERSION = "growth.critic_export.v1"
CRITIC_EXPORT_FILENAME = "growth.critic_export.v1.json"
BENCHMARK_PROTOCOL = "boss.human_editing_gate.v1"
BENCHMARK_AUTHORITY_REPOSITORY = "foto6/boss"
BENCHMARK_AUTHORITY_COMMIT = (
    "e0763bebf2aad9402f8de8c60edb1b9eb8c4be8e"
)
GROWTH_REPOSITORY = "foto6/video3"

BENCHMARK_DIMENSION_MAP = {
    "hook": "hook_clarity_first_1_3s",
    "pacing": "pacing_coherence",
    "semantic_cut_correctness": "semantic_cut_correctness",
    "framing_crop": "subject_framing_crop_quality",
    "broll_relevance": "broll_relevance",
    "captions": "caption_readability_emphasis_relevance",
    "continuity": "visual_continuity",
    "motion_appropriateness": "motion_zoom_appropriateness",
    "audio_balance": "audio_voice_music_balance",
    "payoff_cta_loop": "payoff_cta_loop_coherence",
}
BENCHMARK_DIMENSIONS = tuple(BENCHMARK_DIMENSION_MAP)
R15_TO_BENCHMARK = {
    source: target
    for target, source in BENCHMARK_DIMENSION_MAP.items()
}
_NONHUMAN_CRITIC_MODES = frozenset({
    "structural_rule",
    "vlm_augmented",
    "gemini_native_video",
})
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CriticExportError(ValueError):
    pass


class CriticExportHumanBoundaryError(CriticExportError):
    pass


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CriticExportError(
            f"{field} must be a non-empty string"
        )
    return value


def _sha1(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA1_RE.fullmatch(value):
        raise CriticExportError(
            f"{field} must be lowercase 40-hex Git SHA-1"
        )
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise CriticExportError(
            f"{field} must be lowercase SHA-256 hex"
        )
    return value


def _ms(value: Any) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
    ):
        raise CriticExportError(
            "time evidence must be non-negative numeric"
        )
    return int(round(float(value) * 1000.0))


def _base_report_from_any(
    critic_report: Mapping[str, Any],
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    if not isinstance(critic_report, Mapping):
        raise CriticExportError("critic report must be an object")

    version = critic_report.get("contract_version")
    if version == VISUAL_CRITIC_VERSION:
        base = parse_visual_critic_report(critic_report)
        provider = base["vlm_provider"]
        if provider["state"] == "observations_present":
            mode = "vlm_augmented"
            identity = {
                "kind": "vlm",
                "provider": provider["provider_name"],
                "model": provider["model_name"],
                "mode": "r15_observation_adapter",
                "contract_version": VISUAL_CRITIC_VERSION,
            }
        else:
            mode = "structural_rule"
            identity = {
                "kind": "rule",
                "provider": None,
                "model": None,
                "mode": "deterministic_structural",
                "contract_version": VISUAL_CRITIC_VERSION,
            }
        return base, mode, identity

    if version == GEMINI_NATIVE_VIDEO_CRITIC_VERSION:
        expected = {
            "contract_version",
            "report_digest",
            "base_critic_report",
            "provider_evidence",
            "human_benchmark_readiness",
            "interpretation",
            "authority",
        }
        if set(critic_report) != expected:
            raise CriticExportError(
                "Gemini R15B critic wrapper fields invalid"
            )
        base = parse_visual_critic_report(
            critic_report["base_critic_report"]
        )
        provider = critic_report["provider_evidence"]
        if (
            not isinstance(provider, Mapping)
            or provider.get("provider_name") != "google_gemini"
            or provider.get("mode") != "native_video"
            or provider.get("human_ground_truth") is not False
        ):
            raise CriticExportHumanBoundaryError(
                "Gemini provider evidence boundary invalid"
            )
        identity = {
            "kind": "vlm",
            "provider": provider["provider_name"],
            "model": _nonempty(
                provider.get("model_name"),
                "provider_evidence.model_name",
            ),
            "mode": provider["mode"],
            "processing_mode": _nonempty(
                provider.get("processing_mode"),
                "provider_evidence.processing_mode",
            ),
            "request_digest": _sha256(
                provider.get("request_digest"),
                "provider_evidence.request_digest",
            ),
            "contract_version":
                GEMINI_NATIVE_VIDEO_CRITIC_VERSION,
        }
        return base, "gemini_native_video", identity

    raise CriticExportError(
        f"unsupported critic report contract: {version!r}"
    )


def _dimension_observations(
    base: Mapping[str, Any],
) -> dict[str, Any]:
    heuristics = base["heuristic_editorial_judgments"]
    vlm_by_dimension: dict[str, list[dict[str, Any]]] = {
        dimension: [] for dimension in CRITIC_DIMENSIONS
    }
    for observation in base["vlm_observations"]:
        vlm_by_dimension[observation["dimension"]].append({
            "available": True,
            "judgment": observation["judgment"],
            "confidence": observation["confidence"],
            "time_ms": _ms(observation["time"]),
            "note": observation["note"],
            "provider": observation["provider_name"],
            "model": observation["model_name"],
            "request_digest": observation["request_digest"],
            "human_ground_truth": False,
        })

    mapped: dict[str, Any] = {}
    for benchmark_dimension, source_dimension in (
        BENCHMARK_DIMENSION_MAP.items()
    ):
        rule = heuristics[source_dimension]
        available = rule["score"] is not None
        mapped[benchmark_dimension] = {
            "source_dimension": source_dimension,
            "rule_observation": {
                "available": available,
                "normalized_score": rule["score"],
                "confidence": rule["confidence"],
                "evidence": list(rule["evidence"]),
                "limitation": rule["limitation"],
                "human_ground_truth": False,
            },
            "vlm_observations":
                vlm_by_dimension[source_dimension],
            "unavailable": (
                None
                if available
                else (
                    rule["limitation"]
                    or "No structural score available."
                )
            ),
        }
    return mapped


def _timecoded_evidence(
    base: Mapping[str, Any],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for note in base["actionable_notes"]:
        source_dimension = note["dimension"]
        evidence.append({
            "rubric_dimension":
                R15_TO_BENCHMARK.get(source_dimension),
            "source_dimension": source_dimension,
            "start_ms": _ms(note["time"]),
            "end_ms": _ms(note["time"]),
            "severity": note["severity"],
            "evidence_kind": note["source"],
            "note": note["message"],
            "human_ground_truth": False,
        })
    for observation in base["vlm_observations"]:
        evidence.append({
            "rubric_dimension":
                R15_TO_BENCHMARK.get(observation["dimension"]),
            "source_dimension": observation["dimension"],
            "start_ms": _ms(observation["time"]),
            "end_ms": _ms(observation["time"]),
            "severity": (
                "warning"
                if observation["judgment"] == "negative"
                else "info"
            ),
            "evidence_kind": "vlm_opinion",
            "note": observation["note"],
            "confidence": observation["confidence"],
            "provider": observation["provider_name"],
            "model": observation["model_name"],
            "request_digest": observation["request_digest"],
            "human_ground_truth": False,
        })
    evidence.sort(
        key=lambda item: (
            item["start_ms"] is None,
            -1 if item["start_ms"] is None else item["start_ms"],
            item["source_dimension"],
            item["evidence_kind"],
            item["note"],
        )
    )
    return evidence


def _hard_failures(
    base: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "code": item["code"],
            "time_ms": _ms(item["time"]),
            "source": item["source"],
            "message": item["message"],
            "human_ground_truth": False,
        }
        for item in base["objective_hard_failures"]
    ]


def _pairwise_export(
    pairwise: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if pairwise is None:
        return None
    version = pairwise.get("contract_version")
    if version == PAIRWISE_CRITIC_VERSION:
        parsed = parse_pairwise_comparison(pairwise)
        return {
            "contract_version": PAIRWISE_CRITIC_VERSION,
            "selection": parsed["selection"],
            "reason": parsed["reason"],
            "comparison_digest": parsed["comparison_digest"],
            "candidate_a_render_sha256":
                parsed["candidate_a"]["artifact_sha256"],
            "candidate_b_render_sha256":
                parsed["candidate_b"]["artifact_sha256"],
            "confidence": None,
            "evidence": [
                {
                    "rubric_dimension":
                        R15_TO_BENCHMARK.get(
                            row["dimension"]
                        ),
                    "source_dimension": row["dimension"],
                    "candidate_a_time_ms": None,
                    "candidate_b_time_ms": None,
                    "note": (
                        "Structural comparison score delta "
                        f"{row['delta_a_minus_b']} with "
                        f"confidence A={row['a_confidence']}, "
                        f"B={row['b_confidence']}."
                    ),
                }
                for row in parsed["comparable_dimensions"]
            ],
            "advisory_only": True,
            "human_label": False,
            "human_ground_truth": False,
        }

    if version == GEMINI_NATIVE_VIDEO_PAIRWISE_VERSION:
        expected = {
            "contract_version",
            "result_digest",
            "structural_comparison",
            "presentation",
            "gemini_opinion",
            "final_selection",
            "final_reason",
            "human_benchmark_readiness",
            "authority",
        }
        if set(pairwise) != expected:
            raise CriticExportError(
                "Gemini R15B pairwise fields invalid"
            )
        structural = parse_pairwise_comparison(
            pairwise["structural_comparison"]
        )
        opinion = pairwise["gemini_opinion"]
        if (
            not isinstance(opinion, Mapping)
            or opinion.get("provider_name") != "google_gemini"
            or opinion.get("human_ground_truth") is not False
        ):
            raise CriticExportHumanBoundaryError(
                "Gemini pairwise opinion boundary invalid"
            )
        evidence = []
        for item in opinion.get("evidence", []):
            if not isinstance(item, Mapping):
                raise CriticExportError(
                    "Gemini pairwise evidence must be objects"
                )
            source_dimension = item.get("dimension")
            if source_dimension not in CRITIC_DIMENSIONS:
                raise CriticExportError(
                    "Gemini pairwise evidence dimension invalid"
                )
            evidence.append({
                "rubric_dimension":
                    R15_TO_BENCHMARK.get(source_dimension),
                "source_dimension": source_dimension,
                "candidate_a_time_ms":
                    _ms(item.get("candidate_a_time_seconds")),
                "candidate_b_time_ms":
                    _ms(item.get("candidate_b_time_seconds")),
                "note": _nonempty(
                    item.get("note"),
                    "Gemini pairwise evidence note",
                ),
            })
        return {
            "contract_version":
                GEMINI_NATIVE_VIDEO_PAIRWISE_VERSION,
            "selection": pairwise["final_selection"],
            "reason": pairwise["final_reason"],
            "comparison_digest":
                structural["comparison_digest"],
            "candidate_a_render_sha256":
                structural["candidate_a"]["artifact_sha256"],
            "candidate_b_render_sha256":
                structural["candidate_b"]["artifact_sha256"],
            "confidence": opinion.get("confidence"),
            "evidence": evidence,
            "provider": opinion.get("provider_name"),
            "model": opinion.get("model_name"),
            "mode": opinion.get("mode"),
            "request_digest": opinion.get("request_digest"),
            "blind_selection": opinion.get("blind_selection"),
            "mapped_selection": opinion.get("mapped_selection"),
            "evidence_strength": opinion.get("evidence_strength"),
            "advisory_only": True,
            "human_label": False,
            "human_ground_truth": False,
        }

    raise CriticExportError(
        f"unsupported pairwise contract: {version!r}"
    )


def build_critic_export(
    *,
    critic_report: Mapping[str, Any],
    repository: str,
    commit_sha: str,
    source_id: str,
    pairwise_if_used: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if repository != GROWTH_REPOSITORY:
        raise CriticExportError(
            "repository must be foto6/video3"
        )
    _sha1(commit_sha, "commit_sha")
    _nonempty(source_id, "source_id")
    base, critic_mode, identity = _base_report_from_any(
        critic_report
    )
    render_sha256 = _sha256(
        base["render"]["artifact_sha256"],
        "render_sha256",
    )
    payload = {
        "contract_version": CRITIC_EXPORT_VERSION,
        "repository": repository,
        "commit_sha": commit_sha,
        "source_id": source_id,
        "render_sha256": render_sha256,
        "critic_mode": critic_mode,
        "model_or_rule_identity": identity,
        "dimension_observations":
            _dimension_observations(base),
        "timecoded_evidence": _timecoded_evidence(base),
        "hard_failure_observations":
            _hard_failures(base),
        "pairwise_if_used":
            _pairwise_export(pairwise_if_used),
        "human_ground_truth": False,
    }
    return validate_critic_export(
        json.loads(canonical_json(payload))
    )


def validate_critic_export(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "contract_version",
        "repository",
        "commit_sha",
        "source_id",
        "render_sha256",
        "critic_mode",
        "model_or_rule_identity",
        "dimension_observations",
        "timecoded_evidence",
        "hard_failure_observations",
        "pairwise_if_used",
        "human_ground_truth",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise CriticExportError(
            "critic export keys must match benchmark contract exactly"
        )
    if payload["contract_version"] != CRITIC_EXPORT_VERSION:
        raise CriticExportError(
            "unsupported critic export version"
        )
    if payload["repository"] != GROWTH_REPOSITORY:
        raise CriticExportError(
            "critic export repository mismatch"
        )
    _sha1(payload["commit_sha"], "commit_sha")
    _nonempty(payload["source_id"], "source_id")
    _sha256(payload["render_sha256"], "render_sha256")
    mode = payload["critic_mode"]
    if mode not in _NONHUMAN_CRITIC_MODES:
        raise CriticExportError(
            "unsupported critic_mode"
        )
    if payload["human_ground_truth"] is not False:
        raise CriticExportHumanBoundaryError(
            "Growth rule/model/VLM critic exports can never be human ground truth"
        )

    identity = payload["model_or_rule_identity"]
    if not isinstance(identity, Mapping):
        raise CriticExportError(
            "model_or_rule_identity must be object"
        )
    if identity.get("kind") not in {"rule", "vlm"}:
        raise CriticExportError(
            "model_or_rule_identity kind invalid"
        )

    dimensions = payload["dimension_observations"]
    if (
        not isinstance(dimensions, Mapping)
        or set(dimensions) != set(BENCHMARK_DIMENSIONS)
    ):
        raise CriticExportError(
            "dimension_observations must contain exact benchmark rubric dimensions"
        )
    for benchmark_dimension in BENCHMARK_DIMENSIONS:
        item = dimensions[benchmark_dimension]
        if (
            not isinstance(item, Mapping)
            or set(item)
            != {
                "source_dimension",
                "rule_observation",
                "vlm_observations",
                "unavailable",
            }
        ):
            raise CriticExportError(
                f"{benchmark_dimension} observation fields invalid"
            )
        expected_source = BENCHMARK_DIMENSION_MAP[
            benchmark_dimension
        ]
        if item["source_dimension"] != expected_source:
            raise CriticExportError(
                "benchmark/source dimension mapping invalid"
            )
        rule = item["rule_observation"]
        if (
            not isinstance(rule, Mapping)
            or set(rule)
            != {
                "available",
                "normalized_score",
                "confidence",
                "evidence",
                "limitation",
                "human_ground_truth",
            }
        ):
            raise CriticExportError(
                "rule observation fields invalid"
            )
        if rule["human_ground_truth"] is not False:
            raise CriticExportHumanBoundaryError(
                "rule observation cannot be human ground truth"
            )
        if rule["available"]:
            score = rule["normalized_score"]
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not 0 <= float(score) <= 1
            ):
                raise CriticExportError(
                    "available rule score must be in [0,1]"
                )
            if item["unavailable"] is not None:
                raise CriticExportError(
                    "available observation cannot have unavailable reason"
                )
        else:
            if rule["normalized_score"] is not None:
                raise CriticExportError(
                    "unavailable observation cannot invent a score"
                )
            _nonempty(
                item["unavailable"],
                "unavailable reason",
            )
        confidence = rule["confidence"]
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise CriticExportError(
                "rule confidence must be in [0,1]"
            )
        if not isinstance(rule["evidence"], list):
            raise CriticExportError(
                "rule evidence must be array"
            )
        if not isinstance(item["vlm_observations"], list):
            raise CriticExportError(
                "vlm_observations must be array"
            )
        for vlm in item["vlm_observations"]:
            if (
                not isinstance(vlm, Mapping)
                or vlm.get("human_ground_truth") is not False
            ):
                raise CriticExportHumanBoundaryError(
                    "VLM observations cannot be human ground truth"
                )

    if not isinstance(payload["timecoded_evidence"], list):
        raise CriticExportError(
            "timecoded_evidence must be array"
        )
    for evidence in payload["timecoded_evidence"]:
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("human_ground_truth") is not False
        ):
            raise CriticExportHumanBoundaryError(
                "critic timecoded evidence cannot be human ground truth"
            )
        rubric = evidence.get("rubric_dimension")
        if rubric is not None and rubric not in BENCHMARK_DIMENSIONS:
            raise CriticExportError(
                "timecoded rubric dimension invalid"
            )
        source_dimension = evidence.get("source_dimension")
        if source_dimension not in CRITIC_DIMENSIONS:
            raise CriticExportError(
                "timecoded source dimension invalid"
            )

    hard = payload["hard_failure_observations"]
    if not isinstance(hard, list):
        raise CriticExportError(
            "hard_failure_observations must be array"
        )
    for item in hard:
        if (
            not isinstance(item, Mapping)
            or item.get("human_ground_truth") is not False
        ):
            raise CriticExportHumanBoundaryError(
                "hard failures are machine observations, not human labels"
            )

    pair = payload["pairwise_if_used"]
    if pair is not None:
        if not isinstance(pair, Mapping):
            raise CriticExportError(
                "pairwise_if_used must be object or null"
            )
        if (
            pair.get("advisory_only") is not True
            or pair.get("human_label") is not False
            or pair.get("human_ground_truth") is not False
        ):
            raise CriticExportHumanBoundaryError(
                "model/rule pairwise preference is advisory non-human evidence only"
            )
        if pair.get("selection") not in {
            "A",
            "B",
            "tie",
            "insufficient_evidence",
        }:
            raise CriticExportError(
                "pairwise selection invalid"
            )
        candidate_a = _sha256(
            pair.get("candidate_a_render_sha256"),
            "pairwise candidate A render SHA",
        )
        candidate_b = _sha256(
            pair.get("candidate_b_render_sha256"),
            "pairwise candidate B render SHA",
        )
        if payload["render_sha256"] not in {
            candidate_a,
            candidate_b,
        }:
            raise CriticExportError(
                "pairwise comparison does not include exported render"
            )

    serialized = canonical_json(payload)
    forbidden = (
        '"source_kind":"human_provided"',
        '"human_label":true',
        '"human_ground_truth":true',
    )
    if any(token in serialized for token in forbidden):
        raise CriticExportHumanBoundaryError(
            "critic export contains human-label representation"
        )
    return json.loads(serialized)


def write_critic_export(
    payload: Mapping[str, Any],
    path: str | Path,
) -> Path:
    parsed = validate_critic_export(payload)
    destination = Path(path)
    if destination.name != CRITIC_EXPORT_FILENAME:
        raise CriticExportHumanBoundaryError(
            "critic evidence may only be written as growth.critic_export.v1.json; "
            "human_ratings.v1.ndjson is benchmark-owned"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        canonical_json(parsed) + "\n",
        encoding="utf-8",
    )
    return destination
