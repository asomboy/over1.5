import os
import sys
import json
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, HistoricalResult, MatchStatistics, Prediction,
        CornerPredictionSnapshot, CardPredictionSnapshot, LiveMatchState,
        LivePredictionSnapshot, ModelEvaluation, UnifiedMatchIntelligenceSnapshot,
        Referee
    )
    from services.prediction_service import PoissonPredictionEngine
    from services.corners_service import CornersPredictionEngine
    from services.cards_service import CardsPredictionEngine
    from services.live_service import LiveMatchIntelligenceService
    from services.shots_prediction_service import ShotsPredictionEngine
    from services.match_statistics_prediction_service import MatchStatisticsPredictionEngine
    from services.production_validation_service import ProductionValidationService
    from services.data_reconciliation_service import DataReconciliationService
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, Prediction,
        CornerPredictionSnapshot, CardPredictionSnapshot, LiveMatchState,
        LivePredictionSnapshot, ModelEvaluation, UnifiedMatchIntelligenceSnapshot,
        Referee
    )
    from .prediction_service import PoissonPredictionEngine
    from .corners_service import CornersPredictionEngine
    from .cards_service import CardsPredictionEngine
    from .live_service import LiveMatchIntelligenceService
    from .shots_prediction_service import ShotsPredictionEngine
    from .match_statistics_prediction_service import MatchStatisticsPredictionEngine
    from .production_validation_service import ProductionValidationService
    from .data_reconciliation_service import DataReconciliationService

logger = logging.getLogger(__name__)


class UnifiedMatchIntelligenceService:
    """
    Central orchestration service creating a unified, multi-market match intelligence
    representation combining Goals, Corners, Cards, Shots, SoT, Match Statistics (Possession,
    Fouls, Offsides, Saves, Blocked Shots, Location, Pressure), Referee, Live state, 
    Cross-Market consistency, Match-State classifications, and ranked high-value signals.
    """

    @classmethod
    def get_unified_match_intelligence(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """
        Synthesizes all predictive engines into a single unified Match Intelligence object.
        Consumes specialized models without modifying their individual mathematical logic.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {"error": f"Fixture {fixture_id} not found"}

        # 1. Base Fixture Identity & Status
        home_team_name = fixture.home_team.name if fixture.home_team else "Home"
        away_team_name = fixture.away_team.name if fixture.away_team else "Away"
        competition_name = fixture.league.name if fixture.league else "League"

        is_live = (fixture.status == "LIVE")
        is_finished = fixture.status in ["FINISHED", "FT", "AET", "PEN"]

        # 2. Specialized Engine Predictions
        goals_data = cls._get_goals_intelligence(db, fixture)
        corners_data = cls._get_corners_intelligence(db, fixture)
        cards_data = cls._get_cards_intelligence(db, fixture)
        shots_data = cls._get_shots_intelligence(db, fixture)
        match_stats_data = cls._get_match_stats_intelligence(db, fixture)
        live_data = cls._get_live_intelligence(db, fixture) if is_live else None

        # 3. Cross-Market Consistency & Diagnostics
        consistency = cls._evaluate_cross_market_consistency(goals_data, corners_data, cards_data, live_data, shots_data, match_stats_data)

        # 4. Match-State Classifier
        match_states = cls._classify_match_state(fixture, goals_data, corners_data, cards_data, live_data, shots_data, match_stats_data)

        # 5. Conservative Unified Confidence
        unified_confidence = cls._calculate_unified_confidence(
            goals_data, corners_data, cards_data, live_data, consistency["consistency_score"], shots_data, match_stats_data
        )

        # 6. Ranked Cross-Market Signals
        signals = cls._rank_unified_signals(goals_data, corners_data, cards_data, live_data, unified_confidence, shots_data, match_stats_data)

        # 7. Team Profile Intelligence
        team_profiles = cls._build_team_profiles(db, fixture, goals_data, corners_data, cards_data, shots_data, match_stats_data)

        # 8. Assembled Unified Payload
        payload = {
            "fixture_id": fixture.id,
            "home_team": home_team_name,
            "away_team": away_team_name,
            "competition": competition_name,
            "kickoff": fixture.match_date.isoformat() if fixture.match_date else None,
            "match_status": fixture.status,
            "is_live": is_live,
            "match_minute": live_data.get("current_minute", 0) if live_data else 0,
            "current_score": {
                "home": fixture.home_score if fixture.home_score is not None else 0,
                "away": fixture.away_score if fixture.away_score is not None else 0
            } if (is_live or is_finished) else None,
            "unified_confidence": unified_confidence,
            "match_state_classification": match_states,
            "cross_market_consistency": consistency,
            "ranked_signals": signals,
            "markets": {
                "goals": goals_data,
                "corners": corners_data,
                "cards": cards_data,
                "shots": shots_data.get("shots", {}),
                "shots_on_target": shots_data.get("shots_on_target", {}),
                "possession": match_stats_data.get("possession", {}),
                "fouls": match_stats_data.get("fouls", {}),
                "offsides": match_stats_data.get("offsides", {}),
                "saves": match_stats_data.get("saves", {}),
                "blocked_shots": match_stats_data.get("blocked_shots", {}),
                "shot_location": match_stats_data.get("shot_location", {}),
                "attacking_pressure": match_stats_data.get("attacking_pressure", {}),
                "live": live_data
            },
            "team_profiles": team_profiles,
            "data_provenance_summary": {
                "has_verified_referee": bool(cards_data.get("referee_name")),
                "observed_goals": fixture.home_score is not None,
                "provenance_records": len(DataReconciliationService.get_fixture_provenance(db, fixture.id))
            },
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

        return payload

    # =========================================================================
    # SPECIALIZED ENGINE INTEGRATION
    # =========================================================================

    @classmethod
    def _get_goals_intelligence(cls, db: Session, fixture: Fixture) -> Dict[str, Any]:
        """Extracts goals xG and market probabilities from PoissonPredictionEngine."""
        try:
            pred = PoissonPredictionEngine.predict_fixture(db, fixture.id)
            if pred:
                return {
                    "status": "AVAILABLE",
                    "model_version": "dixon_coles_v2",
                    "home_xg": pred.get("home_xg", 1.45),
                    "away_xg": pred.get("away_xg", 1.15),
                    "total_xg": round(pred.get("home_xg", 1.45) + pred.get("away_xg", 1.15), 2),
                    "probabilities": {
                        "over_0_5": pred.get("over_0_5_prob", 0.92),
                        "over_1_5": pred.get("over_1_5_prob", 0.78),
                        "over_2_5": pred.get("over_2_5_prob", 0.54),
                        "over_3_5": pred.get("over_3_5_prob", 0.31),
                        "btts": pred.get("btts_prob", 0.52),
                        "home_win": pred.get("home_win_prob", 0.48),
                        "draw": pred.get("draw_prob", 0.26),
                        "away_win": pred.get("away_win_prob", 0.26)
                    },
                    "confidence": pred.get("confidence", 75)
                }
        except Exception as ex:
            logger.debug(f"Goals prediction fallback for fixture {fixture.id}: {ex}")

        return {"status": "UNAVAILABLE", "model_version": "dixon_coles_v2", "probabilities": {}}

    @classmethod
    def _get_corners_intelligence(cls, db: Session, fixture: Fixture) -> Dict[str, Any]:
        """Extracts corners expected rates and probabilities from CornersPredictionEngine."""
        try:
            pred = CornersPredictionEngine.predict_corners(db, fixture.id)
            if pred and not pred.get("error"):
                return {
                    "status": "AVAILABLE",
                    "model_version": "corners_negbin_v2",
                    "home_expected_corners": pred.get("home_corners", 5.2),
                    "away_expected_corners": pred.get("away_corners", 4.3),
                    "total_expected_corners": pred.get("total_corners", 9.5),
                    "probabilities": {
                        "over_7_5": pred.get("prob_over_7_5", 0.74),
                        "over_8_5": pred.get("prob_over_8_5", 0.62),
                        "over_9_5": pred.get("prob_over_9_5", 0.49),
                        "over_10_5": pred.get("prob_over_10_5", 0.37),
                        "over_11_5": pred.get("prob_over_11_5", 0.26)
                    },
                    "confidence": pred.get("confidence", 70)
                }
        except Exception as ex:
            logger.debug(f"Corners prediction fallback for fixture {fixture.id}: {ex}")

        return {"status": "UNAVAILABLE", "model_version": "corners_negbin_v2", "probabilities": {}}

    @classmethod
    def _get_cards_intelligence(cls, db: Session, fixture: Fixture) -> Dict[str, Any]:
        """Extracts disciplinary cards and referee data from CardsPredictionEngine."""
        try:
            pred = CardsPredictionEngine.predict_cards(db, fixture.id)
            if pred and not pred.get("error"):
                return {
                    "status": "AVAILABLE",
                    "model_version": "cards_referee_v2",
                    "expected_yellow_cards": pred.get("expected_yellow_cards", 3.8),
                    "expected_red_cards": pred.get("expected_red_cards", 0.18),
                    "total_expected_cards": pred.get("total_expected_cards", 4.0),
                    "referee_name": pred.get("referee_name"),
                    "referee_tier": pred.get("referee_tier", "TIER_5_LEAGUE_DEFAULT"),
                    "referee_strictness_index": pred.get("referee_strictness_index", 1.0),
                    "probabilities": {
                        "over_2_5": pred.get("prob_over_2_5", 0.81),
                        "over_3_5": pred.get("prob_over_3_5", 0.61),
                        "over_4_5": pred.get("prob_over_4_5", 0.39),
                        "over_5_5": pred.get("prob_over_5_5", 0.22),
                        "any_red_card": pred.get("prob_any_red_card", 0.16)
                    },
                    "confidence": pred.get("confidence", 65)
                }
        except Exception as ex:
            logger.debug(f"Cards prediction fallback for fixture {fixture.id}: {ex}")

        return {"status": "UNAVAILABLE", "model_version": "cards_referee_v2", "probabilities": {}}

    @classmethod
    def _get_live_intelligence(cls, db: Session, fixture: Fixture) -> Optional[Dict[str, Any]]:
        """Extracts live dynamic prediction state if match is currently in-play."""
        try:
            live_pred = LiveMatchIntelligenceService.predict_live_match(db, fixture.id)
            if live_pred and not live_pred.get("error"):
                return {
                    "status": "LIVE_ACTIVE",
                    "current_minute": live_pred.get("match_state", {}).get("minute", 45),
                    "period": live_pred.get("match_state", {}).get("period", "2H"),
                    "home_score": live_pred.get("match_state", {}).get("home_score", 0),
                    "away_score": live_pred.get("match_state", {}).get("away_score", 0),
                    "remaining_home_xg": live_pred.get("live_goals", {}).get("remaining_home_xg", 0.6),
                    "remaining_away_xg": live_pred.get("live_goals", {}).get("remaining_away_xg", 0.4),
                    "dynamic_probabilities": live_pred.get("live_goals", {}).get("probabilities", {}),
                    "momentum_pressure": live_pred.get("diagnostics", {}).get("momentum_pressure", 50),
                    "live_confidence": live_pred.get("confidence", {}).get("overall_confidence", 60)
                }
        except Exception as ex:
            logger.debug(f"Live prediction extraction for fixture {fixture.id}: {ex}")
        return None

    @classmethod
    def _get_shots_intelligence(cls, db: Session, fixture: Fixture) -> Dict[str, Any]:
        """Extracts shot and SoT expectations from ShotsPredictionEngine."""
        try:
            pred = ShotsPredictionEngine.predict_shots(db, fixture.id)
            if pred and not pred.get("error"):
                return pred
        except Exception as ex:
            logger.debug(f"Shots prediction fallback for fixture {fixture.id}: {ex}")

        return {
            "status": "UNAVAILABLE",
            "model_version": "v1_shots_nb",
            "shots": {"expected_total_shots": 24.5, "probabilities": {}},
            "shots_on_target": {"expected_total_sot": 8.7, "probabilities": {}},
            "confidence": 0.40,
            "data_quality": 0.40
        }

    @classmethod
    def _get_match_stats_intelligence(cls, db: Session, fixture: Fixture) -> Dict[str, Any]:
        """Extracts match statistics expectations (Possession, Fouls, Offsides, Saves, Blocked, Locations, Pressure)."""
        try:
            pred = MatchStatisticsPredictionEngine.predict_match_statistics(db, fixture.id)
            if pred and not pred.get("error"):
                return pred
        except Exception as ex:
            logger.debug(f"Match statistics prediction fallback for fixture {fixture.id}: {ex}")

        return {
            "status": "UNAVAILABLE",
            "model_version": "v1_match_stats_nb",
            "possession": {"expected_home_possession": 50.0, "expected_away_possession": 50.0},
            "fouls": {"expected_total_fouls": 24.2, "probabilities": {}},
            "offsides": {"expected_total_offsides": 3.3, "probabilities": {}},
            "saves": {"expected_total_saves": 6.0, "probabilities": {}},
            "blocked_shots": {"expected_total_blocked_shots": 5.8, "probabilities": {}},
            "shot_location": {"expected_total_inside_box": 15.0, "expected_total_outside_box": 9.5},
            "attacking_pressure": {"type": "MODEL_DERIVED", "home_pressure_index": 50.0, "away_pressure_index": 50.0},
            "confidence": 0.40,
            "data_quality": 0.40
        }

    # =========================================================================
    # CROSS-MARKET CONSISTENCY & CONTRADICTIONS
    # =========================================================================

    @classmethod
    def _evaluate_cross_market_consistency(
        cls, goals: Dict[str, Any], corners: Dict[str, Any], cards: Dict[str, Any],
        live: Optional[Dict[str, Any]], shots: Optional[Dict[str, Any]] = None,
        match_stats: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Quantifies cross-market consistency and surfaces contradiction diagnostic flags.
        Preserves original specialized outputs while exposing alignment score.
        """
        flags = []
        score = 1.0

        tot_xg = goals.get("total_xg", 2.6)
        tot_corners = corners.get("total_expected_corners", 9.5)
        tot_cards = cards.get("total_expected_cards", 4.0)
        ref_strictness = cards.get("referee_strictness_index", 1.0)
        btts_p = goals.get("probabilities", {}).get("btts", 0.50)
        o25_p = goals.get("probabilities", {}).get("over_2_5", 0.50)

        # 1. Goal vs Corner Attacking Activity Check
        if tot_xg >= 3.4 and tot_corners < 7.0:
            flags.append("HIGH_GOAL_LOW_CORNER_DISCREPANCY")
            score -= 0.15
        elif tot_xg < 1.8 and tot_corners > 12.5:
            flags.append("LOW_GOAL_EXTREME_CORNER_DISCREPANCY")
            score -= 0.15

        # 2. BTTS vs Over 2.5 Covariance Check
        if btts_p >= 0.70 and o25_p < 0.40:
            flags.append("BTTS_O25_COVARIANCE_DIVERGENCE")
            score -= 0.15

        # 3. Card Expectation vs Referee Strictness Check
        if tot_cards >= 5.5 and ref_strictness < 0.85:
            flags.append("HIGH_CARD_WITHOUT_STRICT_REF")
            score -= 0.10

        # 4. Shots ↔ Goals Cross-Market Diagnostics
        if shots and shots.get("status") == "AVAILABLE":
            tot_shots = shots.get("shots", {}).get("expected_total_shots", 24.5)
            tot_sot = shots.get("shots_on_target", {}).get("expected_total_sot", 8.5)
            if tot_shots >= 29.0 and tot_xg < 1.8:
                flags.append("HIGH_SHOT_VOLUME_LOW_GOAL_EXPECTATION")
                score -= 0.10
            elif tot_shots < 16.0 and tot_xg >= 3.2:
                flags.append("LOW_SHOT_VOLUME_HIGH_GOAL_EXPECTATION")
                score -= 0.10
            if tot_sot >= 11.5:
                flags.append("HIGH_SOT_VOLUME_GOAL_PRESSURE")

        # 5. Match Statistics Consistency Diagnostics (Phase 11)
        if match_stats and match_stats.get("status") == "AVAILABLE":
            h_poss = match_stats.get("possession", {}).get("expected_home_possession", 50.0)
            h_shots = shots.get("shots", {}).get("expected_home_shots", 13.0) if shots else 13.0
            tot_sot = shots.get("shots_on_target", {}).get("expected_total_sot", 8.5) if shots else 8.5
            tot_saves = match_stats.get("saves", {}).get("expected_total_saves", 6.0)
            tot_fouls = match_stats.get("fouls", {}).get("expected_total_fouls", 24.0)
            tot_blk = match_stats.get("blocked_shots", {}).get("expected_total_blocked_shots", 5.5)

            # Possession ↔ Shots Check
            if h_poss >= 64.0 and h_shots < 9.0:
                flags.append("HIGH_POSSESSION_LOW_SHOT_OUTPUT")
                score -= 0.10

            # Shots ↔ Saves Check
            if tot_sot >= 10.0 and tot_saves < 4.0:
                flags.append("HIGH_SOT_LOW_SAVE_EXPECTATION")
                score -= 0.10

            # Shots ↔ Blocked Shots Check
            if shots and shots.get("shots", {}).get("expected_total_shots", 24.0) >= 28.0 and tot_blk < 3.0:
                flags.append("HIGH_SHOT_VOLUME_LOW_BLOCK_RATE")
                score -= 0.05

            # Fouls ↔ Cards Check
            if tot_fouls >= 28.0 and tot_cards < 2.5:
                flags.append("HIGH_FOUL_LOW_CARD_DISCREPANCY")
                score -= 0.05

            # Possession ↔ Corners Check
            if h_poss >= 60.0 and corners.get("home_expected_corners", 5.0) >= 7.0:
                flags.append("POSSESSION_PRESSURE_CORNER_ALIGNMENT")

        # 6. Live Remaining Rates vs Current Time Check
        if live:
            minute = live.get("current_minute", 45)
            rem_xg = live.get("remaining_home_xg", 0.0) + live.get("remaining_away_xg", 0.0)
            if minute >= 80 and rem_xg > 1.2:
                flags.append("LATE_MATCH_HIGH_REMAINING_XG_DISCREPANCY")
                score -= 0.20

        if not flags:
            flags.append("CROSS_MARKET_ALIGNED")

        return {
            "consistency_score": max(0.20, min(1.0, round(score, 2))),
            "status": "ALIGNED" if score >= 0.80 else ("MODERATE_VARIANCE" if score >= 0.60 else "CONTRADICTION_WARNING"),
            "contradiction_flags": flags
        }

    # =========================================================================
    # MATCH-STATE CLASSIFIER
    # =========================================================================

    @classmethod
    def _classify_match_state(
        cls, fixture: Fixture, goals: Dict[str, Any], corners: Dict[str, Any],
        cards: Dict[str, Any], live: Optional[Dict[str, Any]], shots: Optional[Dict[str, Any]] = None,
        match_stats: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """Classifies the tactical/dynamic match state into transparent descriptive tags."""
        tags = []
        status = fixture.status

        if status == "SCHEDULED":
            tags.append("PRE_MATCH")
        elif status in ["FINISHED", "FT", "AET", "PEN"]:
            tags.append("COMPLETED")

        h_xg = goals.get("home_xg", 1.4)
        a_xg = goals.get("away_xg", 1.1)
        tot_xg = goals.get("total_xg", 2.5)
        tot_corners = corners.get("total_expected_corners", 9.5)
        tot_cards = cards.get("total_expected_cards", 4.0)

        # Dominance
        if (h_xg - a_xg) >= 0.70:
            tags.append("HOME_DOMINANT")
        elif (a_xg - h_xg) >= 0.70:
            tags.append("AWAY_DOMINANT")
        else:
            tags.append("BALANCED")

        # Tempo & Attacking Volume
        tot_shots = shots.get("shots", {}).get("expected_total_shots", 24.0) if shots else 24.0
        if tot_xg >= 3.0 or tot_corners >= 11.0 or tot_shots >= 28.0:
            tags.append("HIGH_TEMPO")
        elif tot_xg <= 2.1 and tot_corners <= 8.5 and tot_shots <= 19.0:
            tags.append("LOW_TEMPO")

        # Disciplinary Tension
        tot_fouls = match_stats.get("fouls", {}).get("expected_total_fouls", 24.0) if match_stats else 24.0
        if tot_cards >= 4.8 or tot_fouls >= 27.0 or cards.get("referee_strictness_index", 1.0) >= 1.20:
            tags.append("DISCIPLINARY_TENSION")

        # Live Dynamic Characteristics
        if live:
            minute = live.get("current_minute", 45)
            h_s = live.get("home_score", 0)
            a_s = live.get("away_score", 0)
            diff = abs(h_s - a_s)

            if diff >= 3:
                tags.append("BLOWOUT")
            elif minute >= 70 and diff == 1:
                tags.append("LATE_URGENCY")

            if live.get("momentum_pressure", 50) >= 65:
                tags.append("GOAL_PRESSURE")

        return tags

    # =========================================================================
    # CONSERVATIVE UNIFIED CONFIDENCE
    # =========================================================================

    @classmethod
    def _extract_confidence_num(cls, val: Any) -> float:
        """Safely extracts normalized 0.0-1.0 confidence from int, float, or dict."""
        if isinstance(val, dict):
            num = val.get("overall_confidence", val.get("confidence", 50))
            return float(num) / 100.0 if float(num) > 1.0 else float(num)
        elif isinstance(val, (int, float)):
            return float(val) / 100.0 if float(val) > 1.0 else float(val)
        return 0.50

    @classmethod
    def _calculate_unified_confidence(
        cls, goals: Dict[str, Any], corners: Dict[str, Any], cards: Dict[str, Any],
        live: Optional[Dict[str, Any]], consistency_score: float, shots: Optional[Dict[str, Any]] = None,
        match_stats: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Derives multi-factor conservative confidence score.
        High probability does not automatically imply high confidence.
        """
        # Engine availability scores
        g_conf = cls._extract_confidence_num(goals.get("confidence", 50)) if goals.get("status") == "AVAILABLE" else 0.40
        c_conf = cls._extract_confidence_num(corners.get("confidence", 50)) if corners.get("status") == "AVAILABLE" else 0.40
        d_conf = cls._extract_confidence_num(cards.get("confidence", 50)) if cards.get("status") == "AVAILABLE" else 0.40
        s_conf = cls._extract_confidence_num(shots.get("confidence", 50)) if shots and shots.get("status") == "AVAILABLE" else 0.40
        m_conf = cls._extract_confidence_num(match_stats.get("confidence", 50)) if match_stats and match_stats.get("status") == "AVAILABLE" else 0.40

        # Referee bonus
        has_ref = cards.get("referee_tier") not in ["TIER_5_LEAGUE_DEFAULT", None]
        ref_factor = 1.0 if has_ref else 0.75

        # Live penalty
        live_factor = 1.0
        if live:
            l_conf = cls._extract_confidence_num(live.get("live_confidence", 60))
            live_factor = l_conf

        raw = (
            0.25 * g_conf +
            0.15 * c_conf +
            0.15 * d_conf +
            0.10 * s_conf +
            0.10 * m_conf +
            0.05 * ref_factor +
            0.20 * consistency_score
        ) * live_factor

        # Bounded between 0.15 and 0.95
        return round(max(0.15, min(0.95, raw)), 2)

    # =========================================================================
    # RANKED UNIFIED SIGNALS
    # =========================================================================

    @classmethod
    def _rank_unified_signals(
        cls, goals: Dict[str, Any], corners: Dict[str, Any], cards: Dict[str, Any],
        live: Optional[Dict[str, Any]], unified_confidence: float, shots: Optional[Dict[str, Any]] = None,
        match_stats: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Ranks top predictive opportunities across all markets.
        Enforces strict probability and confidence thresholds (never forces a signal).
        """
        candidates = []

        # 1. Goals candidates
        g_probs = goals.get("probabilities", {})
        if g_probs.get("over_1_5", 0) >= 0.75:
            candidates.append({"market": "Over 1.5 Goals", "category": "GOALS", "prob": g_probs["over_1_5"], "model": "Dixon-Coles v2"})
        if g_probs.get("over_2_5", 0) >= 0.65:
            candidates.append({"market": "Over 2.5 Goals", "category": "GOALS", "prob": g_probs["over_2_5"], "model": "Dixon-Coles v2"})
        if g_probs.get("btts", 0) >= 0.64:
            candidates.append({"market": "Both Teams to Score (Yes)", "category": "GOALS", "prob": g_probs["btts"], "model": "Dixon-Coles v2"})

        # 2. Corners candidates
        c_probs = corners.get("probabilities", {})
        if c_probs.get("over_8_5", 0) >= 0.70:
            candidates.append({"market": "Over 8.5 Corners", "category": "CORNERS", "prob": c_probs["over_8_5"], "model": "Negative Binomial"})
        if c_probs.get("over_9_5", 0) >= 0.65:
            candidates.append({"market": "Over 9.5 Corners", "category": "CORNERS", "prob": c_probs["over_9_5"], "model": "Negative Binomial"})

        # 3. Cards candidates
        d_probs = cards.get("probabilities", {})
        if d_probs.get("over_3_5", 0) >= 0.68:
            candidates.append({"market": "Over 3.5 Total Cards", "category": "CARDS", "prob": d_probs["over_3_5"], "model": "Cards & Referee Model"})
        if d_probs.get("over_2_5", 0) >= 0.78:
            candidates.append({"market": "Over 2.5 Total Cards", "category": "CARDS", "prob": d_probs["over_2_5"], "model": "Cards & Referee Model"})

        # 4. Shots candidates (Phase 10)
        if shots and shots.get("status") == "AVAILABLE":
            s_probs = shots.get("shots", {}).get("probabilities", {})
            sot_probs = shots.get("shots_on_target", {}).get("probabilities", {})
            if s_probs.get("over_19_5", 0) >= 0.76:
                candidates.append({"market": "Over 19.5 Total Shots", "category": "SHOTS", "prob": s_probs["over_19_5"], "model": "Negative Binomial Shots"})
            if sot_probs.get("over_6_5", 0) >= 0.74:
                candidates.append({"market": "Over 6.5 Total SoT", "category": "SHOTS_ON_TARGET", "prob": sot_probs["over_6_5"], "model": "SoT Model"})

        # 5. Match Statistics candidates (Phase 11)
        if match_stats and match_stats.get("status") == "AVAILABLE":
            f_probs = match_stats.get("fouls", {}).get("probabilities", {})
            off_probs = match_stats.get("offsides", {}).get("probabilities", {})
            sv_probs = match_stats.get("saves", {}).get("probabilities", {})
            if f_probs.get("over_21_5", 0) >= 0.75:
                candidates.append({"market": "Over 21.5 Total Fouls", "category": "FOULS", "prob": f_probs["over_21_5"], "model": "Fouls Engine"})
            if off_probs.get("over_2_5", 0) >= 0.72:
                candidates.append({"market": "Over 2.5 Total Offsides", "category": "OFFSIDES", "prob": off_probs["over_2_5"], "model": "Offsides Engine"})
            if sv_probs.get("over_4_5", 0) >= 0.70:
                candidates.append({"market": "Over 4.5 Total Saves", "category": "SAVES", "prob": sv_probs["over_4_5"], "model": "Saves Engine"})

        # Sort candidates by probability
        candidates.sort(key=lambda x: x["prob"], reverse=True)

        ranked = []
        for c in candidates:
            prob = c["prob"]
            # Risk tier assignment
            if prob >= 0.78 and unified_confidence >= 0.65:
                risk_tier = "LOW"
            elif prob >= 0.68:
                risk_tier = "MEDIUM"
            else:
                risk_tier = "HIGH"

            ranked.append({
                "market": c["market"],
                "category": c["category"],
                "probability": round(prob, 3),
                "confidence": unified_confidence,
                "risk_tier": risk_tier,
                "model_source": c["model"]
            })

        return ranked if ranked else [{"market": "NO_SIGNAL", "category": "NONE", "probability": 0.0, "confidence": unified_confidence, "risk_tier": "NO_SIGNAL", "model_source": "None"}]

    # =========================================================================
    # TEAM PROFILES
    # =========================================================================

    @classmethod
    def _build_team_profiles(
        cls, db: Session, fixture: Fixture, goals: Dict[str, Any], corners: Dict[str, Any],
        cards: Dict[str, Any], shots: Optional[Dict[str, Any]] = None,
        match_stats: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Constructs unified multi-dimension profile for home and away teams."""
        h_name = fixture.home_team.name if fixture.home_team else "Home"
        a_name = fixture.away_team.name if fixture.away_team else "Away"

        return {
            "home_team": {
                "name": h_name,
                "expected_goals": goals.get("home_xg", 1.4),
                "expected_corners": corners.get("home_expected_corners", 5.0),
                "expected_cards": round((cards.get("total_expected_cards", 4.0) * 0.48), 1),
                "expected_shots": shots.get("shots", {}).get("expected_home_shots", 13.5) if shots else 13.5,
                "expected_sot": shots.get("shots_on_target", {}).get("expected_home_sot", 4.8) if shots else 4.8,
                "expected_possession": match_stats.get("possession", {}).get("expected_home_possession", 50.0) if match_stats else 50.0,
                "expected_fouls": match_stats.get("fouls", {}).get("expected_home_fouls", 11.8) if match_stats else 11.8,
                "expected_saves": match_stats.get("saves", {}).get("expected_home_saves", 2.8) if match_stats else 2.8,
                "data_sample_available": True
            },
            "away_team": {
                "name": a_name,
                "expected_goals": goals.get("away_xg", 1.1),
                "expected_corners": corners.get("away_expected_corners", 4.2),
                "expected_cards": round((cards.get("total_expected_cards", 4.0) * 0.52), 1),
                "expected_shots": shots.get("shots", {}).get("expected_away_shots", 11.2) if shots else 11.2,
                "expected_sot": shots.get("shots_on_target", {}).get("expected_away_sot", 3.9) if shots else 3.9,
                "expected_possession": match_stats.get("possession", {}).get("expected_away_possession", 50.0) if match_stats else 50.0,
                "expected_fouls": match_stats.get("fouls", {}).get("expected_away_fouls", 12.4) if match_stats else 12.4,
                "expected_saves": match_stats.get("saves", {}).get("expected_away_saves", 3.2) if match_stats else 3.2,
                "data_sample_available": True
            }
        }

    # =========================================================================
    # SNAPSHOT PERSISTENCE
    # =========================================================================

    @classmethod
    def capture_unified_snapshot(cls, db: Session, fixture_id: int) -> UnifiedMatchIntelligenceSnapshot:
        """
        Creates and persists an immutable UnifiedMatchIntelligenceSnapshot.
        """
        intelligence = cls.get_unified_match_intelligence(db, fixture_id)
        
        snapshot = UnifiedMatchIntelligenceSnapshot(
            fixture_id=fixture_id,
            match_status=intelligence.get("match_status", "SCHEDULED"),
            match_minute=intelligence.get("match_minute", 0),
            unified_confidence=intelligence.get("unified_confidence", 0.50),
            match_state_tags=json.dumps(intelligence.get("match_state_classification", [])),
            cross_market_consistency_score=intelligence.get("cross_market_consistency", {}).get("consistency_score", 1.0),
            intelligence_payload=json.dumps(intelligence),
            created_at=datetime.now(timezone.utc)
        )
        db.add(snapshot)
        db.commit()
        return snapshot
