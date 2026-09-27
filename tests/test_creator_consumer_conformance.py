from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    CreatorFeedback,
    CreatorSeedHandoff,
    CreatorSeedValidationError,
    DeliveryConflictError,
    DurableAnalyticsEventStream,
    FeedbackBatch,
    FeedbackBatchValidationError,
    FeedbackDeliveryLedger,
    InjectedDeliveryFault,
    MetricoolAnalyticsAdapter,
    TimeWindow,
    VidIQAnalyticsAdapter,
    analytics_event_from_dict,
    analytics_event_to_dict,
    build_feedback_batch,
    creator_seed_handoff_json,
    simulate_campaign,
)


class CreatorConsumerConformanceTests(unittest.TestCase):
    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    @property
    def corpus(self) -> Path:
        return self.root / "fixtures" / "creator_consumer_conformance_v1"

    def read(self, name: str) -> str:
        return (self.corpus / name).read_text(encoding="utf-8").rstrip("\n")

    def manifest(self) -> dict:
        return json.loads((self.corpus / "manifest.json").read_text(encoding="utf-8"))

    def canonical_batch(self) -> FeedbackBatch:
        return FeedbackBatch.from_json(self.read("canonical_batch.json"))

    def campaign_payload(self) -> dict:
        return json.loads((self.root / "fixtures" / "campaign_round2.json").read_text(encoding="utf-8"))

    def seed_from_campaign(self, payload: dict) -> str:
        report = simulate_campaign(payload)
        feedback = tuple(CreatorFeedback.from_dict(item) for item in report["next_cycle_feedback"])
        raw_window = payload["windows"][-1]
        batch = build_feedback_batch(
            campaign_id=report["campaign_id"],
            window=TimeWindow(raw_window["label"], raw_window["start"], raw_window["end"]),
            feedbacks=feedback,
        )
        return creator_seed_handoff_json(batch)

    def test_manifest_hashes_exact_corpus_bytes(self) -> None:
        manifest = self.manifest()
        self.assertEqual(
            manifest["producer_contract_sha"],
            "bcd58e510cc129d6f02504856a1bb9de6dbef358",
        )
        self.assertEqual(manifest["producer_sha"], manifest["producer_contract_sha"])
        self.assertEqual(
            manifest["creator_consumer_sha"],
            "7ece5182bdf0791eadda28f10e7316f3a496ded4",
        )
        for name, expected in manifest["files_sha256"].items():
            with self.subTest(name=name):
                actual = hashlib.sha256((self.corpus / name).read_bytes()).hexdigest()
                self.assertEqual(actual, expected)

    def test_canonical_batch_is_strict_and_byte_stable(self) -> None:
        wire = self.read("canonical_batch.json")
        batch = FeedbackBatch.from_json(wire)
        self.assertEqual(batch.to_json(), wire)
        self.assertEqual(batch.batch_id, self.manifest()["batch_id"])
        self.assertEqual(batch.payload_digest, self.manifest()["payload_digest"])
        self.assertTrue(all(item.contract_version == "1.0" for item in batch.feedback))

        body = batch.to_dict()
        body["unexpected"] = True
        with self.assertRaises(FeedbackBatchValidationError):
            FeedbackBatch.from_dict(body)
        body = batch.to_dict()
        body["handoff_version"] = "growth.feedback_batch.v2"
        with self.assertRaises(FeedbackBatchValidationError):
            FeedbackBatch.from_dict(body)
        body = batch.to_dict()
        body["window"]["label"] = 7
        with self.assertRaises(FeedbackBatchValidationError):
            FeedbackBatch.from_dict(body)

    def test_creator_seed_is_strict_and_byte_stable(self) -> None:
        wire = self.read("creator_seed.json")
        seed = CreatorSeedHandoff.from_json(wire)
        self.assertEqual(seed.to_json(), wire)
        self.assertEqual(seed.idempotency_key, seed.batch_id)
        self.assertEqual(
            hashlib.sha256(wire.encode("utf-8")).hexdigest(),
            self.manifest()["seed_digest"],
        )
        self.assertFalse(seed.causal)
        self.assertTrue(all(item.contract_version == "1.0" for item in seed.feedback))

        body = seed.to_dict()
        body["unexpected"] = True
        with self.assertRaises(CreatorSeedValidationError):
            CreatorSeedHandoff.from_dict(body)
        body = seed.to_dict()
        body["window"]["label"] = 7
        with self.assertRaises(CreatorSeedValidationError):
            CreatorSeedHandoff.from_dict(body)

    def test_negative_seed_corpus_fails_closed(self) -> None:
        for name in (
            "unknown_version.json",
            "malformed_payload.json",
            "changed_payload_digest.json",
        ):
            with self.subTest(name=name):
                with self.assertRaises(CreatorSeedValidationError):
                    CreatorSeedHandoff.from_json(self.read(name))

    def test_changed_evidence_identity_fails_closed(self) -> None:
        with self.assertRaises(FeedbackBatchValidationError):
            FeedbackBatch.from_json(self.read("changed_evidence_identity.json"))

    def test_conflicting_same_id_batch_is_valid_but_ledger_rejects_after_commit(self) -> None:
        canonical = self.canonical_batch()
        conflicting = FeedbackBatch.from_json(self.read("conflicting_same_id_batch.json"))
        self.assertEqual(conflicting.batch_id, canonical.batch_id)
        self.assertNotEqual(conflicting.payload_digest, canonical.payload_digest)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = FeedbackDeliveryLedger(Path(tmp) / "delivery.jsonl")
            ledger.commit(canonical)
            with self.assertRaises(DeliveryConflictError):
                ledger.commit(conflicting)
            self.assertEqual(ledger.committed_count, 1)

    def test_duplicate_replay_is_identical_and_noop_after_first_commit(self) -> None:
        seed_wire = self.read("creator_seed.json")
        replay_wire = self.read("duplicate_replay.json")
        self.assertEqual(replay_wire, seed_wire)
        seed = CreatorSeedHandoff.from_json(seed_wire)
        batch = self.canonical_batch()
        self.assertEqual(seed.batch_id, batch.batch_id)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.jsonl"
            first = FeedbackDeliveryLedger(path)
            self.assertEqual(first.commit(batch).status, "committed")
            reopened = FeedbackDeliveryLedger(path)
            self.assertEqual(reopened.commit(batch).status, "duplicate")
            self.assertEqual(reopened.committed_count, 1)

    def test_crash_boundaries_and_full_event_replay_converge_to_one_seed(self) -> None:
        canonical = self.canonical_batch()
        expected_seed = self.read("creator_seed.json")
        seed_digests: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "before.jsonl"
            ledger = FeedbackDeliveryLedger(path)
            with self.assertRaises(InjectedDeliveryFault):
                ledger.commit(canonical, fault="before_commit")
            receipt = FeedbackDeliveryLedger(path).commit(canonical)
            self.assertEqual(receipt.status, "committed")
            reopened = FeedbackDeliveryLedger(path)
            self.assertEqual(reopened.committed_count, 1)
            seed_digests.append(receipt.seed_digest)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "after.jsonl"
            ledger = FeedbackDeliveryLedger(path)
            with self.assertRaises(InjectedDeliveryFault):
                ledger.commit(canonical, fault="after_commit")
            reopened = FeedbackDeliveryLedger(path)
            receipt = reopened.commit(canonical)
            self.assertEqual(receipt.status, "duplicate")
            self.assertEqual(reopened.committed_count, 1)
            seed_digests.append(receipt.seed_digest)

        payload = self.campaign_payload()
        events = tuple(analytics_event_from_dict(item) for item in payload["events"])
        with tempfile.TemporaryDirectory() as tmp:
            stream_path = Path(tmp) / "events.jsonl"
            stream = DurableAnalyticsEventStream(stream_path)
            stream.append_many(events)
            stream.append(events[0])
            stream.append(events[-1])
            replayed = DurableAnalyticsEventStream(stream_path).replay(order="captured_at")
            replay_payload = dict(payload)
            replay_payload["events"] = [analytics_event_to_dict(item) for item in replayed]
            replay_seed = self.seed_from_campaign(replay_payload)
            self.assertEqual(replay_seed, expected_seed)
            replay_batch = FeedbackBatch.from_json(self.read("canonical_batch.json"))
            ledger = FeedbackDeliveryLedger(Path(tmp) / "delivery.jsonl")
            receipt = ledger.commit(replay_batch)
            self.assertEqual(receipt.status, "committed")
            self.assertEqual(ledger.commit(replay_batch).status, "duplicate")
            self.assertEqual(ledger.committed_count, 1)
            seed_digests.append(receipt.seed_digest)

        self.assertEqual(len(set(seed_digests)), 1)
        self.assertEqual(seed_digests[0], self.manifest()["seed_digest"])

    def test_event_permutation_property_preserves_exact_seed_bytes(self) -> None:
        payload = self.campaign_payload()
        events = list(payload["events"])
        expected = self.read("creator_seed.json")
        permutations = []
        for offset in range(len(events)):
            rotated = events[offset:] + events[:offset]
            permutations.append(rotated)
            permutations.append(list(reversed(rotated)))
        permutations.append(sorted(events, key=lambda item: item["event_id"]))
        permutations.append(sorted(events, key=lambda item: item["captured_at"]))

        for index, permuted in enumerate(permutations):
            with self.subTest(permutation=index):
                candidate = dict(payload)
                candidate["events"] = permuted
                self.assertEqual(self.seed_from_campaign(candidate), expected)

    def test_conformance_payloads_never_claim_causality(self) -> None:
        prohibited = ("causal lift", "causal-lift", "causal_lift")
        for path in sorted(self.corpus.glob("*.json")):
            raw = path.read_text(encoding="utf-8").lower().replace(" ", "")
            with self.subTest(path=path.name):
                self.assertNotIn('"causal":true', raw)
                plain = path.read_text(encoding="utf-8").lower()
                for phrase in prohibited:
                    self.assertNotIn(phrase, plain)
        seed = CreatorSeedHandoff.from_json(self.read("creator_seed.json"))
        self.assertFalse(seed.causal)
        self.assertIn("do not establish causal effects", seed.interpretation)

    def test_provider_adapters_remain_read_only(self) -> None:
        class Client:
            def fetch_analytics(self, **kwargs):
                return []

        for adapter_type in (MetricoolAnalyticsAdapter, VidIQAnalyticsAdapter):
            with self.subTest(adapter=adapter_type.__name__):
                with self.assertRaises(AccountMutationDisabled):
                    adapter_type(Client()).mutate_account("publish", {"forbidden": True})


if __name__ == "__main__":
    unittest.main()
