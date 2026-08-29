import os
import sys
import json
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, cast
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, HistoricalResult, MatchStatistics, League, Team,
        Prediction, CornerPredictionSnapshot, CardPredictionSnapshot,
        LivePredictionSnapshot, ModelEvaluation
    )
    from services.market_resolution_service import MarketResolutionService
    from services.calibration_service import (
        CalibrationService, MIN_PRODUCTION_VALIDATION_SAMPLE
    )
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, League, Team,
        Prediction, CornerPredictionSnapshot, CardPredictionSnapshot,
        LivePredictionSnapshot, ModelEvaluation
    )
    from .market_resolution_service import MarketResolutionService
    from .calibration_service import (
        CalibrationService, MIN_PRODUCTION_VALIDATION_SAMPLE
    )

logger = logging.getLogger(__name__)


class ModelEvaluationService:
    """
    Central evaluation engine linking immutable pre-match and in-play predictions
    to ground-truth outcomes, calculating proper probabilistic metrics, calibration,
    league breakdowns, minute-bucket performance, and model drift detection.
    """

    @classmethod
    def evaluate_finished_fixture(cls, db: Session, fixture_id: int) -> int:
        """
        Locates all immutable prediction snapshots for a finished match, resolves market outcomes,
        and saves ModelEvaluation records idempotently. Returns count of evaluation records processed.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture or fixture.status not in ["FINISHED", "FT", "AET", "PEN"]:
            return 0

        hist = db.query(HistoricalResult).filter(HistoricalResult.fixture_id == fixture_id).first()
        stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture_id).first()

        h_score = fixture.home_score if fixture.home_score is not None else (hist.home_score if hist else None)
        a_score = fixture.away_score if fixture.away_score is not None else (hist.away_score if hist else None)

        if h_score is None or a_score is None:
            return 0

        ht_h = hist.half_time_home_score if hist else None
        ht_a = hist.half_time_away_score if hist else None
        h_corn = stats.home_corners if stats else (hist.home_corners if hist else None)
        a_corn = stats.away_corners if stats else (hist.away_corners if hist else None)
        h_y = stats.home_yellow_cards if stats else (hist.home_yellow_cards if hist else None)
        a_y = stats.away_yellow_cards if stats else (hist.away_yellow_cards if hist else None)
        h_r = stats.home_red_cards if stats else (hist.home_red_cards if hist else None)
        a_r = stats.away_red_cards if stats else (hist.away_red_cards if hist else None)

        comp_name = fixture.league.name if fixture.league else "Unknown Competition"
        eval_records_count = 0

        # Helper to upsert evaluation record
        def _upsert_eval(
            p_type: str,
            mkt: str,
            m_ver: str,
            pred_time: datetime,
            prob: float,
            snap_id: Optional[int] = None,
            conf: Optional[int] = None,
            dq: Optional[int] = None,
            is_live: bool = False,
            minute: int = 0,
            period: str = "PRE"
        ):
            nonlocal eval_records_count
            outcome = MarketResolutionService.resolve_market_outcome(
                mkt, h_score, a_score, ht_h, ht_a, h_corn, a_corn, h_y, a_y, h_r, a_r
            )
            if outcome is None:
                return

            brier = CalibrationService.calculate_brier_component(prob, outcome)
            ll = CalibrationService.calculate_log_loss_component(prob, outcome)
            abs_err = abs(prob - outcome)

            existing = (
                db.query(ModelEvaluation)
                .filter(
                    ModelEvaluation.fixture_id == fixture_id,
                    ModelEvaluation.market == mkt,
                    ModelEvaluation.model_version == m_ver,
                    ModelEvaluation.is_live == is_live,
                    ModelEvaluation.match_minute == minute
                )
                .first()
            )

            if existing:
                existing.predicted_probability = prob
                existing.actual_outcome = outcome
                existing.brier_component = brier
                existing.log_loss_component = ll
                existing.absolute_error = abs_err
                existing.verified = True
                existing.verified_at = datetime.now(timezone.utc)
            else:
                new_eval = ModelEvaluation(
                    fixture_id=fixture_id,
                    prediction_snapshot_id=snap_id,
                    prediction_type=p_type,
                    market=mkt,
                    model_version=m_ver,
                    competition=comp_name,
                    prediction_timestamp=pred_time,
                    match_minute=minute,
                    period=period,
                    predicted_probability=prob,
                    actual_outcome=outcome,
                    confidence=conf,
                    data_quality=dq,
                    is_live=is_live,
                    verified=True,
                    verified_at=datetime.now(timezone.utc),
                    brier_component=brier,
                    log_loss_component=ll,
                    absolute_error=abs_err
                )
                db.add(new_eval)

            eval_records_count += 1

        # 1. Evaluate Pre-Match Goals Predictions
        pred = db.query(Prediction).filter(Prediction.fixture_id == fixture_id).first()
        if pred:
            pred_time = pred.created_at or fixture.match_date
            c_score = getattr(pred, "confidence_score", None) or 0.70
            conf_val = int(round(c_score * 100)) if c_score <= 1.0 else int(round(c_score))

            o05 = getattr(pred, "over_0_5_probability", None) or getattr(pred, "over_0_5_prob", None)
            o15 = getattr(pred, "over_1_5_probability", None) or getattr(pred, "over_1_5_prob", None)
            o25 = getattr(pred, "over_2_5_probability", None) or getattr(pred, "over_2_5_prob", None)
            o35 = getattr(pred, "over_3_5_probability", None) or getattr(pred, "over_3_5_prob", None)
            btts = getattr(pred, "btts_probability", None) or getattr(pred, "btts_yes_prob", None)
            hw = getattr(pred, "home_win_probability", None) or getattr(pred, "home_win_prob", None)
            dr = getattr(pred, "draw_probability", None) or getattr(pred, "draw_prob", None)
            aw = getattr(pred, "away_win_probability", None) or getattr(pred, "away_win_prob", None)

            if o05 is not None:
                _upsert_eval("goals", "over_0_5_goals", "v2_match_intelligence", pred_time, o05, pred.id, conf_val)
            if o15 is not None:
                _upsert_eval("goals", "over_1_5_goals", "v2_match_intelligence", pred_time, o15, pred.id, conf_val)
            if o25 is not None:
                _upsert_eval("goals", "over_2_5_goals", "v2_match_intelligence", pred_time, o25, pred.id, conf_val)
            if o35 is not None:
                _upsert_eval("goals", "over_3_5_goals", "v2_match_intelligence", pred_time, o35, pred.id, conf_val)
            if btts is not None:
                _upsert_eval("goals", "btts_yes", "v2_match_intelligence", pred_time, btts, pred.id, conf_val)
            if hw is not None:
                _upsert_eval("1x2", "1x2_home", "v2_match_intelligence", pred_time, hw, pred.id, conf_val)
            if dr is not None:
                _upsert_eval("1x2", "1x2_draw", "v2_match_intelligence", pred_time, dr, pred.id, conf_val)
            if aw is not None:
                _upsert_eval("1x2", "1x2_away", "v2_match_intelligence", pred_time, aw, pred.id, conf_val)

        # 2. Evaluate Pre-Match Corners Snapshot
        c_snap = db.query(CornerPredictionSnapshot).filter(CornerPredictionSnapshot.fixture_id == fixture_id).first()
        if c_snap:
            pred_time = c_snap.prediction_timestamp or c_snap.created_at or fixture.match_date
            c_ver = c_snap.model_version or "v1_corners_nb"
            conf_val = getattr(c_snap, "confidence_score", getattr(c_snap, "overall_confidence", 50))

            if c_snap.over_7_5_prob is not None:
                _upsert_eval("corners", "over_7_5_corners", c_ver, pred_time, c_snap.over_7_5_prob, c_snap.id, conf_val)
            if c_snap.over_8_5_prob is not None:
                _upsert_eval("corners", "over_8_5_corners", c_ver, pred_time, c_snap.over_8_5_prob, c_snap.id, conf_val)
            if c_snap.over_9_5_prob is not None:
                _upsert_eval("corners", "over_9_5_corners", c_ver, pred_time, c_snap.over_9_5_prob, c_snap.id, conf_val)
            if c_snap.over_10_5_prob is not None:
                _upsert_eval("corners", "over_10_5_corners", c_ver, pred_time, c_snap.over_10_5_prob, c_snap.id, conf_val)

        # 3. Evaluate Pre-Match Cards Snapshot
        cd_snap = db.query(CardPredictionSnapshot).filter(CardPredictionSnapshot.fixture_id == fixture_id).first()
        if cd_snap:
            pred_time = cd_snap.prediction_timestamp or cd_snap.created_at or fixture.match_date
            cd_ver = cd_snap.model_version or "v1_cards_nb"
            conf_val = getattr(cd_snap, "confidence_score", getattr(cd_snap, "overall_confidence", 50))

            if cd_snap.over_3_5_prob is not None:
                _upsert_eval("cards", "over_3_5_cards", cd_ver, pred_time, cd_snap.over_3_5_prob, cd_snap.id, conf_val)
            if cd_snap.over_4_5_prob is not None:
                _upsert_eval("cards", "over_4_5_cards", cd_ver, pred_time, cd_snap.over_4_5_prob, cd_snap.id, conf_val)
            if cd_snap.over_5_5_prob is not None:
                _upsert_eval("cards", "over_5_5_cards", cd_ver, pred_time, cd_snap.over_5_5_prob, cd_snap.id, conf_val)

            any_red = getattr(cd_snap, "any_red_card_prob", getattr(cd_snap, "any_red_prob", None))
            if any_red is not None:
                _upsert_eval("red_cards", "any_red_card", cd_ver, pred_time, any_red, cd_snap.id, conf_val)

        # 4. Evaluate Live Prediction Snapshots
        live_snaps = db.query(LivePredictionSnapshot).filter(LivePredictionSnapshot.fixture_id == fixture_id).all()
        for ls in live_snaps:
            pred_time = ls.prediction_timestamp or ls.created_at
            l_ver = ls.model_version or "v1_live_intelligence"
            min_val = ls.match_minute
            per_val = ls.period

            if ls.goals_prediction_json:
                try:
                    g_data = json.loads(ls.goals_prediction_json)
                    if "at_least_1_more_goal" in g_data:
                        p_val = g_data["at_least_1_more_goal"].get("probability")
                        if p_val is not None:
                            # 1 more goal means final total > score at snapshot
                            tot_at_snap = ls.home_score + ls.away_score
                            actual_1_more = 1.0 if (h_score + a_score) > tot_at_snap else 0.0
                            brier = CalibrationService.calculate_brier_component(p_val, actual_1_more)
                            ll = CalibrationService.calculate_log_loss_component(p_val, actual_1_more)

                            # Record directly
                            existing = (
                                db.query(ModelEvaluation)
                                .filter(
                                    ModelEvaluation.fixture_id == fixture_id,
                                    ModelEvaluation.market == "live_at_least_1_more_goal",
                                    ModelEvaluation.model_version == l_ver,
                                    ModelEvaluation.is_live == True,
                                    ModelEvaluation.match_minute == min_val
                                )
                                .first()
                            )
                            if not existing:
                                db.add(ModelEvaluation(
                                    fixture_id=fixture_id,
                                    prediction_snapshot_id=ls.id,
                                    prediction_type="live_goals",
                                    market="live_at_least_1_more_goal",
                                    model_version=l_ver,
                                    competition=comp_name,
                                    prediction_timestamp=pred_time,
                                    match_minute=min_val,
                                    period=per_val,
                                    predicted_probability=p_val,
                                    actual_outcome=actual_1_more,
                                    confidence=ls.confidence,
                                    data_quality=ls.data_quality,
                                    is_live=True,
                                    verified=True,
                                    verified_at=datetime.now(timezone.utc),
                                    brier_component=brier,
                                    log_loss_component=ll,
                                    absolute_error=abs(p_val - actual_1_more)
                                ))
                                eval_records_count += 1
                except Exception as ex:
                    logger.debug(f"Error evaluating live goals snapshot {ls.id}: {ex}")

        db.commit()
        return eval_records_count

    @classmethod
    def get_model_performance(
        cls, db: Session, model_version: Optional[str] = None, prediction_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Global model performance summary across verified ModelEvaluation records."""
        q = db.query(ModelEvaluation).filter(ModelEvaluation.verified == True)
        if model_version:
            q = q.filter(ModelEvaluation.model_version == model_version)
        if prediction_type:
            q = q.filter(ModelEvaluation.prediction_type == prediction_type)

        records = q.all()
        pairs = [(r.predicted_probability, r.actual_outcome) for r in records]
        metrics = CalibrationService.calculate_aggregate_metrics(pairs)
        cal = CalibrationService.compute_calibration_curve(pairs)

        return {
            "model_version": model_version or "all_models",
            "prediction_type": prediction_type or "all_types",
            "sample_size": metrics["sample_size"],
            "status": metrics["status"],
            "brier_score": metrics["mean_brier"],
            "log_loss": metrics["mean_log_loss"],
            "mae": metrics["mae"],
            "accuracy": metrics["accuracy"],
            "ece": cal["ece"],
            "mce": cal["mce"],
            "calibration_status": cal["status"]
        }

    @classmethod
    def get_market_leaderboard(cls, db: Session) -> List[Dict[str, Any]]:
        """
        Ranks all prediction markets based on composite performance:
        Score = 100 * (1 - Brier) * Calibration_Factor (lower Brier & lower ECE ranks highest).
        """
        # Group evaluations by market
        markets = (
            db.query(ModelEvaluation.market, ModelEvaluation.prediction_type, ModelEvaluation.model_version)
            .filter(ModelEvaluation.verified == True)
            .distinct()
            .all()
        )

        leaderboard = []
        for mkt, p_type, m_ver in markets:
            recs = (
                db.query(ModelEvaluation)
                .filter(
                    ModelEvaluation.market == mkt,
                    ModelEvaluation.model_version == m_ver,
                    ModelEvaluation.verified == True
                )
                .all()
            )
            n = len(recs)
            pairs = [(r.predicted_probability, r.actual_outcome) for r in recs]
            agg = CalibrationService.calculate_aggregate_metrics(pairs)
            cal = CalibrationService.compute_calibration_curve(pairs)

            brier = agg["mean_brier"] or 0.25
            ece = cal["ece"] or 0.15

            # Composite ranking score: higher is better
            # Perfect model: Brier=0, ECE=0 -> Score = 100. Random (Brier=0.25, ECE=0.10) -> Score ~ 67.
            comp_score = max(0.0, (1.0 - brier) * (1.0 - min(0.5, ece)) * 100.0)

            if n < MIN_PRODUCTION_VALIDATION_SAMPLE:
                status = "INSUFFICIENT_DATA"
            elif ece <= 0.07 and brier <= 0.20:
                status = "VALIDATED"
            elif ece <= 0.12:
                status = "ACCEPTABLE"
            else:
                status = "CALIBRATION_WARNING"

            leaderboard.append({
                "market": mkt,
                "prediction_type": p_type,
                "model_version": m_ver,
                "sample_size": n,
                "brier_score": brier,
                "log_loss": agg["mean_log_loss"],
                "ece": ece,
                "mce": cal["mce"],
                "accuracy": agg["accuracy"],
                "composite_score": round(comp_score, 1),
                "calibration_status": cal["status"],
                "validation_status": status
            })

        # Sort primarily by composite score, then sample size
        leaderboard.sort(key=lambda x: (x["composite_score"], x["sample_size"]), reverse=True)
        return leaderboard

    @classmethod
    def get_calibration_dashboard(cls, db: Session, market: Optional[str] = None) -> Dict[str, Any]:
        """Returns 10-decile calibration diagram data and ECE/MCE."""
        q = db.query(ModelEvaluation).filter(ModelEvaluation.verified == True)
        if market:
            q = q.filter(ModelEvaluation.market == market)

        recs = q.all()
        pairs = [(r.predicted_probability, r.actual_outcome) for r in recs]
        return CalibrationService.compute_calibration_curve(pairs)

    @classmethod
    def get_model_drift_analysis(
        cls, db: Session, window_size: int = 100, baseline_size: int = 300
    ) -> Dict[str, Any]:
        """
        Detects predictive model degradation by comparing the recent window of verified predictions
        against the historical baseline.
        """
        recs = (
            db.query(ModelEvaluation)
            .filter(ModelEvaluation.verified == True)
            .order_by(ModelEvaluation.id.desc())
            .all()
        )

        n = len(recs)
        if n < window_size:
            return {
                "status": "INSUFFICIENT_DATA",
                "message": f"Only {n} verified evaluations recorded. Minimum {window_size} required for drift detection.",
                "total_evaluations": n,
                "window_size": window_size
            }

        recent_pairs = [(r.predicted_probability, r.actual_outcome) for r in recs[:window_size]]
        recent_agg = CalibrationService.calculate_aggregate_metrics(recent_pairs)
        recent_cal = CalibrationService.compute_calibration_curve(recent_pairs)

        hist_pairs = [(r.predicted_probability, r.actual_outcome) for r in recs[window_size:window_size + baseline_size]]
        if not hist_pairs:
            hist_pairs = recent_pairs

        hist_agg = CalibrationService.calculate_aggregate_metrics(hist_pairs)
        hist_cal = CalibrationService.compute_calibration_curve(hist_pairs)

        r_brier = recent_agg["mean_brier"] or 0.20
        h_brier = hist_agg["mean_brier"] or 0.20
        brier_diff_pct = round(((r_brier - h_brier) / max(0.01, h_brier)) * 100.0, 1)

        r_ece = recent_cal["ece"] or 0.05
        h_ece = hist_cal["ece"] or 0.05
        ece_diff_pct = round(((r_ece - h_ece) / max(0.01, h_ece)) * 100.0, 1)

        if brier_diff_pct > 25.0 or ece_diff_pct > 50.0:
            drift_status = "MODEL_DRIFT_WARNING"
            label = "Performance Degradation Detected"
        elif brier_diff_pct > 10.0:
            drift_status = "WARNING"
            label = "Minor Performance Variance"
        else:
            drift_status = "STABLE"
            label = "Model Performance Stable"

        return {
            "status": drift_status,
            "label": label,
            "total_evaluations": n,
            "recent_window_size": len(recent_pairs),
            "historical_baseline_size": len(hist_pairs),
            "recent_brier": r_brier,
            "historical_brier": h_brier,
            "brier_change_pct": brier_diff_pct,
            "recent_ece": r_ece,
            "historical_ece": h_ece,
            "ece_change_pct": ece_diff_pct
        }

    @classmethod
    def get_league_performance(
        cls, db: Session, competition: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Evaluates prediction performance segmented by competition."""
        q = (
            db.query(ModelEvaluation.competition)
            .filter(ModelEvaluation.verified == True)
            .distinct()
        )
        if competition:
            q = q.filter(ModelEvaluation.competition == competition)

        leagues = [r[0] for r in q.all() if r[0]]
        results = []

        for comp in leagues:
            recs = (
                db.query(ModelEvaluation)
                .filter(ModelEvaluation.competition == comp, ModelEvaluation.verified == True)
                .all()
            )
            n = len(recs)
            pairs = [(r.predicted_probability, r.actual_outcome) for r in recs]
            agg = CalibrationService.calculate_aggregate_metrics(pairs)
            cal = CalibrationService.compute_calibration_curve(pairs)

            if n < MIN_PRODUCTION_VALIDATION_SAMPLE:
                status = "INSUFFICIENT_DATA"
            elif (cal["ece"] or 0.10) <= 0.07:
                status = "GOOD"
            elif (cal["ece"] or 0.10) <= 0.12:
                status = "ACCEPTABLE"
            else:
                status = "POOR"

            results.append({
                "competition": comp,
                "sample_size": n,
                "brier_score": agg["mean_brier"],
                "log_loss": agg["mean_log_loss"],
                "ece": cal["ece"],
                "accuracy": agg["accuracy"],
                "status": status
            })

        results.sort(key=lambda x: x["sample_size"], reverse=True)
        return results

    @classmethod
    def get_live_minute_performance(cls, db: Session) -> Dict[str, Any]:
        """
        Evaluates dynamic in-play prediction performance across 6 match minute intervals:
        0-15, 16-30, 31-45+, 46-60, 61-75, 76-90+.
        """
        minute_ranges = [
            ("0-15'", 0, 15),
            ("16-30'", 16, 30),
            ("31-45'+", 31, 45),
            ("46-60'", 46, 60),
            ("61-75'", 61, 75),
            ("76-90'+", 76, 120)
        ]

        buckets_out = []
        tot_live = 0

        for label, min_start, min_end in minute_ranges:
            recs = (
                db.query(ModelEvaluation)
                .filter(
                    ModelEvaluation.is_live == True,
                    ModelEvaluation.verified == True,
                    ModelEvaluation.match_minute >= min_start,
                    ModelEvaluation.match_minute <= min_end
                )
                .all()
            )
            n = len(recs)
            tot_live += n
            pairs = [(r.predicted_probability, r.actual_outcome) for r in recs]
            agg = CalibrationService.calculate_aggregate_metrics(pairs)
            cal = CalibrationService.compute_calibration_curve(pairs)

            buckets_out.append({
                "minute_bucket": label,
                "sample_size": n,
                "brier_score": agg["mean_brier"],
                "log_loss": agg["mean_log_loss"],
                "ece": cal["ece"],
                "accuracy": agg["accuracy"],
                "status": "VALIDATED" if n >= 50 else ("VALIDATING" if n >= 15 else "INSUFFICIENT_DATA")
            })

        return {
            "total_live_evaluations": tot_live,
            "status": "VALIDATED" if tot_live >= MIN_PRODUCTION_VALIDATION_SAMPLE else "INSUFFICIENT_DATA",
            "minute_buckets": buckets_out
        }

    @classmethod
    def get_models_readiness_status(cls, db: Session) -> Dict[str, Any]:
        """Returns concise production readiness indicators for each prediction engine."""
        def _get_status_for_type(p_type: str) -> Dict[str, Any]:
            count = (
                db.query(ModelEvaluation)
                .filter(ModelEvaluation.prediction_type == p_type, ModelEvaluation.verified == True)
                .count()
            )
            status = "VALIDATED" if count >= MIN_PRODUCTION_VALIDATION_SAMPLE else ("VALIDATING" if count >= 20 else "INSUFFICIENT_DATA")
            return {
                "status": status,
                "sample_size": count,
                "required_sample": MIN_PRODUCTION_VALIDATION_SAMPLE
            }

        return {
            "goals_model": _get_status_for_type("goals"),
            "corners_model": _get_status_for_type("corners"),
            "cards_model": _get_status_for_type("cards"),
            "live_model": _get_status_for_type("live_goals"),
            "evaluation_engine": "Phase 5 Model Intelligence Core",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
