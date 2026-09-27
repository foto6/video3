from __future__ import annotations

import hashlib
import json
import math
import os
import random
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import NormalDist
from typing import Any, Mapping, Sequence

from .experiment_protocol import ExperimentPlan, plan_digest
from .event_stream import parse_timestamp

REGISTRY_VERSION = "experiment_registry.v1"
LEDGER_VERSION = "experiment_registry_ledger.v1"
FAMILY_REPORT_VERSION = "experiment_family_report.v1"
CORPUS_VERSION = "experiment_registry.synthetic_corpus.v1"
DEFAULT_REGISTRY_SEED = 920260927
MULTIPLICITY_METHOD = "holm_bonferroni_v1"

class ExperimentRegistryError(ValueError):
    pass

class RegistryConflictError(ExperimentRegistryError):
    pass

def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))

def _sha(value: Any) -> str:
    wire = value if isinstance(value, str) else _canonical(value)
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()

def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExperimentRegistryError(f"{name} must be a non-empty string")
    return value

@dataclass(frozen=True)
class ExperimentRegistryEntry:
    registry_version: str
    experiment_id: str
    revision: int
    predecessor_freeze_hash: str | None
    amendment_reason: str | None
    hypothesis: str
    primary_metrics: tuple[str, ...]
    guardrail_metrics: tuple[str, ...]
    arms: tuple[tuple[str, float], ...]
    population: Mapping[str, Any]
    window: Mapping[str, str]
    sequential_policy: Mapping[str, Any]
    planned_analyses: tuple[Mapping[str, Any], ...]
    family_id: str
    multiplicity_method: str
    family_alpha: float
    created_at: str
    frozen_at: str
    creation_hash: str
    freeze_hash: str
    auto_publish: bool
    external_mutation: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_version": self.registry_version,
            "experiment_id": self.experiment_id,
            "revision": self.revision,
            "predecessor_freeze_hash": self.predecessor_freeze_hash,
            "amendment_reason": self.amendment_reason,
            "hypothesis": self.hypothesis,
            "primary_metrics": list(self.primary_metrics),
            "guardrail_metrics": list(self.guardrail_metrics),
            "arms": [
                {"variant_id": variant_id, "weight": weight}
                for variant_id, weight in self.arms
            ],
            "population": dict(self.population),
            "window": dict(self.window),
            "sequential_policy": dict(self.sequential_policy),
            "planned_analyses": [dict(item) for item in self.planned_analyses],
            "family": {
                "family_id": self.family_id,
                "multiplicity_method": self.multiplicity_method,
                "family_alpha": self.family_alpha,
            },
            "created_at": self.created_at,
            "frozen_at": self.frozen_at,
            "creation_hash": self.creation_hash,
            "freeze_hash": self.freeze_hash,
            "auto_publish": self.auto_publish,
            "external_mutation": self.external_mutation,
        }

    def to_json(self) -> str:
        return _canonical(type(self).from_dict(self.to_dict()).to_dict())
    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentRegistryEntry":
        expected = {
            "registry_version", "experiment_id", "revision",
            "predecessor_freeze_hash", "amendment_reason", "hypothesis",
            "primary_metrics", "guardrail_metrics", "arms", "population",
            "window", "sequential_policy", "planned_analyses", "family",
            "created_at", "frozen_at", "creation_hash", "freeze_hash",
            "auto_publish", "external_mutation",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ExperimentRegistryError("registry entry fields must match v1 exactly")
        if payload["registry_version"] != REGISTRY_VERSION:
            raise ExperimentRegistryError("unsupported registry version")
        experiment_id = _nonempty(payload["experiment_id"], "experiment_id")
        hypothesis = _nonempty(payload["hypothesis"], "hypothesis")
        revision = payload["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ExperimentRegistryError("revision must be positive integer")
        predecessor = payload["predecessor_freeze_hash"]
        reason = payload["amendment_reason"]
        if revision == 1:
            if predecessor is not None or reason is not None:
                raise ExperimentRegistryError("revision 1 cannot declare predecessor/amendment")
        else:
            _nonempty(predecessor, "predecessor_freeze_hash")
            _nonempty(reason, "amendment_reason")

        def string_tuple(name: str) -> tuple[str, ...]:
            raw = payload[name]
            if (not isinstance(raw, list) or not raw
                    or any(not isinstance(v, str) or not v for v in raw)
                    or len(raw) != len(set(raw))):
                raise ExperimentRegistryError(f"{name} must be unique strings")
            return tuple(raw)
        primary = string_tuple("primary_metrics")
        guardrails = string_tuple("guardrail_metrics")

        raw_arms = payload["arms"]
        if not isinstance(raw_arms, list) or len(raw_arms) < 2:
            raise ExperimentRegistryError("arms requires at least two variants")
        arms, seen, total = [], set(), 0.0
        for raw in raw_arms:
            if not isinstance(raw, Mapping) or set(raw) != {"variant_id", "weight"}:
                raise ExperimentRegistryError("arm fields invalid")
            vid, weight = raw["variant_id"], raw["weight"]
            _nonempty(vid, "variant_id")
            if vid in seen:
                raise ExperimentRegistryError("duplicate variant id")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
                raise ExperimentRegistryError("arm weight must be positive")
            seen.add(vid); total += float(weight); arms.append((vid, float(weight)))
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ExperimentRegistryError("arm weights must sum to 1.0")
        population = payload["population"]
        if not isinstance(population, Mapping) or set(population) != {
            "assignment_unit", "stratification_keys", "eligibility"
        }:
            raise ExperimentRegistryError("population fields invalid")
        _nonempty(population["assignment_unit"], "population.assignment_unit")
        _nonempty(population["eligibility"], "population.eligibility")
        strata = population["stratification_keys"]
        if (not isinstance(strata, list)
                or any(not isinstance(v, str) or not v for v in strata)
                or len(strata) != len(set(strata))):
            raise ExperimentRegistryError("population stratification_keys invalid")

        window = payload["window"]
        if not isinstance(window, Mapping) or set(window) != {"start", "stop"}:
            raise ExperimentRegistryError("window fields invalid")
        if parse_timestamp(window["start"]) >= parse_timestamp(window["stop"]):
            raise ExperimentRegistryError("registry window invalid")

        sequential = payload["sequential_policy"]
        seq_fields = {
            "method", "plan_digest", "familywise_alpha",
            "max_looks", "look_sample_sizes"
        }
        if not isinstance(sequential, Mapping) or set(sequential) != seq_fields:
            raise ExperimentRegistryError("sequential_policy fields invalid")
        _nonempty(sequential["method"], "sequential_policy.method")
        _nonempty(sequential["plan_digest"], "sequential_policy.plan_digest")
        alpha = sequential["familywise_alpha"]
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
            raise ExperimentRegistryError("sequential familywise alpha invalid")
        looks = sequential["max_looks"]
        sizes = sequential["look_sample_sizes"]
        if (isinstance(looks, bool) or not isinstance(looks, int) or looks < 1
                or not isinstance(sizes, list) or len(sizes) != looks
                or sizes != sorted(sizes) or len(sizes) != len(set(sizes))
                or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in sizes)):
            raise ExperimentRegistryError("sequential look policy invalid")

        analyses = payload["planned_analyses"]
        if not isinstance(analyses, list) or not analyses:
            raise ExperimentRegistryError("planned_analyses required")
        analysis_ids = set()
        normalized_analyses = []
        for raw in analyses:
            fields = {"analysis_id", "metric", "contrast", "sidedness"}
            if not isinstance(raw, Mapping) or set(raw) != fields:
                raise ExperimentRegistryError("planned analysis fields invalid")
            aid = _nonempty(raw["analysis_id"], "analysis_id")
            metric = _nonempty(raw["metric"], "metric")
            _nonempty(raw["contrast"], "contrast")
            if raw["sidedness"] != "two_sided":
                raise ExperimentRegistryError("only two_sided confirmatory analysis supported")
            if aid in analysis_ids:
                raise ExperimentRegistryError("duplicate planned analysis id")
            if metric not in primary:
                raise ExperimentRegistryError("planned analysis metric must be preregistered primary")
            analysis_ids.add(aid); normalized_analyses.append(dict(raw))
        family = payload["family"]
        if not isinstance(family, Mapping) or set(family) != {
            "family_id", "multiplicity_method", "family_alpha"
        }:
            raise ExperimentRegistryError("family fields invalid")
        family_id = _nonempty(family["family_id"], "family_id")
        if family["multiplicity_method"] != MULTIPLICITY_METHOD:
            raise ExperimentRegistryError("unsupported family multiplicity method")
        family_alpha = family["family_alpha"]
        if (isinstance(family_alpha, bool)
                or not isinstance(family_alpha, (int, float))
                or not 0 < family_alpha < 1):
            raise ExperimentRegistryError("family_alpha invalid")
        created_at = _nonempty(payload["created_at"], "created_at")
        frozen_at = _nonempty(payload["frozen_at"], "frozen_at")
        if parse_timestamp(created_at) > parse_timestamp(frozen_at):
            raise ExperimentRegistryError("created_at cannot follow frozen_at")
        if payload["auto_publish"] is not False or payload["external_mutation"] is not False:
            raise ExperimentRegistryError("registry must be read-only/non-publishing")

        creation_hash = _nonempty(payload["creation_hash"], "creation_hash")
        freeze_hash = _nonempty(payload["freeze_hash"], "freeze_hash")
        if len(creation_hash) != 64 or len(freeze_hash) != 64:
            raise ExperimentRegistryError("registry hashes must be SHA-256 hex")

        entry = cls(
            REGISTRY_VERSION, experiment_id, revision, predecessor, reason,
            hypothesis, primary, guardrails, tuple(arms),
            {
                "assignment_unit": population["assignment_unit"],
                "stratification_keys": list(strata),
                "eligibility": population["eligibility"],
            },
            {"start": window["start"], "stop": window["stop"]},
            {
                "method": sequential["method"],
                "plan_digest": sequential["plan_digest"],
                "familywise_alpha": float(alpha),
                "max_looks": looks,
                "look_sample_sizes": list(sizes),
            },
            tuple(normalized_analyses), family_id, MULTIPLICITY_METHOD,
            float(family_alpha), created_at, frozen_at, creation_hash,
            freeze_hash, False, False,
        )
        if _creation_hash(entry) != creation_hash:
            raise ExperimentRegistryError("creation_hash mismatch")
        if _freeze_hash(entry) != freeze_hash:
            raise ExperimentRegistryError("freeze_hash mismatch")
        return entry

def _creation_material(entry: ExperimentRegistryEntry) -> dict[str, Any]:
    return {
        "registry_version": entry.registry_version,
        "experiment_id": entry.experiment_id,
        "revision": entry.revision,
        "predecessor_freeze_hash": entry.predecessor_freeze_hash,
        "amendment_reason": entry.amendment_reason,
        "hypothesis": entry.hypothesis,
        "primary_metrics": list(entry.primary_metrics),
        "guardrail_metrics": list(entry.guardrail_metrics),
        "arms": [
            {"variant_id": variant_id, "weight": weight}
            for variant_id, weight in entry.arms
        ],
        "population": dict(entry.population),
        "window": dict(entry.window),
        "sequential_policy": dict(entry.sequential_policy),
        "planned_analyses": [dict(item) for item in entry.planned_analyses],
        "family": {
            "family_id": entry.family_id,
            "multiplicity_method": entry.multiplicity_method,
            "family_alpha": entry.family_alpha,
        },
        "created_at": entry.created_at,
        "auto_publish": False,
        "external_mutation": False,
    }

def _creation_hash(entry: ExperimentRegistryEntry) -> str:
    return _sha(_creation_material(entry))

def _freeze_hash(entry: ExperimentRegistryEntry) -> str:
    return _sha({
        "creation_hash": entry.creation_hash,
        "frozen_at": entry.frozen_at,
        "experiment_id": entry.experiment_id,
        "revision": entry.revision,
        "predecessor_freeze_hash": entry.predecessor_freeze_hash,
    })

def build_registry_entry(
    plan: ExperimentPlan,
    *,
    hypothesis: str,
    family_id: str,
    created_at: str,
    frozen_at: str,
    eligibility: str = "synthetic_preregistered_population_v1",
    revision: int = 1,
    predecessor_freeze_hash: str | None = None,
    amendment_reason: str | None = None,
    primary_metrics: Sequence[str] | None = None,
    planned_analyses: Sequence[Mapping[str, Any]] | None = None,
    family_alpha: float = 0.05,
) -> ExperimentRegistryEntry:
    primary = tuple(primary_metrics or (plan.primary_metric,))
    analyses = tuple(planned_analyses or ({
        "analysis_id": f"{plan.experiment_id}:{primary[0]}:treatment_vs_control",
        "metric": primary[0],
        "contrast": "treatment_vs_control",
        "sidedness": "two_sided",
    },))
    placeholder = ExperimentRegistryEntry(
        REGISTRY_VERSION, plan.experiment_id, revision,
        predecessor_freeze_hash, amendment_reason, hypothesis,
        primary, tuple(plan.guardrail_metrics),
        tuple((item.variant_id, item.weight) for item in plan.eligible_variants),
        {
            "assignment_unit": plan.assignment_unit,
            "stratification_keys": list(plan.stratification_keys),
            "eligibility": eligibility,
        },
        {"start": plan.start_at, "stop": plan.stop_at},
        {
            "method": "bonferroni_fixed_max_looks_two_sided_z_v1",
            "plan_digest": plan_digest(plan),
            "familywise_alpha": float(plan.decision_rules["alpha"]),
            "max_looks": int(plan.decision_rules["max_looks"]),
            "look_sample_sizes": list(plan.decision_rules["look_sample_sizes"]),
        },
        analyses, family_id, MULTIPLICITY_METHOD, float(family_alpha),
        created_at, frozen_at, "", "", False, False,
    )
    creation = _creation_hash(placeholder)
    with_creation = replace(placeholder, creation_hash=creation)
    freeze = _freeze_hash(with_creation)
    frozen = replace(with_creation, freeze_hash=freeze)
    return ExperimentRegistryEntry.from_dict(frozen.to_dict())

class ExperimentRegistryLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._entries: dict[tuple[str, int], ExperimentRegistryEntry] = {}
        self._latest: dict[str, ExperimentRegistryEntry] = {}
        self._outcomes: dict[tuple[str, int], str] = {}
        self._sequence = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RegistryConflictError(
                    f"invalid registry ledger JSON line {line_number}"
                ) from exc
            expected = {
                "ledger_version", "sequence", "event_type",
                "experiment_id", "revision", "freeze_hash",
                "entry", "outcome_digest",
            }
            if not isinstance(row, Mapping) or set(row) != expected:
                raise RegistryConflictError("registry ledger row fields invalid")
            if row["ledger_version"] != LEDGER_VERSION:
                raise RegistryConflictError("unsupported registry ledger version")
            if row["sequence"] != self._sequence + 1:
                raise RegistryConflictError("registry ledger sequence not contiguous")
            event_type = row["event_type"]
            if event_type == "freeze":
                if row["entry"] is None or row["outcome_digest"] is not None:
                    raise RegistryConflictError("freeze ledger row malformed")
                entry = ExperimentRegistryEntry.from_dict(row["entry"])
                if (entry.experiment_id != row["experiment_id"]
                        or entry.revision != row["revision"]
                        or entry.freeze_hash != row["freeze_hash"]):
                    raise RegistryConflictError("freeze ledger binding mismatch")
                self._apply_freeze(entry, loading=True)
            elif event_type == "outcome_consumed":
                if row["entry"] is not None:
                    raise RegistryConflictError("outcome ledger row cannot contain entry")
                digest = _nonempty(row["outcome_digest"], "outcome_digest")
                self._apply_outcome(
                    row["experiment_id"], row["revision"], row["freeze_hash"],
                    digest, loading=True,
                )
            else:
                raise RegistryConflictError("unknown registry ledger event type")
            self._sequence += 1

    def _append_row(self, row: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        wire = _canonical(row)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(wire + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._sequence += 1

    def _apply_freeze(
        self, entry: ExperimentRegistryEntry, *, loading: bool = False
    ) -> str:
        key = (entry.experiment_id, entry.revision)
        existing = self._entries.get(key)
        if existing is not None:
            if existing.freeze_hash == entry.freeze_hash:
                return "duplicate"
            raise RegistryConflictError(
                "duplicate registry identity changed after freeze"
            )
        latest = self._latest.get(entry.experiment_id)
        if latest is None:
            if entry.revision != 1:
                raise RegistryConflictError("first registry revision must be 1")
        else:
            if entry.revision != latest.revision + 1:
                raise RegistryConflictError("registry revision must advance by one")
            if entry.predecessor_freeze_hash != latest.freeze_hash:
                raise RegistryConflictError("amendment predecessor hash mismatch")
            if not entry.amendment_reason:
                raise RegistryConflictError("amendment reason required")
            if (latest.experiment_id, latest.revision) in self._outcomes:
                raise RegistryConflictError(
                    "amendment cannot follow outcome consumption"
                )
        self._entries[key] = entry
        self._latest[entry.experiment_id] = entry
        return "loaded" if loading else "frozen"

    def freeze(self, entry: ExperimentRegistryEntry) -> str:
        status = self._apply_freeze(entry)
        if status == "duplicate":
            return status
        self._append_row({
            "ledger_version": LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "event_type": "freeze",
            "experiment_id": entry.experiment_id,
            "revision": entry.revision,
            "freeze_hash": entry.freeze_hash,
            "entry": entry.to_dict(),
            "outcome_digest": None,
        })
        return "frozen"
    def _apply_outcome(
        self, experiment_id: str, revision: int, freeze_hash: str,
        outcome_digest: str, *, loading: bool = False,
    ) -> str:
        key = (experiment_id, revision)
        entry = self._entries.get(key)
        if entry is None or entry.freeze_hash != freeze_hash:
            raise RegistryConflictError("outcome references unknown registry freeze")
        latest = self._latest.get(experiment_id)
        if latest is None or latest.revision != revision:
            raise RegistryConflictError("outcome must bind latest registry revision")
        existing = self._outcomes.get(key)
        if existing is not None:
            if existing == outcome_digest:
                return "duplicate"
            raise RegistryConflictError("outcome digest changed for frozen revision")
        self._outcomes[key] = outcome_digest
        return "loaded" if loading else "recorded"

    def record_outcome_consumed(
        self, experiment_id: str, revision: int,
        freeze_hash: str, outcome_digest: str,
    ) -> str:
        _nonempty(outcome_digest, "outcome_digest")
        status = self._apply_outcome(
            experiment_id, revision, freeze_hash, outcome_digest
        )
        if status == "duplicate":
            return status
        self._append_row({
            "ledger_version": LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "event_type": "outcome_consumed",
            "experiment_id": experiment_id,
            "revision": revision,
            "freeze_hash": freeze_hash,
            "entry": None,
            "outcome_digest": outcome_digest,
        })
        return "recorded"

    def latest(self, experiment_id: str) -> ExperimentRegistryEntry | None:
        return self._latest.get(experiment_id)

    def outcome_consumed(self, experiment_id: str, revision: int) -> bool:
        return (experiment_id, revision) in self._outcomes

    @property
    def row_count(self) -> int:
        return self._sequence

def _two_sided_p(effect: float, standard_error: float) -> float:
    if standard_error <= 0:
        return 0.0 if effect != 0 else 1.0
    z = abs(effect / standard_error)
    return max(0.0, min(1.0, 2.0 * (1.0 - NormalDist().cdf(z))))

def evidence_from_sequential_result(
    entry: ExperimentRegistryEntry,
    metric: str,
    sequential_result: Mapping[str, Any],
    *,
    sequential_report_sha256: str,
    integrity_version: str,
    integrity_status: str,
    integrity_report_sha256: str,
) -> dict[str, Any]:
    comparisons = sequential_result.get("comparisons")
    if not isinstance(comparisons, list) or not comparisons:
        raise ExperimentRegistryError("sequential result has no comparison evidence")
    comparison = comparisons[0]
    effect = float(comparison["effect"])
    se = float(comparison["standard_error"])
    raw_p = _two_sided_p(effect, se)
    scope = int(sequential_result["method"]["multiplicity_scope"])
    sequential_p = min(1.0, raw_p * scope)
    return {
        "experiment_id": entry.experiment_id,
        "registry_revision": entry.revision,
        "registry_freeze_hash": entry.freeze_hash,
        "metric": metric,
        "effect_estimate": round(effect, 8),
        "raw_two_sided_p_value": round(raw_p, 12),
        "sequential_multiplicity_scope": scope,
        "sequential_valid_p_value": round(sequential_p, 12),
        "sequential_report_sha256": sequential_report_sha256,
        "integrity_reference": {
            "integrity_version": integrity_version,
            "status": integrity_status,
            "report_sha256": integrity_report_sha256,
        },
        "source_kind": "synthetic_randomized_assignment",
    }

def _validate_family_evidence(
    evidence: Mapping[str, Any],
    entries: Mapping[str, ExperimentRegistryEntry],
) -> dict[str, Any]:
    fields = {
        "experiment_id", "registry_revision", "registry_freeze_hash",
        "metric", "effect_estimate", "raw_two_sided_p_value",
        "sequential_multiplicity_scope", "sequential_valid_p_value",
        "sequential_report_sha256", "integrity_reference", "source_kind",
    }
    if not isinstance(evidence, Mapping) or set(evidence) != fields:
        raise ExperimentRegistryError("family evidence fields invalid")
    experiment_id = _nonempty(evidence["experiment_id"], "experiment_id")
    entry = entries.get(experiment_id)
    if entry is None:
        raise ExperimentRegistryError("family evidence references unregistered experiment")
    if evidence["registry_revision"] != entry.revision:
        raise ExperimentRegistryError("family evidence registry revision mismatch")
    if evidence["registry_freeze_hash"] != entry.freeze_hash:
        raise ExperimentRegistryError("family evidence registry freeze mismatch")
    metric = _nonempty(evidence["metric"], "metric")
    raw_p = evidence["raw_two_sided_p_value"]
    sequential_p = evidence["sequential_valid_p_value"]
    scope = evidence["sequential_multiplicity_scope"]
    if (isinstance(raw_p, bool) or not isinstance(raw_p, (int, float))
            or not 0 <= float(raw_p) <= 1):
        raise ExperimentRegistryError("raw p-value invalid")
    if (isinstance(sequential_p, bool)
            or not isinstance(sequential_p, (int, float))
            or not 0 <= float(sequential_p) <= 1):
        raise ExperimentRegistryError("sequential p-value invalid")
    if isinstance(scope, bool) or not isinstance(scope, int) or scope < 1:
        raise ExperimentRegistryError("sequential multiplicity scope invalid")
    expected_seq = min(1.0, float(raw_p) * scope)
    if not math.isclose(
        float(sequential_p), expected_seq, rel_tol=0.0, abs_tol=1e-10
    ):
        raise ExperimentRegistryError("sequential p-value does not match declared scope")
    integrity = evidence["integrity_reference"]
    if not isinstance(integrity, Mapping) or set(integrity) != {
        "integrity_version", "status", "report_sha256"
    }:
        raise ExperimentRegistryError("integrity reference fields invalid")
    if integrity["status"] not in {"valid", "warning", "invalid"}:
        raise ExperimentRegistryError("integrity status invalid")
    if integrity["integrity_version"] != "experiment_integrity.v1":
        raise ExperimentRegistryError("integrity reference version invalid")
    _nonempty(integrity["report_sha256"], "integrity report hash")
    if evidence["source_kind"] not in {
        "synthetic_randomized_assignment", "historical_observational"
    }:
        raise ExperimentRegistryError("unsupported family evidence source")
    return json.loads(_canonical(evidence))

def _holm_adjust(pvalues: Mapping[str, float]) -> dict[str, dict[str, Any]]:
    ordered = sorted(pvalues.items(), key=lambda item: (item[1], item[0]))
    m = len(ordered)
    adjusted: dict[str, dict[str, Any]] = {}
    running = 0.0
    for index, (key, pvalue) in enumerate(ordered):
        multiplier = m - index
        candidate = min(1.0, pvalue * multiplier)
        running = max(running, candidate)
        adjusted[key] = {
            "rank": index + 1,
            "family_size": m,
            "holm_multiplier": multiplier,
            "adjusted_p_value": round(min(1.0, running), 12),
        }
    return adjusted

def build_family_report(
    entries: Sequence[ExperimentRegistryEntry],
    evidence_rows: Sequence[Mapping[str, Any]],
    *,
    family_id: str,
    family_alpha: float = 0.05,
) -> dict[str, Any]:
    _nonempty(family_id, "family_id")
    if not entries:
        raise ExperimentRegistryError("family report requires registry entries")
    by_experiment: dict[str, ExperimentRegistryEntry] = {}
    seen_identity: set[tuple[str, int]] = set()
    for entry in entries:
        identity = (entry.experiment_id, entry.revision)
        if identity in seen_identity:
            raise RegistryConflictError("duplicate registry identity in family")
        seen_identity.add(identity)
        if entry.family_id != family_id:
            raise ExperimentRegistryError("entry belongs to another family")
        if not math.isclose(
            entry.family_alpha, family_alpha, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ExperimentRegistryError("entry family alpha does not match report")
        previous = by_experiment.get(entry.experiment_id)
        if previous is not None:
            raise ExperimentRegistryError(
                "family report accepts only latest revision per experiment"
            )
        by_experiment[entry.experiment_id] = entry

    validated = [
        _validate_family_evidence(row, by_experiment) for row in evidence_rows
    ]
    evidence_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    exploratory: list[dict[str, Any]] = []
    for row in validated:
        entry = by_experiment[row["experiment_id"]]
        key = (row["experiment_id"], row["metric"])
        if row["metric"] not in entry.primary_metrics:
            exploratory.append({
                **row,
                "label": "exploratory",
                "confirmatory": False,
                "family_adjusted_p_value": None,
                "decision": False,
                "decision_reason": "metric_not_preregistered_primary",
            })
            continue
        if key in evidence_by_key:
            raise ExperimentRegistryError("duplicate family evidence for metric")
        evidence_by_key[key] = row

    slots: list[dict[str, Any]] = []
    family_inputs: dict[str, float] = {}
    for entry in sorted(entries, key=lambda item: item.experiment_id):
        for analysis in entry.planned_analyses:
            slot_id = (
                f"{entry.experiment_id}@{entry.revision}:"
                f"{analysis['analysis_id']}"
            )
            row = evidence_by_key.get(
                (entry.experiment_id, analysis["metric"])
            )
            if row is None:
                family_p = 1.0
                integrity_status = "missing"
            else:
                integrity_status = row["integrity_reference"]["status"]
                randomized = row["source_kind"] == "synthetic_randomized_assignment"
                if integrity_status == "invalid" or not randomized:
                    family_p = 1.0
                else:
                    family_p = float(row["sequential_valid_p_value"])
            family_inputs[slot_id] = family_p
            slots.append({
                "slot_id": slot_id,
                "entry": entry,
                "analysis": dict(analysis),
                "evidence": row,
                "integrity_status": integrity_status,
                "family_input_p_value": family_p,
            })

    adjusted = _holm_adjust(family_inputs)
    confirmatory = []
    for slot in slots:
        info = adjusted[slot["slot_id"]]
        row = slot["evidence"]
        if row is None:
            reason = "missing_confirmatory_evidence"
            decision = False
        elif row["source_kind"] != "synthetic_randomized_assignment":
            reason = "historical_observational_outside_randomized_inference"
            decision = False
        elif slot["integrity_status"] == "invalid":
            reason = "integrity_invalid"
            decision = False
        elif info["adjusted_p_value"] <= family_alpha:
            reason = "holm_adjusted_at_or_below_family_alpha"
            decision = True
        else:
            reason = "holm_adjusted_above_family_alpha"
            decision = False
        confirmatory.append({
            "slot_id": slot["slot_id"],
            "experiment_id": slot["entry"].experiment_id,
            "registry_revision": slot["entry"].revision,
            "registry_freeze_hash": slot["entry"].freeze_hash,
            "analysis_id": slot["analysis"]["analysis_id"],
            "metric": slot["analysis"]["metric"],
            "label": "confirmatory",
            "confirmatory": True,
            "raw_two_sided_p_value": (
                row["raw_two_sided_p_value"] if row else None
            ),
            "sequential_valid_p_value": (
                row["sequential_valid_p_value"] if row else None
            ),
            "family_input_p_value": round(slot["family_input_p_value"], 12),
            "family_adjusted_p_value": info["adjusted_p_value"],
            "holm_rank": info["rank"],
            "holm_multiplier": info["holm_multiplier"],
            "integrity_reference": (
                row["integrity_reference"] if row else None
            ),
            "decision": decision,
            "decision_reason": reason,
            "treatment_effect_interpretation_allowed": bool(
                decision
                and row is not None
                and row["integrity_reference"]["status"] != "invalid"
                and row["source_kind"] == "synthetic_randomized_assignment"
            ),
        })
    return {
        "family_report_version": FAMILY_REPORT_VERSION,
        "family_id": family_id,
        "multiplicity": {
            "method": MULTIPLICITY_METHOD,
            "family_alpha": float(family_alpha),
            "planned_confirmatory_count": len(confirmatory),
            "bonferroni_reference_alpha_per_test": round(
                family_alpha / len(confirmatory), 12
            ) if confirmatory else None,
            "input_is_sequentially_adjusted": True,
        },
        "registry": [
            {
                "experiment_id": entry.experiment_id,
                "revision": entry.revision,
                "freeze_hash": entry.freeze_hash,
                "creation_hash": entry.creation_hash,
                "hypothesis": entry.hypothesis,
                "primary_metrics": list(entry.primary_metrics),
            }
            for entry in sorted(entries, key=lambda item: item.experiment_id)
        ],
        "confirmatory": confirmatory,
        "exploratory": sorted(
            exploratory,
            key=lambda row: (row["experiment_id"], row["metric"]),
        ),
        "summary": {
            "confirmatory_decisions": sum(
                1 for row in confirmatory if row["decision"]
            ),
            "integrity_blocked": sum(
                1 for row in confirmatory
                if row["decision_reason"] == "integrity_invalid"
            ),
            "exploratory_metrics": len(exploratory),
        },
        "historical_observational_outside_randomized_inference": True,
        "auto_publish": False,
        "external_mutation": False,
    }

def _clone_plan(
    plan: ExperimentPlan, *, experiment_id: str, experiment_seed: int
) -> ExperimentPlan:
    payload = plan.to_dict()
    payload["experiment_id"] = experiment_id
    payload["experiment_seed"] = experiment_seed
    return ExperimentPlan.from_dict(payload)

def _synthetic_family_evidence(
    entry: ExperimentRegistryEntry,
    *,
    metric: str,
    raw_p: float,
    sequential_scope: int,
    effect: float,
    integrity_status: str = "valid",
    integrity_hash: str,
    report_hash: str,
    source_kind: str = "synthetic_randomized_assignment",
) -> dict[str, Any]:
    return {
        "experiment_id": entry.experiment_id,
        "registry_revision": entry.revision,
        "registry_freeze_hash": entry.freeze_hash,
        "metric": metric,
        "effect_estimate": round(effect, 8),
        "raw_two_sided_p_value": round(float(raw_p), 12),
        "sequential_multiplicity_scope": sequential_scope,
        "sequential_valid_p_value": round(
            min(1.0, float(raw_p) * sequential_scope), 12
        ),
        "sequential_report_sha256": report_hash,
        "integrity_reference": {
            "integrity_version": "experiment_integrity.v1",
            "status": integrity_status,
            "report_sha256": integrity_hash,
        },
        "source_kind": source_kind,
    }

def generate_registry_synthetic_corpus(
    base_plan: ExperimentPlan,
    wave7_report: Mapping[str, Any],
    *,
    wave7_report_sha256: str,
    integrity_report_sha256: str,
    seed: int = DEFAULT_REGISTRY_SEED,
) -> dict[str, Any]:
    created = "2026-08-20T00:00:00Z"
    frozen = "2026-08-20T01:00:00Z"

    valid_entry = build_registry_entry(
        base_plan,
        hypothesis="Treatment changes the preregistered synthetic primary success rate.",
        family_id="wave9-valid-family",
        created_at=created, frozen_at=frozen,
    )
    valid_evidence = evidence_from_sequential_result(
        valid_entry,
        base_plan.primary_metric,
        wave7_report["cases"]["null_effect"],
        sequential_report_sha256=wave7_report_sha256,
        integrity_version="experiment_integrity.v1",
        integrity_status="valid",
        integrity_report_sha256=integrity_report_sha256,
    )

    rng = random.Random(seed)
    null_entries = []
    null_evidence = []
    null_raw = []
    for index in range(40):
        exp_id = f"wave9-null-{index + 1:02d}"
        plan = _clone_plan(
            base_plan, experiment_id=exp_id,
            experiment_seed=seed + index + 1,
        )
        entry = build_registry_entry(
            plan,
            hypothesis=f"Null-family preregistered hypothesis {index + 1}.",
            family_id="wave9-many-null-family",
            created_at=created, frozen_at=frozen,
        )
        raw_p = rng.random()
        null_raw.append(raw_p)
        null_entries.append(entry)
        null_evidence.append(_synthetic_family_evidence(
            entry,
            metric=plan.primary_metric,
            raw_p=raw_p,
            sequential_scope=4,
            effect=0.0,
            integrity_hash=integrity_report_sha256,
            report_hash=_sha({"null_report": exp_id}),
        ))

    metric_entry = build_registry_entry(
        _clone_plan(
            base_plan, experiment_id="wave9-metric-swap",
            experiment_seed=seed + 100,
        ),
        hypothesis="Only the preregistered primary metric is confirmatory.",
        family_id="wave9-metric-swap-family",
        created_at=created, frozen_at=frozen,
    )
    metric_evidence = [
        _synthetic_family_evidence(
            metric_entry, metric=metric_entry.primary_metrics[0],
            raw_p=0.40, sequential_scope=4, effect=0.01,
            integrity_hash=integrity_report_sha256,
            report_hash=_sha("metric-primary"),
        ),
        _synthetic_family_evidence(
            metric_entry, metric="posthoc_metric_after_results",
            raw_p=0.000001, sequential_scope=4, effect=0.20,
            integrity_hash=integrity_report_sha256,
            report_hash=_sha("metric-posthoc"),
        ),
    ]

    duplicate_plan = _clone_plan(
        base_plan, experiment_id="wave9-duplicate-id",
        experiment_seed=seed + 200,
    )
    duplicate_a = build_registry_entry(
        duplicate_plan, hypothesis="Original frozen hypothesis.",
        family_id="wave9-governance-family",
        created_at=created, frozen_at=frozen,
    )
    duplicate_b = build_registry_entry(
        duplicate_plan, hypothesis="Changed hypothesis under same frozen identity.",
        family_id="wave9-governance-family",
        created_at=created, frozen_at=frozen,
    )

    amendment_plan = _clone_plan(
        base_plan, experiment_id="wave9-amendment",
        experiment_seed=seed + 300,
    )
    amendment_v1 = build_registry_entry(
        amendment_plan, hypothesis="Original preregistered amendment hypothesis.",
        family_id="wave9-governance-family",
        created_at=created, frozen_at=frozen,
    )
    amendment_v2 = build_registry_entry(
        amendment_plan, hypothesis="Amended hypothesis before any outcome data.",
        family_id="wave9-governance-family",
        created_at="2026-08-20T02:00:00Z",
        frozen_at="2026-08-20T03:00:00Z",
        revision=2,
        predecessor_freeze_hash=amendment_v1.freeze_hash,
        amendment_reason="Clarify hypothesis before first outcome consumption.",
    )

    seq_entries, seq_evidence = [], []
    for index, raw_p in enumerate((0.004, 0.008, 0.020), 1):
        plan = _clone_plan(
            base_plan, experiment_id=f"wave9-seq-family-{index}",
            experiment_seed=seed + 400 + index,
        )
        entry = build_registry_entry(
            plan, hypothesis=f"Sequential-family hypothesis {index}.",
            family_id="wave9-sequential-family",
            created_at=created, frozen_at=frozen,
        )
        seq_entries.append(entry)
        seq_evidence.append(_synthetic_family_evidence(
            entry, metric=plan.primary_metric, raw_p=raw_p,
            sequential_scope=4, effect=0.05,
            integrity_hash=integrity_report_sha256,
            report_hash=_sha({"sequential": index}),
        ))

    invalid_plan = _clone_plan(
        base_plan, experiment_id="wave9-integrity-invalid",
        experiment_seed=seed + 500,
    )
    invalid_entry = build_registry_entry(
        invalid_plan, hypothesis="Very small p-value with invalid integrity.",
        family_id="wave9-integrity-family",
        created_at=created, frozen_at=frozen,
    )
    invalid_evidence = _synthetic_family_evidence(
        invalid_entry, metric=invalid_plan.primary_metric,
        raw_p=0.0000001, sequential_scope=4, effect=0.50,
        integrity_status="invalid",
        integrity_hash=integrity_report_sha256,
        report_hash=_sha("integrity-invalid"),
    )

    return {
        "corpus_version": CORPUS_VERSION,
        "seed": seed,
        "valid_preregistered": {
            "entries": [valid_entry.to_dict()],
            "evidence": [valid_evidence],
        },
        "many_null_experiments": {
            "entries": [entry.to_dict() for entry in null_entries],
            "evidence": null_evidence,
            "naive_raw_below_0_05_count": sum(p < 0.05 for p in null_raw),
        },
        "metric_swap_after_results": {
            "entries": [metric_entry.to_dict()],
            "evidence": metric_evidence,
        },
        "duplicate_experiment_id_changed_hypothesis": {
            "first": duplicate_a.to_dict(),
            "conflicting_same_revision": duplicate_b.to_dict(),
            "expected": "reject_fail_closed",
        },
        "amendment_before_vs_after_outcome": {
            "revision_1": amendment_v1.to_dict(),
            "revision_2": amendment_v2.to_dict(),
            "outcome_digest": _sha("first-outcome-consumed"),
            "before_outcome_expected": "accept_new_revision",
            "after_outcome_expected": "reject_fail_closed",
        },
        "sequential_family_budget_interaction": {
            "entries": [entry.to_dict() for entry in seq_entries],
            "evidence": seq_evidence,
        },
        "integrity_invalid_block": {
            "entries": [invalid_entry.to_dict()],
            "evidence": [invalid_evidence],
        },
    }

def build_registry_family_report_bundle(
    corpus: Mapping[str, Any],
) -> dict[str, Any]:
    if corpus.get("corpus_version") != CORPUS_VERSION:
        raise ExperimentRegistryError("unsupported registry synthetic corpus version")
    cases = {}
    family_names = {
        "valid_preregistered": "wave9-valid-family",
        "many_null_experiments": "wave9-many-null-family",
        "metric_swap_after_results": "wave9-metric-swap-family",
        "sequential_family_budget_interaction": "wave9-sequential-family",
        "integrity_invalid_block": "wave9-integrity-family",
    }
    for case_name, family_id in family_names.items():
        case = corpus[case_name]
        entries = [
            ExperimentRegistryEntry.from_dict(raw)
            for raw in case["entries"]
        ]
        cases[case_name] = build_family_report(
            entries, case["evidence"], family_id=family_id
        )
    return {
        "bundle_version": "experiment_family_report_bundle.v1",
        "seed": corpus["seed"],
        "multiplicity_method": MULTIPLICITY_METHOD,
        "cases": cases,
        "governance_expectations": {
            "duplicate_experiment_id_changed_hypothesis":
                corpus["duplicate_experiment_id_changed_hypothesis"]["expected"],
            "amendment_before_outcome":
                corpus["amendment_before_vs_after_outcome"]["before_outcome_expected"],
            "amendment_after_outcome":
                corpus["amendment_before_vs_after_outcome"]["after_outcome_expected"],
        },
        "historical_observational_outside_randomized_inference": True,
        "creator_feedback_contract_version": "1.0",
        "auto_publish": False,
        "external_mutation": False,
    }
