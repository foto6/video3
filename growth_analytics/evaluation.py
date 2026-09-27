from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, replace
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

from .core import AnalyticsEvent, AggregateMetrics, build_creator_feedback, deterministic_score
from .engine import CAUSALITY_NOTICE, TimeWindow, aggregate_events, calibrated_score_uncertainty
from .event_stream import parse_timestamp
from .reliability import canonical_event_set, finalize_window_snapshot


EVALUATION_VERSION = "growth.offline_evaluation.v1"
SPLIT_VERSION = "growth.offline_split.v1"
DEFAULT_BOOTSTRAP_SEED = 6102026
DEFAULT_BOOTSTRAP_RESAMPLES = 1000


class EvaluationError(ValueError):
    pass


@dataclass(frozen=True)
class EvaluationSplit:
    split_version: str
    historical_identities: tuple[str, ...]
    replay_identities: tuple[str, ...]
    historical_event_keys: tuple[str, ...]
    replay_event_keys: tuple[str, ...]
    split_digest: str


@dataclass(frozen=True)
class BootstrapBand:
    estimate: float
    lower: float
    upper: float
    seed: int
    resamples: int
    method: str
    causal: bool
    interpretation: str


@dataclass(frozen=True)
class EvaluationGate:
    status: str
    reasons: tuple[str, ...]
    auto_publish: bool
    causal: bool
    interpretation: str


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _identity(campaign_id: str, window: TimeWindow) -> str:
    return f"{campaign_id}|{window.label}|{window.start}|{window.end}"


def _window(raw: Mapping[str, Any]) -> TimeWindow:
    return TimeWindow(str(raw["label"]), str(raw["start"]), str(raw["end"]))


def build_historical_replay_split(
    fixture: Mapping[str, Any],
    events: Iterable[AnalyticsEvent],
    *,
    historical_windows_per_campaign: int = 2,
) -> EvaluationSplit:
    if (
        isinstance(historical_windows_per_campaign, bool)
        or not isinstance(historical_windows_per_campaign, int)
        or historical_windows_per_campaign < 1
    ):
        raise EvaluationError("historical_windows_per_campaign must be >= 1")
    canonical = canonical_event_set(events)
    historical_ids: list[str] = []
    replay_ids: list[str] = []
    historical_keys: set[str] = set()
    replay_keys: set[str] = set()
    allowed_lateness = int(fixture["allowed_lateness_seconds"])

    for campaign in sorted(fixture["campaigns"], key=lambda item: item["campaign_id"]):
        windows = sorted(
            (_window(raw) for raw in campaign["windows"]),
            key=lambda item: parse_timestamp(item.start),
        )
        if len(windows) <= historical_windows_per_campaign:
            raise EvaluationError(
                f"campaign {campaign['campaign_id']} has no replay holdout windows"
            )
        raw_by_label = {raw["label"]: raw for raw in campaign["windows"]}
        campaign_events = tuple(
            event for event in canonical if event.channel_id == campaign["channel_id"]
        )
        for index, window in enumerate(windows):
            raw = raw_by_label[window.label]
            snapshot = finalize_window_snapshot(
                campaign_id=campaign["campaign_id"],
                window=window,
                events=campaign_events,
                watermark=raw["watermark"],
                allowed_lateness_seconds=allowed_lateness,
            )
            identity = _identity(campaign["campaign_id"], window)
            if identity in historical_ids or identity in replay_ids:
                raise EvaluationError(
                    f"duplicate finalized campaign/window identity: {identity}"
                )
            target_ids = historical_ids if index < historical_windows_per_campaign else replay_ids
            target_keys = historical_keys if index < historical_windows_per_campaign else replay_keys
            target_ids.append(identity)
            target_keys.update(snapshot.event_keys)

    overlap_ids = set(historical_ids) & set(replay_ids)
    overlap_events = historical_keys & replay_keys
    if overlap_ids or overlap_events:
        raise EvaluationError("historical/replay split leakage detected")

    material = {
        "split_version": SPLIT_VERSION,
        "historical_identities": sorted(historical_ids),
        "replay_identities": sorted(replay_ids),
        "historical_event_keys": sorted(historical_keys),
        "replay_event_keys": sorted(replay_keys),
    }
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return EvaluationSplit(
        split_version=SPLIT_VERSION,
        historical_identities=tuple(material["historical_identities"]),
        replay_identities=tuple(material["replay_identities"]),
        historical_event_keys=tuple(material["historical_event_keys"]),
        replay_event_keys=tuple(material["replay_event_keys"]),
        split_digest=digest,
    )


def bootstrap_mean_band(
    values: Sequence[float],
    *,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
) -> BootstrapBand:
    if not values:
        return BootstrapBand(
            estimate=0.0,
            lower=0.0,
            upper=1.0,
            seed=seed,
            resamples=resamples,
            method="fixed_seed_noncausal_bootstrap_mean_v1",
            causal=False,
            interpretation=CAUSALITY_NOTICE,
        )
    if resamples < 100:
        raise EvaluationError("bootstrap resamples must be >= 100")
    rng = random.Random(seed)
    n = len(values)
    draws: list[float] = []
    for _ in range(resamples):
        draws.append(fmean(values[rng.randrange(n)] for _ in range(n)))
    draws.sort()
    lower_index = max(0, int(0.025 * (resamples - 1)))
    upper_index = min(resamples - 1, int(0.975 * (resamples - 1)))
    return BootstrapBand(
        estimate=round(fmean(values), 8),
        lower=round(draws[lower_index], 8),
        upper=round(draws[upper_index], 8),
        seed=seed,
        resamples=resamples,
        method="fixed_seed_noncausal_bootstrap_mean_v1",
        causal=False,
        interpretation=CAUSALITY_NOTICE,
    )


def pairwise_rank_agreement(left: Sequence[str], right: Sequence[str]) -> float:
    if set(left) != set(right):
        raise EvaluationError("rankings must contain the same identities")
    if len(left) < 2:
        return 1.0
    left_pos = {value: index for index, value in enumerate(left)}
    right_pos = {value: index for index, value in enumerate(right)}
    values = sorted(left_pos)
    agree = 0
    total = 0
    for index, first in enumerate(values):
        for second in values[index + 1 :]:
            total += 1
            if (left_pos[first] < left_pos[second]) == (
                right_pos[first] < right_pos[second]
            ):
                agree += 1
    return round(agree / total if total else 1.0, 8)


def recommendation_churn(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 0.0
    return round(1.0 - len(left_set & right_set) / len(union), 8)


def _variant_rows(
    *,
    campaign: Mapping[str, Any],
    events: Sequence[AnalyticsEvent],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for variant in sorted(campaign["variants"], key=lambda item: item["variant_id"]):
        variant_events = tuple(
            event
            for event in events
            if event.variant_id == variant["variant_id"]
            and event.video_id == variant["video_id"]
        )
        metrics = aggregate_events(variant_events)
        duration = float(variant["duration_seconds"])
        score = deterministic_score(metrics, duration)
        uncertainty = calibrated_score_uncertainty(metrics, duration)
        feedback = build_creator_feedback(
            content_job_id=f"offline-{campaign['campaign_id']}-{variant['variant_id']}",
            channel_id=campaign["channel_id"],
            video_id=variant["video_id"],
            variant_id=variant["variant_id"],
            metrics=metrics,
            duration_seconds=duration,
            evidence_event_ids=tuple(sorted(event.event_id for event in variant_events)),
        )
        rows[variant["variant_id"]] = {
            "metrics": metrics,
            "score": score.score,
            "uncertainty": uncertainty,
            "recommendations": tuple(feedback.recommendations),
            "event_count": len(variant_events),
        }
    return rows


def _score_ranking(rows: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(
        variant_id
        for variant_id, _ in sorted(
            rows.items(),
            key=lambda item: (
                -float(item[1]["score"]),
                float(item[1]["uncertainty"].half_width),
                item[0],
            ),
        )
    )


def _ctr_ranking(rows: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(
        variant_id
        for variant_id, _ in sorted(
            rows.items(),
            key=lambda item: (-float(item[1]["metrics"].ctr), item[0]),
        )
    )


def _drop_missing_events(
    events: Sequence[AnalyticsEvent],
    *,
    modulus: int = 5,
) -> tuple[AnalyticsEvent, ...]:
    return tuple(
        event
        for event in events
        if int(hashlib.sha256(event.idempotency_key.encode("utf-8")).hexdigest()[:8], 16)
        % modulus
        != 0
    )


def classify_evaluation_gate(
    *,
    replay_event_count: int,
    evaluated_variant_windows: int,
    coverage: float,
    ranking_stability: float,
    error_band: BootstrapBand,
    churn: float,
    missing_robustness: float,
    uncertainty_interval_coverage: float,
) -> EvaluationGate:
    reasons: list[str] = []
    if replay_event_count < 100 or evaluated_variant_windows < 12:
        status = "insufficient_evidence"
        reasons.append("holdout sample count below minimum offline evidence floor")
    else:
        if coverage < 0.90:
            reasons.append("holdout coverage below 0.90")
        if ranking_stability < 0.70:
            reasons.append("ranking stability below 0.70")
        if error_band.upper > 0.20:
            reasons.append("bootstrap score-error upper band above 0.20")
        if uncertainty_interval_coverage < 0.80:
            reasons.append("calibrated uncertainty coverage below 0.80")
        if churn > 0.60:
            reasons.append("recommendation churn above 0.60")
        if missing_robustness < 0.75:
            reasons.append("missing-event ranking robustness below 0.75")
        status = "unstable" if reasons else "stable_enough_for_experiment"
    return EvaluationGate(
        status=status,
        reasons=tuple(reasons),
        auto_publish=False,
        causal=False,
        interpretation=CAUSALITY_NOTICE,
    )


def _historical_uncertainty_scale(
    fixture: Mapping[str, Any],
    historical_events: Sequence[AnalyticsEvent],
    split: EvaluationSplit,
) -> tuple[float, tuple[float, ...]]:
    historical_identities = set(split.historical_identities)
    ratios: list[float] = []
    for campaign in sorted(fixture["campaigns"], key=lambda item: item["campaign_id"]):
        rows_by_window: list[tuple[TimeWindow, dict[str, dict[str, Any]]]] = []
        for raw_window in sorted(
            campaign["windows"], key=lambda raw: parse_timestamp(raw["start"])
        ):
            window = _window(raw_window)
            identity = _identity(campaign["campaign_id"], window)
            if identity not in historical_identities:
                continue
            window_events = tuple(
                event
                for event in historical_events
                if event.channel_id == campaign["channel_id"]
                and parse_timestamp(window.start)
                <= parse_timestamp(event.captured_at)
                < parse_timestamp(window.end)
            )
            rows_by_window.append(
                (window, _variant_rows(campaign=campaign, events=window_events))
            )
        for (_, left), (_, right) in zip(rows_by_window, rows_by_window[1:]):
            for variant_id in sorted(left):
                if not left[variant_id]["event_count"] or not right[variant_id]["event_count"]:
                    continue
                bound = max(float(left[variant_id]["uncertainty"].half_width), 1e-12)
                ratios.append(
                    abs(float(right[variant_id]["score"]) - float(left[variant_id]["score"]))
                    / bound
                )
    if not ratios:
        return 1.0, ()
    # Non-causal historical calibration only: use the worst observed residual/bound
    # ratio, never replay outcomes, and never shrink the existing uncertainty.
    return round(max(1.0, max(ratios)), 8), tuple(round(value, 8) for value in sorted(ratios))


def evaluate_offline_policy(
    fixture: Mapping[str, Any],
    events: Iterable[AnalyticsEvent],
    *,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    historical_windows_per_campaign: int = 2,
) -> dict[str, Any]:
    canonical = canonical_event_set(events)
    split = build_historical_replay_split(
        fixture,
        canonical,
        historical_windows_per_campaign=historical_windows_per_campaign,
    )
    historical_keys = set(split.historical_event_keys)
    replay_keys = set(split.replay_event_keys)
    historical_events = tuple(
        event for event in canonical if event.idempotency_key in historical_keys
    )
    replay_events = tuple(
        event for event in canonical if event.idempotency_key in replay_keys
    )

    ranking_scores: list[float] = []
    baseline_scores: list[float] = []
    score_errors: list[float] = []
    coverage_hits = 0
    coverage_total = 0
    uncertainty_widths: list[float] = []
    churn_values: list[float] = []
    missing_ranking_scores: list[float] = []
    late_order_scores: list[float] = []
    per_window: list[dict[str, Any]] = []

    historical_identities = set(split.historical_identities)
    replay_identities = set(split.replay_identities)
    uncertainty_scale, historical_calibration_ratios = _historical_uncertainty_scale(
        fixture,
        historical_events,
        split,
    )

    for campaign in sorted(fixture["campaigns"], key=lambda item: item["campaign_id"]):
        campaign_history = tuple(
            event
            for event in historical_events
            if event.channel_id == campaign["channel_id"]
        )
        history_rows = _variant_rows(campaign=campaign, events=campaign_history)
        current_prediction = _score_ranking(history_rows)
        baseline_prediction = _ctr_ranking(history_rows)

        for raw_window in sorted(
            campaign["windows"], key=lambda raw: parse_timestamp(raw["start"])
        ):
            window = _window(raw_window)
            identity = _identity(campaign["campaign_id"], window)
            if identity not in replay_identities:
                continue
            if identity in historical_identities:
                raise EvaluationError("replay identity leaked into historical split")

            eval_window_events = tuple(
                event
                for event in replay_events
                if event.channel_id == campaign["channel_id"]
                and parse_timestamp(window.start)
                <= parse_timestamp(event.captured_at)
                < parse_timestamp(window.end)
            )
            eval_rows = _variant_rows(campaign=campaign, events=eval_window_events)
            realized = _score_ranking(eval_rows)
            current_agreement = pairwise_rank_agreement(
                current_prediction, realized
            )
            baseline_agreement = pairwise_rank_agreement(
                baseline_prediction, realized
            )
            ranking_scores.append(current_agreement)
            baseline_scores.append(baseline_agreement)

            missing_rows = _variant_rows(
                campaign=campaign,
                events=_drop_missing_events(eval_window_events),
            )
            missing_agreement = pairwise_rank_agreement(
                realized, _score_ranking(missing_rows)
            )
            missing_ranking_scores.append(missing_agreement)

            reversed_rows = _variant_rows(
                campaign=campaign,
                events=tuple(reversed(eval_window_events)),
            )
            late_order_agreement = pairwise_rank_agreement(
                realized, _score_ranking(reversed_rows)
            )
            late_order_scores.append(late_order_agreement)

            for variant_id in sorted(history_rows):
                historical = history_rows[variant_id]
                observed = eval_rows[variant_id]
                if historical["event_count"] and observed["event_count"]:
                    coverage_hits += 1
                coverage_total += 1
                error = abs(float(historical["score"]) - float(observed["score"]))
                score_errors.append(error)
                band = historical["uncertainty"]
                uncertainty_widths.append(float(band.half_width))
                calibrated_half_width = min(
                    1.0,
                    float(band.half_width) * uncertainty_scale,
                )
                calibrated_lower = max(
                    0.0,
                    float(historical["score"]) - calibrated_half_width,
                )
                calibrated_upper = min(
                    1.0,
                    float(historical["score"]) + calibrated_half_width,
                )
                if calibrated_lower <= float(observed["score"]) <= calibrated_upper:
                    pass_hit = 1
                else:
                    pass_hit = 0
                churn_values.append(
                    recommendation_churn(
                        historical["recommendations"],
                        observed["recommendations"],
                    )
                )
                observed["_interval_hit"] = pass_hit

            interval_hits = sum(int(row["_interval_hit"]) for row in eval_rows.values())
            per_window.append(
                {
                    "identity": identity,
                    "event_count": len(eval_window_events),
                    "current_ranking": list(current_prediction),
                    "baseline_ranking": list(baseline_prediction),
                    "realized_replay_ranking": list(realized),
                    "current_pairwise_agreement": current_agreement,
                    "baseline_pairwise_agreement": baseline_agreement,
                    "missing_event_pairwise_agreement": missing_agreement,
                    "out_of_order_pairwise_agreement": late_order_agreement,
                    "uncertainty_interval_hits": interval_hits,
                    "variant_count": len(eval_rows),
                    "causal": False,
                }
            )

    interval_hits_total = sum(
        row["uncertainty_interval_hits"] for row in per_window
    )
    evaluated_variant_windows = sum(row["variant_count"] for row in per_window)
    evidence_coverage = round(
        coverage_hits / coverage_total if coverage_total else 0.0, 8
    )
    interval_coverage = round(
        interval_hits_total / evaluated_variant_windows
        if evaluated_variant_windows
        else 0.0,
        8,
    )
    error_band = bootstrap_mean_band(
        score_errors,
        seed=bootstrap_seed,
        resamples=bootstrap_resamples,
    )
    ranking_band = bootstrap_mean_band(
        ranking_scores,
        seed=bootstrap_seed + 1,
        resamples=bootstrap_resamples,
    )
    gate = classify_evaluation_gate(
        replay_event_count=len(replay_events),
        evaluated_variant_windows=evaluated_variant_windows,
        coverage=evidence_coverage,
        ranking_stability=ranking_band.estimate,
        error_band=error_band,
        churn=fmean(churn_values) if churn_values else 1.0,
        missing_robustness=fmean(missing_ranking_scores)
        if missing_ranking_scores
        else 0.0,
        uncertainty_interval_coverage=interval_coverage,
    )
    return {
        "evaluation_version": EVALUATION_VERSION,
        "split": asdict(split),
        "policy": {
            "current": "growth_deterministic_score_v2",
            "baseline": "historical_ctr_only_v1",
        },
        "sample_counts": {
            "unique_events": len(canonical),
            "historical_events": len(historical_events),
            "replay_events": len(replay_events),
            "historical_windows": len(split.historical_identities),
            "replay_windows": len(split.replay_identities),
            "evaluated_variant_windows": evaluated_variant_windows,
        },
        "metrics": {
            "ranking_stability": asdict(ranking_band),
            "baseline_ranking_stability": round(
                fmean(baseline_scores) if baseline_scores else 0.0, 8
            ),
            "ranking_stability_difference_vs_baseline": round(
                (fmean(ranking_scores) if ranking_scores else 0.0)
                - (fmean(baseline_scores) if baseline_scores else 0.0),
                8,
            ),
            "score_absolute_error": asdict(error_band),
            "uncertainty_interval_coverage": interval_coverage,
            "uncertainty_calibration_error_vs_0_95": round(
                abs(0.95 - interval_coverage),
                8,
            ),
            "historical_uncertainty_scale": uncertainty_scale,
            "historical_calibration_ratio_count": len(
                historical_calibration_ratios
            ),
            "historical_calibration_ratio_max": round(
                max(historical_calibration_ratios)
                if historical_calibration_ratios
                else 1.0,
                8,
            ),
            "mean_uncertainty_half_width": round(
                fmean(uncertainty_widths) if uncertainty_widths else 1.0, 8
            ),
            "evidence_coverage": evidence_coverage,
            "recommendation_turnover": round(
                fmean(churn_values) if churn_values else 1.0, 8
            ),
            "missing_event_ranking_robustness": round(
                fmean(missing_ranking_scores)
                if missing_ranking_scores
                else 0.0,
                8,
            ),
            "out_of_order_ranking_robustness": round(
                fmean(late_order_scores) if late_order_scores else 0.0,
                8,
            ),
        },
        "gate": asdict(gate),
        "replay_windows": per_window,
        "causal": False,
        "interpretation": CAUSALITY_NOTICE,
    }


def apply_distribution_shift(
    events: Iterable[AnalyticsEvent],
    *,
    replay_event_keys: Sequence[str],
) -> tuple[AnalyticsEvent, ...]:
    """Deterministic stress transform for offline robustness tests only."""
    replay = set(replay_event_keys)
    shifted: list[AnalyticsEvent] = []
    for event in canonical_event_set(events):
        if event.idempotency_key not in replay or event.variant_id is None:
            shifted.append(event)
            continue
        variant_number = int(event.variant_id.rsplit("v", 1)[-1])
        factor = {1: 1.8, 2: 1.25, 3: 0.75, 4: 0.45}.get(variant_number, 1.0)
        clicks = min(event.impressions, max(0, int(round(event.clicks * factor))))
        views = min(
            event.impressions,
            max(clicks, int(round(event.views * (2.0 - factor / 2.0)))),
        )
        watch_time = event.watch_time_seconds
        if event.views:
            watch_time = (event.watch_time_seconds / event.views) * views
        shifted.append(
            replace(
                event,
                clicks=clicks,
                views=views,
                watch_time_seconds=round(watch_time, 6),
            )
        )
    return canonical_event_set(shifted)


def sparse_replay_events(
    events: Iterable[AnalyticsEvent],
    *,
    replay_event_keys: Sequence[str],
    keep_per_variant_window: int = 1,
) -> tuple[AnalyticsEvent, ...]:
    replay = set(replay_event_keys)
    kept: dict[tuple[str, str], int] = {}
    selected: list[AnalyticsEvent] = []
    for event in canonical_event_set(events):
        if event.idempotency_key not in replay:
            selected.append(event)
            continue
        key = (event.channel_id, event.variant_id or "")
        count = kept.get(key, 0)
        if count < keep_per_variant_window:
            selected.append(event)
            kept[key] = count + 1
    return canonical_event_set(selected)


def build_offline_evaluation_report(
    fixture: Mapping[str, Any],
    events: Iterable[AnalyticsEvent],
    *,
    stress_fixture_sha256: str,
    scenario_fixture_sha256: str,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
) -> dict[str, Any]:
    canonical = canonical_event_set(events)
    split = build_historical_replay_split(fixture, canonical)
    primary = evaluate_offline_policy(
        fixture,
        canonical,
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
    )
    duplicate_replay = evaluate_offline_policy(
        fixture,
        (*canonical, *canonical[:32]),
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
    )
    out_of_order = evaluate_offline_policy(
        fixture,
        tuple(reversed(canonical)),
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
    )
    sparse = evaluate_offline_policy(
        fixture,
        sparse_replay_events(
            canonical,
            replay_event_keys=split.replay_event_keys,
            keep_per_variant_window=1,
        ),
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
    )
    shifted = evaluate_offline_policy(
        fixture,
        apply_distribution_shift(
            canonical,
            replay_event_keys=split.replay_event_keys,
        ),
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
    )
    late_ids = set(fixture.get("designated_late_event_ids", ()))
    without_designated_late = evaluate_offline_policy(
        fixture,
        tuple(event for event in canonical if event.event_id not in late_ids),
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
    )
    primary_wire = _canonical_json(primary)
    report = {
        "report_version": "growth.offline_evaluation_report.v1",
        "source": {
            "stress_fixture": "reliability_stress_v1.json",
            "stress_fixture_sha256": stress_fixture_sha256,
            "scenario_fixture": "offline_evaluation_scenarios_v1.json",
            "scenario_fixture_sha256": scenario_fixture_sha256,
        },
        "bootstrap": {
            "seed": bootstrap_seed,
            "resamples": bootstrap_resamples,
            "method": "fixed_seed_noncausal_bootstrap_mean_v1",
        },
        "primary": primary,
        "robustness": {
            "duplicate_replay_exact": _canonical_json(duplicate_replay) == primary_wire,
            "out_of_order_exact": _canonical_json(out_of_order) == primary_wire,
            "late_event_omission": without_designated_late,
            "sparse": sparse,
            "distribution_shift": shifted,
        },
        "delivery_contracts": {
            "creator_feedback_version": "1.0",
            "feedback_batch_version": "growth.feedback_batch.v1",
            "creator_seed_version": "growth.creator_seed.v1",
            "evaluation_metadata_in_payload": False,
            "auto_publish": False,
        },
        "causal": False,
        "interpretation": CAUSALITY_NOTICE,
    }
    return json.loads(_canonical_json(report))
