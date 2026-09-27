from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .core import AnalyticsEvent, RetentionPoint


class AnalyticsProvider(Protocol):
    name: str

    def fetch_events(self, *, channel_id: str, since: str | None = None) -> Iterable[AnalyticsEvent]:
        ...


class AccountMutationDisabled(RuntimeError):
    pass


class ReadOnlyProviderBase:
    name = "provider"

    def mutate_account(self, *_: Any, **__: Any) -> None:
        raise AccountMutationDisabled("analytics adapters are read-only; posting/account mutation is disabled")


def event_from_mapping(provider: str, payload: Mapping[str, Any]) -> AnalyticsEvent:
    retention = tuple(
        RetentionPoint(float(point["position"]), float(point["retained"]))
        for point in payload.get("retention", [])
    )
    return AnalyticsEvent(
        provider=provider,
        event_id=str(payload["event_id"]),
        channel_id=str(payload["channel_id"]),
        video_id=str(payload["video_id"]),
        variant_id=(str(payload["variant_id"]) if payload.get("variant_id") is not None else None),
        captured_at=str(payload["captured_at"]),
        impressions=int(payload.get("impressions", 0)),
        views=int(payload.get("views", 0)),
        clicks=int(payload.get("clicks", 0)),
        watch_time_seconds=float(payload.get("watch_time_seconds", 0.0)),
        retention=retention,
    )


class FixtureProvider(ReadOnlyProviderBase):
    name = "fixture"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_events(self, *, channel_id: str, since: str | None = None) -> Iterable[AnalyticsEvent]:
        payloads = json.loads(self.path.read_text(encoding="utf-8"))
        for payload in payloads:
            if str(payload["channel_id"]) != channel_id:
                continue
            if since is not None and str(payload["captured_at"]) < since:
                continue
            yield event_from_mapping(self.name, payload)


class _ClientBackedReadOnlyProvider(ReadOnlyProviderBase):
    def __init__(self, client: Any) -> None:
        self.client = client

    def fetch_events(self, *, channel_id: str, since: str | None = None) -> Iterable[AnalyticsEvent]:
        """Normalize provider client dictionaries into the shared event model."""
        for payload in self.client.fetch_analytics(channel_id=channel_id, since=since):
            yield event_from_mapping(self.name, payload)


class VidIQAnalyticsAdapter(_ClientBackedReadOnlyProvider):
    """Explicit read-only boundary for a future vidIQ client implementation."""

    name = "vidiq"


class MetricoolAnalyticsAdapter(_ClientBackedReadOnlyProvider):
    """Explicit read-only boundary for a future Metricool client implementation."""

    name = "metricool"
