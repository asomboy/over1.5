import os
import sys
import json
import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, Prediction, MatchStatistics, ModelEvaluation,
        PredictionDecisionSnapshot, DataQualitySnapshot, ProviderHealth
    )
    from services.calibration_service import CalibrationService, MIN_PRODUCTION_VALIDATION_SAMPLE
    from services.unified_match_intelligence_service import UnifiedMatchIntelligenceService
    from services.prediction_explanation_service import PredictionExplanationService
    from services.production_validation_service import ProductionValidationService
    from services.model_evaluation_service import ModelEvaluationService
    from services.provider_health_service import ProviderHealthService
    from services.data_reconciliation_service import DataReconciliationService
except ImportError:
    from ..models import (
        Fixture, Prediction, MatchStatistics, ModelEvaluation,
        PredictionDecisionSnapshot, DataQualitySnapshot, ProviderHealth
    )
    from .calibration_service import CalibrationService, MIN_PRODUCTION_VALIDATION_SAMPLE
    from .unified_match_intelligence_service import UnifiedMatchIntelligenceService
    from .prediction_explanation_service import PredictionExplanationService
    from .production_validation_service import ProductionValidationService
    from .model_evaluation_service import ModelEvaluationService
    from .provider_health_service import ProviderHealthService
    from .data_reconciliation_service import DataReconciliationService

logger = logging.getLogger(__name__)

DECISION_ENGINE_VERSION = "v1_decision_engine"


class DecisionIntelligenceService:
    """
    Central Phase 12 Decision Intelligence, Explainability & Governance Engine.
    Evaluates multi-market predictions against empirical reliability, calibration error,
    drift state, data quality, and cross-market consistency to produce authoritative signals.
    """

    @classmethod
    def get_fixture_decisions(cls, db: Session, fixture_id: int) -> List[Dict[str, Any]]:
        """
        Normalizes and evaluates all candidate predictions across all markets for a fixture.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return []

        # 1. Retrieve Unified Match Intelligence
        intel = UnifiedMatchIntelligenceService.get_unified_match_intelligence(db, fixture_id)
        if "error" in intel:
            return []

        markets = intel.get("markets", {})
        consistency = intel.get("cross_market_consistency", {"consistency_score": 1.0, "contradiction_flags": []})
        data_quality = 0.85
        if intel.get("data_provenance_summary", {}).get("conflict_count", 0) > 0:
            data_quality -= 0.20
        data_quality = max(0.40, min(0.95, data_quality))

        # 2. Check Provider Health & Drift
        try:
            providers_status = ProviderHealthService.get_providers_status(db)
            espn_status = next((p["status"] for p in providers_status if p.get("provider") == "espn"), "HEALTHY")
            provider_status = espn_status
        except Exception:
            provider_status = "HEALTHY"

        try:
            drift_check = ModelEvaluationService.get_model_drift_analysis(db)
            drift_status = "DRIFT_DETECTED" if drift_check.get("status") == "DRIFT_DETECTED" else "STABLE"
        except Exception:
            drift_status = "STABLE"

        candidates: List[Dict[str, Any]] = []

        # Helper to extract market reliability & calibration metrics
        def _get_market_eval(market_key: str) -> Dict[str, Any]:
            evals = (
                db.query(ModelEvaluation)
                .filter(ModelEvaluation.market.contains(market_key))
                .order_by(ModelEvaluation.prediction_timestamp.desc())
                .limit(300)
                .all()
            )
            n = len(evals)
            if n == 0:
                return {
                    "sample_size": 0,
                    "readiness_status": "INSUFFICIENT_DATA",
                    "brier_score": None,
                    "log_loss": None,
                    "ece": None,
                    "mce": None
                }
            pairs = [(e.predicted_probability, e.actual_outcome) for e in evals]
            cal = CalibrationService.compute_calibration_curve(pairs)
            agg = CalibrationService.calculate_aggregate_metrics(pairs)
            readiness = "VALIDATED" if (n >= 300 and cal.get("ece", 1.0) <= 0.07) else ("VALIDATING" if n >= 100 else "INSUFFICIENT_DATA")

            return {
                "sample_size": n,
                "readiness_status": readiness,
                "brier_score": agg.get("brier_score"),
                "log_loss": agg.get("log_loss"),
                "ece": cal.get("ece"),
                "mce": cal.get("mce")
            }

        # ---------------------------------------------------------------------
        # A. GOALS CANDIDATES
        # ---------------------------------------------------------------------
        goals_data = markets.get("goals", {})
        g_probs = goals_data.get("probabilities", {})
        if not g_probs or g_probs.get("over_1_5") is None:
            # Fallback to database Prediction directly to prevent static defaults
            pred_row = db.query(Prediction).filter(Prediction.fixture_id == fixture_id).first()
            if pred_row:
                g_probs = {
                    "over_0_5": pred_row.over_0_5_probability,
                    "over_1_5": pred_row.over_1_5_probability,
                    "over_2_5": pred_row.over_2_5_probability,
                    "over_3_5": pred_row.over_3_5_probability,
                    "btts": pred_row.btts_probability,
                    "home_win": pred_row.home_win_probability,
                    "away_win": pred_row.away_win_probability
                }

        g_eval = _get_market_eval("over_1_5")

        h_win_p = g_probs.get("home_win") or goals_data.get("home_win_probability")
        a_win_p = g_probs.get("away_win") or goals_data.get("away_win_probability")
        btts_p = g_probs.get("btts")

        goal_lines = [
            ("Over 0.5 Goals", "Over 0.5", g_probs.get("over_0_5"), "over_0_5"),
            ("Over 1.5 Goals", "Over 1.5", g_probs.get("over_1_5"), "over_1_5"),
            ("Over 2.5 Goals", "Over 2.5", g_probs.get("over_2_5"), "over_2_5"),
            ("Over 3.5 Goals", "Over 3.5", g_probs.get("over_3_5"), "over_3_5"),
            ("Both Teams to Score", "Yes", btts_p, "btts"),
            ("Both Teams to Score", "No", (1.0 - btts_p) if btts_p is not None else None, "btts_no"),
            ("Match Result (1X2)", "Home Win (1)", h_win_p, "1x2_home"),
            ("Match Result (1X2)", "Away Win (2)", a_win_p, "1x2_away")
        ]

        for m_name, sel, p, ev_key in goal_lines:
            if p is not None and p > 0.0:
                ev = g_eval if "1_5" in ev_key else _get_market_eval(ev_key)
                cand = cls._build_normalized_decision(
                    fixture_id=fixture_id,
                    market=m_name,
                    selection=sel,
                    probability=round(p, 4),
                    model_version=goals_data.get("model_version", "v2_match_intelligence"),
                    model_data=goals_data,
                    eval_metrics=ev,
                    data_quality=data_quality,
                    provider_status=provider_status,
                    drift_status=drift_status,
                    consistency_data=consistency,
                    is_live=intel.get("is_live", False),
                    match_minute=intel.get("match_minute", 0)
                )
                candidates.append(cand)

        # ---------------------------------------------------------------------
        # B. CORNERS CANDIDATES
        # ---------------------------------------------------------------------
        corners_data = markets.get("corners", {})
        c_probs = corners_data.get("probabilities", {})
        c_eval = _get_market_eval("corners")

        for line in ["7_5", "8_5", "9_5", "10_5", "11_5"]:
            k_over = f"over_{line}"
            p = c_probs.get(k_over)
            if p is not None and p > 0.0:
                cand = cls._build_normalized_decision(
                    fixture_id=fixture_id,
                    market=f"Over {line.replace('_', '.')} Corners",
                    selection="Over",
                    probability=round(p, 4),
                    model_version=corners_data.get("model_version", "v1_corners_nb"),
                    model_data=corners_data,
                    eval_metrics=c_eval,
                    data_quality=data_quality,
                    provider_status=provider_status,
                    drift_status=drift_status,
                    consistency_data=consistency,
                    is_live=intel.get("is_live", False),
                    match_minute=intel.get("match_minute", 0)
                )
                candidates.append(cand)

        # ---------------------------------------------------------------------
        # C. CARDS CANDIDATES
        # ---------------------------------------------------------------------
        cards_data = markets.get("cards", {})
        d_probs = cards_data.get("probabilities", {})
        d_eval = _get_market_eval("cards")

        for line in ["2_5", "3_5", "4_5", "5_5"]:
            k_over = f"over_{line}"
            p = d_probs.get(k_over)
            if p is not None and p > 0.0:
                cand = cls._build_normalized_decision(
                    fixture_id=fixture_id,
                    market=f"Over {line.replace('_', '.')} Cards",
                    selection="Over",
                    probability=round(p, 4),
                    model_version=cards_data.get("model_version", "v1_cards_nb"),
                    model_data=cards_data,
                    eval_metrics=d_eval,
                    data_quality=data_quality,
                    provider_status=provider_status,
                    drift_status=drift_status,
                    consistency_data=consistency,
                    is_live=intel.get("is_live", False),
                    match_minute=intel.get("match_minute", 0)
                )
                candidates.append(cand)

        # ---------------------------------------------------------------------
        # D. SHOTS & SOT CANDIDATES (Phase 10)
        # ---------------------------------------------------------------------
        shots_data = markets.get("shots", {})
        sot_data = markets.get("shots_on_target", {})
        s_eval = _get_market_eval("shots")
        sot_eval = _get_market_eval("sot")

        for line in ["17_5", "19_5", "21_5", "23_5"]:
            p = shots_data.get("probabilities", {}).get(f"over_{line}")
            if p is not None and p > 0.0:
                cand = cls._build_normalized_decision(
                    fixture_id=fixture_id,
                    market=f"Over {line.replace('_', '.')} Total Shots",
                    selection="Over",
                    probability=round(p, 4),
                    model_version="v1_shots_nb",
                    model_data=shots_data,
                    eval_metrics=s_eval,
                    data_quality=data_quality,
                    provider_status=provider_status,
                    drift_status=drift_status,
                    consistency_data=consistency,
                    is_live=intel.get("is_live", False),
                    match_minute=intel.get("match_minute", 0)
                )
                candidates.append(cand)

        for line in ["3_5", "5_5", "7_5"]:
            p = sot_data.get("probabilities", {}).get(f"over_{line}")
            if p is not None and p > 0.0:
                cand = cls._build_normalized_decision(
                    fixture_id=fixture_id,
                    market=f"Over {line.replace('_', '.')} Total SoT",
                    selection="Over",
                    probability=round(p, 4),
                    model_version="v1_shots_nb",
                    model_data=sot_data,
                    eval_metrics=sot_eval,
                    data_quality=data_quality,
                    provider_status=provider_status,
                    drift_status=drift_status,
                    consistency_data=consistency,
                    is_live=intel.get("is_live", False),
                    match_minute=intel.get("match_minute", 0)
                )
                candidates.append(cand)

        # ---------------------------------------------------------------------
        # E. MATCH STATISTICS CANDIDATES (Phase 11)
        # ---------------------------------------------------------------------
        fouls_data = markets.get("fouls", {})
        offsides_data = markets.get("offsides", {})
        saves_data = markets.get("saves", {})

        for line in ["21_5", "23_5", "25_5"]:
            p = fouls_data.get("probabilities", {}).get(f"over_{line}")
            if p is not None and p > 0.0:
                cand = cls._build_normalized_decision(
                    fixture_id=fixture_id,
                    market=f"Over {line.replace('_', '.')} Total Fouls",
                    selection="Over",
                    probability=round(p, 4),
                    model_version="v1_match_stats_nb",
                    model_data=fouls_data,
                    eval_metrics=_get_market_eval("fouls"),
                    data_quality=data_quality,
                    provider_status=provider_status,
                    drift_status=drift_status,
                    consistency_data=consistency,
                    is_live=intel.get("is_live", False),
                    match_minute=intel.get("match_minute", 0)
                )
                candidates.append(cand)

        for line in ["2_5", "3_5"]:
            p = offsides_data.get("probabilities", {}).get(f"over_{line}")
            if p is not None and p > 0.0:
                cand = cls._build_normalized_decision(
                    fixture_id=fixture_id,
                    market=f"Over {line.replace('_', '.')} Offsides",
                    selection="Over",
                    probability=round(p, 4),
                    model_version="v1_match_stats_nb",
                    model_data=offsides_data,
                    eval_metrics=_get_market_eval("offsides"),
                    data_quality=data_quality,
                    provider_status=provider_status,
                    drift_status=drift_status,
                    consistency_data=consistency,
                    is_live=intel.get("is_live", False),
                    match_minute=intel.get("match_minute", 0)
                )
                candidates.append(cand)

        for line in ["3_5", "4_5"]:
            p = saves_data.get("probabilities", {}).get(f"over_{line}")
            if p is not None and p > 0.0:
                cand = cls._build_normalized_decision(
                    fixture_id=fixture_id,
                    market=f"Over {line.replace('_', '.')} Saves",
                    selection="Over",
                    probability=round(p, 4),
                    model_version="v1_match_stats_nb",
                    model_data=saves_data,
                    eval_metrics=_get_market_eval("saves"),
                    data_quality=data_quality,
                    provider_status=provider_status,
                    drift_status=drift_status,
                    consistency_data=consistency,
                    is_live=intel.get("is_live", False),
                    match_minute=intel.get("match_minute", 0)
                )
                candidates.append(cand)

        return candidates

    @classmethod
    def _build_normalized_decision(
        cls,
        fixture_id: int,
        market: str,
        selection: str,
        probability: float,
        model_version: str,
        model_data: Dict[str, Any],
        eval_metrics: Dict[str, Any],
        data_quality: float,
        provider_status: str,
        drift_status: str,
        consistency_data: Dict[str, Any],
        is_live: bool,
        match_minute: int
    ) -> Dict[str, Any]:
        """
        Constructs a normalized decision object calculating conservative Decision Score,
        Decision Confidence, Risk Tier, Signal Status, and Explainability Reason Codes.
        """
        sample_size = eval_metrics.get("sample_size", 0)
        readiness = eval_metrics.get("readiness_status", "INSUFFICIENT_DATA")
        ece = eval_metrics.get("ece", 0.10) if eval_metrics.get("ece") is not None else 0.10
        consistency_score = consistency_data.get("consistency_score", 1.0)

        # 1. Conservative Component Factors (all bounded in [0.0, 1.0])
        # Calibration factor: penalizes high ECE
        cal_factor = max(0.40, min(1.0, 1.0 - (ece * 3.0)))

        # Reliability factor: scales with verified sample size
        rel_factor = 1.0 if sample_size >= 300 else (0.80 if sample_size >= 100 else 0.50)

        # Readiness factor
        readiness_factor = 1.0 if readiness == "VALIDATED" else (0.85 if readiness == "VALIDATING" else 0.55)

        # Drift factor
        drift_factor = 0.70 if drift_status == "DRIFT_DETECTED" else 1.0

        # Provider factor
        provider_factor = 0.60 if provider_status in ["DEGRADED", "CIRCUIT_BREAKER_OPEN"] else 1.0

        # 2. Decision Score Formulation
        # DecisionScore = Probability * Cal * Rel * DQ * Consistency * Drift * Readiness
        decision_score = round(
            probability * cal_factor * rel_factor * data_quality * consistency_score * drift_factor * readiness_factor,
            4
        )
        decision_score = max(0.01, min(0.99, decision_score))

        # 3. Decision Confidence (bounded separate metric incorporating model clarity)
        prob_clarity = round(abs(probability - 0.5) * 2.0, 3)
        decision_confidence = round(
            0.35 * rel_factor + 0.20 * data_quality + 0.15 * consistency_score + 0.15 * cal_factor + 0.15 * prob_clarity,
            2
        )
        decision_confidence = max(0.15, min(0.95, decision_confidence))

        # 4. Signal Status Gating
        if provider_status in ["DEGRADED", "CIRCUIT_BREAKER_OPEN"] or readiness == "DEGRADED":
            signal_status = "DEGRADED"
        elif readiness == "VALIDATED" and sample_size >= 300 and decision_score >= 0.50 and consistency_score >= 0.70:
            signal_status = "PRODUCTION_SIGNAL"
        elif (readiness in ["VALIDATING", "VALIDATED"] or sample_size >= 100) and probability >= 0.64:
            signal_status = "SHADOW_SIGNAL"
        elif probability >= 0.70 and decision_score >= 0.35:
            signal_status = "SHADOW_SIGNAL"
        elif sample_size < 100:
            signal_status = "INSUFFICIENT_DATA"
        elif probability < 0.60:
            signal_status = "NO_SIGNAL"
        else:
            signal_status = "SHADOW_SIGNAL"

        # 5. Risk Tier Classification (Independent from raw probability)
        if signal_status == "PRODUCTION_SIGNAL" and probability >= 0.76 and decision_confidence >= 0.70 and ece <= 0.05:
            risk_tier = "LOW"
        elif signal_status in ["PRODUCTION_SIGNAL", "SHADOW_SIGNAL"] and probability >= 0.68 and decision_confidence >= 0.55:
            risk_tier = "MEDIUM"
        elif signal_status in ["DEGRADED", "INSUFFICIENT_DATA", "SHADOW_SIGNAL"]:
            risk_tier = "HIGH"
        else:
            risk_tier = "NO_SIGNAL"

        # 6. Machine-Readable Explainability
        explanation = PredictionExplanationService.generate_explanation(
            market=market,
            selection=selection,
            probability=probability,
            model_data=model_data,
            eval_metrics=eval_metrics,
            data_quality=data_quality,
            provider_status=provider_status,
            drift_status=drift_status,
            consistency_data=consistency_data,
            is_live=is_live
        )

        return {
            "fixture_id": fixture_id,
            "market": market,
            "selection": selection,
            "probability": probability,
            "confidence": decision_confidence,
            "decision_score": decision_score,
            "model_version": model_version,
            "sample_size": sample_size,
            "historical_brier": eval_metrics.get("brier_score"),
            "historical_log_loss": eval_metrics.get("log_loss"),
            "historical_ece": eval_metrics.get("ece"),
            "historical_mce": eval_metrics.get("mce"),
            "data_quality": data_quality,
            "calibration_status": "CALIBRATED" if (ece <= 0.07 and sample_size >= 100) else "UNCALIBRATED",
            "model_readiness": readiness,
            "drift_status": drift_status,
            "provider_status": provider_status,
            "cross_market_consistency": consistency_score,
            "signal_status": signal_status,
            "risk_tier": risk_tier,
            "explanation": explanation,
            "match_minute": match_minute,
            "is_live": is_live
        }

    @classmethod
    def get_top_signals(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """
        Returns up to 3 highest-ranking qualifying signals that pass status gating.
        Never manufactures or forces a recommendation.
        """
        decisions = cls.get_fixture_decisions(db, fixture_id)
        if not decisions:
            return {"status": "NO_SIGNAL", "signals_count": 0, "signals": []}

        # Filter qualifying signals (PRODUCTION_SIGNAL or high-confidence SHADOW_SIGNAL)
        qualifying = [
            d for d in decisions
            if d["signal_status"] in ["PRODUCTION_SIGNAL", "SHADOW_SIGNAL"] and d["probability"] >= 0.65
        ]

        # Rank by decision_score (conservative governance metric) descending
        qualifying.sort(key=lambda x: x["decision_score"], reverse=True)
        top_3 = qualifying[:3]

        if not top_3:
            return {"status": "NO_SIGNAL", "signals_count": 0, "signals": []}

        overall_status = "PRODUCTION" if any(s["signal_status"] == "PRODUCTION_SIGNAL" for s in top_3) else "SHADOW"

        return {
            "status": overall_status,
            "signals_count": len(top_3),
            "signals": top_3
        }

    @classmethod
    def get_match_decision_summary(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """
        Constructs complete authoritative match decision object consumed by MatchDetailModal.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {"error": f"Fixture {fixture_id} not found"}

        decisions = cls.get_fixture_decisions(db, fixture_id)
        top_signals_obj = cls.get_top_signals(db, fixture_id)

        # Aggregated confidence across top decisions
        confidences = [d["confidence"] for d in decisions if d.get("confidence")]
        overall_conf = round(sum(confidences) / len(confidences), 2) if confidences else 0.50

        # Provenance and conflict summary
        dq_records = DataReconciliationService.get_fixture_provenance(db, fixture_id)
        from models import DataConflict
        conflicts = db.query(DataConflict).filter(DataConflict.fixture_id == fixture_id, DataConflict.status == "CONFLICT").all()

        return {
            "fixture_id": fixture.id,
            "home_team": fixture.home_team.name if fixture.home_team else "Home",
            "away_team": fixture.away_team.name if fixture.away_team else "Away",
            "competition": fixture.league.name if fixture.league else "League",
            "kickoff": fixture.match_date.isoformat() if fixture.match_date else None,
            "match_status": fixture.status,
            "is_live": (fixture.status == "LIVE"),
            "match_minute": fixture.match_minute if hasattr(fixture, "match_minute") and fixture.match_minute else 0,
            "overall_confidence": overall_conf,
            "production_status": top_signals_obj.get("status", "NO_SIGNAL"),
            "top_signals": top_signals_obj.get("signals", []),
            "all_candidate_signals": decisions,
            "market_summary": {
                "total_markets_evaluated": len(decisions),
                "production_signals_count": sum(1 for d in decisions if d["signal_status"] == "PRODUCTION_SIGNAL"),
                "shadow_signals_count": sum(1 for d in decisions if d["signal_status"] == "SHADOW_SIGNAL"),
                "insufficient_data_count": sum(1 for d in decisions if d["signal_status"] == "INSUFFICIENT_DATA")
            },
            "data_quality": {
                "provenance_records_count": len(dq_records),
                "has_conflicts": len(conflicts) > 0
            },
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

    @classmethod
    def save_decision_snapshots(cls, db: Session, fixture_id: int) -> List[PredictionDecisionSnapshot]:
        """
        Persists immutable PredictionDecisionSnapshot records with idempotency protection.
        """
        decisions = cls.get_fixture_decisions(db, fixture_id)
        saved_snapshots = []

        for d in decisions:
            # Check idempotency within 5 minutes for pre-match
            recent = (
                db.query(PredictionDecisionSnapshot)
                .filter(
                    PredictionDecisionSnapshot.fixture_id == fixture_id,
                    PredictionDecisionSnapshot.market == d["market"],
                    PredictionDecisionSnapshot.selection == d["selection"],
                    PredictionDecisionSnapshot.is_live == d.get("is_live", False),
                    PredictionDecisionSnapshot.created_at >= datetime.now(timezone.utc) - timedelta(minutes=5)
                )
                .first()
            )
            if recent:
                continue

            snap = PredictionDecisionSnapshot(
                fixture_id=fixture_id,
                market=d["market"],
                selection=d["selection"],
                model_version=d.get("model_version", DECISION_ENGINE_VERSION),
                probability=d["probability"],
                confidence=d["confidence"],
                decision_score=d["decision_score"],
                risk_tier=d["risk_tier"],
                signal_status=d["signal_status"],
                sample_size=d.get("sample_size", 0),
                brier_score=d.get("historical_brier"),
                log_loss=d.get("historical_log_loss"),
                ece=d.get("historical_ece"),
                mce=d.get("historical_mce"),
                data_quality=d.get("data_quality", 0.50),
                consistency_score=d.get("cross_market_consistency", 1.0),
                drift_status=d.get("drift_status", "STABLE"),
                readiness_status=d.get("model_readiness", "INSUFFICIENT_DATA"),
                explanation_json=json.dumps(d.get("explanation", {})),
                diagnostics_json=json.dumps({"match_minute": d.get("match_minute", 0)}),
                match_minute=d.get("match_minute", 0),
                is_live=d.get("is_live", False),
                prediction_timestamp=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc)
            )
            db.add(snap)
            saved_snapshots.append(snap)

        db.commit()
        return saved_snapshots

    @classmethod
    def get_decision_readiness(cls, db: Session) -> Dict[str, Any]:
        """Returns empirical sample-size validation status across all decision engine markets."""
        evals = db.query(ModelEvaluation).all()
        evals_count = len(evals)

        # Evaluate distinct markets
        markets = {}
        for e in evals:
            mk = e.market or "unknown"
            markets.setdefault(mk, []).append((e.predicted_probability, e.actual_outcome))

        validated_markets_count = 0
        for mk, pairs in markets.items():
            if len(pairs) >= 300:
                cal = CalibrationService.compute_calibration_curve(pairs)
                if cal.get("ece", 1.0) <= 0.07:
                    validated_markets_count += 1

        overall_status = "VALIDATED" if (evals_count >= 300 and validated_markets_count >= 1) else ("VALIDATING" if evals_count >= 100 else "INSUFFICIENT_DATA")

        return {
            "decision_engine_version": DECISION_ENGINE_VERSION,
            "total_evaluations": evals_count,
            "validated_markets_count": validated_markets_count,
            "overall_status": overall_status,
            "required_sample": MIN_PRODUCTION_VALIDATION_SAMPLE,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @classmethod
    def get_decision_status(cls, db: Session) -> Dict[str, Any]:
        """Returns real-time operating metrics for the decision intelligence service."""
        snapshots_count = db.query(PredictionDecisionSnapshot).count()
        production_signals = db.query(PredictionDecisionSnapshot).filter(PredictionDecisionSnapshot.signal_status == "PRODUCTION_SIGNAL").count()
        shadow_signals = db.query(PredictionDecisionSnapshot).filter(PredictionDecisionSnapshot.signal_status == "SHADOW_SIGNAL").count()

        return {
            "status": "OPERATIONAL",
            "decision_engine_version": DECISION_ENGINE_VERSION,
            "total_snapshots_recorded": snapshots_count,
            "production_signals_recorded": production_signals,
            "shadow_signals_recorded": shadow_signals,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
