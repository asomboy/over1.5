import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.prediction_explanation_service import PredictionExplanationService


class TestPredictionExplanation(unittest.TestCase):

    def test_support_and_caution_reason_codes(self):
        """Validates machine-readable explainability generation under positive and cautionary signals."""
        model_data = {
            "total_xg": 3.1,
            "expected_total_shots": 27.5,
            "expected_total_sot": 9.8,
            "total_expected_corners": 10.5,
            "home_xg": 2.0,
            "away_xg": 1.1
        }
        eval_metrics = {
            "sample_size": 320,
            "ece": 0.04,
            "readiness_status": "VALIDATED"
        }
        consistency_data = {
            "consistency_score": 0.90,
            "contradiction_flags": ["CROSS_MARKET_ALIGNED"]
        }

        res = PredictionExplanationService.generate_explanation(
            market="Over 2.5 Goals",
            selection="Over 2.5",
            probability=0.78,
            model_data=model_data,
            eval_metrics=eval_metrics,
            data_quality=0.85,
            provider_status="HEALTHY",
            drift_status="STABLE",
            consistency_data=consistency_data
        )

        self.assertIn("primary_factors", res)
        self.assertIn("supporting_factors", res)
        self.assertIn("caution_factors", res)
        self.assertIn("reason_codes", res)

        # Assert specific support codes
        self.assertIn("ABOVE_BASELINE_XG", res["reason_codes"])
        self.assertIn("LARGE_VERIFIED_SAMPLE", res["reason_codes"])
        self.assertIn("GOOD_MODEL_CALIBRATION", res["reason_codes"])
        self.assertIn("CROSS_MARKET_SUPPORT", res["reason_codes"])

        # Check factor structure
        factor = res["primary_factors"][0]
        self.assertIn("code", factor)
        self.assertIn("category", factor)
        self.assertIn("severity", factor)
        self.assertIn("message", factor)
        self.assertEqual(factor["category"], "SUPPORT")

    def test_caution_factors_on_insufficient_sample_and_drift(self):
        """Verifies caution codes when sample size is low and drift is detected."""
        model_data = {"total_xg": 2.2}
        eval_metrics = {"sample_size": 45, "ece": 0.12, "readiness_status": "INSUFFICIENT_DATA"}
        consistency_data = {"consistency_score": 0.65, "contradiction_flags": ["HIGH_GOAL_LOW_CORNER_DISCREPANCY"]}

        res = PredictionExplanationService.generate_explanation(
            market="Over 1.5 Goals",
            selection="Over 1.5",
            probability=0.75,
            model_data=model_data,
            eval_metrics=eval_metrics,
            data_quality=0.50,
            provider_status="DEGRADED",
            drift_status="DRIFT_DETECTED",
            consistency_data=consistency_data
        )

        self.assertIn("LIMITED_HISTORICAL_SAMPLE", res["reason_codes"])
        self.assertIn("RECENT_DRIFT", res["reason_codes"])
        self.assertIn("PROVIDER_DELAY", res["reason_codes"])
        self.assertIn("CROSS_MARKET_CONFLICT", res["reason_codes"])
        self.assertGreater(len(res["caution_factors"]), 0)


if __name__ == "__main__":
    unittest.main()
