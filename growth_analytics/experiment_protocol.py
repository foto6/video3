from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from statistics import NormalDist, fmean
from typing import Any, Mapping, Sequence

from .engine import CAUSALITY_NOTICE
from .event_stream import parse_timestamp

PLAN_VERSION = "experiment.plan.v1"
EVIDENCE_VERSION = "experiment.evidence.v1"
RESULT_VERSION = "experiment.sequential_result.v1"
REPORT_VERSION = "experiment.sequential_report.v1"
SYNTHETIC_CORPUS_VERSION = "experiment.synthetic_corpus.v1"
DEFAULT_EXPERIMENT_SEED = 720260927

class ExperimentProtocolError(ValueError):
    pass

def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))

def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

@dataclass(frozen=True)
class AllocationVariant:
    variant_id: str
    weight: float

@dataclass(frozen=True)
class ExperimentPlan:
    plan_version: str
    experiment_id: str
    experiment_seed: int
    eligible_variants: tuple[AllocationVariant, ...]
    assignment_unit: str
    stratification_keys: tuple[str, ...]
    start_at: str
    stop_at: str
    primary_metric: str
    guardrail_metrics: tuple[str, ...]
    decision_rules: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version,
            "experiment_id": self.experiment_id,
            "experiment_seed": self.experiment_seed,
            "eligible_variants": [
                {"variant_id": item.variant_id, "weight": item.weight}
                for item in self.eligible_variants
            ],
            "assignment_unit": self.assignment_unit,

            "stratification_keys": list(self.stratification_keys),
            "window": {"start": self.start_at, "stop": self.stop_at},
            "primary_metric": self.primary_metric,
            "guardrail_metrics": list(self.guardrail_metrics),
            "decision_rules": dict(self.decision_rules),
        }

    def to_json(self) -> str:
        return _canonical(type(self).from_dict(self.to_dict()).to_dict())

    @classmethod
    def from_json(cls, wire: str) -> "ExperimentPlan":
        try:
            payload = json.loads(wire)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ExperimentProtocolError("invalid experiment plan JSON") from exc
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentPlan":
        expected = {
            "plan_version", "experiment_id", "experiment_seed",
            "eligible_variants", "assignment_unit", "stratification_keys",
            "window", "primary_metric", "guardrail_metrics", "decision_rules",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ExperimentProtocolError("experiment plan fields must match v1 exactly")

        if payload["plan_version"] != PLAN_VERSION:
            raise ExperimentProtocolError("unsupported experiment plan version")
        for name in ("experiment_id", "assignment_unit", "primary_metric"):
            if not isinstance(payload[name], str) or not payload[name]:
                raise ExperimentProtocolError(f"{name} must be a non-empty string")
        seed = payload["experiment_seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ExperimentProtocolError("experiment_seed must be non-negative integer")
        raw_variants = payload["eligible_variants"]
        if not isinstance(raw_variants, list) or len(raw_variants) < 2:
            raise ExperimentProtocolError("at least two eligible variants required")
        variants, seen, total = [], set(), 0.0
        for raw in raw_variants:
            if not isinstance(raw, Mapping) or set(raw) != {"variant_id", "weight"}:
                raise ExperimentProtocolError("eligible variant fields invalid")
            vid, weight = raw["variant_id"], raw["weight"]
            if not isinstance(vid, str) or not vid or vid in seen:
                raise ExperimentProtocolError("variant ids must be unique strings")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
                raise ExperimentProtocolError("allocation weights must be positive")
            seen.add(vid); total += float(weight)
            variants.append(AllocationVariant(vid, float(weight)))
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ExperimentProtocolError("allocation weights must sum to 1.0")

        strata = payload["stratification_keys"]
        if (not isinstance(strata, list)
                or any(not isinstance(v, str) or not v for v in strata)
                or len(strata) != len(set(strata))):
            raise ExperimentProtocolError("stratification_keys invalid")
        window = payload["window"]
        if not isinstance(window, Mapping) or set(window) != {"start", "stop"}:
            raise ExperimentProtocolError("window must contain start/stop exactly")
        start, stop = window["start"], window["stop"]
        if not isinstance(start, str) or not isinstance(stop, str):
            raise ExperimentProtocolError("window values must be strings")
        if parse_timestamp(start) >= parse_timestamp(stop):
            raise ExperimentProtocolError("experiment start must precede stop")
        guards = payload["guardrail_metrics"]
        if (not isinstance(guards, list)
                or any(not isinstance(v, str) or not v for v in guards)
                or len(guards) != len(set(guards))):
            raise ExperimentProtocolError("guardrail_metrics invalid")
        rules = payload["decision_rules"]
        fields = {
            "alpha", "max_looks", "look_sample_sizes", "min_total_sample",
            "min_per_variant", "max_missing_rate",
            "max_allocation_relative_deviation", "distribution_reference_mean",
            "max_distribution_shift", "max_recommendation_churn",
        }
        if not isinstance(rules, Mapping) or set(rules) != fields:
            raise ExperimentProtocolError("decision_rules fields must match v1 exactly")

        alpha, looks = rules["alpha"], rules["max_looks"]
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
            raise ExperimentProtocolError("alpha must be in (0,1)")
        if isinstance(looks, bool) or not isinstance(looks, int) or looks < 1:
            raise ExperimentProtocolError("max_looks must be positive integer")
        sizes = rules["look_sample_sizes"]
        if (not isinstance(sizes, list) or len(sizes) != looks
                or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in sizes)
                or sizes != sorted(sizes) or len(sizes) != len(set(sizes))):
            raise ExperimentProtocolError("look_sample_sizes invalid")
        for name in ("min_total_sample", "min_per_variant"):
            value = rules[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ExperimentProtocolError(f"{name} must be positive integer")
        for name in (
            "max_missing_rate", "max_allocation_relative_deviation",
            "distribution_reference_mean", "max_distribution_shift",
            "max_recommendation_churn",
        ):
            value = rules[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ExperimentProtocolError(f"{name} must be numeric")
            if not 0 <= float(value) <= 1:
                raise ExperimentProtocolError(f"{name} must be in [0,1]")
        normalized = dict(rules)
        normalized["alpha"] = float(alpha)
        for name in fields - {"look_sample_sizes", "max_looks", "min_total_sample", "min_per_variant", "alpha"}:
            normalized[name] = float(rules[name])

        return cls(
            PLAN_VERSION, payload["experiment_id"], seed, tuple(variants),
            payload["assignment_unit"], tuple(strata), start, stop,
            payload["primary_metric"], tuple(guards), normalized,
        )

def plan_digest(plan: ExperimentPlan) -> str:
    return _sha(plan.to_json())

def deterministic_assignment(
    plan: ExperimentPlan, *, unit_id: str, strata: Mapping[str, str]
) -> str:
    if not isinstance(unit_id, str) or not unit_id:
        raise ExperimentProtocolError("unit_id must be non-empty")
    if not isinstance(strata, Mapping) or set(strata) != set(plan.stratification_keys):
        raise ExperimentProtocolError("strata keys must exactly match plan")
    if any(not isinstance(strata[k], str) or not strata[k] for k in plan.stratification_keys):
        raise ExperimentProtocolError("strata values must be non-empty strings")
    material = {
        "experiment_id": plan.experiment_id,
        "experiment_seed": plan.experiment_seed,
        "assignment_unit": plan.assignment_unit,
        "unit_id": unit_id,
        "strata": {k: strata[k] for k in sorted(strata)},
    }
    bucket = int(_sha(_canonical(material)), 16) / float(1 << 256)
    cumulative = 0.0
    for index, variant in enumerate(plan.eligible_variants):
        cumulative += variant.weight
        if bucket < cumulative or index == len(plan.eligible_variants) - 1:
            return variant.variant_id
    raise AssertionError("unreachable")

@dataclass(frozen=True)
class ExperimentObservation:
    observation_id: str
    unit_id: str
    strata: Mapping[str, str]
    assigned_variant: str
    primary_success: int
    observed_at: str
    missing: bool
    distribution_value: float
    recommendation_token: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id, "unit_id": self.unit_id,
            "strata": dict(self.strata), "assigned_variant": self.assigned_variant,
            "primary_success": self.primary_success, "observed_at": self.observed_at,
            "missing": self.missing, "distribution_value": self.distribution_value,
            "recommendation_token": self.recommendation_token,
        }

@dataclass(frozen=True)
class ExperimentEvidence:
    evidence_version: str
    experiment_id: str
    plan_digest: str
    source_kind: str
    observations: tuple[ExperimentObservation, ...]
    randomized: bool
    observational: bool
    interpretation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_version": self.evidence_version,
            "experiment_id": self.experiment_id, "plan_digest": self.plan_digest,

            "source_kind": self.source_kind,
            "observations": [o.to_dict() for o in self.observations],
            "randomized": self.randomized, "observational": self.observational,
            "interpretation": self.interpretation,
        }

    def to_json(self) -> str:
        return _canonical(self.to_dict())

def _parse_observation(plan: ExperimentPlan, raw: Mapping[str, Any]) -> ExperimentObservation:
    fields = {
        "observation_id", "unit_id", "strata", "assigned_variant",
        "primary_success", "observed_at", "missing", "distribution_value",
        "recommendation_token",
    }
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise ExperimentProtocolError("observation fields must match v1 exactly")
    for name in ("observation_id", "unit_id", "assigned_variant", "observed_at", "recommendation_token"):
        if not isinstance(raw[name], str) or not raw[name]:
            raise ExperimentProtocolError(f"{name} must be non-empty string")
    if raw["primary_success"] not in (0, 1) or isinstance(raw["primary_success"], bool):
        raise ExperimentProtocolError("primary_success must be 0 or 1")
    if not isinstance(raw["missing"], bool):
        raise ExperimentProtocolError("missing must be boolean")
    dv = raw["distribution_value"]
    if isinstance(dv, bool) or not isinstance(dv, (int, float)) or not 0 <= float(dv) <= 1:
        raise ExperimentProtocolError("distribution_value must be in [0,1]")

    when = parse_timestamp(raw["observed_at"])
    if when < parse_timestamp(plan.start_at) or when >= parse_timestamp(plan.stop_at):
        raise ExperimentProtocolError("observation outside experiment window")
    assigned = deterministic_assignment(plan, unit_id=raw["unit_id"], strata=raw["strata"])
    if raw["assigned_variant"] != assigned:
        raise ExperimentProtocolError("observed assignment does not match plan")
    return ExperimentObservation(
        raw["observation_id"], raw["unit_id"],
        {k: raw["strata"][k] for k in sorted(raw["strata"])},
        raw["assigned_variant"], int(raw["primary_success"]),
        raw["observed_at"], raw["missing"], float(dv),
        raw["recommendation_token"],
    )

def parse_experiment_evidence(
    plan: ExperimentPlan, payload: Mapping[str, Any]
) -> ExperimentEvidence:
    fields = {
        "evidence_version", "experiment_id", "plan_digest", "source_kind",
        "observations", "randomized", "observational", "interpretation",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise ExperimentProtocolError("evidence fields must match v1 exactly")
    if payload["evidence_version"] != EVIDENCE_VERSION:
        raise ExperimentProtocolError("unsupported evidence version")
    if payload["experiment_id"] != plan.experiment_id or payload["plan_digest"] != plan_digest(plan):
        raise ExperimentProtocolError("evidence identity does not match plan")

    source = payload["source_kind"]
    if source == "synthetic_randomized_assignment":
        if payload["randomized"] is not True or payload["observational"] is not False:
            raise ExperimentProtocolError("randomized evidence flags invalid")
        interpretation = "synthetic_randomized_assignment_evidence"
    elif source == "historical_observational":
        if payload["randomized"] is not False or payload["observational"] is not True:
            raise ExperimentProtocolError("observational evidence flags invalid")
        interpretation = CAUSALITY_NOTICE
    else:
        raise ExperimentProtocolError("unsupported evidence source_kind")
    if payload["interpretation"] != interpretation:
        raise ExperimentProtocolError("evidence interpretation invalid")
    if not isinstance(payload["observations"], list):
        raise ExperimentProtocolError("observations must be array")
    by_id, units = {}, {}
    for raw in payload["observations"]:
        item = _parse_observation(plan, raw)
        previous = by_id.get(item.observation_id)
        if previous is not None:
            if previous != item:
                raise ExperimentProtocolError("conflicting duplicate observation")
            continue
        if item.unit_id in units:
            raise ExperimentProtocolError("assignment unit observed more than once")
        by_id[item.observation_id] = item
        units[item.unit_id] = item
    observations = tuple(sorted(
        by_id.values(),
        key=lambda x: (parse_timestamp(x.observed_at), x.observation_id),
    ))

    return ExperimentEvidence(
        EVIDENCE_VERSION, plan.experiment_id, plan_digest(plan), source,
        observations, bool(payload["randomized"]), bool(payload["observational"]),
        interpretation,
    )

def allocation_diagnostics(
    plan: ExperimentPlan, observations: Sequence[ExperimentObservation]
) -> dict[str, Any]:
    counts = {v.variant_id: 0 for v in plan.eligible_variants}
    current = [o for o in observations if not o.missing]
    for item in current:
        counts[item.assigned_variant] += 1
    total = len(current)
    rows, max_dev = [], 0.0
    for variant in plan.eligible_variants:
        expected = total * variant.weight
        observed = counts[variant.variant_id]
        dev = abs(observed - expected) / expected if expected else 0.0
        max_dev = max(max_dev, dev)
        rows.append({
            "variant_id": variant.variant_id, "observed": observed,
            "expected": round(expected, 8), "relative_deviation": round(dev, 8),
        })
    threshold = float(plan.decision_rules["max_allocation_relative_deviation"])
    min_n = int(plan.decision_rules["min_per_variant"])

    coverage = sum(1 for n in counts.values() if n >= min_n) / len(counts)
    return {
        "total_nonmissing": total, "by_variant": rows,
        "max_relative_deviation": round(max_dev, 8),
        "sample_ratio_mismatch": max_dev > threshold,
        "variant_coverage": round(coverage, 8), "threshold": threshold,
    }

def _recommendation_churn(obs: Sequence[ExperimentObservation]) -> float:
    tokens = [o.recommendation_token for o in obs if not o.missing]
    if not tokens:
        return 1.0
    counts = {token: tokens.count(token) for token in set(tokens)}
    return round(1.0 - max(counts.values()) / len(tokens), 8)

def _distribution_shift(plan: ExperimentPlan, obs: Sequence[ExperimentObservation]) -> float:
    values = [o.distribution_value for o in obs if not o.missing]
    if not values:
        return 1.0
    return round(abs(
        fmean(values) - float(plan.decision_rules["distribution_reference_mean"])
    ), 8)

def _comparisons(
    plan: ExperimentPlan, obs: Sequence[ExperimentObservation], look: int
) -> list[dict[str, Any]]:
    variants = tuple(v.variant_id for v in plan.eligible_variants)
    control = variants[0]
    scope = int(plan.decision_rules["max_looks"]) * max(1, len(variants) - 1)
    alpha_each = float(plan.decision_rules["alpha"]) / scope
    critical = NormalDist().inv_cdf(1 - alpha_each / 2)

    by_variant = {
        v: [o.primary_success for o in obs if not o.missing and o.assigned_variant == v]
        for v in variants
    }
    cvals = by_variant[control]
    crate = fmean(cvals) if cvals else 0.0
    rows = []
    for treatment in variants[1:]:
        tvals = by_variant[treatment]
        trate = fmean(tvals) if tvals else 0.0
        effect = trate - crate
        n0, n1 = len(cvals), len(tvals)
        se = math.sqrt(
            (crate * (1 - crate) / n0 if n0 else 0.0)
            + (trate * (1 - trate) / n1 if n1 else 0.0)
        )
        lower, upper = effect - critical * se, effect + critical * se
        rows.append({
            "control_variant": control, "treatment_variant": treatment,
            "control_n": n0, "treatment_n": n1,
            "control_rate": round(crate, 8), "treatment_rate": round(trate, 8),
            "effect": round(effect, 8), "standard_error": round(se, 8),
            "confidence_lower": round(lower, 8),
            "confidence_upper": round(upper, 8),
            "per_comparison_look_alpha": round(alpha_each, 12),
            "critical_z": round(critical, 8),
            "crosses_positive_boundary": lower > 0,
            "crosses_negative_boundary": upper < 0, "look_index": look,
        })
    return rows

def _look_result(
    plan: ExperimentPlan,
    observations: Sequence[ExperimentObservation],
    look: int,
) -> dict[str, Any]:
    allocation = allocation_diagnostics(plan, observations)
    missing = sum(1 for item in observations if item.missing)
    missing_rate = missing / len(observations) if observations else 1.0
    shift = _distribution_shift(plan, observations)
    churn = _recommendation_churn(observations)
    breaches = []
    if allocation["sample_ratio_mismatch"]:
        breaches.append("sample_ratio_mismatch")
    if missing_rate > float(plan.decision_rules["max_missing_rate"]):
        breaches.append("missingness")
    if shift > float(plan.decision_rules["max_distribution_shift"]):
        breaches.append("distribution_shift")
    if churn > float(plan.decision_rules["max_recommendation_churn"]):
        breaches.append("recommendation_churn")
    nonmissing = [item for item in observations if not item.missing]
    insufficient = (
        len(nonmissing) < int(plan.decision_rules["min_total_sample"])
        or allocation["variant_coverage"] < 1.0
    )
    comparisons = _comparisons(plan, observations, look)
    crossed = any(
        row["crosses_positive_boundary"] or row["crosses_negative_boundary"]
        for row in comparisons
    )
    if breaches:
        state = "stop_for_guardrail"
    elif insufficient:
        state = "insufficient_evidence"
    elif crossed or look >= int(plan.decision_rules["max_looks"]):
        state = "analysis_complete"
    else:
        state = "continue"
    return {
        "look_number": look,
        "look_sample_size": len(observations),
        "sample_counts": {
            "look_observations": len(observations),
            "nonmissing": len(nonmissing),
            "missing": missing,
        },
        "allocation": allocation,
        "guardrails": {
            "missing_rate": round(missing_rate, 8),
            "distribution_shift": shift,
            "recommendation_churn": churn,
            "breaches": breaches,
        },
        "comparisons": comparisons,
        "state": state,
    }


def evaluate_sequential(
    plan: ExperimentPlan, evidence: ExperimentEvidence
) -> dict[str, Any]:
    if evidence.source_kind != "synthetic_randomized_assignment":
        raise ExperimentProtocolError(
            "historical observational data cannot be experiment evidence"
        )
    sizes = tuple(int(value) for value in plan.decision_rules["look_sample_sizes"])
    available = len(evidence.observations)
    looks = []
    stopped = None
    for look, size in enumerate(sizes, 1):
        if available < size:
            break
        current = evidence.observations[:size]
        row = _look_result(plan, current, look)
        looks.append(row)
        if row["state"] in {"stop_for_guardrail", "analysis_complete"}:
            stopped = row
            break
    if stopped is None:
        if looks:
            stopped = looks[-1]
        else:
            current = evidence.observations[:available]
            stopped = _look_result(plan, current, 1)
            looks.append(stopped)
    scope = int(plan.decision_rules["max_looks"]) * max(
        1, len(plan.eligible_variants) - 1
    )
    return {
        "result_version": RESULT_VERSION,
        "experiment_id": plan.experiment_id,
        "plan_digest": plan_digest(plan),
        "evidence_digest": _sha(evidence.to_json()),
        "evidence_source_kind": evidence.source_kind,
        "method": {
            "name": "bonferroni_fixed_max_looks_two_sided_z_v1",
            "familywise_alpha": float(plan.decision_rules["alpha"]),
            "max_looks": int(plan.decision_rules["max_looks"]),
            "multiplicity_scope": scope,
            "per_comparison_look_alpha": round(
                float(plan.decision_rules["alpha"]) / scope, 12
            ),
        },
        "available_observations": available,
        "look_history": looks,
        "final_look_number": stopped["look_number"],
        "final_look_sample_size": stopped["look_sample_size"],
        "sample_counts": stopped["sample_counts"],
        "allocation": stopped["allocation"],
        "guardrails": stopped["guardrails"],
        "comparisons": stopped["comparisons"],
        "state": stopped["state"],
        "randomized_experiment_evidence": True,
        "auto_publish": False,
        "external_mutation": False,
        "interpretation": (
            "Synthetic randomized-assignment evidence evaluated under a "
            "predeclared multiple-look-safe protocol."
        ),
    }


def default_experiment_plan(seed: int = DEFAULT_EXPERIMENT_SEED) -> ExperimentPlan:
    return ExperimentPlan.from_dict({
        "plan_version": PLAN_VERSION, "experiment_id": "synthetic-growth-exp-v1",
        "experiment_seed": seed,
        "eligible_variants": [
            {"variant_id": "control", "weight": 0.5},
            {"variant_id": "treatment", "weight": 0.5},
        ],
        "assignment_unit": "synthetic_viewer_id",
        "stratification_keys": ["region", "device"],
        "window": {
            "start": "2026-09-01T00:00:00Z",
            "stop": "2026-10-01T00:00:00Z",
        },
        "primary_metric": "synthetic_primary_success_rate",
        "guardrail_metrics": [
            "sample_ratio_mismatch", "missing_rate",
            "distribution_shift", "recommendation_churn",
        ],
        "decision_rules": {
            "alpha": 0.05, "max_looks": 4,
            "look_sample_sizes": [400, 800, 1200, 1600],

            "min_total_sample": 400, "min_per_variant": 150,
            "max_missing_rate": 0.15,
            "max_allocation_relative_deviation": 0.15,
            "distribution_reference_mean": 0.5,
            "max_distribution_shift": 0.15,
            "max_recommendation_churn": 0.35,
        },
    })

def _synthetic_rows(
    plan: ExperimentPlan, *, seed: int, sample_size: int,
    control_rate: float, treatment_rate: float,
    distribution_value: float = 0.5, churn: bool = False,
) -> list[dict[str, Any]]:
    rng, rows = random.Random(seed), []
    for i in range(sample_size):
        unit = f"unit-{i:05d}"
        strata = {
            "region": ("na", "eu", "apac")[i % 3],
            "device": ("mobile", "desktop")[i % 2],
        }
        assigned = deterministic_assignment(plan, unit_id=unit, strata=strata)
        rate = control_rate if assigned == "control" else treatment_rate
        success = int(rng.random() < rate)
        day, hour = 1 + i // 80, i % 24
        rows.append({
            "observation_id": f"obs-{i:05d}", "unit_id": unit,
            "strata": strata, "assigned_variant": assigned,
            "primary_success": success,

            "observed_at": f"2026-09-{day:02d}T{hour:02d}:00:00Z",
            "missing": False, "distribution_value": distribution_value,
            "recommendation_token": (
                f"rec-{i % 5}" if churn else "preserve_current_pattern"
            ),
        })
    return rows

def _evidence(plan: ExperimentPlan, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "evidence_version": EVIDENCE_VERSION,
        "experiment_id": plan.experiment_id,
        "plan_digest": plan_digest(plan),
        "source_kind": "synthetic_randomized_assignment",
        "observations": [dict(row) for row in rows],
        "randomized": True, "observational": False,
        "interpretation": "synthetic_randomized_assignment_evidence",
    }

def generate_synthetic_experiment_corpus(
    seed: int = DEFAULT_EXPERIMENT_SEED,
) -> dict[str, Any]:
    plan = default_experiment_plan(seed)
    null = _synthetic_rows(
        plan, seed=seed + 1, sample_size=1600,
        control_rate=0.10, treatment_rate=0.10,
    )
    positive = _synthetic_rows(
        plan, seed=seed + 2, sample_size=1600,
        control_rate=0.10, treatment_rate=0.17,
    )

    sparse = _synthetic_rows(
        plan, seed=seed + 3, sample_size=120,
        control_rate=0.10, treatment_rate=0.10,
    )
    guard = _synthetic_rows(
        plan, seed=seed + 4, sample_size=800,
        control_rate=0.10, treatment_rate=0.15,
        distribution_value=0.85, churn=True,
    )
    source = _synthetic_rows(
        plan, seed=seed + 5, sample_size=1600,
        control_rate=0.10, treatment_rate=0.10,
    )
    srm = [
        row for row in source
        if row["assigned_variant"] == "control"
        or int(row["unit_id"].split("-")[-1]) % 4 == 0
    ][:800]
    late = list(null)
    random.Random(seed + 6).shuffle(late)
    late = late + [dict(row) for row in late[:64]]
    return {
        "corpus_version": SYNTHETIC_CORPUS_VERSION, "seed": seed,
        "plan": plan.to_dict(),
        "cases": {
            "null_effect": _evidence(plan, null),
            "positive_synthetic_effect": _evidence(plan, positive),
            "sample_ratio_mismatch": _evidence(plan, srm),
            "sparse_data": _evidence(plan, sparse),
            "late_replay": _evidence(plan, late),
            "guardrail_breach": _evidence(plan, guard),
        },
        "historical_observational_policy": {
            "source_kind": "historical_observational",
            "experiment_inference_allowed": False,
            "interpretation": CAUSALITY_NOTICE,
        },
    }

def build_sequential_report(corpus: Mapping[str, Any]) -> dict[str, Any]:
    if corpus.get("corpus_version") != SYNTHETIC_CORPUS_VERSION:
        raise ExperimentProtocolError("unsupported synthetic corpus version")
    plan = ExperimentPlan.from_dict(corpus["plan"])
    results = {}
    for name in sorted(corpus["cases"]):
        evidence = parse_experiment_evidence(plan, corpus["cases"][name])
        results[name] = evaluate_sequential(plan, evidence)
    return {
        "report_version": REPORT_VERSION, "seed": corpus["seed"],
        "plan_digest": plan_digest(plan), "plan_version": PLAN_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "method": "bonferroni_fixed_max_looks_two_sided_z_v1",
        "cases": results,
        "historical_observational_policy": dict(
            corpus["historical_observational_policy"]
        ),
        "creator_feedback_contract_version": "1.0",
        "feedback_batch_version": "growth.feedback_batch.v1",
        "creator_seed_version": "growth.creator_seed.v1",
        "experiment_evidence_separate_from_creator_payload": True,
        "auto_publish": False, "external_mutation": False,
    }
