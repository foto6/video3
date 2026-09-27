from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .core import AnalyticsEvent, CreatorFeedback
from .delivery import FeedbackBatch, build_feedback_batch, creator_seed_handoff_json
from .engine import (
    BatchFeedbackInput,
    CAUSALITY_NOTICE,
    TimeWindow,
    aggregate_events,
    build_creator_feedback_batch,
)
from .event_stream import analytics_event_to_dict, parse_timestamp


FINALIZATION_VERSION = "growth.window_finalization.v1"


class WindowFinalizationError(ValueError):
    pass


class WindowFinalizationConflictError(WindowFinalizationError):
    pass


@dataclass(frozen=True)
class FinalizedWindowSnapshot:
    campaign_id: str
    window: TimeWindow
    watermark: str
    allowed_lateness_seconds: int
    event_set_digest: str
    event_keys: tuple[str, ...]
    events: tuple[AnalyticsEvent, ...]

    @property
    def identity(self) -> str:
        return (
            f"{self.campaign_id}|{self.window.label}|"
            f"{self.window.start}|{self.window.end}"
        )


@dataclass(frozen=True)
class FinalizationReceipt:
    identity: str
    sequence: int
    status: str
    event_set_digest: str
    batch_id: str
    payload_digest: str
    seed_digest: str


def canonical_event_set(events: Iterable[AnalyticsEvent]) -> tuple[AnalyticsEvent, ...]:
    unique: dict[str, AnalyticsEvent] = {}
    for event in events:
        existing = unique.get(event.idempotency_key)
        if existing is None:
            unique[event.idempotency_key] = event
        elif existing != event:
            raise WindowFinalizationConflictError(
                f"conflicting event identity {event.idempotency_key}"
            )
    return tuple(
        sorted(
            unique.values(),
            key=lambda event: (
                parse_timestamp(event.captured_at),
                event.idempotency_key,
            ),
        )
    )


def event_set_digest(events: Sequence[AnalyticsEvent]) -> str:
    canonical = canonical_event_set(events)
    wire = "\n".join(
        json.dumps(
            analytics_event_to_dict(event),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for event in canonical
    )
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def finalize_window_snapshot(
    *,
    campaign_id: str,
    window: TimeWindow,
    events: Iterable[AnalyticsEvent],
    watermark: str,
    allowed_lateness_seconds: int,
) -> FinalizedWindowSnapshot:
    if not isinstance(campaign_id, str) or not campaign_id:
        raise WindowFinalizationError("campaign_id must be a non-empty string")
    if (
        isinstance(allowed_lateness_seconds, bool)
        or not isinstance(allowed_lateness_seconds, int)
        or allowed_lateness_seconds < 0
    ):
        raise WindowFinalizationError(
            "allowed_lateness_seconds must be a non-negative integer"
        )
    watermark_time = parse_timestamp(watermark)
    finalize_after = parse_timestamp(window.end) + timedelta(
        seconds=allowed_lateness_seconds
    )
    if watermark_time < finalize_after:
        raise WindowFinalizationError(
            "watermark has not passed window end plus allowed lateness"
        )

    start = parse_timestamp(window.start)
    end = parse_timestamp(window.end)
    canonical = canonical_event_set(events)
    selected = tuple(
        event
        for event in canonical
        if start <= parse_timestamp(event.captured_at) < end
    )
    return FinalizedWindowSnapshot(
        campaign_id=campaign_id,
        window=window,
        watermark=watermark,
        allowed_lateness_seconds=allowed_lateness_seconds,
        event_set_digest=event_set_digest(selected),
        event_keys=tuple(event.idempotency_key for event in selected),
        events=selected,
    )


def build_finalized_feedback_batch(
    *,
    snapshot: FinalizedWindowSnapshot,
    channel_id: str,
    variants: Sequence[Mapping[str, Any]],
) -> FeedbackBatch:
    inputs: list[BatchFeedbackInput] = []
    for raw in sorted(variants, key=lambda item: str(item["variant_id"])):
        variant_id = raw["variant_id"]
        video_id = raw["video_id"]
        duration_seconds = raw["duration_seconds"]
        next_content_job_id = raw["next_content_job_id"]
        if not isinstance(variant_id, str) or not variant_id:
            raise WindowFinalizationError("variant_id must be a non-empty string")
        if not isinstance(video_id, str) or not video_id:
            raise WindowFinalizationError("video_id must be a non-empty string")
        if not isinstance(next_content_job_id, str) or not next_content_job_id:
            raise WindowFinalizationError(
                "next_content_job_id must be a non-empty string"
            )
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or duration_seconds <= 0
        ):
            raise WindowFinalizationError(
                "duration_seconds must be a positive number"
            )
        variant_events = tuple(
            event
            for event in snapshot.events
            if event.variant_id == variant_id and event.video_id == video_id
        )
        inputs.append(
            BatchFeedbackInput(
                content_job_id=next_content_job_id,
                channel_id=channel_id,
                video_id=video_id,
                variant_id=variant_id,
                metrics=aggregate_events(variant_events),
                duration_seconds=float(duration_seconds),
                evidence_event_ids=tuple(
                    sorted(event.event_id for event in variant_events)
                ),
            )
        )

    feedback = build_creator_feedback_batch(inputs)
    return build_feedback_batch(
        campaign_id=snapshot.campaign_id,
        window=snapshot.window,
        feedbacks=feedback,
    )


class WindowFinalizationLedger:
    """Durable one-finalization record per campaign/window identity.

    New in-window events after finalization are rejected. Exact duplicate event
    identities from the finalized event set are duplicate/no-op. Windows are
    never reopened; a correction requires a new window identity.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise WindowFinalizationError(
                        f"invalid finalization ledger JSON on line {line_number}"
                    ) from exc
                expected = {
                    "finalization_version",
                    "sequence",
                    "identity",
                    "campaign_id",
                    "window",
                    "watermark",
                    "allowed_lateness_seconds",
                    "event_set_digest",
                    "event_keys",
                    "batch_id",
                    "payload_digest",
                    "seed_digest",
                    "causal",
                    "interpretation",
                    "status",
                }
                if not isinstance(row, Mapping) or set(row) != expected:
                    raise WindowFinalizationError(
                        "finalization ledger row fields are invalid"
                    )
                if row["finalization_version"] != FINALIZATION_VERSION:
                    raise WindowFinalizationError(
                        "unsupported finalization ledger version"
                    )
                if row["sequence"] != len(self._rows) + 1:
                    raise WindowFinalizationError(
                        "finalization ledger sequence is not contiguous"
                    )
                if row["status"] != "finalized":
                    raise WindowFinalizationError(
                        "finalization ledger status must be finalized"
                    )
                if row["causal"] is not False or row["interpretation"] != CAUSALITY_NOTICE:
                    raise WindowFinalizationError(
                        "finalization ledger must preserve observational semantics"
                    )
                identity = row["identity"]
                if not isinstance(identity, str) or not identity:
                    raise WindowFinalizationError(
                        "finalization identity must be non-empty"
                    )
                if identity in self._rows:
                    raise WindowFinalizationError(
                        f"duplicate finalized window identity {identity}"
                    )
                self._rows[identity] = dict(row)

    def commit(
        self,
        snapshot: FinalizedWindowSnapshot,
        batch: FeedbackBatch,
    ) -> FinalizationReceipt:
        if batch.campaign_id != snapshot.campaign_id:
            raise WindowFinalizationError("batch campaign does not match snapshot")
        if (
            batch.window_label != snapshot.window.label
            or batch.window_start != snapshot.window.start
            or batch.window_end != snapshot.window.end
        ):
            raise WindowFinalizationError("batch window does not match snapshot")
        seed_digest = hashlib.sha256(
            creator_seed_handoff_json(batch).encode("utf-8")
        ).hexdigest()
        identity = snapshot.identity
        existing = self._rows.get(identity)
        comparable = {
            "event_set_digest": snapshot.event_set_digest,
            "event_keys": list(snapshot.event_keys),
            "batch_id": batch.batch_id,
            "payload_digest": batch.payload_digest,
            "seed_digest": seed_digest,
            "watermark": snapshot.watermark,
            "allowed_lateness_seconds": snapshot.allowed_lateness_seconds,
        }
        if existing is not None:
            if any(existing[key] != value for key, value in comparable.items()):
                raise WindowFinalizationConflictError(
                    f"conflicting finalization for {identity}"
                )
            return FinalizationReceipt(
                identity=identity,
                sequence=int(existing["sequence"]),
                status="duplicate",
                event_set_digest=snapshot.event_set_digest,
                batch_id=batch.batch_id,
                payload_digest=batch.payload_digest,
                seed_digest=seed_digest,
            )

        row = {
            "finalization_version": FINALIZATION_VERSION,
            "sequence": len(self._rows) + 1,
            "identity": identity,
            "campaign_id": snapshot.campaign_id,
            "window": {
                "label": snapshot.window.label,
                "start": snapshot.window.start,
                "end": snapshot.window.end,
            },
            "watermark": snapshot.watermark,
            "allowed_lateness_seconds": snapshot.allowed_lateness_seconds,
            "event_set_digest": snapshot.event_set_digest,
            "event_keys": list(snapshot.event_keys),
            "batch_id": batch.batch_id,
            "payload_digest": batch.payload_digest,
            "seed_digest": seed_digest,
            "causal": False,
            "interpretation": CAUSALITY_NOTICE,
            "status": "finalized",
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            row,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._rows[identity] = row
        return FinalizationReceipt(
            identity=identity,
            sequence=int(row["sequence"]),
            status="finalized",
            event_set_digest=snapshot.event_set_digest,
            batch_id=batch.batch_id,
            payload_digest=batch.payload_digest,
            seed_digest=seed_digest,
        )

    def classify_after_finalization(
        self,
        *,
        campaign_id: str,
        window: TimeWindow,
        event: AnalyticsEvent,
    ) -> str:
        identity = f"{campaign_id}|{window.label}|{window.start}|{window.end}"
        row = self._rows.get(identity)
        if row is None:
            return "window_open"
        event_time = parse_timestamp(event.captured_at)
        if not (
            parse_timestamp(window.start)
            <= event_time
            < parse_timestamp(window.end)
        ):
            return "outside_window"
        if event.idempotency_key in set(row["event_keys"]):
            return "duplicate_noop"
        return "reject_late_after_finalization"

    @property
    def finalized_count(self) -> int:
        return len(self._rows)

    def rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            dict(row)
            for row in sorted(
                self._rows.values(), key=lambda item: int(item["sequence"])
            )
        )
