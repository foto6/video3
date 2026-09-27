from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .core import AnalyticsEvent, AnalyticsStore, RetentionPoint


STREAM_VERSION = "analytics.event.v1"
TAIL_POLICIES = frozenset({"fail_closed", "recover_truncated_tail"})


class EventConflictError(ValueError):
    pass


class InvalidAnalyticsEvent(ValueError):
    pass


class InjectedEventStreamFault(RuntimeError):
    pass


@dataclass(frozen=True)
class IngestReceipt:
    key: str
    sequence: int
    status: str
    late: bool


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise InvalidAnalyticsEvent("captured_at must be a non-empty ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InvalidAnalyticsEvent(f"invalid captured_at: {value!r}") from exc
    if parsed.tzinfo is None:
        raise InvalidAnalyticsEvent("captured_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_event_metrics(event: AnalyticsEvent) -> None:
    for field in ("impressions", "views", "clicks"):
        value = getattr(event, field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidAnalyticsEvent(f"{field} must be a non-negative integer")
    if isinstance(event.watch_time_seconds, bool) or not isinstance(event.watch_time_seconds, (int, float)):
        raise InvalidAnalyticsEvent("watch_time_seconds must be numeric")
    if event.watch_time_seconds < 0:
        raise InvalidAnalyticsEvent("watch_time_seconds must be non-negative")
    if event.clicks > event.impressions:
        raise InvalidAnalyticsEvent("clicks cannot exceed impressions")
    if event.variant_id is not None and not event.variant_id:
        raise InvalidAnalyticsEvent("variant_id must be non-empty when present")
    parse_timestamp(event.captured_at)


def analytics_event_to_dict(event: AnalyticsEvent) -> dict[str, Any]:
    validate_event_metrics(event)
    return {
        "provider": event.provider,
        "event_id": event.event_id,
        "channel_id": event.channel_id,
        "video_id": event.video_id,
        "variant_id": event.variant_id,
        "captured_at": event.captured_at,
        "impressions": event.impressions,
        "views": event.views,
        "clicks": event.clicks,
        "watch_time_seconds": event.watch_time_seconds,
        "retention": [
            {"position": point.position, "retained": point.retained}
            for point in event.retention
        ],
    }


def analytics_event_from_dict(payload: Mapping[str, Any]) -> AnalyticsEvent:
    if not isinstance(payload, Mapping):
        raise InvalidAnalyticsEvent("event payload must be an object")
    required = {
        "provider",
        "event_id",
        "channel_id",
        "video_id",
        "variant_id",
        "captured_at",
        "impressions",
        "views",
        "clicks",
        "watch_time_seconds",
        "retention",
    }
    keys = set(payload)
    if keys != required:
        raise InvalidAnalyticsEvent(
            "event fields must match analytics.event.v1 exactly"
        )
    retention_payload = payload["retention"]
    if not isinstance(retention_payload, list):
        raise InvalidAnalyticsEvent("retention must be an array")
    try:
        retention = tuple(
            RetentionPoint(float(point["position"]), float(point["retained"]))
            for point in retention_payload
        )
        event = AnalyticsEvent(
            provider=str(payload["provider"]),
            event_id=str(payload["event_id"]),
            channel_id=str(payload["channel_id"]),
            video_id=str(payload["video_id"]),
            variant_id=(str(payload["variant_id"]) if payload["variant_id"] is not None else None),
            captured_at=str(payload["captured_at"]),
            impressions=payload["impressions"],
            views=payload["views"],
            clicks=payload["clicks"],
            watch_time_seconds=payload["watch_time_seconds"],
            retention=retention,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidAnalyticsEvent("invalid analytics event payload") from exc
    for field in ("provider", "event_id", "channel_id", "video_id"):
        if not getattr(event, field):
            raise InvalidAnalyticsEvent(f"{field} must be non-empty")
    validate_event_metrics(event)
    return event


class DurableAnalyticsEventStream:
    """Append-only JSONL stream with explicit arrival order and event time.

    captured_at is event time. sequence is durable ingestion/replay order.
    Only a syntactically truncated final JSON line may be recovered, and only
    when recover_truncated_tail is explicitly selected.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        tail_policy: str = "fail_closed",
    ) -> None:
        if tail_policy not in TAIL_POLICIES:
            raise ValueError(
                "tail_policy must be 'fail_closed' or 'recover_truncated_tail'"
            )
        self.path = Path(path)
        self.tail_policy = tail_policy
        self._events: dict[str, tuple[int, AnalyticsEvent]] = {}
        self._arrival: list[AnalyticsEvent] = []
        self._max_captured_at: datetime | None = None
        self._recovered_truncated_tail = False
        self._load()

    def _recover_tail(self, prefix: bytes) -> None:
        with self.path.open("r+b") as handle:
            handle.truncate(len(prefix))
            handle.flush()
            os.fsync(handle.fileno())
        self._recovered_truncated_tail = True

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidAnalyticsEvent("event stream is not valid UTF-8") from exc
        raw_lines = raw.splitlines(keepends=True)
        for index, raw_line in enumerate(raw_lines):
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                is_final_line = index == len(raw_lines) - 1
                final_line_unterminated = is_final_line and not raw_line.endswith(b"\n")
                if (
                    self.tail_policy == "recover_truncated_tail"
                    and final_line_unterminated
                ):
                    prefix = b"".join(raw_lines[:index])
                    self._recover_tail(prefix)
                    break
                raise InvalidAnalyticsEvent(
                    f"invalid stream JSON on line {index + 1}"
                ) from exc
            if not isinstance(row, Mapping):
                raise InvalidAnalyticsEvent("event stream row must be an object")
            if set(row) != {"stream_version", "sequence", "event"}:
                raise InvalidAnalyticsEvent(
                    "event stream row fields must match analytics.event.v1 exactly"
                )
            if row.get("stream_version") != STREAM_VERSION:
                raise InvalidAnalyticsEvent("unknown analytics event stream version")
            expected_sequence = len(self._arrival) + 1
            if row.get("sequence") != expected_sequence:
                raise InvalidAnalyticsEvent("event stream sequence is not contiguous")
            event = analytics_event_from_dict(row.get("event"))
            key = event.idempotency_key
            previous = self._events.get(key)
            if previous is not None:
                if previous[1] != event:
                    raise EventConflictError(f"conflicting replay for {key}")
                raise InvalidAnalyticsEvent(f"duplicate durable row for {key}")
            self._events[key] = (expected_sequence, event)
            self._arrival.append(event)
            captured = parse_timestamp(event.captured_at)
            if self._max_captured_at is None or captured > self._max_captured_at:
                self._max_captured_at = captured

    def append(
        self,
        event: AnalyticsEvent,
        *,
        fault: str | None = None,
    ) -> IngestReceipt:
        validate_event_metrics(event)
        if fault not in {None, "before_commit", "after_commit"}:
            raise ValueError(
                "fault must be None, 'before_commit', or 'after_commit'"
            )
        key = event.idempotency_key
        existing = self._events.get(key)
        if existing is not None:
            if existing[1] != event:
                raise EventConflictError(f"idempotency conflict for {key}")
            return IngestReceipt(key, existing[0], "duplicate", False)

        captured = parse_timestamp(event.captured_at)
        late = self._max_captured_at is not None and captured < self._max_captured_at
        sequence = len(self._arrival) + 1
        row = {
            "stream_version": STREAM_VERSION,
            "sequence": sequence,
            "event": analytics_event_to_dict(event),
        }
        serialized = json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if fault == "before_commit":
            raise InjectedEventStreamFault("injected crash before event commit")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        if fault == "after_commit":
            raise InjectedEventStreamFault("injected crash after event commit")

        self._events[key] = (sequence, event)
        self._arrival.append(event)
        if self._max_captured_at is None or captured > self._max_captured_at:
            self._max_captured_at = captured
        return IngestReceipt(key, sequence, "accepted", late)

    def append_many(self, events: Iterable[AnalyticsEvent]) -> tuple[IngestReceipt, ...]:
        return tuple(self.append(event) for event in events)

    def replay(self, *, order: str = "arrival") -> tuple[AnalyticsEvent, ...]:
        if order == "arrival":
            return tuple(self._arrival)
        if order == "captured_at":
            return tuple(
                sorted(
                    self._arrival,
                    key=lambda event: (
                        parse_timestamp(event.captured_at),
                        event.idempotency_key,
                    ),
                )
            )
        raise ValueError("order must be 'arrival' or 'captured_at'")

    def rebuild_store(self) -> AnalyticsStore:
        store = AnalyticsStore()
        store.ingest_many(self._arrival)
        return store

    @property
    def event_count(self) -> int:
        return len(self._arrival)

    @property
    def recovered_truncated_tail(self) -> bool:
        return self._recovered_truncated_tail
