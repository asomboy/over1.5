import os
import sys
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import ModelEvaluation
except ImportError:
    from ..models import ModelEvaluation

logger = logging.getLogger(__name__)

MIN_ENSEMBLE_SAMPLE_THRESHOLD = 100
MAX_MODEL_WEIGHT = 0.70
MIN_ACTIVE_MODEL_WEIGHT = 0.05


class AdaptiveEnsembleService:
    """
    Adaptive model ensemble framework computing dynamic model blending weights
    based on empirical Brier scores, calibration reliability, and sample size safeguards.
    Domain-isolated so goal markets only blend goal sub-models and never cross-contaminate
    with corners, cards, or shots models.
    """

    DEFAULT_MODELS = [
        "v2_match_intelligence",
        "dixon_coles_v2",
        "elo_poisson_v1",
    ]

    DOMAIN_CANDIDATE_MODELS: Dict[str, List[str]] = {
        "goals": ["v2_match_intelligence", "dixon_coles_v2", "elo_poisson_v1"],
        "corners": ["v1_corners_nb", "corners_poisson_v1"],
        "cards": ["v1_cards_nb", "cards_poisson_v1"],
        "shots": ["v1_shots_nb", "shots_poisson_v1"],
        "stats": ["v1_match_stats_nb", "stats_poisson_v1"],
    }

    @classmethod
    def resolve_domain_models(cls, market: str) -> List[str]:
        m_low = (market or "").lower()
        if "corner" in m_low:
            return list(cls.DOMAIN_CANDIDATE_MODELS["corners"])
        if "card" in m_low or "booking" in m_low:
            return list(cls.DOMAIN_CANDIDATE_MODELS["cards"])
        if "shot" in m_low or "sot" in m_low:
            return list(cls.DOMAIN_CANDIDATE_MODELS["shots"])
        if any(k in m_low for k in ("foul", "offside", "possession", "save")):
            return list(cls.DOMAIN_CANDIDATE_MODELS["stats"])
        return list(cls.DOMAIN_CANDIDATE_MODELS["goals"])

    @classmethod
    def calculate_ensemble_weights(
        cls, db: Session, market: str, candidate_models: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Derives normalized ensemble weights for candidate models on a specific market.
        Enforces strict safety thresholds: if sample < 100, returns baseline default prior.
        """
        candidate_models = candidate_models or cls.resolve_domain_models(market)

        model_scores = {}
        total_sample = 0

        for model_ver in candidate_models:
            recs = (
                db.query(ModelEvaluation)
                .filter(
                    ModelEvaluation.market == market,
                    ModelEvaluation.model_version == model_ver,
                    ModelEvaluation.verified == True,
                )
                .all()
            )
            n = len(recs)
            total_sample += n

            if n >= MIN_ENSEMBLE_SAMPLE_THRESHOLD:
                mean_brier = sum(r.brier_component or 0.25 for r in recs) / float(n)
                perf_score = max(0.1, 1.0 - mean_brier)
                sample_rel = min(1.0, n / 300.0)
                raw_score = perf_score * (0.5 + 0.5 * sample_rel)
            else:
                raw_score = 0.5

            model_scores[model_ver] = {
                "sample_size": n,
                "raw_score": raw_score,
                "eligible": n >= MIN_ENSEMBLE_SAMPLE_THRESHOLD,
            }

        if total_sample < MIN_ENSEMBLE_SAMPLE_THRESHOLD:
            uniform_w = round(1.0 / float(len(candidate_models)), 4)
            weights = {m: uniform_w for m in candidate_models}
            w_sum = sum(weights.values())
            if candidate_models and abs(w_sum - 1.0) > 0.0001:
                weights[candidate_models[0]] = round(weights[candidate_models[0]] + (1.0 - w_sum), 4)
            return {
                "status": "INSUFFICIENT_DATA",
                "market": market,
                "total_verified_sample": total_sample,
                "required_sample": MIN_ENSEMBLE_SAMPLE_THRESHOLD,
                "weights": weights,
                "model_weights": weights,
                "model_diagnostics": model_scores,
            }

        sum_raw = sum(v["raw_score"] for v in model_scores.values())
        raw_weights = {
            m: (model_scores[m]["raw_score"] / max(0.01, sum_raw))
            for m in candidate_models
        }

        bounded = {}
        for m, w in raw_weights.items():
            bounded[m] = max(MIN_ACTIVE_MODEL_WEIGHT, min(MAX_MODEL_WEIGHT, w))

        sum_bounded = sum(bounded.values())
        final_weights = {m: round(bounded[m] / sum_bounded, 4) for m in candidate_models}

        w_sum = sum(final_weights.values())
        if candidate_models and abs(w_sum - 1.0) > 0.0001:
            first_m = candidate_models[0]
            final_weights[first_m] = round(final_weights[first_m] + (1.0 - w_sum), 4)

        return {
            "status": "ADAPTIVE_ENSEMBLE_ACTIVE",
            "market": market,
            "total_verified_sample": total_sample,
            "weights": final_weights,
            "model_weights": final_weights,
            "model_diagnostics": model_scores,
        }

    calculate_dynamic_ensemble_weights = calculate_ensemble_weights
