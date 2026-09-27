from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any, Mapping, Sequence

from .experiment_protocol import (
    ExperimentPlan,
    ExperimentProtocolError,
    deterministic_assignment,
    plan_digest,
)
from .event_stream import parse_timestamp

INTEGRITY_VERSION = "experiment_integrity.v1"
CALIBRATION_VERSION = "experiment_integrity.calibration.v1"
CORPUS_VERSION = "experiment_integrity.synthetic_corpus.v1"
DEFAULT_INTEGRITY_SEED = 820260927

class ExperimentIntegrityError(ValueError):
    pass

def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))

def _sha(value: Any) -> str:
    wire = value if isinstance(value, str) else _canonical(value)
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()

def _status(invalid: list[str], warning: list[str]) -> str:
    if invalid:
        return "invalid"
    if warning:
        return "warning"
    return "valid"
def randomization_digest(plan: ExperimentPlan, observations: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for raw in observations:
        unit = raw.get("unit_id")
        strata = raw.get("strata")
        assigned = raw.get("assigned_variant")
        if isinstance(unit, str) and isinstance(strata, Mapping) and isinstance(assigned, str):
            rows.append({
                "unit_id": unit,
                "strata": {str(k): str(v) for k, v in sorted(strata.items())},
                "assigned_variant": assigned,
            })
    rows.sort(key=lambda row: (row["unit_id"], _canonical(row["strata"]), row["assigned_variant"]))
    return _sha({
        "experiment_id": plan.experiment_id,
        "experiment_seed": plan.experiment_seed,
        "assignment_unit": plan.assignment_unit,
        "rows": rows,
    })

def _allocation_check(plan: ExperimentPlan, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    variants = {item.variant_id: item.weight for item in plan.eligible_variants}
    counts = {variant: 0 for variant in variants}
    for row in rows:
        arm = row.get("assigned_variant")
        if arm in counts:
            counts[arm] += 1
    total = sum(counts.values())
    details, max_dev = [], 0.0
    for arm, weight in variants.items():
        expected = total * weight
        observed = counts[arm]
        dev = abs(observed - expected) / expected if expected else 0.0
        max_dev = max(max_dev, dev)
        details.append({
            "variant_id": arm, "observed": observed,
            "expected": round(expected, 8),
            "relative_deviation": round(dev, 8),
        })
    threshold = float(plan.decision_rules["max_allocation_relative_deviation"])
    return {
        "name": "sample_ratio_mismatch",
        "status": "invalid" if max_dev > threshold else "valid",
        "max_relative_deviation": round(max_dev, 8),
        "threshold": threshold,
        "by_variant": details,
    }
def _assignment_check(plan: ExperimentPlan, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    observation_ids: dict[str, str] = {}
    units: dict[str, set[str]] = {}
    duplicate_observation_ids = 0
    duplicated_units = 0
    cross_arm_units = 0
    wrong_assignment = 0
    malformed = 0
    seen_unit_observations: dict[str, set[str]] = {}
    for raw in rows:
        oid, unit, strata, arm = (
            raw.get("observation_id"), raw.get("unit_id"),
            raw.get("strata"), raw.get("assigned_variant"),
        )
        if not all(isinstance(v, str) and v for v in (oid, unit, arm)) or not isinstance(strata, Mapping):
            malformed += 1
            continue
        digest = _sha(raw)
        if oid in observation_ids:
            duplicate_observation_ids += 1
            if observation_ids[oid] != digest:
                malformed += 1
        observation_ids[oid] = digest
        units.setdefault(unit, set()).add(arm)
        seen_unit_observations.setdefault(unit, set()).add(oid)
        try:
            expected = deterministic_assignment(plan, unit_id=unit, strata=strata)
        except ExperimentProtocolError:
            malformed += 1
            continue
        if expected != arm:
            wrong_assignment += 1
    for unit, arms in units.items():
        if len(arms) > 1:
            cross_arm_units += 1
        if len(seen_unit_observations.get(unit, ())) > 1:
            duplicated_units += 1
    invalid = cross_arm_units or duplicated_units or wrong_assignment or malformed
    return {
        "name": "assignment_integrity",
        "status": "invalid" if invalid else "valid",
        "exact_replay_duplicate_rows": duplicate_observation_ids,
        "duplicated_assignment_units": duplicated_units,
        "cross_arm_contaminated_units": cross_arm_units,
        "wrong_assignment_rows": wrong_assignment,
        "malformed_assignment_rows": malformed,
    }
def _missingness_check(plan: ExperimentPlan, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    variants = [item.variant_id for item in plan.eligible_variants]
    per_arm = {}
    rates = []
    for arm in variants:
        arm_rows = [row for row in rows if row.get("assigned_variant") == arm]
        missing = sum(1 for row in arm_rows if row.get("missing") is True)
        rate = missing / len(arm_rows) if arm_rows else 1.0
        rates.append(rate)
        per_arm[arm] = {
            "total": len(arm_rows), "missing": missing,
            "missing_rate": round(rate, 8),
        }
    imbalance = max(rates) - min(rates) if rates else 1.0
    absolute_limit = float(plan.decision_rules["max_missing_rate"])
    invalid = max(rates or [1.0]) > absolute_limit or imbalance > 0.15
    warning = not invalid and imbalance > 0.08
    return {
        "name": "missingness_dropout_imbalance",
        "status": "invalid" if invalid else ("warning" if warning else "valid"),
        "per_variant": per_arm,
        "max_missing_rate": round(max(rates or [1.0]), 8),
        "missing_rate_imbalance": round(imbalance, 8),
        "absolute_limit": absolute_limit,
        "imbalance_warning": 0.08,
        "imbalance_invalid": 0.15,
    }

def _window_check(plan: ExperimentPlan, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    before = after = malformed = 0
    start, stop = parse_timestamp(plan.start_at), parse_timestamp(plan.stop_at)
    for row in rows:
        value = row.get("observed_at")
        try:
            when = parse_timestamp(value)
        except Exception:
            malformed += 1
            continue
        before += int(when < start)
        after += int(when >= stop)
    leakage = before + after + malformed
    return {
        "name": "event_time_window_leakage",
        "status": "invalid" if leakage else "valid",
        "before_start": before, "at_or_after_stop": after,
        "malformed_timestamps": malformed,
    }
def _covariate_check(plan: ExperimentPlan, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    variants = [item.variant_id for item in plan.eligible_variants]
    diagnostics = []
    max_gap = 0.0
    for key in plan.stratification_keys:
        values = sorted({
            str(row.get("strata", {}).get(key))
            for row in rows
            if isinstance(row.get("strata"), Mapping) and key in row["strata"]
        })
        for value in values:
            rates = []
            counts = {}
            for arm in variants:
                arm_rows = [row for row in rows if row.get("assigned_variant") == arm]
                count = sum(
                    1 for row in arm_rows
                    if isinstance(row.get("strata"), Mapping)
                    and str(row["strata"].get(key)) == value
                )
                rate = count / len(arm_rows) if arm_rows else 0.0
                rates.append(rate)
                counts[arm] = {"count": count, "share": round(rate, 8)}
            gap = max(rates) - min(rates) if rates else 0.0
            max_gap = max(max_gap, gap)
            diagnostics.append({
                "covariate": key, "level": value,
                "absolute_share_gap": round(gap, 8),
                "by_variant": counts,
            })
    status = "invalid" if max_gap > 0.20 else ("warning" if max_gap > 0.10 else "valid")
    return {
        "name": "pre_randomization_covariate_imbalance",
        "status": status,
        "max_absolute_share_gap": round(max_gap, 8),
        "warning_threshold": 0.10, "invalid_threshold": 0.20,
        "diagnostics": diagnostics,
    }
def _guardrail_coverage_check(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    required = ("missing", "distribution_value", "recommendation_token")
    total = len(rows)
    covered = 0
    missing_by_field = {name: 0 for name in required}
    for row in rows:
        row_ok = True
        for name in required:
            if name not in row or row[name] is None:
                missing_by_field[name] += 1
                row_ok = False
        covered += int(row_ok)
    coverage = covered / total if total else 0.0
    status = "invalid" if coverage < 0.90 else ("warning" if coverage < 1.0 else "valid")
    return {
        "name": "guardrail_data_coverage", "status": status,
        "coverage": round(coverage, 8),
        "covered_rows": covered, "total_rows": total,
        "missing_by_field": missing_by_field,
    }

def _peek_check(plan: ExperimentPlan, report: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        return {
            "name": "sequential_peek_stopping_conformance",
            "status": "invalid", "reasons": ["missing_sequential_report"],
        }
    reasons = []
    sizes = list(plan.decision_rules["look_sample_sizes"])
    history = report.get("look_history")
    if not isinstance(history, list) or not history:
        reasons.append("missing_look_history")
        history = []
    for row in history:
        look = row.get("look_number")
        sample = row.get("look_sample_size")
        if not isinstance(look, int) or look < 1 or look > len(sizes):
            reasons.append("undeclared_look")
            continue
        if sample != sizes[look - 1]:
            reasons.append("undeclared_peek_sample_size")
    final_look = report.get("final_look_number")
    state = report.get("state")
    if state == "analysis_complete" and isinstance(final_look, int) and final_look < len(sizes):
        comparisons = report.get("comparisons") or []
        crossed = any(
            row.get("crosses_positive_boundary") or row.get("crosses_negative_boundary")
            for row in comparisons if isinstance(row, Mapping)
        )
        if not crossed:
            reasons.append("early_stop_without_boundary")
    if state == "continue" and final_look == len(sizes):
        reasons.append("continued_past_final_look")
    reasons = sorted(set(reasons))
    return {
        "name": "sequential_peek_stopping_conformance",
        "status": "invalid" if reasons else "valid",
        "reasons": reasons,
        "declared_look_sample_sizes": sizes,
    }

def _wilson(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    if trials == 0:
        return (0.0, 1.0)
    p = successes / trials
    z2 = z * z
    denom = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denom
    margin = z * math.sqrt(
        p * (1 - p) / trials + z2 / (4 * trials * trials)
    ) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))

def monte_carlo_aa_calibration(
    plan: ExperimentPlan,
    *,
    seed: int = DEFAULT_INTEGRITY_SEED,
    simulations: int = 1000,
    null_rate: float = 0.10,
) -> dict[str, Any]:
    if simulations < 100:
        raise ExperimentIntegrityError("simulations must be >= 100")
    max_n = int(plan.decision_rules["look_sample_sizes"][-1])
    assignments = []
    for index in range(max_n):
        unit = f"mc-unit-{index:05d}"
        strata = {
            "region": ("na", "eu", "apac")[index % 3],
            "device": ("mobile", "desktop")[index % 2],
        }
        assignments.append(
            deterministic_assignment(plan, unit_id=unit, strata=strata)
        )
    variants = [item.variant_id for item in plan.eligible_variants]
    if len(variants) != 2:
        raise ExperimentIntegrityError("A/A calibration fixture requires two arms")
    scope = int(plan.decision_rules["max_looks"]) * (len(variants) - 1)
    alpha_each = float(plan.decision_rules["alpha"]) / scope
    critical = NormalDist().inv_cdf(1 - alpha_each / 2)
    rng = random.Random(seed)
    false_positives = 0
    stop_looks = {str(i): 0 for i in range(1, len(plan.decision_rules["look_sample_sizes"]) + 1)}
    for _ in range(simulations):
        outcomes = [int(rng.random() < null_rate) for _ in range(max_n)]
        rejected = False
        for look, size in enumerate(plan.decision_rules["look_sample_sizes"], 1):
            control = [
                outcomes[i] for i in range(size) if assignments[i] == variants[0]
            ]
            treatment = [
                outcomes[i] for i in range(size) if assignments[i] == variants[1]
            ]
            p0 = sum(control) / len(control)
            p1 = sum(treatment) / len(treatment)
            se = math.sqrt(
                p0 * (1 - p0) / len(control)
                + p1 * (1 - p1) / len(treatment)
            )
            if se and abs((p1 - p0) / se) > critical:
                false_positives += 1
                stop_looks[str(look)] += 1
                rejected = True
                break
        if not rejected:
            stop_looks["no_rejection"] = stop_looks.get("no_rejection", 0) + 1
    rate = false_positives / simulations
    lower, upper = _wilson(false_positives, simulations)
    alpha = float(plan.decision_rules["alpha"])
    status = "invalid" if rate > alpha else ("warning" if upper > alpha else "valid")
    return {
        "calibration_version": CALIBRATION_VERSION,
        "seed": seed, "simulations": simulations,
        "null_success_rate": null_rate,
        "method": "fixed_seed_null_monte_carlo_v1",
        "sequential_method": "bonferroni_fixed_max_looks_two_sided_z_v1",
        "familywise_alpha": alpha,
        "false_positive_count": false_positives,
        "false_positive_rate": round(rate, 8),
        "wilson_95_lower": round(lower, 8),
        "wilson_95_upper": round(upper, 8),
        "stopping_counts": stop_looks,
        "status": status,
        "causal": False,
        "interpretation": (
            "Synthetic A/A null calibration of repeated-look false-positive behavior; "
            "not a business or historical causal claim."
        ),
    }

def _aa_check(calibration: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(calibration, Mapping):
        return {"name": "aa_null_calibration", "status": "invalid",
                "reasons": ["missing_aa_calibration"]}
    status = calibration.get("status")
    if status not in {"valid", "warning", "invalid"}:
        status = "invalid"
    return {
        "name": "aa_null_calibration", "status": status,
        "false_positive_rate": calibration.get("false_positive_rate"),
        "wilson_95_upper": calibration.get("wilson_95_upper"),
        "familywise_alpha": calibration.get("familywise_alpha"),
        "seed": calibration.get("seed"),
        "simulations": calibration.get("simulations"),
    }

def evaluate_experiment_integrity(
    plan: ExperimentPlan,
    evidence_payload: Mapping[str, Any],
    sequential_report: Mapping[str, Any] | None,
    *,
    plan_sha256: str,
    corpus_sha256: str,
    sequential_report_sha256: str,
    calibration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw_rows = evidence_payload.get("observations", [])
    if not isinstance(raw_rows, list):
        raw_rows = []
    checks = [
        _aa_check(calibration),
        _allocation_check(plan, raw_rows),
        _assignment_check(plan, raw_rows),
        _missingness_check(plan, raw_rows),
        _window_check(plan, raw_rows),
        _covariate_check(plan, raw_rows),
        _peek_check(plan, sequential_report),
        _guardrail_coverage_check(raw_rows),
    ]
    invalid = sorted(check["name"] for check in checks if check["status"] == "invalid")
    warning = sorted(check["name"] for check in checks if check["status"] == "warning")
    binding_reasons = []
    if evidence_payload.get("plan_digest") != plan_digest(plan):
        binding_reasons.append("evidence_plan_digest_mismatch")
    if sequential_report is not None and sequential_report.get("plan_digest") != plan_digest(plan):
        binding_reasons.append("report_plan_digest_mismatch")
    if binding_reasons:
        invalid.extend(binding_reasons)
    status = _status(invalid, warning)
    source_kind = evidence_payload.get("source_kind")
    randomized = source_kind == "synthetic_randomized_assignment"
    return {
        "integrity_version": INTEGRITY_VERSION,
        "experiment_id": plan.experiment_id,
        "status": status,
        "reasons": {
            "invalid": sorted(set(invalid)),
            "warning": warning,
        },
        "bindings": {
            "plan_sha256": plan_sha256,
            "plan_digest": plan_digest(plan),
            "randomization_sha256": randomization_digest(plan, raw_rows),
            "corpus_sha256": corpus_sha256,
            "sequential_report_sha256": sequential_report_sha256,
        },
        "sample_counts": {
            "raw_observations": len(raw_rows),
            "unique_observation_ids": len({
                row.get("observation_id") for row in raw_rows
                if isinstance(row, Mapping) and isinstance(row.get("observation_id"), str)
            }),
            "unique_assignment_units": len({
                row.get("unit_id") for row in raw_rows
                if isinstance(row, Mapping) and isinstance(row.get("unit_id"), str)
            }),
        },
        "checks": checks,
        "randomized_evidence": randomized,
        "treatment_effect_interpretation_allowed": bool(
            randomized and status != "invalid"
        ),
        "raw_report_inspectable": True,
        "auto_publish": False,
        "external_mutation": False,
    }

def generate_integrity_synthetic_corpus(
    wave7_corpus: Mapping[str, Any],
    wave7_report: Mapping[str, Any],
) -> dict[str, Any]:
    plan = ExperimentPlan.from_dict(wave7_corpus["plan"])
    null_payload = json.loads(_canonical(wave7_corpus["cases"]["null_effect"]))
    null_report = json.loads(_canonical(wave7_report["cases"]["null_effect"]))

    srm_payload = json.loads(_canonical(wave7_corpus["cases"]["sample_ratio_mismatch"]))
    srm_report = json.loads(_canonical(wave7_report["cases"]["sample_ratio_mismatch"]))

    contamination = json.loads(_canonical(null_payload))
    contamination["observations"] = contamination["observations"][:400]
    source = contamination["observations"][0]
    contaminated = dict(source)
    contaminated["observation_id"] = source["observation_id"] + "-cross-arm"
    contaminated["assigned_variant"] = (
        "control" if source["assigned_variant"] == "treatment" else "treatment"
    )
    contamination["observations"].append(contaminated)

    differential = json.loads(_canonical(null_payload))
    differential["observations"] = differential["observations"][:400]
    treatment_rows = [
        row for row in differential["observations"]
        if row["assigned_variant"] == "treatment"
    ]
    for row in treatment_rows[:80]:
        row["missing"] = True

    leakage = json.loads(_canonical(null_payload))
    leakage["observations"] = leakage["observations"][:400]
    for row in leakage["observations"][:20]:
        row["observed_at"] = "2026-10-02T00:00:00Z"

    misuse_evidence = json.loads(_canonical(null_payload))
    misuse_evidence["observations"] = misuse_evidence["observations"][:400]
    misuse_report = json.loads(_canonical(null_report))
    first_look = json.loads(_canonical(misuse_report["look_history"][0]))
    first_look["state"] = "analysis_complete"
    misuse_report["look_history"] = [first_look]
    misuse_report["final_look_number"] = 1
    misuse_report["final_look_sample_size"] = 400
    misuse_report["sample_counts"] = first_look["sample_counts"]
    misuse_report["allocation"] = first_look["allocation"]
    misuse_report["guardrails"] = first_look["guardrails"]
    misuse_report["comparisons"] = first_look["comparisons"]
    misuse_report["state"] = "analysis_complete"

    return {
        "corpus_version": CORPUS_VERSION,
        "seed": DEFAULT_INTEGRITY_SEED,
        "plan_digest": plan_digest(plan),
        "cases": {
            "valid_null_aa": {
                "evidence": null_payload, "sequential_report": null_report,
            },
            "sample_ratio_mismatch": {
                "evidence": srm_payload, "sequential_report": srm_report,
            },
            "cross_arm_contamination": {
                "evidence": contamination, "sequential_report": null_report,
            },
            "differential_missingness": {
                "evidence": differential, "sequential_report": null_report,
            },
            "late_event_leakage": {
                "evidence": leakage, "sequential_report": null_report,
            },
            "early_stopping_misuse": {
                "evidence": misuse_evidence, "sequential_report": misuse_report,
            },
        },
    }

def build_integrity_report(
    plan: ExperimentPlan,
    corpus: Mapping[str, Any],
    *,
    plan_sha256: str,
    corpus_sha256: str,
    calibration: Mapping[str, Any],
) -> dict[str, Any]:
    if corpus.get("corpus_version") != CORPUS_VERSION:
        raise ExperimentIntegrityError("unsupported integrity corpus version")
    cases = {}
    for name in sorted(corpus["cases"]):
        case = corpus["cases"][name]
        report_wire = _canonical(case["sequential_report"])
        case_wire = _canonical(case)
        cases[name] = evaluate_experiment_integrity(
            plan,
            case["evidence"],
            case["sequential_report"],
            plan_sha256=plan_sha256,
            corpus_sha256=_sha(case_wire),
            sequential_report_sha256=_sha(report_wire),
            calibration=calibration,
        )
    return {
        "report_version": "experiment_integrity.report.v1",
        "integrity_version": INTEGRITY_VERSION,
        "seed": corpus["seed"],
        "plan_sha256": plan_sha256,
        "plan_digest": plan_digest(plan),
        "corpus_sha256": corpus_sha256,
        "calibration": dict(calibration),
        "cases": cases,
        "status_counts": {
            status: sum(1 for row in cases.values() if row["status"] == status)
            for status in ("valid", "warning", "invalid")
        },
        "creator_feedback_contract_version": "1.0",
        "historical_observational_contract_preserved": True,
        "auto_publish": False,
        "external_mutation": False,
    }
