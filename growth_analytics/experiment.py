from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from math import sqrt
from typing import Sequence


class ExperimentLifecycleError(ValueError):
    pass


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ExperimentVariant:
    variant_id: str
    video_id: str
    label: str

    def __post_init__(self) -> None:
        if not self.variant_id or not self.video_id or not self.label:
            raise ValueError("experiment variant identity fields must be non-empty")


_ALLOWED_TRANSITIONS = {
    ExperimentStatus.DRAFT: {ExperimentStatus.RUNNING, ExperimentStatus.CANCELLED},
    ExperimentStatus.RUNNING: {
        ExperimentStatus.PAUSED,
        ExperimentStatus.COMPLETED,
        ExperimentStatus.CANCELLED,
    },
    ExperimentStatus.PAUSED: {
        ExperimentStatus.RUNNING,
        ExperimentStatus.COMPLETED,
        ExperimentStatus.CANCELLED,
    },
    ExperimentStatus.COMPLETED: set(),
    ExperimentStatus.CANCELLED: set(),
}


@dataclass(frozen=True)
class MultiVariantExperiment:
    experiment_id: str
    hypothesis_id: str
    primary_metric: str
    variants: tuple[ExperimentVariant, ...]
    started_at: str | None = None
    ended_at: str | None = None
    status: ExperimentStatus = ExperimentStatus.DRAFT
    observational: bool = True

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.hypothesis_id or not self.primary_metric:
            raise ValueError("experiment identity and primary metric must be non-empty")
        if len(self.variants) < 2:
            raise ValueError("multi-variant experiment requires at least two variants")
        ids = [variant.variant_id for variant in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError("variant ids must be unique within an experiment")
        if self.status in {ExperimentStatus.RUNNING, ExperimentStatus.PAUSED} and not self.started_at:
            raise ValueError("active experiment requires started_at")
        if self.status == ExperimentStatus.COMPLETED and (not self.started_at or not self.ended_at):
            raise ValueError("completed experiment requires started_at and ended_at")

    def transition(self, status: ExperimentStatus, *, at: str | None = None) -> "MultiVariantExperiment":
        if status == self.status:
            return self
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise ExperimentLifecycleError(f"invalid experiment transition {self.status.value} -> {status.value}")
        started_at = self.started_at
        ended_at = self.ended_at
        if status == ExperimentStatus.RUNNING and started_at is None:
            if not at:
                raise ExperimentLifecycleError("starting an experiment requires a timestamp")
            started_at = at
        if status in {ExperimentStatus.COMPLETED, ExperimentStatus.CANCELLED}:
            if not at:
                raise ExperimentLifecycleError("terminal experiment transition requires a timestamp")
            ended_at = at
        return replace(self, status=status, started_at=started_at, ended_at=ended_at)


@dataclass(frozen=True)
class UncertaintyReport:
    estimate: float
    lower: float
    upper: float
    half_width: float
    sample_size: int
    confidence: float
    method: str
    causal: bool
    interpretation: str


def wilson_interval(successes: int, trials: int, *, z: float = 1.96) -> UncertaintyReport:
    if isinstance(successes, bool) or isinstance(trials, bool):
        raise ValueError("successes and trials must be integers")
    if successes < 0 or trials < 0 or successes > trials:
        raise ValueError("successes must satisfy 0 <= successes <= trials")
    if trials == 0:
        return UncertaintyReport(
            estimate=0.0,
            lower=0.0,
            upper=1.0,
            half_width=0.5,
            sample_size=0,
            confidence=0.95,
            method="wilson",
            causal=False,
            interpretation="insufficient_observational_data",
        )
    p = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    center = (p + z2 / (2.0 * trials)) / denominator
    margin = z * sqrt((p * (1.0 - p) / trials) + (z2 / (4.0 * trials * trials))) / denominator
    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)
    return UncertaintyReport(
        estimate=round(p, 8),
        lower=round(lower, 8),
        upper=round(upper, 8),
        half_width=round((upper - lower) / 2.0, 8),
        sample_size=trials,
        confidence=0.95,
        method="wilson",
        causal=False,
        interpretation="observational_association_only",
    )


def experiment_variant_ids(experiment: MultiVariantExperiment) -> tuple[str, ...]:
    return tuple(variant.variant_id for variant in experiment.variants)


def validate_variant_coverage(
    experiment: MultiVariantExperiment,
    observed_variant_ids: Sequence[str],
) -> tuple[str, ...]:
    known = set(experiment_variant_ids(experiment))
    unknown = sorted(set(observed_variant_ids) - known)
    if unknown:
        raise ValueError("events reference variants outside experiment: " + ", ".join(unknown))
    return tuple(sorted(known & set(observed_variant_ids)))
