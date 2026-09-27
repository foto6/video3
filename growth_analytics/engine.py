from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import fmean
from typing import Iterable, Mapping, Sequence

from .core import (
    AggregateMetrics,
    AnalyticsEvent,
    AnalyticsStore,
    CreatorFeedback,
    RetentionPoint,
    ScoreResult,
    build_creator_feedback,
    deterministic_score,
    retention_auc,
)
from .event_stream import parse_timestamp
from .experiment import (
    MultiVariantExperiment,
    UncertaintyReport,
    experiment_variant_ids,
    wilson_interval,
)


CAUSALITY_NOTICE = (
    "Observational analytics describe associations only; they do not establish causal effects."
)


@dataclass(frozen=True)
class TimeWindow:
    label: str
    start: str
    end: str

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("window label must be non-empty")
        if parse_timestamp(self.start) >= parse_timestamp(self.end):
            raise ValueError("window start must be before end")


@dataclass(frozen=True)
class WindowAggregate:
    window: TimeWindow
    metrics: AggregateMetrics
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class TrendDelta:
    metric: str
    previous: float
    current: float
    absolute: float
    relative: float | None


@dataclass(frozen=True)
class RetentionDeltaPoint:
    position: float
    left: float
    right: float
    delta: float


@dataclass(frozen=True)
class CohortRetention:
    cohort_id: str
    curve: tuple[RetentionPoint, ...]
    auc: float
    event_count: int
    total_views: int


@dataclass(frozen=True)
class RetentionComparison:
    left_cohort_id: str
    right_cohort_id: str
    left_auc: float
    right_auc: float
    auc_delta: float
    points: tuple[RetentionDeltaPoint, ...]
    causal: bool
    interpretation: str


@dataclass(frozen=True)
class CalibratedScoreUncertainty:
    estimate: float
    lower: float
    upper: float
    half_width: float
    sample_size: int
    method: str
    causal: bool
    interpretation: str


@dataclass(frozen=True)
class VariantAnalysis:
    variant_id: str
    metrics: AggregateMetrics
    score: ScoreResult
    score_uncertainty: CalibratedScoreUncertainty
    ctr_uncertainty: UncertaintyReport
    evidence_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExperimentAnalysis:
    experiment_id: str
    status: str
    window_label: str | None
    variants: tuple[VariantAnalysis, ...]
    ranking: tuple[str, ...]
    causal: bool
    interpretation: str


@dataclass(frozen=True)
class RecommendationRank:
    token: str
    priority: float
    support_count: int
    content_job_ids: tuple[str, ...]
    variant_ids: tuple[str, ...]
    mean_uncertainty: float


@dataclass(frozen=True)
class BatchFeedbackInput:
    content_job_id: str
    channel_id: str
    video_id: str
    variant_id: str | None
    metrics: AggregateMetrics
    duration_seconds: float
    evidence_event_ids: tuple[str, ...]


def events_in_window(events: Iterable[AnalyticsEvent], window: TimeWindow) -> tuple[AnalyticsEvent, ...]:
    start = parse_timestamp(window.start)
    end = parse_timestamp(window.end)
    return tuple(
        event
        for event in events
        if start <= parse_timestamp(event.captured_at) < end
    )


def aggregate_events(events: Iterable[AnalyticsEvent]) -> AggregateMetrics:
    store = AnalyticsStore()
    store.ingest_many(events)
    return store.aggregate()


def aggregate_window(events: Iterable[AnalyticsEvent], window: TimeWindow) -> WindowAggregate:
    selected = events_in_window(events, window)
    return WindowAggregate(
        window=window,
        metrics=aggregate_events(selected),
        event_ids=tuple(sorted(event.event_id for event in selected)),
    )


def metric_trend(previous: WindowAggregate, current: WindowAggregate) -> tuple[TrendDelta, ...]:
    pairs = (
        ("ctr", previous.metrics.ctr, current.metrics.ctr),
        (
            "average_watch_time_seconds",
            previous.metrics.average_watch_time_seconds,
            current.metrics.average_watch_time_seconds,
        ),
        ("retention_auc", retention_auc(previous.metrics.retention), retention_auc(current.metrics.retention)),
    )
    deltas: list[TrendDelta] = []
    for metric, previous_value, current_value in pairs:
        absolute = current_value - previous_value
        relative = absolute / previous_value if previous_value != 0 else None
        deltas.append(
            TrendDelta(
                metric=metric,
                previous=round(previous_value, 8),
                current=round(current_value, 8),
                absolute=round(absolute, 8),
                relative=round(relative, 8) if relative is not None else None,
            )
        )
    return tuple(deltas)


def _retention_at(curve: Sequence[RetentionPoint], position: float) -> float:
    points = sorted(curve, key=lambda point: point.position)
    if not points:
        return 0.0
    if position <= points[0].position:
        return points[0].retained
    if position >= points[-1].position:
        return points[-1].retained
    for left, right in zip(points, points[1:]):
        if left.position <= position <= right.position:
            span = right.position - left.position
            if span == 0:
                return right.retained
            ratio = (position - left.position) / span
            return left.retained + ratio * (right.retained - left.retained)
    return 0.0


def aggregate_retention_cohort(cohort_id: str, events: Iterable[AnalyticsEvent]) -> CohortRetention:
    selected = tuple(event for event in events if event.retention)
    if not selected:
        return CohortRetention(cohort_id, (), 0.0, 0, 0)
    positions = sorted({point.position for event in selected for point in event.retention})
    total_views = sum(event.views for event in selected)
    curve: list[RetentionPoint] = []
    for position in positions:
        weighted_sum = 0.0
        weight_sum = 0
        for event in selected:
            weight = max(event.views, 1)
            weighted_sum += _retention_at(event.retention, position) * weight
            weight_sum += weight
        curve.append(RetentionPoint(position, weighted_sum / weight_sum))
    normalized_curve = tuple(curve)
    return CohortRetention(
        cohort_id=cohort_id,
        curve=normalized_curve,
        auc=round(retention_auc(normalized_curve), 8),
        event_count=len(selected),
        total_views=total_views,
    )


def compare_retention_cohorts(
    left: CohortRetention,
    right: CohortRetention,
    *,
    checkpoints: Sequence[float] = (0.25, 0.5, 0.75, 1.0),
) -> RetentionComparison:
    points: list[RetentionDeltaPoint] = []
    for position in checkpoints:
        if not 0.0 <= position <= 1.0:
            raise ValueError("retention checkpoints must be between 0 and 1")
        left_value = _retention_at(left.curve, position)
        right_value = _retention_at(right.curve, position)
        points.append(
            RetentionDeltaPoint(
                position=position,
                left=round(left_value, 8),
                right=round(right_value, 8),
                delta=round(right_value - left_value, 8),
            )
        )
    return RetentionComparison(
        left_cohort_id=left.cohort_id,
        right_cohort_id=right.cohort_id,
        left_auc=left.auc,
        right_auc=right.auc,
        auc_delta=round(right.auc - left.auc, 8),
        points=tuple(points),
        causal=False,
        interpretation=CAUSALITY_NOTICE,
    )


def calibrated_score_uncertainty(
    metrics: AggregateMetrics,
    duration_seconds: float,
) -> CalibratedScoreUncertainty:
    score = deterministic_score(metrics, duration_seconds)
    ctr_interval = wilson_interval(metrics.clicks, metrics.impressions)
    ctr_component_half_width = min(1.0, ctr_interval.half_width / 0.10) * 0.35
    watch_component_half_width = 0.35 / sqrt(max(metrics.views, 1))
    retention_component_half_width = 0.30 / sqrt(max(metrics.views, 1))
    half_width = min(
        1.0,
        ctr_component_half_width + watch_component_half_width + retention_component_half_width,
    )
    return CalibratedScoreUncertainty(
        estimate=score.score,
        lower=round(max(0.0, score.score - half_width), 8),
        upper=round(min(1.0, score.score + half_width), 8),
        half_width=round(half_width, 8),
        sample_size=metrics.impressions,
        method="component_observational_bound_v2",
        causal=False,
        interpretation=CAUSALITY_NOTICE,
    )


def analyze_experiment(
    experiment: MultiVariantExperiment,
    events: Iterable[AnalyticsEvent],
    *,
    duration_seconds_by_variant: Mapping[str, float],
    window: TimeWindow | None = None,
) -> ExperimentAnalysis:
    selected = tuple(events)
    if window is not None:
        selected = events_in_window(selected, window)
    known = set(experiment_variant_ids(experiment))
    selected = tuple(event for event in selected if event.variant_id in known)

    analyses: list[VariantAnalysis] = []
    for variant in experiment.variants:
        variant_events = tuple(event for event in selected if event.variant_id == variant.variant_id)
        metrics = aggregate_events(variant_events)
        duration = duration_seconds_by_variant.get(variant.variant_id)
        if duration is None or duration <= 0:
            raise ValueError(f"missing positive duration for variant {variant.variant_id}")
        analyses.append(
            VariantAnalysis(
                variant_id=variant.variant_id,
                metrics=metrics,
                score=deterministic_score(metrics, duration),
                score_uncertainty=calibrated_score_uncertainty(metrics, duration),
                ctr_uncertainty=wilson_interval(metrics.clicks, metrics.impressions),
                evidence_event_ids=tuple(sorted(event.event_id for event in variant_events)),
            )
        )

    ranking = tuple(
        item.variant_id
        for item in sorted(
            analyses,
            key=lambda item: (
                -item.score.score,
                item.score_uncertainty.half_width,
                item.variant_id,
            ),
        )
    )
    return ExperimentAnalysis(
        experiment_id=experiment.experiment_id,
        status=experiment.status.value,
        window_label=window.label if window is not None else None,
        variants=tuple(analyses),
        ranking=ranking,
        causal=False,
        interpretation=CAUSALITY_NOTICE,
    )


def rank_recommendations(feedbacks: Iterable[CreatorFeedback]) -> tuple[RecommendationRank, ...]:
    items = tuple(feedbacks)
    evidence: dict[str, list[tuple[float, CreatorFeedback]]] = {}
    for feedback in items:
        CreatorFeedback.from_dict(feedback.to_dict())
        confidence = 1.0 - feedback.uncertainty
        for token in feedback.recommendations:
            base = feedback.score if token == "preserve_current_pattern" else 1.0 - feedback.score
            evidence.setdefault(token, []).append((base * confidence, feedback))

    ranked: list[RecommendationRank] = []
    denominator = max(len(items), 1)
    for token, rows in evidence.items():
        ranked.append(
            RecommendationRank(
                token=token,
                priority=round(sum(weight for weight, _ in rows) / denominator, 8),
                support_count=len(rows),
                content_job_ids=tuple(sorted({feedback.content_job_id for _, feedback in rows})),
                variant_ids=tuple(
                    sorted(
                        {
                            feedback.variant_id
                            for _, feedback in rows
                            if feedback.variant_id is not None
                        }
                    )
                ),
                mean_uncertainty=round(
                    fmean(feedback.uncertainty for _, feedback in rows),
                    8,
                ),
            )
        )
    return tuple(
        sorted(
            ranked,
            key=lambda item: (
                -item.priority,
                -item.support_count,
                item.mean_uncertainty,
                item.token,
            ),
        )
    )


def build_creator_feedback_batch(
    inputs: Iterable[BatchFeedbackInput],
) -> tuple[CreatorFeedback, ...]:
    feedbacks: list[CreatorFeedback] = []
    seen_jobs: set[str] = set()
    for item in sorted(
        inputs,
        key=lambda value: (
            value.content_job_id,
            value.variant_id or "",
            value.video_id,
        ),
    ):
        if item.content_job_id in seen_jobs:
            raise ValueError(f"duplicate content_job_id in batch: {item.content_job_id}")
        seen_jobs.add(item.content_job_id)
        feedback = build_creator_feedback(
            content_job_id=item.content_job_id,
            channel_id=item.channel_id,
            video_id=item.video_id,
            variant_id=item.variant_id,
            metrics=item.metrics,
            duration_seconds=item.duration_seconds,
            evidence_event_ids=item.evidence_event_ids,
        )
        feedbacks.append(CreatorFeedback.from_dict(feedback.to_dict()))
    return tuple(feedbacks)
