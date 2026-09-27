from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from math import isfinite, sqrt
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence


class MetricKind(str, Enum):
    IMPRESSIONS = "impressions"
    VIEWS = "views"
    CLICKS = "clicks"
    WATCH_TIME_SECONDS = "watch_time_seconds"


@dataclass(frozen=True)
class Channel:
    channel_id: str
    provider: str
    name: str


@dataclass(frozen=True)
class Video:
    video_id: str
    channel_id: str
    title: str
    duration_seconds: float
    published_at: str | None = None


@dataclass(frozen=True)
class Variant:
    variant_id: str
    video_id: str
    label: str
    hypothesis_id: str | None = None


@dataclass(frozen=True)
class RetentionPoint:
    position: float
    retained: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.position <= 1.0:
            raise ValueError("position must be between 0 and 1")
        if not 0.0 <= self.retained <= 1.0:
            raise ValueError("retained must be between 0 and 1")


@dataclass(frozen=True)
class AnalyticsEvent:
    provider: str
    event_id: str
    channel_id: str
    video_id: str
    variant_id: str | None
    captured_at: str
    impressions: int = 0
    views: int = 0
    clicks: int = 0
    watch_time_seconds: float = 0.0
    retention: tuple[RetentionPoint, ...] = ()

    @property
    def idempotency_key(self) -> str:
        return f"{self.provider}:{self.event_id}"

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def average_watch_time_seconds(self) -> float:
        return self.watch_time_seconds / self.views if self.views else 0.0


@dataclass(frozen=True)
class AggregateMetrics:
    impressions: int
    views: int
    clicks: int
    watch_time_seconds: float
    retention: tuple[RetentionPoint, ...]

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def average_watch_time_seconds(self) -> float:
        return self.watch_time_seconds / self.views if self.views else 0.0


@dataclass(frozen=True)
class ScoreResult:
    score: float
    uncertainty: float
    components: Mapping[str, float]
    sample_size: int


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    statement: str
    primary_metric: str
    expected_direction: str = "increase"


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    hypothesis_id: str
    control_variant_id: str
    treatment_variant_id: str
    started_at: str
    ended_at: str | None = None


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    metric: str
    control_value: float
    treatment_value: float
    absolute_lift: float
    relative_lift: float | None
    uncertainty: float
    sample_size: int


class CreatorFeedbackValidationError(ValueError):
    """Raised when a CreatorFeedback wire payload violates contract 1.0."""


_CREATOR_FEEDBACK_FIELDS = frozenset(
    {
        "contract_version",
        "content_job_id",
        "channel_id",
        "video_id",
        "variant_id",
        "score",
        "uncertainty",
        "observed",
        "recommendations",
        "evidence_event_ids",
    }
)
_CREATOR_FEEDBACK_OBSERVED_FIELDS = frozenset(
    {"ctr", "average_watch_time_seconds", "retention_auc"}
)


def _feedback_string(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise CreatorFeedbackValidationError(f"{field} must be a non-empty string")
    return value


def _feedback_number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CreatorFeedbackValidationError(f"{field} must be a finite number")
    number = float(value)
    if not isfinite(number):
        raise CreatorFeedbackValidationError(f"{field} must be a finite number")
    if minimum is not None and number < minimum:
        raise CreatorFeedbackValidationError(f"{field} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise CreatorFeedbackValidationError(f"{field} must be <= {maximum}")
    return number


def _feedback_string_array(value: Any, field: str, *, require_nonempty: bool) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise CreatorFeedbackValidationError(f"{field} must be an array of strings")
    if require_nonempty and not value:
        raise CreatorFeedbackValidationError(f"{field} must not be empty")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise CreatorFeedbackValidationError(f"{field} must contain only non-empty strings")
        items.append(item)
    return tuple(items)


def _creator_feedback_from_mapping(payload: Mapping[str, Any]) -> "CreatorFeedback":
    if not isinstance(payload, Mapping):
        raise CreatorFeedbackValidationError("CreatorFeedback payload must be an object")

    keys = set(payload.keys())
    missing = _CREATOR_FEEDBACK_FIELDS - keys
    extra = keys - _CREATOR_FEEDBACK_FIELDS
    if missing:
        raise CreatorFeedbackValidationError(
            "CreatorFeedback payload missing fields: " + ", ".join(sorted(missing))
        )
    if extra:
        raise CreatorFeedbackValidationError(
            "CreatorFeedback payload has unknown fields: " + ", ".join(sorted(extra))
        )

    version = payload["contract_version"]
    if version != "1.0" or not isinstance(version, str):
        raise CreatorFeedbackValidationError(
            f"unsupported contract_version: {version!r}; expected '1.0'"
        )

    observed = payload["observed"]
    if not isinstance(observed, Mapping):
        raise CreatorFeedbackValidationError("observed must be an object")
    observed_keys = set(observed.keys())
    if observed_keys != _CREATOR_FEEDBACK_OBSERVED_FIELDS:
        missing_observed = _CREATOR_FEEDBACK_OBSERVED_FIELDS - observed_keys
        extra_observed = observed_keys - _CREATOR_FEEDBACK_OBSERVED_FIELDS
        details: list[str] = []
        if missing_observed:
            details.append("missing " + ", ".join(sorted(missing_observed)))
        if extra_observed:
            details.append("unknown " + ", ".join(sorted(extra_observed)))
        raise CreatorFeedbackValidationError("observed fields invalid: " + "; ".join(details))

    normalized_observed = {
        "ctr": _feedback_number(observed["ctr"], "observed.ctr", minimum=0.0, maximum=1.0),
        "average_watch_time_seconds": _feedback_number(
            observed["average_watch_time_seconds"],
            "observed.average_watch_time_seconds",
            minimum=0.0,
        ),
        "retention_auc": _feedback_number(
            observed["retention_auc"],
            "observed.retention_auc",
            minimum=0.0,
            maximum=1.0,
        ),
    }

    return CreatorFeedback(
        contract_version="1.0",
        content_job_id=_feedback_string(payload["content_job_id"], "content_job_id"),
        channel_id=_feedback_string(payload["channel_id"], "channel_id"),
        video_id=_feedback_string(payload["video_id"], "video_id"),
        variant_id=_feedback_string(payload["variant_id"], "variant_id", nullable=True),
        score=_feedback_number(payload["score"], "score", minimum=0.0, maximum=1.0),
        uncertainty=_feedback_number(
            payload["uncertainty"],
            "uncertainty",
            minimum=0.0,
            maximum=1.0,
        ),
        observed=normalized_observed,
        recommendations=_feedback_string_array(
            payload["recommendations"],
            "recommendations",
            require_nonempty=True,
        ),
        evidence_event_ids=_feedback_string_array(
            payload["evidence_event_ids"],
            "evidence_event_ids",
            require_nonempty=False,
        ),
    )


@dataclass(frozen=True)
class CreatorFeedback:
    contract_version: str
    content_job_id: str
    channel_id: str
    video_id: str
    variant_id: str | None
    score: float
    uncertainty: float
    observed: Mapping[str, float]
    recommendations: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CreatorFeedback":
        return _creator_feedback_from_mapping(payload)

    def to_json(self) -> str:
        validated = type(self).from_dict(self.to_dict())
        return json.dumps(
            validated.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, wire_json: str) -> "CreatorFeedback":
        if not isinstance(wire_json, str):
            raise CreatorFeedbackValidationError("CreatorFeedback wire JSON must be a string")
        try:
            payload = json.loads(wire_json)
        except json.JSONDecodeError as exc:
            raise CreatorFeedbackValidationError("invalid CreatorFeedback JSON") from exc
        return cls.from_dict(payload)


class AnalyticsStore:
    """In-memory, provider-neutral ingest with idempotent event semantics."""

    def __init__(self) -> None:
        self._events: dict[str, AnalyticsEvent] = {}

    def ingest(self, event: AnalyticsEvent) -> bool:
        key = event.idempotency_key
        existing = self._events.get(key)
        if existing is None:
            self._events[key] = event
            return True
        if existing != event:
            raise ValueError(f"idempotency conflict for {key}")
        return False

    def ingest_many(self, events: Iterable[AnalyticsEvent]) -> int:
        return sum(1 for event in events if self.ingest(event))

    def events_for(
        self,
        *,
        video_id: str | None = None,
        variant_id: str | None = None,
        channel_id: str | None = None,
    ) -> tuple[AnalyticsEvent, ...]:
        events = self._events.values()
        if video_id is not None:
            events = (e for e in events if e.video_id == video_id)
        if variant_id is not None:
            events = (e for e in events if e.variant_id == variant_id)
        if channel_id is not None:
            events = (e for e in events if e.channel_id == channel_id)
        return tuple(sorted(events, key=lambda e: (e.captured_at, e.idempotency_key)))

    def aggregate(
        self,
        *,
        video_id: str | None = None,
        variant_id: str | None = None,
        channel_id: str | None = None,
    ) -> AggregateMetrics:
        events = self.events_for(video_id=video_id, variant_id=variant_id, channel_id=channel_id)
        if not events:
            return AggregateMetrics(0, 0, 0, 0.0, ())
        retention = _average_retention([e.retention for e in events if e.retention])
        return AggregateMetrics(
            impressions=sum(e.impressions for e in events),
            views=sum(e.views for e in events),
            clicks=sum(e.clicks for e in events),
            watch_time_seconds=sum(e.watch_time_seconds for e in events),
            retention=retention,
        )


def _retention_at(curve: Sequence[RetentionPoint], position: float) -> float:
    points = sorted(curve, key=lambda p: p.position)
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


def _average_retention(curves: Sequence[Sequence[RetentionPoint]]) -> tuple[RetentionPoint, ...]:
    if not curves:
        return ()
    positions = sorted({p.position for curve in curves for p in curve})
    return tuple(
        RetentionPoint(position, fmean(_retention_at(curve, position) for curve in curves))
        for position in positions
    )


def retention_auc(curve: Sequence[RetentionPoint]) -> float:
    """Area under a normalized retention curve, bounded to [0, 1]."""
    if not curve:
        return 0.0
    points = sorted(curve, key=lambda p: p.position)
    if points[0].position > 0:
        points = [RetentionPoint(0.0, points[0].retained), *points]
    if points[-1].position < 1:
        points = [*points, RetentionPoint(1.0, points[-1].retained)]
    area = 0.0
    for left, right in zip(points, points[1:]):
        area += (right.position - left.position) * (left.retained + right.retained) / 2.0
    return min(1.0, max(0.0, area))


def deterministic_score(metrics: AggregateMetrics, duration_seconds: float) -> ScoreResult:
    """Deterministic 0..1 score; uncertainty monotonically shrinks with impressions."""
    ctr_component = min(metrics.ctr / 0.10, 1.0)
    watch_ratio = 0.0
    if duration_seconds > 0:
        watch_ratio = min(metrics.average_watch_time_seconds / duration_seconds, 1.0)
    retention_component = retention_auc(metrics.retention)
    score = 0.35 * ctr_component + 0.35 * watch_ratio + 0.30 * retention_component
    uncertainty = min(1.0, 1.0 / sqrt(max(metrics.impressions, 1)))
    return ScoreResult(
        score=round(score, 8),
        uncertainty=round(uncertainty, 8),
        components={
            "ctr": round(ctr_component, 8),
            "watch_ratio": round(watch_ratio, 8),
            "retention_auc": round(retention_component, 8),
        },
        sample_size=metrics.impressions,
    )


def compare_ctr(experiment: Experiment, control: AggregateMetrics, treatment: AggregateMetrics) -> ExperimentResult:
    c = control.ctr
    t = treatment.ctr
    variance = (
        (c * (1 - c) / control.impressions if control.impressions else 0.0)
        + (t * (1 - t) / treatment.impressions if treatment.impressions else 0.0)
    )
    standard_error = sqrt(variance)
    lift = t - c
    relative = lift / c if c else None
    return ExperimentResult(
        experiment_id=experiment.experiment_id,
        metric="ctr",
        control_value=round(c, 8),
        treatment_value=round(t, 8),
        absolute_lift=round(lift, 8),
        relative_lift=round(relative, 8) if relative is not None else None,
        uncertainty=round(1.96 * standard_error, 8),
        sample_size=control.impressions + treatment.impressions,
    )


def build_creator_feedback(
    *,
    content_job_id: str,
    channel_id: str,
    video_id: str,
    variant_id: str | None,
    metrics: AggregateMetrics,
    duration_seconds: float,
    evidence_event_ids: Sequence[str],
) -> CreatorFeedback:
    result = deterministic_score(metrics, duration_seconds)
    recommendations: list[str] = []
    if metrics.ctr < 0.04:
        recommendations.append("test_thumbnail_or_title")
    if duration_seconds > 0 and metrics.average_watch_time_seconds / duration_seconds < 0.35:
        recommendations.append("strengthen_opening_and_pacing")
    if retention_auc(metrics.retention) < 0.45:
        recommendations.append("inspect_retention_drop_points")
    if not recommendations:
        recommendations.append("preserve_current_pattern")
    return CreatorFeedback(
        contract_version="1.0",
        content_job_id=content_job_id,
        channel_id=channel_id,
        video_id=video_id,
        variant_id=variant_id,
        score=result.score,
        uncertainty=result.uncertainty,
        observed={
            "ctr": round(metrics.ctr, 8),
            "average_watch_time_seconds": round(metrics.average_watch_time_seconds, 8),
            "retention_auc": round(retention_auc(metrics.retention), 8),
        },
        recommendations=tuple(recommendations),
        evidence_event_ids=tuple(sorted(evidence_event_ids)),
    )
