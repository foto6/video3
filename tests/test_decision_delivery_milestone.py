from __future__ import annotations

import copy
import hashlib
import json
import random
import tempfile
import unittest
from pathlib import Path

from growth_analytics import (
    AccountMutationDisabled,
    CREATOR_DECISION_SEED_VERSION,
    CreatorDecisionConsumerLedger,
    DecisionDeliveryConflictError,
    DecisionDeliveryOutbox,
    DeliverySignatureError,
    FeedbackBatch,
    IncompleteExportError,
    MetricoolAnalyticsAdapter,
    SourceProvenanceError,
    StaleExperimentRevisionError,
    VidIQAnalyticsAdapter,
    build_creator_decision_seed,
    build_signed_delivery_audit,
    build_source_snapshot,
    creator_seed_handoff_json,
    validate_creator_decision_seed,
    verify_signed_delivery_audit,
)
from growth_analytics.adapters import event_from_mapping
from growth_analytics.decision_delivery import sha256_json


class DurableDecisionDeliveryMilestoneTests(unittest.TestCase):
    FIXTURE_KEY_ID = "fixture:decision-delivery-v1"
    FIXTURE_KEY = b"growth-decision-delivery-fixture-key-v1"
    PRODUCER_SHA = "5c01ae8a402342ebef1d8b30b4c763e78ef61751"

    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def load(self, relative: str):
        return json.loads(
            (self.root / relative).read_text(encoding="utf-8")
        )

    def events(self):
        payloads = self.load(
            "fixtures/decision_delivery_v1/synthetic_provider_events.json"
        )
        return tuple(
            event_from_mapping("fixture", payload)
            for payload in payloads
        )

    def manifest(self):
        return self.load(
            "fixtures/decision_delivery_v1/synthetic_export_manifest.json"
        )

    def handoff(self):
        return self.load(
            "fixtures/creator_decision_handoff_v1/canonical_handoff.json"
        )

    def signed_seed(self):
        snapshot = build_source_snapshot(
            events=self.events(),
            export_manifest=self.manifest(),
        )
        audit = build_signed_delivery_audit(
            decision_handoff=self.handoff(),
            source_snapshot=snapshot,
            producer_sha=self.PRODUCER_SHA,
            signing_key=self.FIXTURE_KEY,
            key_id=self.FIXTURE_KEY_ID,
        )
        return audit, build_creator_decision_seed(audit)

    def test_source_snapshot_carries_denominators_metrics_and_uncertainty(self):
        snapshot = build_source_snapshot(
            events=self.events(),
            export_manifest=self.manifest(),
        )
        self.assertEqual(
            snapshot["snapshot_version"],
            "growth.decision_source_snapshot.v1",
        )
        overall = snapshot["overall"]
        self.assertEqual(overall["denominators"]["event_count"], 8)
        self.assertGreater(overall["denominators"]["impressions"], 0)
        self.assertGreater(overall["denominators"]["views"], 0)
        self.assertGreater(overall["denominators"]["retention_views"], 0)
        self.assertGreater(overall["metrics"]["ctr"], 0)
        self.assertGreater(
            overall["metrics"]["average_watch_time_seconds"], 0
        )
        self.assertGreater(overall["metrics"]["retention_auc"], 0)
        self.assertEqual(
            overall["uncertainty"]["ctr"]["method"], "wilson"
        )
        self.assertEqual(
            overall["uncertainty"]["average_watch_time_seconds"]["method"],
            "aggregate_export_not_estimable",
        )
        self.assertEqual(
            overall["uncertainty"]["retention_auc"]["method"],
            "aggregate_export_not_estimable",
        )
        self.assertFalse(
            snapshot["source"]["live_performance_claim_allowed"]
        )
        self.assertEqual(
            snapshot["source"]["source_class"], "synthetic_fixture"
        )

    def test_duplicate_and_out_of_order_provider_events_are_digest_invariant(self):
        events = list(self.events())
        baseline = build_source_snapshot(
            events=events,
            export_manifest=self.manifest(),
        )
        replay = list(reversed(events)) + copy.deepcopy(events[:3])
        random.Random(20260928).shuffle(replay)
        actual = build_source_snapshot(
            events=replay,
            export_manifest=self.manifest(),
        )
        self.assertEqual(
            actual["snapshot_digest"],
            baseline["snapshot_digest"],
        )
        self.assertEqual(
            actual["event_set"]["event_set_digest"],
            baseline["event_set"]["event_set_digest"],
        )
        self.assertEqual(actual["overall"], baseline["overall"])

    def test_partial_export_is_blocked_before_decision_seed(self):
        manifest = self.manifest()
        manifest["complete"] = False
        with self.assertRaises(IncompleteExportError):
            build_source_snapshot(
                events=self.events(),
                export_manifest=manifest,
            )
        manifest = self.manifest()
        manifest["exports"][0]["complete"] = False
        with self.assertRaises(IncompleteExportError):
            build_source_snapshot(
                events=self.events(),
                export_manifest=manifest,
            )

    def test_fixture_cannot_masquerade_as_live_provider_performance(self):
        snapshot = build_source_snapshot(
            events=self.events(),
            export_manifest=self.manifest(),
        )
        with self.assertRaises(SourceProvenanceError):
            build_signed_delivery_audit(
                decision_handoff=self.handoff(),
                source_snapshot=snapshot,
                producer_sha=self.PRODUCER_SHA,
                signing_key=self.FIXTURE_KEY,
                key_id="live:key",
            )
        audit, seed = self.signed_seed()
        self.assertFalse(
            audit["source_snapshot"]["source"][
                "live_performance_claim_allowed"
            ]
        )
        with self.assertRaises(SourceProvenanceError):
            validate_creator_decision_seed(
                seed,
                verification_keys={
                    self.FIXTURE_KEY_ID: self.FIXTURE_KEY
                },
                expected_registry_revision=1,
            )
        accepted = validate_creator_decision_seed(
            seed,
            verification_keys={
                self.FIXTURE_KEY_ID: self.FIXTURE_KEY
            },
            expected_registry_revision=1,
            allow_synthetic_fixture=True,
        )
        self.assertEqual(
            accepted["contract_version"],
            CREATOR_DECISION_SEED_VERSION,
        )

    def test_signed_audit_binds_handoff_source_and_multiplicity(self):
        audit, seed = self.signed_seed()
        self.assertEqual(
            audit,
            self.load(
                "fixtures/decision_delivery_v1/canonical_signed_audit.json"
            ),
        )
        self.assertEqual(
            seed,
            self.load(
                "fixtures/decision_delivery_v1/canonical_creator_seed.json"
            ),
        )
        verified = verify_signed_delivery_audit(
            audit,
            verification_keys={
                self.FIXTURE_KEY_ID: self.FIXTURE_KEY
            },
        )
        self.assertEqual(
            verified["decision"]["handoff_digest"],
            self.handoff()["handoff_digest"],
        )
        self.assertEqual(
            verified["decision"]["audit_bundle_digest"],
            self.handoff()["audit_bundle_digest"],
        )
        self.assertEqual(
            verified["safeguards"]["multiplicity"][
                "decision_reason"
            ],
            "holm_adjusted_above_family_alpha",
        )
        self.assertFalse(
            verified["safeguards"]["release_authorized"]
        )
        tampered = copy.deepcopy(audit)
        tampered["source_snapshot"]["overall"]["numerators"][
            "clicks"
        ] += 1
        with self.assertRaises(DeliverySignatureError):
            verify_signed_delivery_audit(
                tampered,
                verification_keys={
                    self.FIXTURE_KEY_ID: self.FIXTURE_KEY
                },
            )

    def test_stale_experiment_revision_is_rejected(self):
        snapshot = build_source_snapshot(
            events=self.events(),
            export_manifest=self.manifest(),
        )
        stale = copy.deepcopy(snapshot)
        stale["registry_revision"] = 2
        material = dict(stale)
        material.pop("snapshot_digest")
        stale["snapshot_digest"] = sha256_json(material)
        with self.assertRaises(StaleExperimentRevisionError):
            build_signed_delivery_audit(
                decision_handoff=self.handoff(),
                source_snapshot=stale,
                producer_sha=self.PRODUCER_SHA,
                signing_key=self.FIXTURE_KEY,
                key_id=self.FIXTURE_KEY_ID,
            )
        _, seed = self.signed_seed()
        with self.assertRaises(StaleExperimentRevisionError):
            validate_creator_decision_seed(
                seed,
                verification_keys={
                    self.FIXTURE_KEY_ID: self.FIXTURE_KEY
                },
                expected_registry_revision=2,
                allow_synthetic_fixture=True,
            )

    def test_lost_ack_restart_resends_same_seed_and_creator_accepts_once(self):
        _, seed = self.signed_seed()
        with tempfile.TemporaryDirectory() as tmp:
            outbox_path = Path(tmp) / "outbox.jsonl"
            consumer_path = Path(tmp) / "consumer.jsonl"

            outbox = DecisionDeliveryOutbox(outbox_path)
            self.assertEqual(
                outbox.prepare(
                    seed,
                    expected_registry_revision=1,
                ),
                "prepared",
            )
            consumer = CreatorDecisionConsumerLedger(
                consumer_path
            )
            self.assertEqual(
                consumer.consume(
                    seed,
                    verification_keys={
                        self.FIXTURE_KEY_ID:
                            self.FIXTURE_KEY
                    },
                    expected_registry_revision=1,
                    allow_synthetic_fixture=True,
                ),
                "accepted",
            )

            # Simulated lost acknowledgement: Growth restarts
            # before recording Creator's acknowledgement.
            outbox = DecisionDeliveryOutbox(outbox_path)
            pending = outbox.pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(
                pending[0]["seed_digest"],
                seed["seed_digest"],
            )

            consumer = CreatorDecisionConsumerLedger(
                consumer_path
            )
            self.assertEqual(
                consumer.consume(
                    pending[0],
                    verification_keys={
                        self.FIXTURE_KEY_ID:
                            self.FIXTURE_KEY
                    },
                    expected_registry_revision=1,
                    allow_synthetic_fixture=True,
                ),
                "duplicate",
            )
            self.assertEqual(consumer.accepted_count, 1)

            self.assertEqual(
                outbox.acknowledge(
                    seed["delivery_id"],
                    seed["seed_digest"],
                ),
                "acknowledged",
            )
            self.assertEqual(outbox.pending(), ())
            self.assertEqual(outbox.logical_delivery_count, 1)

    def test_same_logical_seed_survives_provider_replay_and_process_restart(self):
        events = list(self.events())
        snapshot_a = build_source_snapshot(
            events=events,
            export_manifest=self.manifest(),
        )
        replay = events + list(reversed(events)) + events[:2]
        random.Random(11).shuffle(replay)
        snapshot_b = build_source_snapshot(
            events=replay,
            export_manifest=self.manifest(),
        )
        audit_a = build_signed_delivery_audit(
            decision_handoff=self.handoff(),
            source_snapshot=snapshot_a,
            producer_sha=self.PRODUCER_SHA,
            signing_key=self.FIXTURE_KEY,
            key_id=self.FIXTURE_KEY_ID,
        )
        audit_b = build_signed_delivery_audit(
            decision_handoff=self.handoff(),
            source_snapshot=snapshot_b,
            producer_sha=self.PRODUCER_SHA,
            signing_key=self.FIXTURE_KEY,
            key_id=self.FIXTURE_KEY_ID,
        )
        seed_a = build_creator_decision_seed(audit_a)
        seed_b = build_creator_decision_seed(audit_b)
        self.assertEqual(
            audit_a["bundle_digest"],
            audit_b["bundle_digest"],
        )
        self.assertEqual(seed_a, seed_b)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outbox.jsonl"
            ledger = DecisionDeliveryOutbox(path)
            self.assertEqual(
                ledger.prepare(
                    seed_a,
                    expected_registry_revision=1,
                ),
                "prepared",
            )
            ledger = DecisionDeliveryOutbox(path)
            self.assertEqual(
                ledger.prepare(
                    seed_b,
                    expected_registry_revision=1,
                ),
                "duplicate",
            )
            self.assertEqual(ledger.logical_delivery_count, 1)

    def test_conflicting_event_identity_and_conflicting_seed_fail_closed(self):
        events = list(self.events())
        conflicting = copy.deepcopy(events[0])
        object.__setattr__(
            conflicting,
            "clicks",
            conflicting.clicks + 1,
        )
        with self.assertRaises(SourceProvenanceError):
            build_source_snapshot(
                events=events + [conflicting],
                export_manifest=self.manifest(),
            )

        _, seed = self.signed_seed()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionDeliveryOutbox(
                Path(tmp) / "outbox.jsonl"
            )
            ledger.prepare(
                seed,
                expected_registry_revision=1,
            )
            changed = copy.deepcopy(seed)
            changed["decision_classification"] = (
                "insufficient_evidence"
            )
            material = dict(changed)
            material.pop("seed_digest")
            changed["seed_digest"] = sha256_json(material)
            with self.assertRaises(
                Exception
            ):
                ledger.prepare(
                    changed,
                    expected_registry_revision=1,
                )

    def test_creator_contract_rejects_tampered_signature_and_unknown_fields(self):
        _, seed = self.signed_seed()
        tampered = copy.deepcopy(seed)
        tampered["payload"]["signature"]["value"] = "0" * 64
        tampered["signature"]["value"] = "0" * 64
        material = dict(tampered)
        material.pop("seed_digest")
        tampered["seed_digest"] = sha256_json(material)
        with self.assertRaises(DeliverySignatureError):
            validate_creator_decision_seed(
                tampered,
                verification_keys={
                    self.FIXTURE_KEY_ID: self.FIXTURE_KEY
                },
                expected_registry_revision=1,
                allow_synthetic_fixture=True,
            )

        unknown = copy.deepcopy(seed)
        unknown["unexpected"] = True
        with self.assertRaises(Exception):
            validate_creator_decision_seed(
                unknown,
                verification_keys={
                    self.FIXTURE_KEY_ID: self.FIXTURE_KEY
                },
                expected_registry_revision=1,
                allow_synthetic_fixture=True,
            )

    def test_source_fixture_bytes_are_bound_and_not_live_claims(self):
        path = (
            self.root
            / "fixtures"
            / "decision_delivery_v1"
            / "synthetic_provider_events.json"
        )
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(
            actual,
            "2f3d83b9443a3d90dfd3dc0f4e62d103a7115ac8c6864ba2e4613f9dee8d698d",
        )
        manifest = self.manifest()
        self.assertEqual(
            manifest["fixture_source_sha256"],
            actual,
        )
        self.assertEqual(
            manifest["source_class"],
            "synthetic_fixture",
        )

    def test_creator_feedback_bytes_and_provider_boundaries_remain_unchanged(self):
        corpus = (
            self.root
            / "fixtures"
            / "creator_consumer_conformance_v1"
        )
        batch_wire = (
            corpus / "canonical_batch.json"
        ).read_text(encoding="utf-8").rstrip("\n")
        batch = FeedbackBatch.from_json(batch_wire)
        seed_wire = creator_seed_handoff_json(batch)
        feedback_wire = tuple(
            item.to_json() for item in batch.feedback
        )

        self.signed_seed()

        self.assertEqual(batch.to_json(), batch_wire)
        self.assertEqual(
            creator_seed_handoff_json(batch),
            seed_wire,
        )
        self.assertEqual(
            tuple(item.to_json() for item in batch.feedback),
            feedback_wire,
        )

        class Client:
            def fetch_analytics(self, **kwargs):
                return []

        for adapter_type in (
            MetricoolAnalyticsAdapter,
            VidIQAnalyticsAdapter,
        ):
            with self.subTest(
                adapter=adapter_type.__name__
            ):
                with self.assertRaises(
                    AccountMutationDisabled
                ):
                    adapter_type(Client()).mutate_account(
                        "publish",
                        {"forbidden": True},
                    )


if __name__ == "__main__":
    unittest.main()
