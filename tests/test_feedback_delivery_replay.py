from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    AnalyticsEvent,
    CreatorFeedback,
    CreatorFeedbackValidationError,
    DeliveryConflictError,
    DurableAnalyticsEventStream,
    FeedbackBatch,
    FeedbackBatchValidationError,
    FeedbackDeliveryLedger,
    InjectedDeliveryFault,
    MetricoolAnalyticsAdapter,
    TimeWindow,
    VidIQAnalyticsAdapter,
    build_feedback_batch,
    creator_seed_handoff_json,
    simulate_campaign,
)


class FeedbackDeliveryReplayTests(unittest.TestCase):
    @property
    def fixture_dir(self) -> Path:
        return Path(__file__).resolve().parents[1] / "fixtures"

    def campaign_payload(self) -> dict:
        return json.loads(
            (self.fixture_dir / "campaign_round2.json").read_text(encoding="utf-8")
        )

    def campaign_feedback(self) -> tuple[dict, tuple[CreatorFeedback, ...], TimeWindow]:
        payload = self.campaign_payload()
        report = simulate_campaign(payload)
        feedback = tuple(
            CreatorFeedback.from_dict(item) for item in report["next_cycle_feedback"]
        )
        raw_window = payload["windows"][-1]
        window = TimeWindow(raw_window["label"], raw_window["start"], raw_window["end"])
        return report, feedback, window

    def build_batch(self) -> FeedbackBatch:
        report, feedback, window = self.campaign_feedback()
        return build_feedback_batch(
            campaign_id=report["campaign_id"],
            window=window,
            feedbacks=feedback,
        )

    def test_batch_identity_and_payload_are_stable_across_input_order(self) -> None:
        report, feedback, window = self.campaign_feedback()
        forward = build_feedback_batch(
            campaign_id=report["campaign_id"],
            window=window,
            feedbacks=feedback,
        )
        reverse = build_feedback_batch(
            campaign_id=report["campaign_id"],
            window=window,
            feedbacks=reversed(feedback),
        )
        self.assertEqual(forward.batch_id, reverse.batch_id)
        self.assertEqual(forward.payload_digest, reverse.payload_digest)
        self.assertEqual(forward.to_json(), reverse.to_json())
        self.assertEqual(
            tuple(item.content_job_id for item in forward.feedback),
            tuple(sorted(item.content_job_id for item in forward.feedback)),
        )

    def test_batch_round_trip_keeps_creator_feedback_v1_exact(self) -> None:
        batch = self.build_batch()
        parsed = FeedbackBatch.from_json(batch.to_json())
        self.assertEqual(parsed, batch)
        for feedback in parsed.feedback:
            self.assertEqual(feedback.contract_version, "1.0")
            self.assertEqual(CreatorFeedback.from_json(feedback.to_json()), feedback)

    def test_cross_version_feedback_fails_closed_inside_handoff(self) -> None:
        batch = self.build_batch()
        body = batch.to_dict()
        body["feedback"][0]["contract_version"] = "2.0"
        with self.assertRaises(FeedbackBatchValidationError):
            FeedbackBatch.from_dict(body)

        feedback_body = batch.feedback[0].to_dict()
        feedback_body["contract_version"] = "2.0"
        with self.assertRaises(CreatorFeedbackValidationError):
            CreatorFeedback.from_dict(feedback_body)

    def test_unknown_feedback_batch_version_fails_closed(self) -> None:
        body = self.build_batch().to_dict()
        body["handoff_version"] = "growth.feedback_batch.v2"
        with self.assertRaises(FeedbackBatchValidationError):
            FeedbackBatch.from_dict(body)

    def test_changed_evidence_under_reused_identity_fails_closed(self) -> None:
        body = self.build_batch().to_dict()
        body["feedback"][0]["evidence_event_ids"] = [
            *body["feedback"][0]["evidence_event_ids"],
            "changed-evidence",
        ]
        with self.assertRaises(FeedbackBatchValidationError):
            FeedbackBatch.from_dict(body)

    def test_repeated_export_request_is_one_durable_commit(self) -> None:
        batch = self.build_batch()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.jsonl"
            ledger = FeedbackDeliveryLedger(path)
            first = ledger.commit(batch)
            second = ledger.commit(batch)
            reopened = FeedbackDeliveryLedger(path)
            third = reopened.commit(batch)
            self.assertEqual(first.status, "committed")
            self.assertEqual(second.status, "duplicate")
            self.assertEqual(third.status, "duplicate")
            self.assertEqual(first.sequence, second.sequence)
            self.assertEqual(first.seed_digest, third.seed_digest)
            self.assertEqual(reopened.committed_count, 1)
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)

    def test_crash_before_delivery_commit_retries_cleanly(self) -> None:
        batch = self.build_batch()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.jsonl"
            ledger = FeedbackDeliveryLedger(path)
            with self.assertRaises(InjectedDeliveryFault):
                ledger.commit(batch, fault="before_commit")
            self.assertFalse(path.exists())
            reopened = FeedbackDeliveryLedger(path)
            receipt = reopened.commit(batch)
            self.assertEqual(receipt.status, "committed")
            self.assertEqual(reopened.committed_count, 1)

    def test_crash_after_delivery_commit_replay_is_duplicate(self) -> None:
        batch = self.build_batch()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.jsonl"
            ledger = FeedbackDeliveryLedger(path)
            with self.assertRaises(InjectedDeliveryFault):
                ledger.commit(batch, fault="after_commit")
            reopened = FeedbackDeliveryLedger(path)
            receipt = reopened.commit(batch)
            self.assertEqual(receipt.status, "duplicate")
            self.assertEqual(reopened.committed_count, 1)
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)

    def test_conflicting_duplicate_identity_is_rejected(self) -> None:
        batch = self.build_batch()
        changed_feedback = list(batch.feedback)
        changed_feedback[0] = replace(
            changed_feedback[0],
            score=max(0.0, changed_feedback[0].score - 0.01),
        )
        conflicting = replace(
            batch,
            feedback=tuple(changed_feedback),
            payload_digest="0" * 64,
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = FeedbackDeliveryLedger(Path(tmp) / "delivery.jsonl")
            ledger.commit(batch)
            with self.assertRaises(FeedbackBatchValidationError):
                ledger.commit(conflicting)

    def test_event_replay_duplicate_and_late_event_produce_one_logical_handoff(self) -> None:
        payload = self.campaign_payload()
        raw_events = payload["events"]
        events = tuple(
            AnalyticsEvent(
                provider=item["provider"],
                event_id=item["event_id"],
                channel_id=item["channel_id"],
                video_id=item["video_id"],
                variant_id=item["variant_id"],
                captured_at=item["captured_at"],
                impressions=item["impressions"],
                views=item["views"],
                clicks=item["clicks"],
                watch_time_seconds=item["watch_time_seconds"],
                retention=tuple(),
            )
            for item in raw_events
        )

        with tempfile.TemporaryDirectory() as tmp:
            stream_path = Path(tmp) / "events.jsonl"
            stream = DurableAnalyticsEventStream(stream_path)
            receipts = stream.append_many(events)
            self.assertTrue(any(receipt.late for receipt in receipts))
            self.assertEqual(stream.append(events[0]).status, "duplicate")
            self.assertEqual(stream.append(events[-1]).status, "duplicate")

            reopened = DurableAnalyticsEventStream(stream_path)
            replay_ids = tuple(item.event_id for item in reopened.replay(order="captured_at"))
            self.assertEqual(len(replay_ids), len(set(replay_ids)))
            self.assertEqual(reopened.event_count, len(events))

            original_batch = self.build_batch()
            replay_batch = self.build_batch()
            self.assertEqual(original_batch.to_json(), replay_batch.to_json())

            ledger = FeedbackDeliveryLedger(Path(tmp) / "delivery.jsonl")
            self.assertEqual(ledger.commit(original_batch).status, "committed")
            self.assertEqual(ledger.commit(replay_batch).status, "duplicate")
            self.assertEqual(ledger.committed_count, 1)

    def test_creator_seed_handoff_fixture_is_byte_stable_and_exactly_once_keyed(self) -> None:
        batch = self.build_batch()
        wire = creator_seed_handoff_json(batch)
        fixture = self.fixture_dir / "creator_next_cycle_seed_v1.json"
        self.assertEqual(fixture.read_text(encoding="utf-8").rstrip("\n"), wire)
        payload = json.loads(wire)
        self.assertEqual(payload["handoff_version"], "growth.creator_seed.v1")
        self.assertEqual(payload["seed_kind"], "growth_feedback_batch")
        self.assertEqual(payload["idempotency_key"], batch.batch_id)
        self.assertEqual(payload["batch_id"], batch.batch_id)
        self.assertFalse(payload["causal"])
        self.assertEqual(len(payload["feedback"]), 6)
        self.assertTrue(
            all(item["contract_version"] == "1.0" for item in payload["feedback"])
        )

    def test_delivery_replay_fixture_declares_one_logical_handoff(self) -> None:
        fixture = json.loads(
            (self.fixture_dir / "campaign_round2_delivery.json").read_text(
                encoding="utf-8"
            )
        )
        batch = self.build_batch()
        self.assertEqual(fixture["fixture_version"], "growth.feedback_delivery_fixture.v1")
        self.assertEqual(fixture["campaign_id"], "round2-learning-demo")
        self.assertEqual(fixture["window_label"], "followup")
        self.assertEqual(fixture["expected_batch_id"], batch.batch_id)
        self.assertEqual(fixture["expected_payload_digest"], batch.payload_digest)
        self.assertEqual(fixture["expected_logical_handoffs"], 1)
        self.assertGreaterEqual(len(fixture["duplicate_replay_event_ids"]), 2)
        self.assertGreaterEqual(len(fixture["late_event_ids"]), 1)

    def test_provider_boundaries_remain_read_only(self) -> None:
        class Client:
            def fetch_analytics(self, **kwargs):
                return []

        for adapter_type in (MetricoolAnalyticsAdapter, VidIQAnalyticsAdapter):
            with self.subTest(adapter=adapter_type.__name__):
                with self.assertRaises(AccountMutationDisabled):
                    adapter_type(Client()).mutate_account("publish", {"forbidden": True})


if __name__ == "__main__":
    unittest.main()
