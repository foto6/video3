import copy
from pathlib import Path
import tempfile
import unittest

from growth_analytics import media_qa_bind_r39 as r39
from growth_analytics import real_local_bundle_verifier_r38 as r38
from growth_analytics import real_local_bundle_verifier_r38_sim as sim


class MediaQaBindingTests(unittest.TestCase):
    def test_accepted_source_does_not_claim_local_render_or_model_review(self):
        result = r39.diagnose()
        self.assertEqual(result["state"], "SOURCE_READY_FOR_REAL_BUNDLE")
        self.assertFalse(result["real_bundle_verified"])
        self.assertFalse(result["real_quality_review_verified"])
        self.assertIsNone(result["ci_authority"])

    def test_stale_and_fixture_certificates_are_rejected(self):
        original = r39.load_certificate()
        for field, value in (("producer_sha", "1"*40), ("ci_run_id", 1), ("artifact_id", 1),
                             ("matrix_digest", "1"*64), ("fixture_only", True), ("fixture_only", None)):
            with self.subTest(field=field):
                bad = copy.deepcopy(original); bad[field] = value
                with self.assertRaises(r38.AuthorityDrift): r39.validate_certificate(bad)

    def test_synthetic_bundle_with_valid_hashes_cannot_claim_real_encoded_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = sim.build_fixture(Path(tmp), evidence_class="GENUINE_REVIEW")
            with self.assertRaisesRegex(r38.EvidenceInvalid, "valid encoded MP4"):
                r39.verify_bundle(fixture["root"], fixture["review"])


if __name__ == "__main__": unittest.main()
