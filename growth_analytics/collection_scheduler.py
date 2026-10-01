from __future__ import annotations

import heapq
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .autonomous_reels import (
    NextCycleOutbox,
    ReelsFeedbackLedger,
    build_metric_snapshot,
    build_next_cycle_seed,
    canonical_json,
    parse_metric_snapshot,
    parse_publish_result,
    sha256_json,
)
from .event_stream import parse_timestamp
from .provider_ingest import (
    BackoffActive,
    CredentialReference,
    ProviderContractError,
    ProviderIngestError,
    ProviderIngestLedger,
    ProviderLedgerConflictError,
    ProviderMetricsAdapter,
    ProviderMetricsIngestor,
    StaleProviderRevision,
)


COLLECTION_SCHEDULER_VERSION = "growth.metrics_collection_scheduler.v1"
COLLECTION_SCHEDULE_LEDGER_VERSION = (
    "growth.metrics_collection_schedule_ledger.v1"
)
COLLECTION_TICK_REPORT_VERSION = "growth.metrics_collection_tick_report.v1"

ACTIVE_STATES = frozenset({"scheduled", "backoff"})
TERMINAL_STATES = frozenset({"terminal", "expired"})
ALL_STATES = ACTIVE_STATES | TERMINAL_STATES


class CollectionSchedulerError(RuntimeError):
    pass


class CollectionScheduleConflictError(CollectionSchedulerError):
    pass


class InjectedCollectionSchedulerFault(RuntimeError):
    pass


@dataclass(frozen=True)
class CollectionSchedulePolicy:
    window_offsets_seconds: tuple[int, ...] = (
        900,
        3600,
        21600,
        86400,
        259200,
    )
    expiry_seconds: int = 604800
    max_attempts_per_window: int = 5
    retry_base_seconds: int = 60
    retry_cap_seconds: int = 3600
    partial_retry_seconds: int = 300
    max_due_per_tick: int = 32

    def __post_init__(self) -> None:
        offsets = self.window_offsets_seconds
        if (
            not isinstance(offsets, tuple)
            or not offsets
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                for value in offsets
            )
        ):
            raise CollectionSchedulerError(
                "window_offsets_seconds must be positive integers"
            )
        if tuple(sorted(set(offsets))) != offsets:
            raise CollectionSchedulerError(
                "window offsets must be unique and strictly increasing"
            )
        for value, field in (
            (self.expiry_seconds, "expiry_seconds"),
            (
                self.max_attempts_per_window,
                "max_attempts_per_window",
            ),
            (self.retry_base_seconds, "retry_base_seconds"),
            (self.retry_cap_seconds, "retry_cap_seconds"),
            (
                self.partial_retry_seconds,
                "partial_retry_seconds",
            ),
            (self.max_due_per_tick, "max_due_per_tick"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                raise CollectionSchedulerError(
                    f"{field} must be a positive integer"
                )
        if self.retry_base_seconds > self.retry_cap_seconds:
            raise CollectionSchedulerError(
                "retry_base_seconds cannot exceed retry_cap_seconds"
            )
        if offsets[-1] >= self.expiry_seconds:
            raise CollectionSchedulerError(
                "final collection window must precede expiry"
            )


_STATE_FIELDS = frozenset({
    "post_key",
    "publish_result",
    "credential_ref_id",
    "authorization_lineage",
    "status",
    "window_index",
    "collection_round",
    "attempts_for_window",
    "next_due_at",
    "last_success_at",
    "last_error",
    "backoff_until",
    "latest_snapshot_digest",
    "latest_evidence_revision_digest",
    "latest_seed_digest",
    "latest_provider_revision",
    "terminal_reason",
    "updated_at",
})


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CollectionSchedulerError(
            "scheduler clock must return timezone-aware datetime"
        )
    return value.astimezone(timezone.utc)


def _digest_or_none(value: Any, field: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise CollectionScheduleConflictError(
            f"{field} must be null or lowercase SHA-256 hex"
        )


def normalized_evidence_revision_digest(
    snapshot: Mapping[str, Any],
) -> str:
    parsed = parse_metric_snapshot(snapshot)
    return sha256_json({
        "window": parsed["window"],
        "available_metrics": parsed["available_metrics"],
        "normalized_metrics": parsed["normalized_metrics"],
        "normalization_sources": parsed["normalization_sources"],
        "denominators": parsed["denominators"],
        "uncertainty": parsed["uncertainty"],
    })


def _post_key(publish_result: Mapping[str, Any]) -> str:
    parsed = parse_publish_result(publish_result)
    return "gcs1:" + sha256_json({
        "publish_result_id": parsed["publish_result_id"],
        "publish_result_digest": parsed["publish_result_digest"],
    })


def _collection_id(
    post_key: str,
    window_index: int,
    collection_round: int,
) -> str:
    return (
        f"scheduler:{post_key}:window-{window_index}"
        f":round-{collection_round}"
    )


def _seed_cycle_id(
    post_key: str,
    evidence_revision_digest: str,
) -> str:
    return (
        "scheduler-next:"
        + post_key.split(":", 1)[1][:24]
        + ":"
        + evidence_revision_digest[:24]
    )


class CollectionScheduleLedger:
    """Append-only durable scheduler state with one current state per post."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._sequence = 0
        self._states: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CollectionScheduleConflictError(
                    f"invalid scheduler ledger JSON line {line_number}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "post_key",
                    "state",
                }
            ):
                raise CollectionScheduleConflictError(
                    "scheduler ledger row fields invalid"
                )
            if (
                row["ledger_version"]
                != COLLECTION_SCHEDULE_LEDGER_VERSION
            ):
                raise CollectionScheduleConflictError(
                    "unsupported scheduler ledger version"
                )
            if row["sequence"] != self._sequence + 1:
                raise CollectionScheduleConflictError(
                    "scheduler ledger sequence not contiguous"
                )
            state = self._validate_state(row["state"])
            if row["post_key"] != state["post_key"]:
                raise CollectionScheduleConflictError(
                    "scheduler row post key mismatch"
                )
            previous = self._states.get(state["post_key"])
            if previous is not None:
                if (
                    previous["publish_result"]["publish_result_digest"]
                    != state["publish_result"]["publish_result_digest"]
                ):
                    raise CollectionScheduleConflictError(
                        "registered publish result changed"
                    )
                if (
                    previous["credential_ref_id"]
                    != state["credential_ref_id"]
                    or previous["authorization_lineage"]
                    != state["authorization_lineage"]
                ):
                    raise CollectionScheduleConflictError(
                        "credential reference lineage changed"
                    )
            self._states[state["post_key"]] = state
            self._sequence += 1

    def _validate_state(
        self,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping) or set(raw) != _STATE_FIELDS:
            raise CollectionScheduleConflictError(
                "scheduler state fields invalid"
            )
        state = json.loads(canonical_json(dict(raw)))
        published = parse_publish_result(state["publish_result"])
        expected_key = _post_key(published)
        if state["post_key"] != expected_key:
            raise CollectionScheduleConflictError(
                "scheduler post key does not bind publish result"
            )
        CredentialReference(
            state["credential_ref_id"],
            state["authorization_lineage"],
        )
        if state["status"] not in ALL_STATES:
            raise CollectionScheduleConflictError(
                "unsupported scheduler status"
            )
        for field, minimum in (
            ("window_index", 0),
            ("collection_round", 1),
            ("attempts_for_window", 0),
        ):
            value = state[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < minimum
            ):
                raise CollectionScheduleConflictError(
                    f"{field} must be integer >= {minimum}"
                )
        revision = state["latest_provider_revision"]
        if revision is not None and (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
        ):
            raise CollectionScheduleConflictError(
                "latest_provider_revision invalid"
            )
        for field in (
            "next_due_at",
            "last_success_at",
            "backoff_until",
            "updated_at",
        ):
            value = state[field]
            if value is not None:
                if not isinstance(value, str):
                    raise CollectionScheduleConflictError(
                        f"{field} must be timestamp or null"
                    )
                parse_timestamp(value)
        if state["updated_at"] is None:
            raise CollectionScheduleConflictError(
                "updated_at cannot be null"
            )
        for field in (
            "latest_snapshot_digest",
            "latest_evidence_revision_digest",
            "latest_seed_digest",
        ):
            _digest_or_none(state[field], field)
        if (
            state["last_error"] is not None
            and (
                not isinstance(state["last_error"], str)
                or len(state["last_error"]) > 160
            )
        ):
            raise CollectionScheduleConflictError(
                "last_error must be null or bounded text"
            )
        if (
            state["terminal_reason"] is not None
            and (
                not isinstance(state["terminal_reason"], str)
                or len(state["terminal_reason"]) > 160
            )
        ):
            raise CollectionScheduleConflictError(
                "terminal_reason must be null or bounded text"
            )
        if state["status"] in TERMINAL_STATES:
            if state["next_due_at"] is not None:
                raise CollectionScheduleConflictError(
                    "terminal scheduler state cannot be due"
                )
        else:
            if state["next_due_at"] is None:
                raise CollectionScheduleConflictError(
                    "active scheduler state requires next_due_at"
                )
        return state

    def _append(self, state: Mapping[str, Any]) -> None:
        parsed = self._validate_state(state)
        row = {
            "ledger_version": COLLECTION_SCHEDULE_LEDGER_VERSION,
            "sequence": self._sequence + 1,
            "post_key": parsed["post_key"],
            "state": parsed,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._sequence += 1
        self._states[parsed["post_key"]] = parsed

    def register(
        self,
        *,
        publish_result: Mapping[str, Any],
        credential: CredentialReference,
        next_due_at: str,
        updated_at: str,
    ) -> tuple[str, str]:
        published = parse_publish_result(publish_result)
        key = _post_key(published)
        parse_timestamp(next_due_at)
        parse_timestamp(updated_at)
        existing = self._states.get(key)
        if existing is not None:
            expected = (
                published["publish_result_digest"],
                credential.credential_ref_id,
                credential.authorization_lineage,
            )
            actual = (
                existing["publish_result"]["publish_result_digest"],
                existing["credential_ref_id"],
                existing["authorization_lineage"],
            )
            if actual != expected:
                raise CollectionScheduleConflictError(
                    "post registration identity changed"
                )
            return key, "duplicate"
        state = {
            "post_key": key,
            "publish_result": published,
            "credential_ref_id": credential.credential_ref_id,
            "authorization_lineage":
                credential.authorization_lineage,
            "status": "scheduled",
            "window_index": 0,
            "collection_round": 1,
            "attempts_for_window": 0,
            "next_due_at": next_due_at,
            "last_success_at": None,
            "last_error": None,
            "backoff_until": None,
            "latest_snapshot_digest": None,
            "latest_evidence_revision_digest": None,
            "latest_seed_digest": None,
            "latest_provider_revision": None,
            "terminal_reason": None,
            "updated_at": updated_at,
        }
        self._append(state)
        return key, "registered"

    def update(
        self,
        post_key: str,
        *,
        updated_at: str,
        **changes: Any,
    ) -> dict[str, Any]:
        current = self._states.get(post_key)
        if current is None:
            raise CollectionScheduleConflictError(
                "cannot update unknown scheduled post"
            )
        if "post_key" in changes or "publish_result" in changes:
            raise CollectionScheduleConflictError(
                "immutable scheduler identity cannot be changed"
            )
        next_state = json.loads(canonical_json(current))
        next_state.update(changes)
        next_state["updated_at"] = updated_at
        self._append(next_state)
        return json.loads(canonical_json(next_state))

    def get(self, post_key: str) -> dict[str, Any] | None:
        state = self._states.get(post_key)
        return (
            None
            if state is None
            else json.loads(canonical_json(state))
        )

    def states(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            json.loads(canonical_json(self._states[key]))
            for key in sorted(self._states)
        )

    @property
    def post_count(self) -> int:
        return len(self._states)

    @property
    def row_count(self) -> int:
        return self._sequence


class DurableMetricsCollectionScheduler:
    """Finite, restart-safe collection loop over the R11 read-only boundary."""

    def __init__(
        self,
        *,
        schedule_ledger: CollectionScheduleLedger,
        provider_ledger: ProviderIngestLedger,
        feedback_ledger: ReelsFeedbackLedger,
        seed_outbox: NextCycleOutbox,
        adapters: Mapping[str, ProviderMetricsAdapter],
        policy: CollectionSchedulePolicy | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.schedule_ledger = schedule_ledger
        self.provider_ledger = provider_ledger
        self.feedback_ledger = feedback_ledger
        self.seed_outbox = seed_outbox
        self.adapters = dict(adapters)
        self.policy = policy or CollectionSchedulePolicy()
        self.now = now or (lambda: datetime.now(timezone.utc))
        for platform, adapter in self.adapters.items():
            if adapter.platform != platform:
                raise CollectionSchedulerError(
                    "adapter mapping key does not match platform"
                )
            if adapter.read_only is not True:
                raise CollectionSchedulerError(
                    "scheduler accepts read-only provider adapters only"
                )

    def register_post(
        self,
        *,
        publish_result: Mapping[str, Any],
        credential: CredentialReference,
    ) -> tuple[str, str]:
        published = parse_publish_result(publish_result)
        published_at = parse_timestamp(published["published_at"])
        first_due = published_at + timedelta(
            seconds=self.policy.window_offsets_seconds[0]
        )
        now = _aware_utc(self.now())
        return self.schedule_ledger.register(
            publish_result=published,
            credential=credential,
            next_due_at=_timestamp(first_due),
            updated_at=_timestamp(now),
        )

    def tick(
        self,
        *,
        inject_fault: str | None = None,
    ) -> dict[str, Any]:
        now = _aware_utc(self.now())
        states = self.schedule_ledger.states()
        due_count = 0

        def due_states():
            nonlocal due_count
            for state in states:
                if state["status"] not in ACTIVE_STATES:
                    continue
                due_at = parse_timestamp(state["next_due_at"])
                if due_at <= now:
                    due_count += 1
                    yield (
                        due_at,
                        state["post_key"],
                        state,
                    )

        selected = heapq.nsmallest(
            self.policy.max_due_per_tick,
            due_states(),
            key=lambda item: (item[0], item[1]),
        )
        results: list[dict[str, Any]] = []
        fault_available = inject_fault
        for _, _, state in selected:
            result = self._process_state(
                state,
                now=now,
                inject_fault=fault_available,
            )
            fault_available = None
            results.append(result)
        return {
            "report_version": COLLECTION_TICK_REPORT_VERSION,
            "scheduler_version": COLLECTION_SCHEDULER_VERSION,
            "observed_at": _timestamp(now),
            "registered_posts": self.schedule_ledger.post_count,
            "due_total": due_count,
            "processed": len(results),
            "deferred_due": max(0, due_count - len(results)),
            "max_due_per_tick": self.policy.max_due_per_tick,
            "logical_seed_count": self.seed_outbox.logical_seed_count,
            "results": results,
        }

    def _process_state(
        self,
        state: Mapping[str, Any],
        *,
        now: datetime,
        inject_fault: str | None,
    ) -> dict[str, Any]:
        published = state["publish_result"]
        published_at = parse_timestamp(published["published_at"])
        expiry = published_at + timedelta(
            seconds=self.policy.expiry_seconds
        )
        if now >= expiry:
            self.schedule_ledger.update(
                state["post_key"],
                updated_at=_timestamp(now),
                status="expired",
                next_due_at=None,
                backoff_until=None,
                terminal_reason="collection_expired",
                last_error=state["last_error"],
            )
            return {
                "post_key": state["post_key"],
                "outcome": "expired",
                "seed_emitted": False,
            }

        index = state["window_index"]
        if index >= len(self.policy.window_offsets_seconds):
            raise CollectionScheduleConflictError(
                "active state points beyond schedule"
            )
        window_end = published_at + timedelta(
            seconds=self.policy.window_offsets_seconds[index]
        )
        credential = CredentialReference(
            state["credential_ref_id"],
            state["authorization_lineage"],
        )
        from .provider_ingest import ProviderFetchRequest

        request = ProviderFetchRequest(
            platform=published["platform"],
            account_id=published["account_id"],
            post_id=published["post_id"],
            cycle_revision=published["cycle_revision"],
            window_start=published["published_at"],
            window_end=_timestamp(window_end),
            collection_id=_collection_id(
                state["post_key"],
                index,
                state["collection_round"],
            ),
            credential=credential,
        )
        adapter = self.adapters.get(published["platform"])
        if adapter is None:
            return self._terminal_failure(
                state,
                now,
                "adapter_unavailable",
            )
        ingestor = ProviderMetricsIngestor(
            adapter=adapter,
            provider_ledger=self.provider_ledger,
            feedback_ledger=self.feedback_ledger,
            now=lambda: now,
        )
        try:
            outcome = ingestor.ingest_window(request)
        except BackoffActive as exc:
            return self._retry(
                state,
                now,
                error="provider_rate_limited",
                provider_not_before=parse_timestamp(
                    exc.not_before
                ),
            )
        except StaleProviderRevision:
            return self._retry(
                state,
                now,
                error="stale_provider_revision",
            )
        except (TimeoutError, ConnectionError, OSError) as exc:
            return self._retry(
                state,
                now,
                error=f"temporary:{type(exc).__name__}",
            )
        except (
            ProviderContractError,
            ProviderLedgerConflictError,
            ProviderIngestError,
        ) as exc:
            return self._terminal_failure(
                state,
                now,
                f"provider_contract:{type(exc).__name__}",
            )

        attempts = state["attempts_for_window"] + 1
        event = outcome.metrics_event
        common = {
            "last_success_at": _timestamp(now),
            "last_error": None,
            "backoff_until": None,
            "latest_provider_revision":
                outcome.provider_revision,
        }
        if event["complete"] is not True:
            if attempts >= self.policy.max_attempts_per_window:
                updated = self.schedule_ledger.update(
                    state["post_key"],
                    updated_at=_timestamp(now),
                    status="terminal",
                    next_due_at=None,
                    attempts_for_window=attempts,
                    terminal_reason="incomplete_retry_exhausted",
                    **common,
                )
                return {
                    "post_key": state["post_key"],
                    "outcome": "terminal",
                    "reason": updated["terminal_reason"],
                    "seed_emitted": False,
                }
            retry_at = now + timedelta(
                seconds=max(
                    self.policy.partial_retry_seconds,
                    self._retry_delay(attempts),
                )
            )
            self.schedule_ledger.update(
                state["post_key"],
                updated_at=_timestamp(now),
                status="scheduled",
                collection_round=state["collection_round"] + 1,
                attempts_for_window=attempts,
                next_due_at=_timestamp(retry_at),
                terminal_reason=None,
                **common,
            )
            return {
                "post_key": state["post_key"],
                "outcome": "partial",
                "next_due_at": _timestamp(retry_at),
                "seed_emitted": False,
            }

        metrics_events = self.feedback_ledger.metrics_for_post(
            platform=published["platform"],
            account_id=published["account_id"],
            post_id=published["post_id"],
        )
        snapshot = build_metric_snapshot(
            publish_result=published,
            metrics_events=metrics_events,
        )
        evidence_revision = normalized_evidence_revision_digest(
            snapshot
        )
        seed_emitted = False
        latest_seed_digest = state["latest_seed_digest"]
        if (
            evidence_revision
            != state["latest_evidence_revision_digest"]
        ):
            seed = build_next_cycle_seed(
                publish_result=published,
                metric_snapshot=snapshot,
                next_cycle_id=_seed_cycle_id(
                    state["post_key"],
                    evidence_revision,
                ),
                expected_cycle_revision=
                    published["cycle_revision"],
            )
            self.seed_outbox.prepare(
                seed,
                expected_cycle_revision=
                    published["cycle_revision"],
                allow_synthetic_fixture=(
                    published["source_class"]
                    == "synthetic_fixture"
                ),
            )
            latest_seed_digest = seed["seed_digest"]
            seed_emitted = True
            if inject_fault == "after_seed_prepare":
                raise InjectedCollectionSchedulerFault(
                    "fault after durable next-cycle seed prepare"
                )

        next_index = index + 1
        if next_index >= len(self.policy.window_offsets_seconds):
            status = "terminal"
            next_due_at = None
            terminal_reason = "schedule_complete"
        else:
            status = "scheduled"
            next_due_at = _timestamp(
                published_at
                + timedelta(
                    seconds=
                        self.policy.window_offsets_seconds[next_index]
                )
            )
            terminal_reason = None
        self.schedule_ledger.update(
            state["post_key"],
            updated_at=_timestamp(now),
            status=status,
            window_index=next_index,
            collection_round=1,
            attempts_for_window=0,
            next_due_at=next_due_at,
            latest_snapshot_digest=snapshot["snapshot_digest"],
            latest_evidence_revision_digest=evidence_revision,
            latest_seed_digest=latest_seed_digest,
            terminal_reason=terminal_reason,
            **common,
        )
        return {
            "post_key": state["post_key"],
            "outcome": (
                "terminal" if status == "terminal" else "collected"
            ),
            "provider_revision": outcome.provider_revision,
            "snapshot_digest": snapshot["snapshot_digest"],
            "evidence_revision_digest": evidence_revision,
            "seed_emitted": seed_emitted,
            "seed_digest": (
                latest_seed_digest if seed_emitted else None
            ),
            "next_due_at": next_due_at,
        }

    def _retry(
        self,
        state: Mapping[str, Any],
        now: datetime,
        *,
        error: str,
        provider_not_before: datetime | None = None,
    ) -> dict[str, Any]:
        attempts = state["attempts_for_window"] + 1
        if attempts >= self.policy.max_attempts_per_window:
            return self._terminal_failure(
                state,
                now,
                "retry_exhausted:" + error,
                attempts=attempts,
            )
        retry_at = now + timedelta(
            seconds=self._retry_delay(attempts)
        )
        if (
            provider_not_before is not None
            and provider_not_before > retry_at
        ):
            retry_at = provider_not_before
        self.schedule_ledger.update(
            state["post_key"],
            updated_at=_timestamp(now),
            status="backoff",
            attempts_for_window=attempts,
            next_due_at=_timestamp(retry_at),
            last_error=error,
            backoff_until=_timestamp(retry_at),
            terminal_reason=None,
        )
        return {
            "post_key": state["post_key"],
            "outcome": "backoff",
            "last_error": error,
            "backoff_until": _timestamp(retry_at),
            "seed_emitted": False,
        }

    def _terminal_failure(
        self,
        state: Mapping[str, Any],
        now: datetime,
        reason: str,
        *,
        attempts: int | None = None,
    ) -> dict[str, Any]:
        self.schedule_ledger.update(
            state["post_key"],
            updated_at=_timestamp(now),
            status="terminal",
            attempts_for_window=(
                state["attempts_for_window"]
                if attempts is None
                else attempts
            ),
            next_due_at=None,
            last_error=reason,
            backoff_until=None,
            terminal_reason=reason,
        )
        return {
            "post_key": state["post_key"],
            "outcome": "terminal",
            "reason": reason,
            "seed_emitted": False,
        }

    def _retry_delay(self, attempt: int) -> int:
        exponent = max(0, attempt - 1)
        return min(
            self.policy.retry_cap_seconds,
            self.policy.retry_base_seconds * (2 ** exponent),
        )

    def operator_state(
        self,
        post_key: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if post_key is None:
            states = self.schedule_ledger.states()
        else:
            one = self.schedule_ledger.get(post_key)
            states = () if one is None else (one,)
        return tuple(
            {
                "post_key": state["post_key"],
                "platform": state["publish_result"]["platform"],
                "account_id":
                    state["publish_result"]["account_id"],
                "post_id": state["publish_result"]["post_id"],
                "status": state["status"],
                "window_index": state["window_index"],
                "collection_round": state["collection_round"],
                "attempts_for_window":
                    state["attempts_for_window"],
                "next_due_at": state["next_due_at"],
                "last_success_at": state["last_success_at"],
                "last_error": state["last_error"],
                "backoff_until": state["backoff_until"],
                "latest_snapshot_digest":
                    state["latest_snapshot_digest"],
                "latest_seed_digest":
                    state["latest_seed_digest"],
                "latest_provider_revision":
                    state["latest_provider_revision"],
                "terminal_reason": state["terminal_reason"],
            }
            for state in states
        )
