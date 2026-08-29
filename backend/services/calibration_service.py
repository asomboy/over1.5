import os
import sys
import math
import logging
from typing import Dict, Any, List, Optional, Tuple

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logger = logging.getLogger(__name__)

MIN_BUCKET_SAMPLE = 20
MIN_PRODUCTION_VALIDATION_SAMPLE = 100
EPSILON = 1e-6


class CalibrationService:
    """
    Mathematical calibration and probabilistic scoring engine for predictive sports models.
    Computes Brier scores, Log Loss, MAE, RMSE, Multi-class Brier, 10-decile reliability curves,
    ECE (Expected Calibration Error), and MCE (Maximum Calibration Error).
    """

    @classmethod
    def calculate_brier_component(cls, predicted_prob: float, actual_outcome: float) -> float:
        """Atomic binary Brier score component: (p - y)^2."""
        p = max(0.0, min(1.0, float(predicted_prob)))
        y = 1.0 if actual_outcome >= 0.5 else 0.0
        return round((p - y) ** 2, 6)

    @classmethod
    def calculate_multiclass_brier(
        cls, p_home: float, p_draw: float, p_away: float, actual_class: str
    ) -> float:
        """
        Calculates multi-class Brier score for 1X2 market:
        Brier = (P_home - Y_home)^2 + (P_draw - Y_draw)^2 + (P_away - Y_away)^2
        """
        # Normalize sum to 1.0
        tot = p_home + p_draw + p_away
        if tot > 0:
            ph, pd, pa = p_home / tot, p_draw / tot, p_away / tot
        else:
            ph, pd, pa = 0.333, 0.334, 0.333

        yh = 1.0 if actual_class in ["home", "HOME", "1"] else 0.0
        yd = 1.0 if actual_class in ["draw", "DRAW", "X"] else 0.0
        ya = 1.0 if actual_class in ["away", "AWAY", "2"] else 0.0

        brier = ((ph - yh) ** 2) + ((pd - yd) ** 2) + ((pa - ya) ** 2)
        return round(brier, 6)

    @classmethod
    def calculate_log_loss_component(cls, predicted_prob: float, actual_outcome: float) -> float:
        """
        Atomic binary Log Loss component: -(y * log(p) + (1-y) * log(1-p)).
        Clamped to [1e-6, 1 - 1e-6] to prevent math domain error.
        """
        p = max(EPSILON, min(1.0 - EPSILON, float(predicted_prob)))
        y = 1.0 if actual_outcome >= 0.5 else 0.0
        ll = -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
        return round(ll, 6)

    @classmethod
    def calculate_multiclass_log_loss(
        cls, p_home: float, p_draw: float, p_away: float, actual_class: str
    ) -> float:
        """Multi-class Log Loss: -log(P(actual_class))."""
        tot = p_home + p_draw + p_away
        if tot > 0:
            ph, pd, pa = p_home / tot, p_draw / tot, p_away / tot
        else:
            ph, pd, pa = 0.333, 0.334, 0.333

        if actual_class in ["home", "HOME", "1"]:
            p_act = max(EPSILON, ph)
        elif actual_class in ["draw", "DRAW", "X"]:
            p_act = max(EPSILON, pd)
        else:
            p_act = max(EPSILON, pa)

        return round(-math.log(p_act), 6)

    @classmethod
    def calculate_aggregate_metrics(
        cls, predictions: List[Tuple[float, float]]
    ) -> Dict[str, Any]:
        """
        Given list of (predicted_prob, actual_outcome) pairs, computes full probabilistic summary.
        """
        n = len(predictions)
        if n == 0:
            return {
                "sample_size": 0,
                "status": "INSUFFICIENT_DATA",
                "mean_brier": None,
                "mean_log_loss": None,
                "mae": None,
                "rmse": None,
                "accuracy": None
            }

        brier_sum = sum(cls.calculate_brier_component(p, y) for p, y in predictions)
        ll_sum = sum(cls.calculate_log_loss_component(p, y) for p, y in predictions)
        mae_sum = sum(abs(p - y) for p, y in predictions)
        rmse_sum = sum((p - y) ** 2 for p, y in predictions)
        correct_count = sum(1 for p, y in predictions if ((p >= 0.5 and y >= 0.5) or (p < 0.5 and y < 0.5)))

        return {
            "sample_size": n,
            "mean_brier": round(brier_sum / float(n), 4),
            "mean_log_loss": round(ll_sum / float(n), 4),
            "mae": round(mae_sum / float(n), 4),
            "rmse": round(math.sqrt(rmse_sum / float(n)), 4),
            "accuracy": round(correct_count / float(n), 4),
            "status": "VALIDATED" if n >= MIN_PRODUCTION_VALIDATION_SAMPLE else "VALIDATING"
        }

    @classmethod
    def compute_calibration_curve(
        cls, predictions: Any, outcomes: Optional[Any] = None, num_buckets: int = 10
    ) -> Dict[str, Any]:
        """
        Generates standard 10-decile reliability diagram data, ECE, and MCE.
        Accepts either:
        - predictions: List[Tuple[float, float]] of (prob, outcome)
        - predictions: List[float], outcomes: List[float]
        """
        if outcomes is not None and isinstance(predictions, list):
            pairs = list(zip(predictions, outcomes))
        elif isinstance(predictions, list) and len(predictions) > 0 and isinstance(predictions[0], (tuple, list)):
            pairs = predictions
        else:
            pairs = []

        n = len(pairs)
        if n == 0:
            return {
                "status": "INSUFFICIENT_DATA",
                "sample_size": 0,
                "ece": None,
                "mce": None,
                "buckets": []
            }

        bucket_ranges = [
            (i / float(num_buckets), (i + 1) / float(num_buckets))
            for i in range(num_buckets)
        ]

        buckets_data = []
        weighted_ece_sum = 0.0
        max_error = 0.0

        for lower, upper in bucket_ranges:
            # Match items in bucket [lower, upper) or [lower, 1.0] for last bucket
            if upper >= 1.0:
                in_bucket = [(p, y) for p, y in pairs if lower <= p <= upper]
            else:
                in_bucket = [(p, y) for p, y in pairs if lower <= p < upper]

            b_count = len(in_bucket)
            if b_count > 0:
                avg_p = sum(p for p, _ in in_bucket) / float(b_count)
                avg_y = sum(y for _, y in in_bucket) / float(b_count)
                cal_err = abs(avg_p - avg_y)

                if b_count >= MIN_BUCKET_SAMPLE:
                    status = "WELL_CALIBRATED" if cal_err <= 0.06 else ("OVERCONFIDENT" if avg_p > avg_y else "UNDERCONFIDENT")
                else:
                    status = "INSUFFICIENT_DATA"

                weighted_ece_sum += (b_count / float(n)) * cal_err
                if b_count >= 5:
                    max_error = max(max_error, cal_err)
            else:
                avg_p = (lower + upper) / 2.0
                avg_y = 0.0
                cal_err = 0.0
                status = "EMPTY"

            buckets_data.append({
                "bucket_range": f"{int(lower * 100)}-{int(upper * 100)}%",
                "lower_bound": round(lower, 2),
                "upper_bound": round(upper, 2),
                "predictions_count": b_count,
                "avg_predicted_prob": round(avg_p, 4),
                "actual_event_rate": round(avg_y, 4),
                "calibration_error": round(cal_err, 4),
                "status": status
            })

        ece = round(weighted_ece_sum, 4)
        mce = round(max_error, 4)

        if n < MIN_PRODUCTION_VALIDATION_SAMPLE:
            overall_status = "INSUFFICIENT_DATA"
        elif ece <= 0.06:
            overall_status = "VALIDATED (Well Calibrated)"
        elif ece <= 0.12:
            overall_status = "ACCEPTABLE (Moderate Calibration)"
        else:
            overall_status = "CALIBRATION_WARNING (High Error)"

        return {
            "status": overall_status,
            "sample_size": n,
            "ece": ece,
            "mce": mce,
            "buckets": buckets_data
        }

    # Alias for backward compatibility
    calculate_calibration_curve = compute_calibration_curve
