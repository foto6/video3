from __future__ import annotations

import json
import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    CreatorSeedHandoff,
    CreatorSeedValidationError,
    DeliveryConflictError,
    DurableAnalyticsEventStream,
    EventConflictError,
    FeedbackDeliveryLedger,
    InvalidAnalyticsEvent,
    MetricoolAnalyticsAdapter,
    TimeWindow,
    VidIQAnalyticsAdapter,
    WindowFinalizationConflictError,
    WindowFinalizationError,
    WindowFinalizationLedger,
    analytics_event_from_dict,
    analytics_event_to_dict,
    build_finalized_feedback_batch,
    canonical_json,
    finalize_window_snapshot,
    finalized_learning_report,
    generate_stress_fixture,
    run_reliability_stress,
)


class Wave5ReliabilityTests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def load_fixture(self) -> dict:
        return json.loads(
            (self.root / "fixtures" / "reliability_stress_v1.json").read_text(
                encoding="utf-8"
            )
        )

    def load_report(self) -> dict:
        return json.loads(
            (self.root / "fixtures" / "reliability_report_v1.json").read_text(
                encoding="utf-8"
            )
        )

    def first_campaign_window(self):
        payload = self.load_fixture()
        campaign = payload["campaigns"][0]
        raw_window = campaign["windows"][0]
        window = TimeWindow(
            raw_window["label"], raw_window["start"], raw_window["end"]
        )
        events = tuple(
            analytics_event_from_dict(item)
            for item in payload["events"]
            if item["channel_id"] == campaign["channel_id"]
        )
        return payload, campaign, raw_window, window, events

    def variant_specs(self, campaign: dict, window: TimeWindow) -> list[dict]:
        return [
            {
                **variant,
                "next_content_job_id": (
                    f"next-{campaign['campaign_id']}-"
                    f"{window.label}-{variant['variant_id']}"
                ),
            }
            for variant in campaign["variants"]
        ]

    def test_seeded_fixture_is_reproducible_and_large(self) -> None:
        fixture = self.load_fixture()
        regenerated = generate_stress_fixture()
        self.assertEqual(canonical_json(regenerated), canonical_json(fixture))
        self.assertEqual(len(fixture["events"]), 1152)
        self.assertEqual(len(fixture["ingestion_order"]), 1280)
        self.assertEqual(len(fixture["duplicate_event_ids"]), 128)
        self.assertEqual(len(fixture["designated_late_event_ids"]), 96)
        self.assertEqual(len(fixture["campaigns"]), 3)
        self.assertEqual(
            sum(len(item["windows"]) for item in fixture["campaigns"]), 12
        )

    def test_watermark_required_and_finalized_late_policy_is_closed(self) -> None:
        payload, campaign, raw_window, window, events = self.first_campaign_window()
        with self.assertRaises(WindowFinalizationError):
            finalize_window_snapshot(
                campaign_id=campaign["campaign_id"],
                window=window,
                events=events,
                watermark=window.end,
                allowed_lateness_seconds=payload["allowed_lateness_seconds"],
            )

        snapshot = finalize_window_snapshot(
            campaign_id=campaign["campaign_id"],
            window=window,
            events=events,
            watermark=raw_window["watermark"],
            allowed_lateness_seconds=payload["allowed_lateness_seconds"],
        )
        batch = build_finalized_feedback_batch(
            snapshot=snapshot,
            channel_id=campaign["channel_id"],
            variants=self.variant_specs(campaign, window),
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = WindowFinalizationLedger(Path(tmp) / "finalized.jsonl")
            receipt = ledger.commit(snapshot, batch)
            self.assertEqual(receipt.status, "finalized")
            existing = snapshot.events[0]
            self.assertEqual(
                ledger.classify_after_finalization(
                    campaign_id=campaign["campaign_id"],
                    window=window,
                    event=existing,
                ),
                "duplicate_noop",
            )
            new_late = replace(existing, event_id=existing.event_id + "-late")
            self.assertEqual(
                ledger.classify_after_finalization(
                    campaign_id=campaign["campaign_id"],
                    window=window,
                    event=new_late,
                ),
                "reject_late_after_finalization",
            )
            outside = replace(
                existing,
                event_id=existing.event_id + "-outside",
                captured_at=window.end,
            )
            self.assertEqual(
                ledger.classify_after_finalization(
                    campaign_id=campaign["campaign_id"],
                    window=window,
                    event=outside,
                ),
                "outside_window",
            )

            changed_snapshot = finalize_window_snapshot(
                campaign_id=campaign["campaign_id"],
                window=window,
                events=(*events, new_late),
                watermark=raw_window["watermark"],
                allowed_lateness_seconds=payload["allowed_lateness_seconds"],
            )
            changed_batch = build_finalized_feedback_batch(
                snapshot=changed_snapshot,
                channel_id=campaign["channel_id"],
                variants=self.variant_specs(campaign, window),
            )
            with self.assertRaises(WindowFinalizationConflictError):
                ledger.commit(changed_snapshot, changed_batch)

    def test_event_stream_pre_and_post_commit_restart_semantics(self) -> None:
        payload = self.load_fixture()
        first = analytics_event_from_dict(payload["events"][0])
        second = analytics_event_from_dict(payload["events"][1])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            stream = DurableAnalyticsEventStream(path)
            from growth_analytics import InjectedEventStreamFault

            with self.assertRaises(InjectedEventStreamFault):
                stream.append(first, fault="before_commit")
            self.assertFalse(path.exists())

            stream = DurableAnalyticsEventStream(path)
            self.assertEqual(stream.append(first).status, "accepted")
            with self.assertRaises(InjectedEventStreamFault):
                stream.append(second, fault="after_commit")

            reopened = DurableAnalyticsEventStream(path)
            self.assertEqual(reopened.event_count, 2)
            self.assertEqual(reopened.append(second).status, "duplicate")

    def test_truncated_tail_recovery_is_explicit_and_provable(self) -> None:
        payload = self.load_fixture()
        event = analytics_event_from_dict(payload["events"][0])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            DurableAnalyticsEventStream(path).append(event)
            with path.open("ab") as handle:
                handle.write(b'{"stream_version":"analytics.event.v1"')

            with self.assertRaises(InvalidAnalyticsEvent):
                DurableAnalyticsEventStream(path)

            recovered = DurableAnalyticsEventStream(
                path, tail_policy="recover_truncated_tail"
            )
            self.assertTrue(recovered.recovered_truncated_tail)
            self.assertEqual(recovered.event_count, 1)
            self.assertTrue(path.read_bytes().endswith(b"\n"))
            self.assertEqual(
                DurableAnalyticsEventStream(path).event_count,
                1,
            )

    def test_duplicate_sequence_and_conflicting_id_fail_closed(self) -> None:
        payload = self.load_fixture()
        first = analytics_event_from_dict(payload["events"][0])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            stream = DurableAnalyticsEventStream(path)
            stream.append(first)
            line = path.read_text(encoding="utf-8")
            path.write_text(line + line, encoding="utf-8", newline="\n")
            with self.assertRaises(InvalidAnalyticsEvent):
                DurableAnalyticsEventStream(path)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            stream = DurableAnalyticsEventStream(path)
            stream.append(first)
            changed = replace(first, clicks=min(first.impressions, first.clicks + 1))
            row = {
                "stream_version": "analytics.event.v1",
                "sequence": 2,
                "event": analytics_event_to_dict(changed),
            }
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                )
            with self.assertRaises(EventConflictError):
                DurableAnalyticsEventStream(path)

    def test_noncontiguous_delivery_ledger_fails_closed(self) -> None:
        report = self.load_report()
        row = {
            "ledger_version": "growth.feedback_delivery_ledger.v1",
            "sequence": 2,
            "batch_id": report["finalized_learning"]["windows"][0]["batch_id"],
            "payload_digest": report["finalized_learning"]["windows"][0][
                "payload_digest"
            ],
            "seed_digest": report["finalized_learning"]["windows"][0][
                "seed_digest"
            ],
            "status": "committed",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.jsonl"
            path.write_text(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaises(DeliveryConflictError):
                FeedbackDeliveryLedger(path)

    def test_malformed_canonical_seed_fails_closed(self) -> None:
        with self.assertRaises(CreatorSeedValidationError):
            CreatorSeedHandoff.from_json(
                '{"handoff_version":"growth.creator_seed.v1","feedback":['
            )

    def test_semantic_event_permutations_preserve_finalized_learning_bytes(self) -> None:
        payload = self.load_fixture()
        events = [analytics_event_from_dict(item) for item in payload["events"]]
        expected = canonical_json(self.load_report()["finalized_learning"])

        permutations = [
            list(events),
            list(reversed(events)),
            sorted(events, key=lambda item: item.event_id),
            sorted(events, key=lambda item: item.captured_at),
        ]
        for seed in (1, 7, 42, 271828, 20260927):
            shuffled = list(events)
            random.Random(seed).shuffle(shuffled)
            permutations.append(shuffled)

        for index, permuted in enumerate(permutations):
            with self.subTest(permutation=index):
                actual = canonical_json(
                    finalized_learning_report(payload, permuted)
                )
                self.assertEqual(actual, expected)

    def test_focused_stress_run_matches_committed_report(self) -> None:
        payload = self.load_fixture()
        expected = self.load_report()
        with tempfile.TemporaryDirectory() as tmp:
            actual = run_reliability_stress(payload, tmp)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["logical_delivery_count"], 12)
        self.assertEqual(actual["finalized_window_count"], 12)
        self.assertEqual(actual["unique_event_count"], 1152)
        self.assertEqual(actual["configured_duplicate_attempt_count"], 128)
        self.assertEqual(actual["designated_late_event_count"], 96)

    def test_observational_semantics_and_read_only_providers(self) -> None:
        report = self.load_report()
        wire = canonical_json(report).lower()
        self.assertNotIn('"causal":true', wire.replace(" ", ""))
        for phrase in ("causal lift", "causal-lift", "causal_lift"):
            self.assertNotIn(phrase, wire)
        self.assertFalse(report["causal"])
        for row in report["finalized_learning"]["windows"]:
            self.assertFalse(row["causal"])

        class Client:
            def fetch_analytics(self, **kwargs):
                return []

        for adapter_type in (MetricoolAnalyticsAdapter, VidIQAnalyticsAdapter):
            with self.subTest(adapter=adapter_type.__name__):
                with self.assertRaises(AccountMutationDisabled):
                    adapter_type(Client()).mutate_account(
                        "publish", {"forbidden": True}
                    )


if __name__ == "__main__":
    unittest.main()
