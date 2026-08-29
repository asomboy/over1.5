import unittest
from services.calibration_service import CalibrationService


class TestRealCalibration(unittest.TestCase):

    def test_perfect_calibration_produces_zero_ece(self):
        """When empirical event rates perfectly match predicted probabilities, ECE = 0."""
        # 100 predictions at 0.5 with exactly 50 positive outcomes
        predictions = [0.55] * 100
        outcomes = [1] * 55 + [0] * 45

        cal = CalibrationService.calculate_calibration_curve(predictions, outcomes, num_buckets=10)
        self.assertAlmostEqual(cal["ece"], 0.0, places=2)
        self.assertEqual(cal["status"], "VALIDATED (Well Calibrated)")

    def test_miscalibrated_model_produces_positive_ece(self):
        """Overconfident model yields high ECE."""
        predictions = [0.95] * 100
        outcomes = [0] * 100 # All negative outcomes despite 95% predicted

        cal = CalibrationService.calculate_calibration_curve(predictions, outcomes, num_buckets=10)
        self.assertGreater(cal["ece"], 0.5)


if __name__ == "__main__":
    unittest.main()
