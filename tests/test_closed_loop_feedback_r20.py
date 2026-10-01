from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.autonomous_reels import sha256_json
from growth_analytics.closed_loop_feedback import (
    CLOSED_LOOP_BRIEF_SEED_VERSION,
    CLOSED_LOOP_FEEDBACK_REPLAY_VERSION,
    CLOSED_LOOP_FEEDBACK_VERSION,
    ClosedLoopFeedbackConflictError,
    ClosedLoopFeedbackError,
    ClosedLoopFeedbackLedger,
    ClosedLoopFeedbackOutOfOrder,
    build_closed_loop_feedback,
    parse_closed_loop_feedback,
)
from growth_analytics.closed_loop_feedback_replay import (
    _decision,
    _learning,
    run_closed_loop_feedback_replay,
)
from growth_analytics.post_publish_learning import (
    parse_post_publish_learning,
)


class GrowthR20ClosedLoopFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    def fixtures(self):
        decision, good_render = _decision()
        learning = _learning(
            decision,
            good_render,
        )
        return decision, learning

    def bundle(
        self,
        *,
        decision=None,
        learning="default",
        revision=1,
    ):
        if decision is None:
            decision, default_learning = (
                self.fixtures()
            )
        else:
            _, default_learning = (
                self.fixtures()
            )
        if learning == "default":
            learning = default_learning
        return build_closed_loop_feedback(
            candidate_decision=decision,
            post_publish_learning=learning,
            bundle_revision=revision,
            next_cycle_id="cycle-r21",
        )

    def test_contract_binds_r18_r19_source_cycle_and_render_lineage(self):
        decision, learning = self.fixtures()
        bundle = self.bundle(
            decision=decision,
            learning=learning,
        )
        self.assertEqual(
            bundle["contract_version"],
            CLOSED_LOOP_FEEDBACK_VERSION,
        )
        self.assertEqual(
            bundle["source_id"],
            decision["source_id"],
        )
        self.assertEqual(
            bundle["source_sha256"],
            decision["source_sha256"],
        )
        self.assertEqual(
            bundle["cycle_revision"],
            decision["cycle_revision"],
        )
        self.assertEqual(
            bundle["lineage"]["r18"][
                "decision_digest"
            ],
            decision["decision_digest"],
        )
        self.assertEqual(
            bundle["lineage"]["r19"][
                "learning_digest"
            ],
            learning["learning_digest"],
        )
        self.assertEqual(
            bundle["lineage"]["r19"][
                "media_render_sha256"
            ],
            learning["lineage"][
                "media_render_sha256"
            ],
        )

    def test_evidence_classes_stay_separate_and_synthetic_is_never_live(self):
        bundle = self.bundle()
        evidence = bundle["evidence"]
        self.assertEqual(
            set(evidence),
            {
                "objective_defects",
                "model_aesthetic_judgment",
                "human_evidence",
                "observed_provider_metrics",
                "derived_analytics",
                "speculative_hypotheses",
            },
        )
        self.assertFalse(
            evidence[
                "model_aesthetic_judgment"
            ]["human_ground_truth"]
        )
        self.assertFalse(
            bundle[
                "evidence_availability"
            ]["human_labels_present"]
        )
        self.assertFalse(
            bundle[
                "evidence_availability"
            ]["live_platform_metrics"]
        )
        self.assertFalse(
            bundle[
                "next_cycle_brief_seed"
            ]["creator_cycle_eligible"]
        )

    def test_candidate_decision_only_path_is_bounded_and_explicit(self):
        decision, _ = self.fixtures()
        bundle = build_closed_loop_feedback(
            candidate_decision=decision,
            post_publish_learning=None,
            bundle_revision=1,
            next_cycle_id="cycle-r21",
        )
        self.assertIsNone(
            bundle["lineage"]["r19"]
        )
        self.assertIn(
            "post_publish_learning",
            bundle["unavailable_evidence"],
        )
        self.assertTrue(
            bundle[
                "evidence_availability"
            ]["candidate_decision"]
        )
        self.assertFalse(
            bundle[
                "evidence_availability"
            ][
                "post_publish_learning"
            ]
        )
        self.assertTrue(
            1 <= len(
                bundle["evidence"][
                    "speculative_hypotheses"
                ]
            ) <= 8
        )
        self.assertTrue(
            all(
                row["source"]
                == "candidate_decision"
                for row in bundle[
                    "evidence"
                ][
                    "speculative_hypotheses"
                ]
            )
        )

    def test_stale_or_clock_skew_learning_falls_back_to_candidate_guidance(self):
        decision, learning = self.fixtures()
        stale = copy.deepcopy(learning)
        stale["runtime_state"]["freshness"] = "stale"
        stale["runtime_state"][
            "collector_state"
        ] = "backoff"
        stale["runtime_state"][
            "error_classification"
        ] = "out_of_order_or_clock_skew"
        stale["evidence_blockers"] = [
            "metric_snapshot_not_fresh",
            "collector_not_healthy",
        ]
        stale["learning_digest"] = ""
        stale["learning_digest"] = sha256_json(
            stale
        )
        stale = parse_post_publish_learning(
            stale
        )
        bundle = self.bundle(
            decision=decision,
            learning=stale,
        )
        self.assertFalse(
            bundle[
                "evidence_availability"
            ][
                "post_publish_learning_usable_for_guidance"
            ]
        )
        self.assertIn(
            "post_publish_freshness",
            bundle["unavailable_evidence"],
        )
        self.assertIn(
            "post_publish_collector",
            bundle["unavailable_evidence"],
        )
        self.assertTrue(
            all(
                row["source"]
                == "candidate_decision"
                for row in bundle[
                    "evidence"
                ][
                    "speculative_hypotheses"
                ]
            )
        )

    def test_partial_learning_propagates_unavailable_evidence(self):
        decision, learning = self.fixtures()
        partial = copy.deepcopy(learning)
        partial[
            "unavailable_evidence"
        ][
            "observed_provider_metrics.saves"
        ] = "provider_metric_unavailable"
        partial["learning_digest"] = ""
        partial["learning_digest"] = (
            sha256_json(partial)
        )
        partial = parse_post_publish_learning(
            partial
        )
        bundle = self.bundle(
            decision=decision,
            learning=partial,
        )
        self.assertEqual(
            bundle[
                "unavailable_evidence"
            ][
                "r19.observed_provider_metrics.saves"
            ],
            "provider_metric_unavailable",
        )

    def test_every_hypothesis_has_target_directive_observable_and_falsification(self):
        bundle = self.bundle()
        for item in bundle[
            "evidence"
        ]["speculative_hypotheses"]:
            self.assertTrue(
                item["target_segment"][
                    "label"
                ]
            )
            self.assertTrue(
                item["directive"]
            )
            self.assertTrue(
                item[
                    "expected_observable"
                ]
            )
            self.assertTrue(
                item[
                    "falsification_criterion"
                ]
            )
            self.assertFalse(
                item["causal_claim"]
            )
            self.assertFalse(
                item[
                    "human_preference_inferred"
                ]
            )

    def test_conflicting_r18_r19_decision_digest_fails_closed(self):
        decision, learning = self.fixtures()
        changed = copy.deepcopy(
            decision
        )
        changed["decision_digest"] = (
            "f" * 64
        )
        with self.assertRaises(
            ClosedLoopFeedbackError
        ):
            self.bundle(
                decision=changed,
                learning=learning,
            )

    def test_wrong_source_or_render_input_is_rejected(self):
        decision, learning = self.fixtures()
        wrong_source = copy.deepcopy(
            decision
        )
        wrong_source["source_sha256"] = (
            "0" * 64
        )
        with self.assertRaises(
            ClosedLoopFeedbackError
        ):
            self.bundle(
                decision=wrong_source,
                learning=learning,
            )

        wrong_render_learning = copy.deepcopy(
            learning
        )
        wrong_render_learning["lineage"][
            "media_render_sha256"
        ] = "1" * 64
        wrong_render_learning[
            "learning_digest"
        ] = ""
        wrong_render_learning[
            "learning_digest"
        ] = sha256_json(
            wrong_render_learning
        )
        wrong_render_learning = (
            parse_post_publish_learning(
                wrong_render_learning
            )
        )
        with self.assertRaises(
            ClosedLoopFeedbackConflictError
        ):
            self.bundle(
                decision=decision,
                learning=wrong_render_learning,
            )

    def test_brief_seed_is_bound_and_advisory(self):
        bundle = self.bundle()
        seed = bundle[
            "next_cycle_brief_seed"
        ]
        self.assertEqual(
            seed["contract_version"],
            CLOSED_LOOP_BRIEF_SEED_VERSION,
        )
        self.assertEqual(
            seed["decision_digest"],
            bundle["lineage"]["r18"][
                "decision_digest"
            ],
        )
        self.assertEqual(
            seed["learning_digest"],
            bundle["lineage"]["r19"][
                "learning_digest"
            ],
        )
        self.assertFalse(
            seed["authority"][
                "auto_publish"
            ]
        )
        self.assertFalse(
            seed["authority"][
                "external_mutation"
            ]
        )

    def test_ledger_restart_duplicate_conflict_and_revision_ordering(self):
        first = self.bundle(
            revision=1
        )
        second = self.bundle(
            revision=2
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "feedback.jsonl"
            ledger = ClosedLoopFeedbackLedger(
                path
            )
            self.assertEqual(
                ledger.record(first),
                "accepted",
            )
            self.assertEqual(
                ledger.record(first),
                "duplicate",
            )
            restarted = ClosedLoopFeedbackLedger(
                path
            )
            self.assertEqual(
                restarted.record(second),
                "accepted",
            )
            with self.assertRaises(
                ClosedLoopFeedbackOutOfOrder
            ):
                restarted.record(first)
            latest = restarted.latest(
                source_id=second[
                    "source_id"
                ],
                source_sha256=second[
                    "source_sha256"
                ],
                cycle_revision=second[
                    "cycle_revision"
                ],
            )
            self.assertEqual(
                latest[
                    "bundle_revision"
                ],
                2,
            )

            changed = copy.deepcopy(
                second
            )
            changed[
                "unavailable_evidence"
            ][
                "changed_fixture"
            ] = "conflict"
            changed[
                "bundle_digest"
            ] = ""
            changed[
                "bundle_digest"
            ] = sha256_json(
                changed
            )
            changed = (
                parse_closed_loop_feedback(
                    changed
                )
            )
            with self.assertRaises(
                ClosedLoopFeedbackConflictError
            ):
                restarted.record(
                    changed
                )

    def test_deterministic_replay_is_nonlive_and_nonmutating(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            left = (
                run_closed_loop_feedback_replay(
                    a
                )
            )
            right = (
                run_closed_loop_feedback_replay(
                    b
                )
            )
        self.assertEqual(left, right)
        self.assertEqual(
            left["report_version"],
            CLOSED_LOOP_FEEDBACK_REPLAY_VERSION,
        )
        self.assertFalse(
            left[
                "synthetic_metrics_as_live"
            ]
        )
        self.assertFalse(
            left[
                "model_inferred_human_labels"
            ]
        )
        self.assertFalse(
            left["provider_mutation"]
        )

    def test_pinned_replay_and_readiness(self):
        replay_path = (
            self.root
            / "fixtures"
            / "closed_loop_feedback_v1"
            / "replay_report.json"
        )
        readiness_path = (
            self.root
            / "fixtures"
            / "closed_loop_feedback_v1"
            / "readiness_report.json"
        )
        if (
            not replay_path.exists()
            or not readiness_path.exists()
        ):
            with tempfile.TemporaryDirectory() as temp:
                replay = (
                    run_closed_loop_feedback_replay(
                        temp
                    )
                )
            print(
                "R20_REPLAY_JSON="
                + json.dumps(
                    replay,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return
        replay = json.loads(
            replay_path.read_text(
                encoding="utf-8"
            )
        )
        readiness = json.loads(
            readiness_path.read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            readiness[
                "contract_version"
            ],
            CLOSED_LOOP_FEEDBACK_VERSION,
        )
        self.assertFalse(
            readiness["authority"][
                "provider_mutation"
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            rebuilt = (
                run_closed_loop_feedback_replay(
                    temp
                )
            )
        self.assertEqual(replay, rebuilt)


if __name__ == "__main__":
    unittest.main()
