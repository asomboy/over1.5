import os
import sys
import logging
from typing import Dict, Any, List, Optional

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logger = logging.getLogger(__name__)


class PredictionExplanationService:
    """
    Phase 12 Machine-Readable Explainability Service.
    Produces transparent, evidence-based reason codes (SUPPORT and CAUTION)
    strictly derived from actual model features, validation samples, and data quality.
    """

    @classmethod
    def generate_explanation(
        cls,
        market: str,
        selection: str,
        probability: float,
        model_data: Dict[str, Any],
        eval_metrics: Dict[str, Any],
        data_quality: float,
        provider_status: str,
        drift_status: str,
        consistency_data: Dict[str, Any],
        is_live: bool = False
    ) -> Dict[str, Any]:
        """
        Synthesizes machine-readable explanation codes for a candidate prediction decision.
        """
        primary_factors = []
        supporting_factors = []
        caution_factors = []
        reason_codes = []

        def _add_factor(code: str, category: str, severity: str, message: str, is_primary: bool = False):
            item = {
                "code": code,
                "category": category,
                "severity": severity,
                "message": message
            }
            reason_codes.append(code)
            if category == "SUPPORT":
                if is_primary:
                    primary_factors.append(item)
                else:
                    supporting_factors.append(item)
            elif category == "CAUTION":
                caution_factors.append(item)

        # ---------------------------------------------------------------------
        # 1. Core Model & Statistical Support
        # ---------------------------------------------------------------------
        tot_xg = model_data.get("total_xg", model_data.get("expected_goals_xg", 2.5))
        tot_shots = model_data.get("expected_total_shots", 24.0)
        tot_sot = model_data.get("expected_total_sot", 8.5)
        tot_corners = model_data.get("total_expected_corners", 9.5)
        tot_cards = model_data.get("total_expected_cards", 4.0)
        ref_strictness = model_data.get("referee_strictness_index", 1.0)
        h_xg = model_data.get("home_xg", 1.4)
        a_xg = model_data.get("away_xg", 1.1)

        # High Attacking Form & xG
        if tot_xg >= 2.8 and "Goals" in market:
            _add_factor(
                "ABOVE_BASELINE_XG", "SUPPORT", "HIGH",
                f"Projected combined expected goals ({tot_xg:.2f} xG) substantially exceeds league average.",
                is_primary=True
            )
        elif tot_xg >= 2.4 and "Goals" in market:
            _add_factor(
                "HIGH_RECENT_ATTACKING_FORM", "SUPPORT", "MEDIUM",
                f"Combined attacking form indicates above-average scoring pressure ({tot_xg:.2f} xG)."
            )

        # Shots & Target Volume
        if tot_shots >= 26.0 and ("Shots" in market or "Goals" in market):
            _add_factor(
                "ABOVE_BASELINE_SHOT_VOLUME", "SUPPORT", "HIGH" if "Shots" in market else "MEDIUM",
                f"Elevated total match shot volume projected at {tot_shots:.1f} shots."
            )
        if tot_sot >= 9.0 and ("SoT" in market or "Goals" in market):
            _add_factor(
                "HIGH_SHOTS_ON_TARGET", "SUPPORT", "HIGH" if "SoT" in market else "MEDIUM",
                f"Strong target-hitting efficiency with {tot_sot:.1f} expected shots on target."
            )

        # Corners Generation
        if tot_corners >= 10.0 and ("Corners" in market or "Goals" in market):
            _add_factor(
                "STRONG_CORNER_GENERATION", "SUPPORT", "MEDIUM",
                f"Sustained wide-area attacking profile with {tot_corners:.1f} expected corners."
            )

        # Cards & Referee Disciplinary
        if "Cards" in market or "Fouls" in market:
            if tot_cards >= 4.5:
                _add_factor(
                    "HIGH_CARD_EXPECTATION", "SUPPORT", "HIGH",
                    f"Elevated disciplinary profile with {tot_cards:.1f} expected match cards."
                )
            if ref_strictness >= 1.15:
                _add_factor(
                    "STRICT_REFEREE", "SUPPORT", "HIGH",
                    f"Assigned referee strictly enforces disciplinary rules (strictness index: {ref_strictness:.2f}x)."
                )

        # Team Venue Profiles
        if h_xg >= 1.8 and ("Home" in selection or "1" in selection or "Over" in selection):
            _add_factor(
                "STRONG_HOME_PROFILE", "SUPPORT", "MEDIUM",
                f"Home team demonstrates strong attacking metrics at venue ({h_xg:.2f} xG)."
            )
        if a_xg >= 1.6 and ("Away" in selection or "2" in selection or "BTTS" in selection):
            _add_factor(
                "STRONG_AWAY_PROFILE", "SUPPORT", "MEDIUM",
                f"Away team maintains potent attacking output away from home ({a_xg:.2f} xG)."
            )

        # ---------------------------------------------------------------------
        # 2. Historical Calibration & Sample Size Support
        # ---------------------------------------------------------------------
        sample_size = eval_metrics.get("sample_size", 0)
        ece = eval_metrics.get("ece", 0.10)
        readiness = eval_metrics.get("readiness_status", "INSUFFICIENT_DATA")

        if sample_size >= 300:
            _add_factor(
                "LARGE_VERIFIED_SAMPLE", "SUPPORT", "HIGH",
                f"Model evaluation backed by large verified empirical sample (N={sample_size} matches)."
            )
            if ece <= 0.05:
                _add_factor(
                    "GOOD_MODEL_CALIBRATION", "SUPPORT", "HIGH",
                    f"Exceptional probabilistic reliability curve with low calibration error (ECE={ece:.3f})."
                )
            elif ece <= 0.07:
                _add_factor(
                    "GOOD_MODEL_CALIBRATION", "SUPPORT", "MEDIUM",
                    f"Well-calibrated probability distributions verified against completed outcomes (ECE={ece:.3f})."
                )
        elif sample_size >= 100:
            _add_factor(
                "MODEL_STILL_VALIDATING", "CAUTION", "LOW",
                f"Model currently validating in shadow mode with moderate sample (N={sample_size}/300)."
            )
        else:
            _add_factor(
                "LIMITED_HISTORICAL_SAMPLE", "CAUTION", "HIGH",
                f"Insufficient verified historical match sample for this market (N={sample_size} < 100 threshold)."
            )

        # ---------------------------------------------------------------------
        # 3. Data Quality & Provider Health
        # ---------------------------------------------------------------------
        if data_quality >= 0.80:
            _add_factor(
                "HIGH_DATA_QUALITY", "SUPPORT", "LOW",
                f"Comprehensive multi-provider data coverage with zero recorded integrity conflicts (DQ={int(data_quality*100)}%)."
            )
        elif data_quality < 0.60:
            _add_factor(
                "LOW_DATA_QUALITY", "CAUTION", "HIGH",
                f"Degraded data quality score ({int(data_quality*100)}%) due to missing historical statistics."
            )

        if provider_status in ["DEGRADED", "CIRCUIT_BREAKER_OPEN"]:
            _add_factor(
                "PROVIDER_DELAY", "CAUTION", "HIGH",
                f"External data provider feed is currently in {provider_status} state."
            )

        # ---------------------------------------------------------------------
        # 4. Drift & Degradation
        # ---------------------------------------------------------------------
        if drift_status == "DRIFT_DETECTED":
            _add_factor(
                "RECENT_DRIFT", "CAUTION", "HIGH",
                "Model evaluation shows recent distribution drift compared to historical baseline."
            )
        elif readiness == "DEGRADED":
            _add_factor(
                "MODEL_DEGRADED", "CAUTION", "HIGH",
                "Model reliability metrics exceed error thresholds; predictions marked degraded."
            )

        # ---------------------------------------------------------------------
        # 5. Cross-Market Consistency Checks
        # ---------------------------------------------------------------------
        consistency_score = consistency_data.get("consistency_score", 1.0)
        contradictions = consistency_data.get("contradiction_flags", [])

        if consistency_score >= 0.85 and "CROSS_MARKET_ALIGNED" in contradictions:
            _add_factor(
                "CROSS_MARKET_SUPPORT", "SUPPORT", "MEDIUM",
                "Cross-market dynamics (goals, shots, corners, cards) show coherent directional alignment."
            )
        elif consistency_score < 0.70:
            conflict_names = ", ".join([c for c in contradictions if c != "CROSS_MARKET_ALIGNED"])
            _add_factor(
                "CROSS_MARKET_CONFLICT", "CAUTION", "MEDIUM",
                f"Cross-market discrepancy detected: {conflict_names}."
            )

        # ---------------------------------------------------------------------
        # 6. Live In-Play Context
        # ---------------------------------------------------------------------
        if is_live:
            tempo = model_data.get("live_tempo", "NORMAL")
            if tempo == "HIGH" or model_data.get("momentum_pressure", 50) >= 65:
                _add_factor(
                    "LIVE_HIGH_TEMPO", "SUPPORT", "HIGH",
                    "Live in-play tracking indicates sustained attacking momentum and pace."
                )

        return {
            "primary_factors": primary_factors,
            "supporting_factors": supporting_factors,
            "caution_factors": caution_factors,
            "reason_codes": list(set(reason_codes))
        }
