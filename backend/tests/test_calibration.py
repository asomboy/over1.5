import os
import sys
import math
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.calibration_service import (
    CalibrationService, MIN_BUCKET_SAMPLE, MIN_PRODUCTION_VALIDATION_SAMPLE
)


class TestCalibrationService(unittest.TestCase):

    def test_brier_component(self):
        """Brier component (p - y)^2 calculations."""
        self.assertAlmostEqual(CalibrationService.calculate_brier_component(0.8, 1.0), 0.04, places=4)
        self.assertAlmostEqual(CalibrationService.calculate_brier_component(0.8, 0.0), 0.64, places=4)
        self.assertAlmostEqual(CalibrationService.calculate_brier_component(0.5, 1.0), 0.25, places=4)

    def test_multiclass_brier(self):
        """Multi-class 1X2 Brier score."""
        # Perfect home win prediction
        b_perf = CalibrationService.calculate_multiclass_brier(1.0, 0.0, 0.0, "home")
        self.assertAlmostEqual(b_perf, 0.0, places=4)

        # Uniform prediction on draw: (0.333-0)^2 + (0.333-1)^2 + (0.333-0)^2 = 0.111 + 0.444 + 0.111 = 0.666
        b_uni = CalibrationService.calculate_multiclass_brier(0.3333, 0.3333, 0.3333, "draw")
        self.assertAlmostEqual(b_uni, 0.6667, places=3)

    def test_log_loss_component_clamping(self):
        """Log loss clamping prevents math domain error at 0.0 or 1.0."""
        ll_0 = CalibrationService.calculate_log_loss_component(0.0, 1.0)
        ll_1 = CalibrationService.calculate_log_loss_component(1.0, 0.0)
        self.assertGreater(ll_0, 10.0)
        self.assertGreater(ll_1, 10.0)
        self.assertFalse(math.isnan(ll_0))
        self.assertFalse(math.isinf(ll_0))

    def test_multiclass_log_loss(self):
        """Multi-class 1X2 Log Loss."""
        ll = CalibrationService.calculate_multiclass_log_loss(0.70, 0.20, 0.10, "home")
        self.assertAlmostEqual(ll, -math.log(0.70), places=4)

    def test_aggregate_metrics(self):
        """Computes mean Brier, Log Loss, MAE, RMSE, Accuracy."""
        pairs = [(0.8, 1.0), (0.7, 1.0), (0.2, 0.0), (0.9, 0.0)]
        agg = CalibrationService.calculate_aggregate_metrics(pairs)
        self.assertEqual(agg["sample_size"], 4)
        self.assertGreater(agg["mean_brier"], 0.0)
        self.assertGreater(agg["mean_log_loss"], 0.0)
        self.assertEqual(agg["status"], "VALIDATING")

    def test_calibration_curve_and_ece(self):
        """10-decile reliability curve, ECE, MCE, and status."""
        # Perfectly calibrated synthetic sample of 100 predictions
        pairs = []
        for i in range(100):
            p = (i % 10) / 10.0 + 0.05
            # Event occurs with probability p
            y = 1.0 if (i % 10) >= 5 else 0.0
            pairs.append((p, y))

        cal = CalibrationService.compute_calibration_curve(pairs, num_buckets=10)
        self.assertEqual(len(cal["buckets"]), 10)
        self.assertIsNotNone(cal["ece"])
        self.assertIsNotNone(cal["mce"])
        self.assertEqual(cal["sample_size"], 100)


if __name__ == "__main__":
    unittest.main()
