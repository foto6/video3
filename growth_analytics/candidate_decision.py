from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Sequence

from .autonomous_reels import (
    canonical_json,
    parse_metric_snapshot,
    parse_publish_result,
    sha256_json,
)
from .critic_export import (
    BENCHMARK_DIMENSIONS,
    CRITIC_EXPORT_VERSION,
    validate_critic_export,
)
from .event_stream import parse_timestamp
from .visual_critic import (
    PAIRWISE_CRITIC_VERSION,
    VisualCriticError,
    parse_human_pairwise_label,
    parse_pairwise_comparison,
)


CANDIDATE_DECISION_VERSION = "growth.candidate_decision.v1"
CANDIDATE_DECISION_LEDGER_VERSION = (
    "growth.candidate_decision_ledger.v1"
)
CANDIDATE_DECISION_REPLAY_VERSION = (
    "growth.candidate_decision_replay.r18.v1"
)

DECISIONS = frozenset({
    "winner",
    "tie",
    "insufficient_evidence",
})
LIVE_METRIC_BASIS = frozenset({
    "observational",
    "randomized",
})
HISTORICAL_INTERPRETATION = "directional_observational_not_causal"

_DIRECTIVES = {
    "hook_clarity_first_1_3s":
        "Rewrite or recut the opening so the viewer understands the hook within the first 1-3 seconds.",
    "pacing_coherence":
        "Tighten this segment by removing dead time or adjusting cut timing while preserving semantic continuity.",
    "semantic_cut_correctness":
        "Move the cut to a source-semantic boundary so the sentence or action is not truncated.",
    "subject_framing_crop_quality":
        "Reframe this segment to keep the primary subject safely inside the vertical crop.",
    "broll_relevance":
        "Replace or retime this B-roll with imagery that directly matches the spoken/source semantic beat.",
    "caption_readability_emphasis_relevance":
        "Reposition or restyle the caption to remove collision and emphasize the semantically relevant words.",
    "visual_continuity":
        "Repair the continuity break at this segment with a coherent cut, bridge, or transition.",
    "motion_zoom_appropriateness":
        "Reduce or retime the zoom/motion so it supports rather than distracts from the editorial beat.",
    "audio_voice_music_balance":
        "Remix this segment so voice remains intelligible and music/peaks stay subordinate to speech.",
    "payoff_cta_loop_coherence":
        "Recut the ending so payoff, CTA, and loop resolve in a coherent sequence.",
    "awkward_dead_moments":
        "Remove or shorten this awkward/dead segment and reconnect the surrounding beats cleanly.",
}

_SHA256 = set("0123456789abcdef")


class CandidateDecisionError(ValueError):
    pass


class CandidateDecisionConflictError(CandidateDecisionError):
    pass


class OutOfOrderCandidateDecision(CandidateDecisionError):
    pass


class CandidateDecisionSyntheticLiveRejected(
    CandidateDecisionError
):
    pass


class CandidateDecisionCausalMisuse(
    CandidateDecisionError
):
    pass


@dataclass(frozen=True)
class CandidateDecisionPolicy:
    min_candidates: int = 2
    max_candidates: int = 4
    min_comparable_dimensions: int = 6
    min_rule_confidence: float = 0.35
    winner_margin: float = 0.08
    metric_freshness_required: str = "fresh"

    def __post_init__(self) -> None:
        for value, field in (
            (self.min_candidates, "min_candidates"),
            (self.max_candidates, "max_candidates"),
            (
                self.min_comparable_dimensions,
                "min_comparable_dimensions",
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise CandidateDecisionError(
                    f"{field} must be positive integer"
                )
        if not 2 <= self.min_candidates <= self.max_candidates <= 4:
            raise CandidateDecisionError(
                "candidate policy must be bounded to 2-4"
            )
        for value, field in (
            (
                self.min_rule_confidence,
                "min_rule_confidence",
            ),
            (self.winner_margin, "winner_margin"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= float(value) <= 1
            ):
                raise CandidateDecisionError(
                    f"{field} must be in [0,1]"
                )
        if self.metric_freshness_required != "fresh":
            raise CandidateDecisionError(
                "r18 requires fresh live metrics for eligible live evidence"
            )


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidateDecisionError(
            f"{field} must be non-empty string"
        )
    return value


def _digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in _SHA256 for ch in value)
    ):
        raise CandidateDecisionError(
            f"{field} must be lowercase SHA-256"
        )
    return value


def _positive_int(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise CandidateDecisionError(
            f"{field} must be integer >= 1"
        )
    return value


def _candidate_index(
    candidates: Sequence[Mapping[str, Any]],
    *,
    expected_candidate_ids: Sequence[str],
    source_id: str,
    source_sha256: str,
) -> tuple[
    dict[str, dict[str, Any]],
    tuple[str, ...],
]:
    if (
        not isinstance(expected_candidate_ids, Sequence)
        or isinstance(expected_candidate_ids, (str, bytes))
    ):
        raise CandidateDecisionError(
            "expected_candidate_ids must be sequence"
        )
    expected = tuple(expected_candidate_ids)
    if not 2 <= len(expected) <= 4:
        raise CandidateDecisionError(
            "expected candidate set must contain 2-4 ids"
        )
    if len(set(expected)) != len(expected):
        raise CandidateDecisionError(
            "expected candidate ids must be unique"
        )
    for candidate_id in expected:
        _nonempty(candidate_id, "expected_candidate_id")
    if (
        not isinstance(candidates, Sequence)
        or isinstance(candidates, (str, bytes))
    ):
        raise CandidateDecisionError(
            "candidates must be sequence"
        )
    if len(candidates) > len(expected):
        raise CandidateDecisionError(
            "more supplied candidates than expected"
        )
    parsed: dict[str, dict[str, Any]] = {}
    render_seen: set[str] = set()
    for raw in candidates:
        if (
            not isinstance(raw, Mapping)
            or set(raw)
            != {
                "candidate_id",
                "source_id",
                "source_sha256",
                "render_sha256",
                "critic_export",
            }
        ):
            raise CandidateDecisionError(
                "candidate fields invalid"
            )
        candidate_id = _nonempty(
            raw["candidate_id"],
            "candidate_id",
        )
        if candidate_id not in expected:
            raise CandidateDecisionError(
                "supplied candidate was not expected"
            )
        if candidate_id in parsed:
            raise CandidateDecisionError(
                "duplicate candidate_id"
            )
        if raw["source_id"] != source_id:
            raise CandidateDecisionError(
                "candidate source_id mismatch"
            )
        if _digest(
            raw["source_sha256"],
            "candidate.source_sha256",
        ) != source_sha256:
            raise CandidateDecisionError(
                "candidate source hash mismatch"
            )
        render_sha = _digest(
            raw["render_sha256"],
            "candidate.render_sha256",
        )
        if render_sha in render_seen:
            raise CandidateDecisionError(
                "candidate render hashes must be unique"
            )
        try:
            critic = validate_critic_export(
                raw["critic_export"]
            )
        except ValueError as exc:
            raise CandidateDecisionError(
                "invalid critic export"
            ) from exc
        if critic["contract_version"] != CRITIC_EXPORT_VERSION:
            raise CandidateDecisionError(
                "critic export version mismatch"
            )
        if critic["source_id"] != source_id:
            raise CandidateDecisionError(
                "critic source_id mismatch"
            )
        if critic["render_sha256"] != render_sha:
            raise CandidateDecisionError(
                "critic/render hash mismatch"
            )
        if critic["human_ground_truth"] is not False:
            raise CandidateDecisionError(
                "critic export cannot be human ground truth"
            )
        parsed[candidate_id] = {
            "candidate_id": candidate_id,
            "source_id": source_id,
            "source_sha256": source_sha256,
            "render_sha256": render_sha,
            "critic_export": critic,
        }
        render_seen.add(render_sha)
    missing = tuple(
        candidate_id
        for candidate_id in expected
        if candidate_id not in parsed
    )
    return parsed, missing


def _runtime_status(
    raw: Any,
    *,
    publish: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise CandidateDecisionError(
            "runtime_status must be object"
        )
    required = {
        "platform",
        "post_id",
        "cycle_revision",
        "collector_state",
        "freshness",
        "lag_seconds",
        "last_success_at",
        "last_error",
        "error_classification",
        "backoff_until",
        "backfill_recovery_state",
        "latest_snapshot_digest",
        "latest_provider_revision",
        "unavailable_evidence",
        "lineage",
    }
    if set(raw) != required:
        raise CandidateDecisionError(
            "runtime_status fields do not match r17"
        )
    if (
        raw["platform"] != publish["platform"]
        or raw["post_id"] != publish["post_id"]
        or raw["cycle_revision"]
        != publish["cycle_revision"]
    ):
        raise CandidateDecisionError(
            "runtime status publish identity mismatch"
        )
    lineage = raw["lineage"]
    if (
        not isinstance(lineage, Mapping)
        or lineage.get("publish_result_id")
        != publish["publish_result_id"]
        or lineage.get("publish_result_digest")
        != publish["publish_result_digest"]
        or lineage.get("provider_receipt_digest")
        != publish["provenance"]["provider_receipt_digest"]
        or lineage.get("media_render_sha256")
        != publish["artifact"]["media_artifact_digest"]
        or lineage.get("live_performance_claim_allowed")
        is not True
    ):
        raise CandidateDecisionError(
            "runtime status lineage mismatch"
        )
    if snapshot is not None:
        if raw["latest_snapshot_digest"] != snapshot["snapshot_digest"]:
            raise CandidateDecisionError(
                "runtime status does not bind supplied metric snapshot"
            )
    if (
        isinstance(raw["lag_seconds"], bool)
        or not isinstance(raw["lag_seconds"], int)
        or raw["lag_seconds"] < 0
    ):
        raise CandidateDecisionError(
            "runtime lag_seconds invalid"
        )
    if raw["last_success_at"] is not None:
        parse_timestamp(raw["last_success_at"])
    if raw["freshness"] not in {
        "fresh",
        "stale",
        "no_data_yet",
        "broken",
    }:
        raise CandidateDecisionError(
            "runtime freshness invalid"
        )
    return json.loads(canonical_json(dict(raw)))


def _live_observations(
    rows: Sequence[Mapping[str, Any]],
    *,
    candidates: Mapping[str, Mapping[str, Any]],
    cycle_revision: int,
    policy: CandidateDecisionPolicy,
) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(rows, Sequence)
        or isinstance(rows, (str, bytes))
    ):
        raise CandidateDecisionError(
            "live_metric_evidence must be sequence"
        )
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in rows:
        if (
            not isinstance(raw, Mapping)
            or set(raw)
            != {
                "candidate_id",
                "publish_result",
                "metric_snapshot",
                "runtime_status",
                "evidence_basis",
            }
        ):
            raise CandidateDecisionError(
                "live metric evidence fields invalid"
            )
        candidate_id = _nonempty(
            raw["candidate_id"],
            "live candidate_id",
        )
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise CandidateDecisionError(
                "live metric evidence references missing candidate"
            )
        if candidate_id in seen:
            raise CandidateDecisionError(
                "duplicate live metric evidence for candidate"
            )
        seen.add(candidate_id)
        try:
            publish = parse_publish_result(
                raw["publish_result"]
            )
        except ValueError as exc:
            raise CandidateDecisionError(
                "invalid live publish result"
            ) from exc
        if (
            publish["source_class"] != "platform_export"
            or publish["provenance"][
                "live_performance_claim_allowed"
            ] is not True
        ):
            raise CandidateDecisionSyntheticLiveRejected(
                "live metric evidence must come from platform_export"
            )
        if publish["cycle_revision"] != cycle_revision:
            raise CandidateDecisionError(
                "live metric cycle revision mismatch"
            )
        if (
            publish["artifact"]["media_artifact_digest"]
            != candidate["render_sha256"]
        ):
            raise CandidateDecisionError(
                "live metric render lineage mismatch"
            )
        metric_raw = raw["metric_snapshot"]
        snapshot = None
        if metric_raw is not None:
            try:
                snapshot = parse_metric_snapshot(metric_raw)
            except ValueError as exc:
                raise CandidateDecisionError(
                    "invalid live metric snapshot"
                ) from exc
            if (
                snapshot["source_class"] != "platform_export"
                or snapshot[
                    "live_performance_claim_allowed"
                ] is not True
            ):
                raise CandidateDecisionSyntheticLiveRejected(
                    "synthetic metric snapshot cannot be live evidence"
                )
            if (
                snapshot["publish_result_id"]
                != publish["publish_result_id"]
                or snapshot["publish_result_digest"]
                != publish["publish_result_digest"]
                or snapshot["platform"] != publish["platform"]
                or snapshot["account_id"]
                != publish["account_id"]
                or snapshot["post_id"] != publish["post_id"]
                or snapshot["cycle_revision"]
                != publish["cycle_revision"]
            ):
                raise CandidateDecisionError(
                    "metric snapshot/publish lineage mismatch"
                )
        status = _runtime_status(
            raw["runtime_status"],
            publish=publish,
            snapshot=snapshot,
        )
        basis = raw["evidence_basis"]
        if (
            not isinstance(basis, Mapping)
            or set(basis)
            != {
                "kind",
                "experiment_id",
                "assignment_digest",
            }
        ):
            raise CandidateDecisionError(
                "live evidence_basis fields invalid"
            )
        kind = basis["kind"]
        if kind not in LIVE_METRIC_BASIS:
            raise CandidateDecisionError(
                "live evidence basis kind invalid"
            )
        if kind == "randomized":
            _nonempty(
                basis["experiment_id"],
                "live experiment_id",
            )
            _digest(
                basis["assignment_digest"],
                "live assignment_digest",
            )
        else:
            if (
                basis["experiment_id"] is not None
                or basis["assignment_digest"] is not None
            ):
                raise CandidateDecisionCausalMisuse(
                    "observational live metrics cannot claim randomized assignment"
                )
        fresh = (
            snapshot is not None
            and status["freshness"]
            == policy.metric_freshness_required
            and status["collector_state"]
            in {"running", "complete"}
        )
        result.append({
            "candidate_id": candidate_id,
            "render_sha256":
                candidate["render_sha256"],
            "publish_result_id":
                publish["publish_result_id"],
            "publish_result_digest":
                publish["publish_result_digest"],
            "provider_receipt_digest":
                publish["provenance"][
                    "provider_receipt_digest"
                ],
            "platform": publish["platform"],
            "post_id": publish["post_id"],
            "metric_snapshot_digest": (
                None
                if snapshot is None
                else snapshot["snapshot_digest"]
            ),
            "metric_window": (
                None
                if snapshot is None
                else dict(snapshot["window"])
            ),
            "normalized_metrics": (
                None
                if snapshot is None
                else dict(
                    snapshot["normalized_metrics"]
                )
            ),
            "freshness": status["freshness"],
            "collector_state":
                status["collector_state"],
            "lag_seconds": status["lag_seconds"],
            "eligible_live_evidence": fresh,
            "evidence_basis": {
                "kind": kind,
                "experiment_id":
                    basis["experiment_id"],
                "assignment_digest":
                    basis["assignment_digest"],
            },
            "observational": True,
            "causal_claim_allowed": (
                kind == "randomized"
            ),
            "interpretation": (
                "Platform metrics are observed live performance. "
                "Observational samples are descriptive and not causal; "
                "randomized assignment metadata permits experiment-aware "
                "interpretation but this decision contract does not estimate causal lift."
            ),
        })
    return tuple(
        sorted(
            result,
            key=lambda item: item["candidate_id"],
        )
    )


def _historical_context(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(rows, Sequence)
        or isinstance(rows, (str, bytes))
    ):
        raise CandidateDecisionError(
            "historical_metrics must be sequence"
        )
    result = []
    seen: set[str] = set()
    for raw in rows:
        if (
            not isinstance(raw, Mapping)
            or set(raw)
            != {
                "metric_snapshot",
                "interpretation",
                "causal",
            }
        ):
            raise CandidateDecisionError(
                "historical metric context fields invalid"
            )
        if raw["causal"] is not False:
            raise CandidateDecisionCausalMisuse(
                "historical metrics cannot be represented as causal evidence"
            )
        if raw["interpretation"] != HISTORICAL_INTERPRETATION:
            raise CandidateDecisionCausalMisuse(
                "historical metrics must be explicitly directional and non-causal"
            )
        try:
            snapshot = parse_metric_snapshot(
                raw["metric_snapshot"]
            )
        except ValueError as exc:
            raise CandidateDecisionError(
                "invalid historical metric snapshot"
            ) from exc
        digest = snapshot["snapshot_digest"]
        if digest in seen:
            continue
        seen.add(digest)
        result.append({
            "snapshot_digest": digest,
            "source_class":
                snapshot["source_class"],
            "platform": snapshot["platform"],
            "post_id": snapshot["post_id"],
            "cycle_revision":
                snapshot["cycle_revision"],
            "normalized_metrics":
                dict(snapshot["normalized_metrics"]),
            "interpretation":
                HISTORICAL_INTERPRETATION,
            "causal": False,
            "winner_eligible": False,
        })
    return tuple(
        sorted(
            result,
            key=lambda item: item["snapshot_digest"],
        )
    )


def _objective_evidence(
    candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    by_candidate = {}
    for candidate_id, candidate in candidates.items():
        failures = candidate["critic_export"][
            "hard_failure_observations"
        ]
        by_candidate[candidate_id] = {
            "count": len(failures),
            "failures": json.loads(
                canonical_json(failures)
            ),
        }
    return {
        "kind": "objective_defects",
        "by_candidate": by_candidate,
        "human_ground_truth": False,
    }


def _rule_evidence(
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    policy: CandidateDecisionPolicy,
) -> dict[str, Any]:
    comparable = []
    for dimension in BENCHMARK_DIMENSIONS:
        rows = {}
        eligible = True
        for candidate_id, candidate in candidates.items():
            observation = candidate["critic_export"][
                "dimension_observations"
            ][dimension]["rule_observation"]
            if (
                observation["available"] is not True
                or observation["normalized_score"] is None
                or observation["confidence"]
                < policy.min_rule_confidence
            ):
                eligible = False
                break
            rows[candidate_id] = {
                "score":
                    observation["normalized_score"],
                "confidence":
                    observation["confidence"],
            }
        if eligible:
            comparable.append({
                "dimension": dimension,
                "candidates": rows,
            })
    candidate_scores = {}
    for candidate_id in sorted(candidates):
        weighted = []
        for dimension in comparable:
            item = dimension["candidates"][
                candidate_id
            ]
            weighted.append(
                item["score"] * item["confidence"]
            )
        candidate_scores[candidate_id] = (
            None
            if not weighted
            else round(fmean(weighted), 8)
        )
    return {
        "kind": "structural_rule",
        "comparable_dimension_count":
            len(comparable),
        "comparable_dimensions": comparable,
        "candidate_scores": candidate_scores,
        "minimum_required":
            policy.min_comparable_dimensions,
        "minimum_confidence":
            policy.min_rule_confidence,
        "winner_margin":
            policy.winner_margin,
        "human_ground_truth": False,
    }


def _nonhuman_pairwise(
    candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    render_to_id = {
        candidate["render_sha256"]: candidate_id
        for candidate_id, candidate in candidates.items()
    }
    observations = []
    by_pair: dict[
        tuple[str, str],
        list[tuple[str, str]],
    ] = defaultdict(list)
    seen_digests: set[str] = set()
    for candidate in candidates.values():
        pair = candidate["critic_export"][
            "pairwise_if_used"
        ]
        if pair is None:
            continue
        a_render = pair[
            "candidate_a_render_sha256"
        ]
        b_render = pair[
            "candidate_b_render_sha256"
        ]
        if (
            a_render not in render_to_id
            or b_render not in render_to_id
        ):
            raise CandidateDecisionError(
                "pairwise critic references render outside candidate set"
            )
        digest = pair["comparison_digest"]
        if digest in seen_digests:
            continue
        seen_digests.add(digest)
        a_id = render_to_id[a_render]
        b_id = render_to_id[b_render]
        selection = pair["selection"]
        selected_id = None
        if selection == "A":
            selected_id = a_id
        elif selection == "B":
            selected_id = b_id
        observation = {
            "pair": sorted((a_id, b_id)),
            "candidate_a": a_id,
            "candidate_b": b_id,
            "selection": selection,
            "selected_candidate_id":
                selected_id,
            "reason": pair["reason"],
            "comparison_digest": digest,
            "contract_version":
                pair["contract_version"],
            "confidence": pair.get("confidence"),
            "advisory_only": True,
            "human_label": False,
            "human_ground_truth": False,
        }
        observations.append(observation)
        if selected_id is not None:
            pair_key = tuple(
                sorted((a_id, b_id))
            )
            by_pair[pair_key].append(
                (digest, selected_id)
            )
    contradictions = []
    for pair_key, selections in sorted(
        by_pair.items()
    ):
        chosen = {
            selected
            for _, selected in selections
        }
        if len(chosen) > 1:
            contradictions.append({
                "pair": list(pair_key),
                "comparison_digests": [
                    digest
                    for digest, _ in selections
                ],
                "selected_candidates":
                    sorted(chosen),
            })
    return {
        "kind": "model_and_pairwise_advisory",
        "observations": sorted(
            observations,
            key=lambda item:
                item["comparison_digest"],
        ),
        "contradictions": contradictions,
        "has_contradiction": bool(
            contradictions
        ),
        "human_ground_truth": False,
    }


def _human_evidence(
    rows: Sequence[Mapping[str, Any]],
    *,
    candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if (
        not isinstance(rows, Sequence)
        or isinstance(rows, (str, bytes))
    ):
        raise CandidateDecisionError(
            "human_pairwise_evidence must be sequence"
        )
    render_to_id = {
        candidate["render_sha256"]: candidate_id
        for candidate_id, candidate in candidates.items()
    }
    labels = []
    pair_preferences: dict[
        tuple[str, str],
        set[str],
    ] = defaultdict(set)
    seen: set[str] = set()
    for raw in rows:
        if (
            not isinstance(raw, Mapping)
            or set(raw)
            != {"comparison", "label"}
        ):
            raise CandidateDecisionError(
                "human evidence row fields invalid"
            )
        try:
            comparison = parse_pairwise_comparison(
                raw["comparison"]
            )
            label = parse_human_pairwise_label(
                raw["label"],
                comparison=comparison,
            )
        except VisualCriticError as exc:
            raise CandidateDecisionError(
                "invalid human pairwise evidence"
            ) from exc
        if label["label_id"] in seen:
            continue
        seen.add(label["label_id"])
        a_render = comparison["candidate_a"][
            "artifact_sha256"
        ]
        b_render = comparison["candidate_b"][
            "artifact_sha256"
        ]
        if (
            a_render not in render_to_id
            or b_render not in render_to_id
        ):
            raise CandidateDecisionError(
                "human label comparison references render outside candidate set"
            )
        a_id = render_to_id[a_render]
        b_id = render_to_id[b_render]
        preference = label["preference"]
        selected = (
            a_id
            if preference == "A"
            else (
                b_id
                if preference == "B"
                else "tie"
            )
        )
        pair_key = tuple(
            sorted((a_id, b_id))
        )
        pair_preferences[pair_key].add(
            selected
        )
        labels.append({
            "label_id": label["label_id"],
            "source_kind": "human_provided",
            "comparison_id":
                label["comparison_id"],
            "comparison_digest":
                label["comparison_digest"],
            "candidate_a": a_id,
            "candidate_b": b_id,
            "preference": preference,
            "selected_candidate_id": (
                None
                if selected == "tie"
                else selected
            ),
            "observed_at":
                label["observed_at"],
            "annotator_ref":
                label["annotator_ref"],
            "provenance_digest":
                label["provenance_digest"],
            "human_ground_truth": True,
        })
    contradictions = []
    for pair_key, preferences in sorted(
        pair_preferences.items()
    ):
        if len(preferences) > 1:
            contradictions.append({
                "pair": list(pair_key),
                "preferences":
                    sorted(preferences),
            })
    return {
        "kind": "actual_human_labels",
        "labels": sorted(
            labels,
            key=lambda item: item["label_id"],
        ),
        "contradictions": contradictions,
        "has_contradiction": bool(
            contradictions
        ),
        "human_ground_truth": (
            True if labels else None
        ),
    }


def _human_condorcet_winner(
    human: Mapping[str, Any],
    candidate_ids: Sequence[str],
) -> str | None:
    if human["has_contradiction"]:
        return None
    labels = human["labels"]
    if not labels:
        return None
    wins: dict[str, set[str]] = {
        candidate_id: set()
        for candidate_id in candidate_ids
    }
    covered: set[tuple[str, str]] = set()
    for label in labels:
        pair = tuple(
            sorted((
                label["candidate_a"],
                label["candidate_b"],
            ))
        )
        covered.add(pair)
        selected = label[
            "selected_candidate_id"
        ]
        if selected is not None:
            loser = (
                label["candidate_b"]
                if selected
                == label["candidate_a"]
                else label["candidate_a"]
            )
            wins[selected].add(loser)
    expected_pairs = (
        len(candidate_ids)
        * (len(candidate_ids) - 1)
        // 2
    )
    if len(covered) != expected_pairs:
        return None
    winners = [
        candidate_id
        for candidate_id, defeated in wins.items()
        if len(defeated)
        == len(candidate_ids) - 1
    ]
    return (
        winners[0]
        if len(winners) == 1
        else None
    )


def _reedit_guidance(
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    winner_candidate_id: str | None,
) -> list[dict[str, Any]]:
    guidance = []
    seen: set[tuple[Any, ...]] = set()
    for candidate_id in sorted(candidates):
        if candidate_id == winner_candidate_id:
            continue
        critic = candidates[candidate_id][
            "critic_export"
        ]
        for hard in critic[
            "hard_failure_observations"
        ]:
            if hard["time_ms"] is None:
                continue
            key = (
                candidate_id,
                "objective_hard_failure",
                hard["code"],
                hard["time_ms"],
            )
            if key in seen:
                continue
            seen.add(key)
            guidance.append({
                "candidate_id": candidate_id,
                "evidence_class":
                    "objective_defect",
                "defect_or_dimension":
                    hard["code"],
                "start_ms": hard["time_ms"],
                "end_ms": hard["time_ms"],
                "directive": (
                    "Correct the objective failure at this exact segment before reconsidering the render."
                ),
                "evidence_note":
                    hard["message"],
            })
        for item in critic["timecoded_evidence"]:
            if item["severity"] not in {
                "warning",
                "hard_failure",
            }:
                continue
            dimension = item[
                "source_dimension"
            ]
            start = item["start_ms"]
            end = item["end_ms"]
            if start is None:
                continue
            key = (
                candidate_id,
                item["evidence_kind"],
                dimension,
                start,
                end,
                item["note"],
            )
            if key in seen:
                continue
            seen.add(key)
            guidance.append({
                "candidate_id": candidate_id,
                "evidence_class": (
                    "model_aesthetic_judgment"
                    if item["evidence_kind"]
                    == "vlm_opinion"
                    else "structural_editorial_judgment"
                ),
                "defect_or_dimension":
                    dimension,
                "start_ms": start,
                "end_ms": end,
                "directive": _DIRECTIVES[
                    dimension
                ],
                "evidence_note":
                    item["note"],
            })
    return guidance


def _decision_outcome(
    *,
    expected_ids: Sequence[str],
    candidates: Mapping[str, Mapping[str, Any]],
    missing: Sequence[str],
    objective: Mapping[str, Any],
    rules: Mapping[str, Any],
    pairwise: Mapping[str, Any],
    human: Mapping[str, Any],
    policy: CandidateDecisionPolicy,
) -> tuple[str, str | None, str, list[str]]:
    gates: list[str] = []
    if missing:
        return (
            "insufficient_evidence",
            None,
            "missing_expected_candidates",
            [
                "all_expected_candidates_present=false",
            ],
        )
    candidate_ids = tuple(
        sorted(candidates)
    )
    counts = {
        candidate_id:
            objective["by_candidate"][
                candidate_id
            ]["count"]
        for candidate_id in candidate_ids
    }
    min_count = min(counts.values())
    best = [
        candidate_id
        for candidate_id, count in counts.items()
        if count == min_count
    ]
    if (
        min_count == 0
        and len(best) == 1
        and any(
            count > 0
            for count in counts.values()
        )
    ):
        gates.extend([
            "all_expected_candidates_present=true",
            "unique_zero_hard_failure_candidate=true",
        ])
        return (
            "winner",
            best[0],
            "objective_hard_failure_advantage",
            gates,
        )
    if min_count > 0:
        return (
            "insufficient_evidence",
            None,
            "all_candidates_have_objective_hard_failures",
            [
                "all_expected_candidates_present=true",
                "all_candidates_defective=true",
            ],
        )

    human_winner = _human_condorcet_winner(
        human,
        candidate_ids,
    )
    if human_winner is not None:
        gates.extend([
            "all_expected_candidates_present=true",
            "human_pairwise_complete=true",
            "human_pairwise_noncontradictory=true",
            "human_condorcet_winner=true",
        ])
        return (
            "winner",
            human_winner,
            "actual_human_pairwise_preference",
            gates,
        )
    if human["has_contradiction"]:
        return (
            "tie",
            None,
            "contradictory_human_labels",
            [
                "human_pairwise_noncontradictory=false",
            ],
        )
    if pairwise["has_contradiction"]:
        return (
            "tie",
            None,
            "contradictory_nonhuman_critics",
            [
                "nonhuman_pairwise_noncontradictory=false",
            ],
        )
    if (
        rules["comparable_dimension_count"]
        < policy.min_comparable_dimensions
    ):
        return (
            "insufficient_evidence",
            None,
            "too_few_comparable_rule_dimensions",
            [
                (
                    "comparable_rule_dimensions="
                    + str(
                        rules[
                            "comparable_dimension_count"
                        ]
                    )
                ),
            ],
        )
    ranked = sorted(
        (
            (score, candidate_id)
            for candidate_id, score in
            rules["candidate_scores"].items()
            if score is not None
        ),
        key=lambda row: (-row[0], row[1]),
    )
    if len(ranked) < 2:
        return (
            "insufficient_evidence",
            None,
            "rule_scores_unavailable",
            [],
        )
    margin = round(
        ranked[0][0] - ranked[1][0],
        8,
    )
    if margin < policy.winner_margin:
        return (
            "tie",
            None,
            "rule_score_margin_below_policy",
            [
                f"rule_margin={margin}",
                (
                    "required_margin="
                    + str(policy.winner_margin)
                ),
            ],
        )
    gates.extend([
        "all_expected_candidates_present=true",
        "objective_hard_failures_tied_at_zero=true",
        "nonhuman_pairwise_noncontradictory=true",
        (
            "comparable_rule_dimensions="
            + str(
                rules[
                    "comparable_dimension_count"
                ]
            )
        ),
        f"rule_margin={margin}",
    ])
    return (
        "winner",
        ranked[0][1],
        "structural_rule_margin",
        gates,
    )


def build_candidate_decision(
    *,
    campaign_id: str,
    source_id: str,
    source_sha256: str,
    cycle_revision: int,
    decision_revision: int,
    expected_candidate_ids: Sequence[str],
    candidates: Sequence[Mapping[str, Any]],
    live_metric_evidence: Sequence[
        Mapping[str, Any]
    ] = (),
    human_pairwise_evidence: Sequence[
        Mapping[str, Any]
    ] = (),
    historical_metrics: Sequence[
        Mapping[str, Any]
    ] = (),
    policy: CandidateDecisionPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or CandidateDecisionPolicy()
    _nonempty(campaign_id, "campaign_id")
    _nonempty(source_id, "source_id")
    _digest(source_sha256, "source_sha256")
    cycle_revision = _positive_int(
        cycle_revision,
        "cycle_revision",
    )
    decision_revision = _positive_int(
        decision_revision,
        "decision_revision",
    )
    parsed, missing = _candidate_index(
        candidates,
        expected_candidate_ids=
            expected_candidate_ids,
        source_id=source_id,
        source_sha256=source_sha256,
    )
    objective = _objective_evidence(parsed)
    rules = _rule_evidence(
        parsed,
        policy=policy,
    )
    pairwise = _nonhuman_pairwise(parsed)
    human = _human_evidence(
        human_pairwise_evidence,
        candidates=parsed,
    )
    live = _live_observations(
        live_metric_evidence,
        candidates=parsed,
        cycle_revision=cycle_revision,
        policy=policy,
    )
    historical = _historical_context(
        historical_metrics
    )
    decision, winner, reason, gates = (
        _decision_outcome(
            expected_ids=tuple(
                expected_candidate_ids
            ),
            candidates=parsed,
            missing=missing,
            objective=objective,
            rules=rules,
            pairwise=pairwise,
            human=human,
            policy=policy,
        )
    )
    live_by_candidate = {
        item["candidate_id"]: item
        for item in live
    }
    live_coverage = {
        candidate_id: (
            live_by_candidate.get(
                candidate_id,
                {},
            ).get(
                "eligible_live_evidence",
                False,
            )
        )
        for candidate_id in sorted(parsed)
    }
    if winner is not None:
        selected_live = live_by_candidate.get(
            winner
        )
        if selected_live is not None:
            gates.append(
                (
                    "winner_live_metric_evidence="
                    + (
                        "eligible"
                        if selected_live[
                            "eligible_live_evidence"
                        ]
                        else "present_but_not_fresh"
                    )
                )
            )
    evidence = {
        "objective_defects": objective,
        "structural_rule_judgment": rules,
        "model_aesthetic_judgment": {
            **pairwise,
            "per_candidate_vlm": {
                candidate_id: [
                    {
                        "rubric_dimension":
                            dimension,
                        "observations":
                            candidate[
                                "critic_export"
                            ][
                                "dimension_observations"
                            ][dimension][
                                "vlm_observations"
                            ],
                    }
                    for dimension in
                    BENCHMARK_DIMENSIONS
                    if candidate[
                        "critic_export"
                    ][
                        "dimension_observations"
                    ][dimension][
                        "vlm_observations"
                    ]
                ]
                for candidate_id, candidate
                in sorted(parsed.items())
            },
        },
        "human_labels": human,
        "live_platform_observations":
            list(live),
        "historical_metric_context":
            list(historical),
    }
    candidate_refs = [
        {
            "candidate_id": candidate_id,
            "source_id": source_id,
            "source_sha256":
                candidate["source_sha256"],
            "render_sha256":
                candidate["render_sha256"],
            "critic_export_commit_sha":
                candidate["critic_export"][
                    "commit_sha"
                ],
            "critic_mode":
                candidate["critic_export"][
                    "critic_mode"
                ],
        }
        for candidate_id, candidate
        in sorted(parsed.items())
    ]
    material = {
        "contract_version":
            CANDIDATE_DECISION_VERSION,
        "decision_id": "",
        "decision_digest": "",
        "campaign_id": campaign_id,
        "source_id": source_id,
        "source_sha256": source_sha256,
        "cycle_revision": cycle_revision,
        "decision_revision": decision_revision,
        "expected_candidate_ids":
            list(expected_candidate_ids),
        "candidate_refs": candidate_refs,
        "missing_candidate_ids":
            list(missing),
        "decision": decision,
        "winner_candidate_id": winner,
        "reason": reason,
        "evidence_policy": {
            "min_candidates":
                policy.min_candidates,
            "max_candidates":
                policy.max_candidates,
            "min_comparable_dimensions":
                policy.min_comparable_dimensions,
            "min_rule_confidence":
                policy.min_rule_confidence,
            "winner_margin":
                policy.winner_margin,
            "live_metric_freshness_required":
                policy.metric_freshness_required,
            "model_score_is_human_preference":
                False,
            "synthetic_metric_is_live_evidence":
                False,
            "historical_metric_causal":
                False,
        },
        "evidence": evidence,
        "live_metric_eligibility":
            live_coverage,
        "policy_gates": gates,
        "reedit_guidance":
            _reedit_guidance(
                parsed,
                winner_candidate_id=winner,
            ),
        "authority": {
            "advisory_only": True,
            "publish_authorized": False,
            "provider_mutation": False,
            "creator_mutation": False,
            "media_mutation": False,
            "reedit_authorized": False,
        },
        "interpretation": (
            "Candidate decisions separate objective defects, deterministic "
            "editorial rules, model/VLM opinion, actual human labels, and "
            "live platform observations. Historical and observational "
            "platform metrics are directional/descriptive and are never "
            "reported as causal lift."
        ),
    }
    material["decision_id"] = "gcd1:" + sha256_json({
        "campaign_id": campaign_id,
        "source_id": source_id,
        "source_sha256": source_sha256,
        "cycle_revision": cycle_revision,
        "decision_revision": decision_revision,
        "expected_candidate_ids":
            list(expected_candidate_ids),
    })
    digest_material = dict(material)
    digest_material["decision_digest"] = ""
    material["decision_digest"] = sha256_json(
        digest_material
    )
    return parse_candidate_decision(material)


def parse_candidate_decision(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "contract_version",
        "decision_id",
        "decision_digest",
        "campaign_id",
        "source_id",
        "source_sha256",
        "cycle_revision",
        "decision_revision",
        "expected_candidate_ids",
        "candidate_refs",
        "missing_candidate_ids",
        "decision",
        "winner_candidate_id",
        "reason",
        "evidence_policy",
        "evidence",
        "live_metric_eligibility",
        "policy_gates",
        "reedit_guidance",
        "authority",
        "interpretation",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != expected
    ):
        raise CandidateDecisionError(
            "candidate decision fields must match v1 exactly"
        )
    if (
        payload["contract_version"]
        != CANDIDATE_DECISION_VERSION
    ):
        raise CandidateDecisionError(
            "unsupported candidate decision version"
        )
    _nonempty(
        payload["campaign_id"],
        "campaign_id",
    )
    _nonempty(payload["source_id"], "source_id")
    _digest(
        payload["source_sha256"],
        "source_sha256",
    )
    cycle_revision = _positive_int(
        payload["cycle_revision"],
        "cycle_revision",
    )
    decision_revision = _positive_int(
        payload["decision_revision"],
        "decision_revision",
    )
    expected_ids = payload[
        "expected_candidate_ids"
    ]
    if (
        not isinstance(expected_ids, list)
        or not 2 <= len(expected_ids) <= 4
        or len(set(expected_ids))
        != len(expected_ids)
        or any(
            not isinstance(value, str)
            or not value
            for value in expected_ids
        )
    ):
        raise CandidateDecisionError(
            "expected candidate ids invalid"
        )
    refs = payload["candidate_refs"]
    if not isinstance(refs, list):
        raise CandidateDecisionError(
            "candidate_refs must be array"
        )
    seen_ids: set[str] = set()
    seen_renders: set[str] = set()
    for ref in refs:
        if (
            not isinstance(ref, Mapping)
            or set(ref)
            != {
                "candidate_id",
                "source_id",
                "source_sha256",
                "render_sha256",
                "critic_export_commit_sha",
                "critic_mode",
            }
        ):
            raise CandidateDecisionError(
                "candidate ref fields invalid"
            )
        candidate_id = _nonempty(
            ref["candidate_id"],
            "candidate_ref.candidate_id",
        )
        if (
            candidate_id in seen_ids
            or candidate_id not in expected_ids
        ):
            raise CandidateDecisionError(
                "candidate ref identity invalid"
            )
        seen_ids.add(candidate_id)
        if ref["source_id"] != payload["source_id"]:
            raise CandidateDecisionError(
                "candidate ref source id mismatch"
            )
        if (
            _digest(
                ref["source_sha256"],
                "candidate_ref.source_sha256",
            )
            != payload["source_sha256"]
        ):
            raise CandidateDecisionError(
                "candidate ref source hash mismatch"
            )
        render = _digest(
            ref["render_sha256"],
            "candidate_ref.render_sha256",
        )
        if render in seen_renders:
            raise CandidateDecisionError(
                "duplicate render in candidate refs"
            )
        seen_renders.add(render)
        commit_sha = ref[
            "critic_export_commit_sha"
        ]
        if (
            not isinstance(commit_sha, str)
            or len(commit_sha) != 40
        ):
            raise CandidateDecisionError(
                "critic export commit SHA invalid"
            )
        _nonempty(
            ref["critic_mode"],
            "candidate_ref.critic_mode",
        )
    missing = payload["missing_candidate_ids"]
    if (
        not isinstance(missing, list)
        or missing
        != [
            candidate_id
            for candidate_id in expected_ids
            if candidate_id not in seen_ids
        ]
    ):
        raise CandidateDecisionError(
            "missing candidate list mismatch"
        )
    if payload["decision"] not in DECISIONS:
        raise CandidateDecisionError(
            "decision state invalid"
        )
    winner = payload["winner_candidate_id"]
    if payload["decision"] == "winner":
        if (
            winner not in seen_ids
            or missing
        ):
            raise CandidateDecisionError(
                "winner requires present complete candidate set"
            )
    elif winner is not None:
        raise CandidateDecisionError(
            "tie/insufficient decision cannot name winner"
        )
    _nonempty(payload["reason"], "reason")
    policy = payload["evidence_policy"]
    if (
        not isinstance(policy, Mapping)
        or policy.get(
            "model_score_is_human_preference"
        ) is not False
        or policy.get(
            "synthetic_metric_is_live_evidence"
        ) is not False
        or policy.get(
            "historical_metric_causal"
        ) is not False
    ):
        raise CandidateDecisionError(
            "evidence policy separation invalid"
        )
    evidence = payload["evidence"]
    if (
        not isinstance(evidence, Mapping)
        or set(evidence)
        != {
            "objective_defects",
            "structural_rule_judgment",
            "model_aesthetic_judgment",
            "human_labels",
            "live_platform_observations",
            "historical_metric_context",
        }
    ):
        raise CandidateDecisionError(
            "evidence classes invalid"
        )
    if evidence[
        "objective_defects"
    ].get("human_ground_truth") is not False:
        raise CandidateDecisionError(
            "objective defects cannot be human labels"
        )
    if evidence[
        "structural_rule_judgment"
    ].get("human_ground_truth") is not False:
        raise CandidateDecisionError(
            "rule judgment cannot be human preference"
        )
    model = evidence[
        "model_aesthetic_judgment"
    ]
    if model.get("human_ground_truth") is not False:
        raise CandidateDecisionError(
            "model aesthetic judgment cannot be human preference"
        )
    human = evidence["human_labels"]
    if (
        human.get("labels")
        and human.get("human_ground_truth")
        is not True
    ):
        raise CandidateDecisionError(
            "actual human labels must remain explicitly human"
        )
    if (
        not human.get("labels")
        and human.get("human_ground_truth")
        is not None
    ):
        raise CandidateDecisionError(
            "empty human evidence cannot claim ground truth"
        )
    live = evidence[
        "live_platform_observations"
    ]
    if not isinstance(live, list):
        raise CandidateDecisionError(
            "live platform observations must be array"
        )
    for item in live:
        if (
            item.get("observational") is not True
            or item.get(
                "candidate_id"
            ) not in seen_ids
        ):
            raise CandidateDecisionError(
                "live platform observation invalid"
            )
        if (
            item["evidence_basis"]["kind"]
            == "observational"
            and item["causal_claim_allowed"]
            is not False
        ):
            raise CandidateDecisionCausalMisuse(
                "observational live metric cannot be causal"
            )
    historical = evidence[
        "historical_metric_context"
    ]
    if not isinstance(historical, list):
        raise CandidateDecisionError(
            "historical context must be array"
        )
    for item in historical:
        if (
            item.get("causal") is not False
            or item.get("winner_eligible")
            is not False
            or item.get("interpretation")
            != HISTORICAL_INTERPRETATION
        ):
            raise CandidateDecisionCausalMisuse(
                "historical metric context became causal/winner evidence"
            )
    eligibility = payload[
        "live_metric_eligibility"
    ]
    if (
        not isinstance(eligibility, Mapping)
        or set(eligibility) != seen_ids
        or any(
            not isinstance(value, bool)
            for value in eligibility.values()
        )
    ):
        raise CandidateDecisionError(
            "live metric eligibility invalid"
        )
    if not isinstance(
        payload["policy_gates"],
        list,
    ):
        raise CandidateDecisionError(
            "policy_gates must be array"
        )
    guidance = payload["reedit_guidance"]
    if not isinstance(guidance, list):
        raise CandidateDecisionError(
            "reedit_guidance must be array"
        )
    for item in guidance:
        if (
            not isinstance(item, Mapping)
            or set(item)
            != {
                "candidate_id",
                "evidence_class",
                "defect_or_dimension",
                "start_ms",
                "end_ms",
                "directive",
                "evidence_note",
            }
        ):
            raise CandidateDecisionError(
                "reedit guidance fields invalid"
            )
        if item["candidate_id"] not in seen_ids:
            raise CandidateDecisionError(
                "reedit guidance candidate invalid"
            )
        if (
            isinstance(item["start_ms"], bool)
            or not isinstance(
                item["start_ms"],
                int,
            )
            or item["start_ms"] < 0
            or isinstance(item["end_ms"], bool)
            or not isinstance(
                item["end_ms"],
                int,
            )
            or item["end_ms"]
            < item["start_ms"]
        ):
            raise CandidateDecisionError(
                "reedit guidance segment invalid"
            )
        _nonempty(
            item["directive"],
            "reedit directive",
        )
        _nonempty(
            item["evidence_note"],
            "reedit evidence note",
        )
    if payload["authority"] != {
        "advisory_only": True,
        "publish_authorized": False,
        "provider_mutation": False,
        "creator_mutation": False,
        "media_mutation": False,
        "reedit_authorized": False,
    }:
        raise CandidateDecisionError(
            "candidate decision authority boundary invalid"
        )
    expected_id = "gcd1:" + sha256_json({
        "campaign_id":
            payload["campaign_id"],
        "source_id": payload["source_id"],
        "source_sha256":
            payload["source_sha256"],
        "cycle_revision": cycle_revision,
        "decision_revision":
            decision_revision,
        "expected_candidate_ids":
            expected_ids,
    })
    if payload["decision_id"] != expected_id:
        raise CandidateDecisionError(
            "candidate decision identity mismatch"
        )
    _digest(
        payload["decision_digest"],
        "decision_digest",
    )
    material = dict(payload)
    material["decision_digest"] = ""
    if sha256_json(material) != payload[
        "decision_digest"
    ]:
        raise CandidateDecisionError(
            "candidate decision digest mismatch"
        )
    return json.loads(
        canonical_json(dict(payload))
    )


class CandidateDecisionLedger:
    """Append-only exactly-once decision history by campaign/source/cycle."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows: list[dict[str, Any]] = []
        self._latest: dict[
            tuple[str, str, int],
            dict[str, Any],
        ] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_number, line in enumerate(
            self.path.read_text(
                encoding="utf-8"
            ).splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CandidateDecisionConflictError(
                    f"invalid decision ledger JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "decision",
                }
                or row["ledger_version"]
                != CANDIDATE_DECISION_LEDGER_VERSION
                or row["sequence"]
                != len(self._rows) + 1
            ):
                raise CandidateDecisionConflictError(
                    "decision ledger row invalid"
                )
            decision = parse_candidate_decision(
                row["decision"]
            )
            key = (
                decision["campaign_id"],
                decision["source_id"],
                decision["cycle_revision"],
            )
            previous = self._latest.get(key)
            if previous is not None:
                if (
                    decision["decision_revision"]
                    <= previous[
                        "decision_revision"
                    ]
                ):
                    raise CandidateDecisionConflictError(
                        "decision ledger revisions not strictly increasing"
                    )
            self._latest[key] = decision
            self._rows.append(dict(row))

    def record(
        self,
        decision: Mapping[str, Any],
    ) -> str:
        parsed = parse_candidate_decision(
            decision
        )
        key = (
            parsed["campaign_id"],
            parsed["source_id"],
            parsed["cycle_revision"],
        )
        previous = self._latest.get(key)
        if previous is not None:
            if (
                parsed["decision_revision"]
                < previous["decision_revision"]
            ):
                raise OutOfOrderCandidateDecision(
                    "candidate decision revision is older than durable state"
                )
            if (
                parsed["decision_revision"]
                == previous["decision_revision"]
            ):
                if (
                    parsed["decision_digest"]
                    != previous["decision_digest"]
                ):
                    raise CandidateDecisionConflictError(
                        "same decision revision changed payload"
                    )
                return "duplicate"
        row = {
            "ledger_version":
                CANDIDATE_DECISION_LEDGER_VERSION,
            "sequence": len(self._rows) + 1,
            "decision": parsed,
        }
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(
                canonical_json(row) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        self._rows.append(row)
        self._latest[key] = parsed
        return "accepted"

    def latest(
        self,
        *,
        campaign_id: str,
        source_id: str,
        cycle_revision: int,
    ) -> dict[str, Any] | None:
        row = self._latest.get(
            (
                campaign_id,
                source_id,
                cycle_revision,
            )
        )
        return (
            None
            if row is None
            else json.loads(
                canonical_json(row)
            )
        )
