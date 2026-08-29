import os
import sys
import json
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import (
        Fixture, HistoricalResult, MatchStatistics, League, Team,
        ShotPredictionSnapshot, ModelEvaluation
    )
    from services.calibration_service import CalibrationService, MIN_PRODUCTION_VALIDATION_SAMPLE
    from services.data_reconciliation_service import DataReconciliationService
except ImportError:
    from ..models import (
        Fixture, HistoricalResult, MatchStatistics, League, Team,
        ShotPredictionSnapshot, ModelEvaluation
    )
    from .calibration_service import CalibrationService, MIN_PRODUCTION_VALIDATION_SAMPLE
    from .data_reconciliation_service import DataReconciliationService

logger = logging.getLogger(__name__)

SHOTS_MODEL_VERSION = "v1_shots_nb"
LIVE_SHOTS_MODEL_VERSION = "v1_live_shots"

# Baseline League Priors (Per Team)
DEFAULT_LEAGUE_SHOTS_HOME = 13.5
DEFAULT_LEAGUE_SHOTS_AWAY = 11.2
DEFAULT_LEAGUE_SOT_HOME = 4.8
DEFAULT_LEAGUE_SOT_AWAY = 3.9
DEFAULT_SHOTS_DISPERSION_R = 14.0 # r parameter for Negative Binomial shot count variance
DEFAULT_SOT_DISPERSION_R = 6.0    # r parameter for SoT count variance

MAX_SHOT_GRID = 50
MAX_SOT_GRID = 25


def _negative_binomial_pmf(k: int, mu: float, r: float) -> float:
    """Computes Negative Binomial PMF: P(K=k) given mean mu and dispersion r."""
    if k < 0 or mu <= 0.0 or r <= 0.0:
        return 1.0 if k == 0 else 0.0
    p = r / (r + mu)
    try:
        log_comb = math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r)
        log_prob = log_comb + (r * math.log(p)) + (k * math.log(1.0 - p))
        return math.exp(log_prob)
    except (ValueError, OverflowError):
        return 0.0


class ShotsFeatureService:
    """
    Extracts team shot generation and opponent shot suppression features
    with strict temporal integrity (match_date < kickoff) and Bayesian shrinkage.
    """

    @classmethod
    def get_team_shots_features(
        cls, db: Session, team_id: int, before_date: datetime, is_home: bool
    ) -> Dict[str, Any]:
        """
        Calculates rolling and overall shot generation and suppression averages.
        """
        # Query past completed match statistics
        matches = (
            db.query(MatchStatistics, Fixture)
            .join(Fixture, MatchStatistics.fixture_id == Fixture.id)
            .filter(
                or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                Fixture.match_date < before_date,
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"])
            )
            .order_by(Fixture.match_date.desc())
            .limit(15)
            .all()
        )

        n = len(matches)
        if n == 0:
            return {
                "sample_size": 0,
                "coverage_status": "INSUFFICIENT_DATA",
                "avg_shots_for": DEFAULT_LEAGUE_SHOTS_HOME if is_home else DEFAULT_LEAGUE_SHOTS_AWAY,
                "avg_shots_against": DEFAULT_LEAGUE_SHOTS_AWAY if is_home else DEFAULT_LEAGUE_SHOTS_HOME,
                "avg_sot_for": DEFAULT_LEAGUE_SOT_HOME if is_home else DEFAULT_LEAGUE_SOT_AWAY,
                "avg_sot_against": DEFAULT_LEAGUE_SOT_AWAY if is_home else DEFAULT_LEAGUE_SOT_HOME,
                "sot_conversion_rate": 0.35
            }

        shots_for = []
        shots_against = []
        sot_for = []
        sot_against = []

        for stats, f in matches:
            if f.home_team_id == team_id:
                if stats.home_shots is not None: shots_for.append(stats.home_shots)
                if stats.away_shots is not None: shots_against.append(stats.away_shots)
                if stats.home_shots_on_target is not None: sot_for.append(stats.home_shots_on_target)
                if stats.away_shots_on_target is not None: sot_against.append(stats.away_shots_on_target)
            else:
                if stats.away_shots is not None: shots_for.append(stats.away_shots)
                if stats.home_shots is not None: shots_against.append(stats.home_shots)
                if stats.away_shots_on_target is not None: sot_for.append(stats.away_shots_on_target)
                if stats.home_shots_on_target is not None: sot_against.append(stats.home_shots_on_target)

        # Baseline priors
        base_shots_for = DEFAULT_LEAGUE_SHOTS_HOME if is_home else DEFAULT_LEAGUE_SHOTS_AWAY
        base_shots_against = DEFAULT_LEAGUE_SHOTS_AWAY if is_home else DEFAULT_LEAGUE_SHOTS_HOME
        base_sot_for = DEFAULT_LEAGUE_SOT_HOME if is_home else DEFAULT_LEAGUE_SOT_AWAY
        base_sot_against = DEFAULT_LEAGUE_SOT_AWAY if is_home else DEFAULT_LEAGUE_SOT_HOME

        # Bayesian shrinkage: w = min(1.0, N / 8.0)
        n_shots = len(shots_for)
        w_shots = min(1.0, n_shots / 8.0)
        raw_shots_for = (sum(shots_for) / float(n_shots)) if n_shots > 0 else base_shots_for
        shrunk_shots_for = (w_shots * raw_shots_for) + ((1.0 - w_shots) * base_shots_for)

        n_against = len(shots_against)
        w_against = min(1.0, n_against / 8.0)
        raw_shots_against = (sum(shots_against) / float(n_against)) if n_against > 0 else base_shots_against
        shrunk_shots_against = (w_against * raw_shots_against) + ((1.0 - w_against) * base_shots_against)

        n_sot = len(sot_for)
        w_sot = min(1.0, n_sot / 8.0)
        raw_sot_for = (sum(sot_for) / float(n_sot)) if n_sot > 0 else base_sot_for
        shrunk_sot_for = (w_sot * raw_sot_for) + ((1.0 - w_sot) * base_sot_for)

        n_sot_against = len(sot_against)
        w_sot_against = min(1.0, n_sot_against / 8.0)
        raw_sot_against = (sum(sot_against) / float(n_sot_against)) if n_sot_against > 0 else base_sot_against
        shrunk_sot_against = (w_sot_against * raw_sot_against) + ((1.0 - w_sot_against) * base_sot_against)

        conversion = (shrunk_sot_for / max(1.0, shrunk_shots_for))

        return {
            "sample_size": n_shots,
            "coverage_status": "VALIDATED" if n_shots >= 8 else ("VALIDATING" if n_shots >= 3 else "INSUFFICIENT_DATA"),
            "avg_shots_for": round(shrunk_shots_for, 2),
            "avg_shots_against": round(shrunk_shots_against, 2),
            "avg_sot_for": round(shrunk_sot_for, 2),
            "avg_sot_against": round(shrunk_sot_against, 2),
            "sot_conversion_rate": round(max(0.20, min(0.50, conversion)), 3)
        }


class ShotsPredictionEngine:
    """
    First-class negative binomial prediction engine for match total shots,
    team shots, total shots on target (SoT), and team SoT.
    """

    @classmethod
    def predict_shots(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """
        Generates comprehensive pre-match shot and SoT predictions for a fixture.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {"error": f"Fixture {fixture_id} not found"}

        now = fixture.match_date or datetime.now(timezone.utc)
        h_team_id = fixture.home_team_id
        a_team_id = fixture.away_team_id

        h_feat = ShotsFeatureService.get_team_shots_features(db, h_team_id, now, is_home=True)
        a_feat = ShotsFeatureService.get_team_shots_features(db, a_team_id, now, is_home=False)

        # Calculate Expected Rates (Lambda) via Attack * Opponent Defense interaction
        # Lambda_H = (Home Attacking Shots * Away Defensive Shots Conceded) / Baseline
        lambda_h_shots = (h_feat["avg_shots_for"] * a_feat["avg_shots_against"]) / DEFAULT_LEAGUE_SHOTS_HOME
        lambda_a_shots = (a_feat["avg_shots_for"] * h_feat["avg_shots_against"]) / DEFAULT_LEAGUE_SHOTS_AWAY

        lambda_h_shots = max(4.0, min(28.0, lambda_h_shots))
        lambda_a_shots = max(3.0, min(24.0, lambda_a_shots))
        lambda_total_shots = lambda_h_shots + lambda_a_shots

        # SoT Rates (bounded by shots)
        lambda_h_sot = min(lambda_h_shots * 0.90, max(1.0, h_feat["avg_sot_for"] * (a_feat["avg_sot_against"] / DEFAULT_LEAGUE_SOT_AWAY)))
        lambda_a_sot = min(lambda_a_shots * 0.90, max(0.8, a_feat["avg_sot_for"] * (h_feat["avg_sot_against"] / DEFAULT_LEAGUE_SOT_HOME)))
        lambda_total_sot = lambda_h_sot + lambda_a_sot

        # Construct Discrete PMFs (Negative Binomial)
        r_shots = DEFAULT_SHOTS_DISPERSION_R
        r_sot = DEFAULT_SOT_DISPERSION_R

        h_shots_pmf = [_negative_binomial_pmf(k, lambda_h_shots, r_shots) for k in range(MAX_SHOT_GRID)]
        a_shots_pmf = [_negative_binomial_pmf(k, lambda_a_shots, r_shots) for k in range(MAX_SHOT_GRID)]

        # Convolution for total shots PMF
        total_shots_pmf = [0.0] * MAX_SHOT_GRID
        for i in range(MAX_SHOT_GRID):
            for j in range(MAX_SHOT_GRID - i):
                total_shots_pmf[i + j] += h_shots_pmf[i] * a_shots_pmf[j]

        # SoT PMFs
        h_sot_pmf = [_negative_binomial_pmf(k, lambda_h_sot, r_sot) for k in range(MAX_SOT_GRID)]
        a_sot_pmf = [_negative_binomial_pmf(k, lambda_a_sot, r_sot) for k in range(MAX_SOT_GRID)]

        total_sot_pmf = [0.0] * MAX_SOT_GRID
        for i in range(MAX_SOT_GRID):
            for j in range(MAX_SOT_GRID - i):
                total_sot_pmf[i + j] += h_sot_pmf[i] * a_sot_pmf[j]

        # Derive Monotonic Market Probabilities
        # Total Shots Over/Under Lines
        shots_lines = [15.5, 17.5, 19.5, 21.5, 23.5, 25.5, 27.5]
        shots_markets = {}
        for line in shots_lines:
            cutoff = int(math.ceil(line))
            over_p = sum(total_shots_pmf[k] for k in range(cutoff, len(total_shots_pmf)))
            key_over = f"over_{str(line).replace('.', '_')}"
            key_under = f"under_{str(line).replace('.', '_')}"
            shots_markets[key_over] = round(max(0.01, min(0.99, over_p)), 4)
            shots_markets[key_under] = round(max(0.01, min(0.99, 1.0 - over_p)), 4)

        # Team Shots Over Lines
        team_shots_lines = [5.5, 7.5, 9.5, 11.5, 13.5]
        home_shots_markets = {}
        away_shots_markets = {}
        for line in team_shots_lines:
            cutoff = int(math.ceil(line))
            h_over = sum(h_shots_pmf[k] for k in range(cutoff, len(h_shots_pmf)))
            a_over = sum(a_shots_pmf[k] for k in range(cutoff, len(a_shots_pmf)))
            home_shots_markets[f"over_{str(line).replace('.', '_')}"] = round(max(0.01, min(0.99, h_over)), 4)
            away_shots_markets[f"over_{str(line).replace('.', '_')}"] = round(max(0.01, min(0.99, a_over)), 4)

        # Total SoT Over/Under Lines
        sot_lines = [2.5, 3.5, 4.5, 5.5, 6.5]
        sot_markets = {}
        for line in sot_lines:
            cutoff = int(math.ceil(line))
            over_p = sum(total_sot_pmf[k] for k in range(cutoff, len(total_sot_pmf)))
            key_over = f"over_{str(line).replace('.', '_')}"
            key_under = f"under_{str(line).replace('.', '_')}"
            sot_markets[key_over] = round(max(0.01, min(0.99, over_p)), 4)
            sot_markets[key_under] = round(max(0.01, min(0.99, 1.0 - over_p)), 4)

        # Team SoT Lines
        team_sot_lines = [0.5, 1.5, 2.5, 3.5, 4.5]
        home_sot_markets = {}
        away_sot_markets = {}
        for line in team_sot_lines:
            cutoff = int(math.ceil(line))
            h_over = sum(h_sot_pmf[k] for k in range(cutoff, len(h_sot_pmf)))
            a_over = sum(a_sot_pmf[k] for k in range(cutoff, len(a_sot_pmf)))
            home_sot_markets[f"over_{str(line).replace('.', '_')}"] = round(max(0.01, min(0.99, h_over)), 4)
            away_sot_markets[f"over_{str(line).replace('.', '_')}"] = round(max(0.01, min(0.99, a_over)), 4)

        # Confidence & Data Quality Score
        sample_sum = h_feat["sample_size"] + a_feat["sample_size"]
        dq = 0.85 if sample_sum >= 16 else (0.65 if sample_sum >= 6 else 0.40)
        confidence = round(0.40 + 0.50 * (min(sample_sum, 20) / 20.0), 2)

        payload = {
            "fixture_id": fixture.id,
            "status": "AVAILABLE",
            "model_version": SHOTS_MODEL_VERSION,
            "shots": {
                "expected_home_shots": round(lambda_h_shots, 2),
                "expected_away_shots": round(lambda_a_shots, 2),
                "expected_total_shots": round(lambda_total_shots, 2),
                "probabilities": shots_markets,
                "home_probabilities": home_shots_markets,
                "away_probabilities": away_shots_markets
            },
            "shots_on_target": {
                "expected_home_sot": round(lambda_h_sot, 2),
                "expected_away_sot": round(lambda_a_sot, 2),
                "expected_total_sot": round(lambda_total_sot, 2),
                "probabilities": sot_markets,
                "home_probabilities": home_sot_markets,
                "away_probabilities": away_sot_markets
            },
            "diagnostics": {
                "sot_to_shot_ratio": round(lambda_total_sot / max(1.0, lambda_total_shots), 3),
                "home_sample_size": h_feat["sample_size"],
                "away_sample_size": a_feat["sample_size"],
                "captured_probability_mass": round(sum(total_shots_pmf), 4)
            },
            "confidence": confidence,
            "data_quality": dq,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

        return payload

    @classmethod
    def predict_live_shots(cls, db: Session, fixture_id: int) -> Dict[str, Any]:
        """
        Calculates dynamic in-play remaining shots and SoT expectations.
        Resolves markets that have already mathematically occurred.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {"error": f"Fixture {fixture_id} not found"}

        # Get pre-match prior
        pre_match = cls.predict_shots(db, fixture_id)
        if "error" in pre_match:
            return pre_match

        stats = db.query(MatchStatistics).filter(MatchStatistics.fixture_id == fixture_id).first()

        # Extract live observed stats safely without fabrication
        obs_h_shots = stats.home_shots if (stats and stats.home_shots is not None) else 0
        obs_a_shots = stats.away_shots if (stats and stats.away_shots is not None) else 0
        obs_tot_shots = obs_h_shots + obs_a_shots

        obs_h_sot = stats.home_shots_on_target if (stats and stats.home_shots_on_target is not None) else 0
        obs_a_sot = stats.away_shots_on_target if (stats and stats.away_shots_on_target is not None) else 0
        obs_tot_sot = obs_h_sot + obs_a_sot

        # Match minute
        minute = max(1, min(90, fixture.match_minute if hasattr(fixture, "match_minute") and fixture.match_minute else 45))
        rem_fraction = max(0.0, (90.0 - float(minute)) / 90.0)

        # Remaining Expected Rates
        rem_h_shots = pre_match["shots"]["expected_home_shots"] * rem_fraction
        rem_a_shots = pre_match["shots"]["expected_away_shots"] * rem_fraction
        rem_tot_shots = rem_h_shots + rem_a_shots

        rem_h_sot = pre_match["shots_on_target"]["expected_home_sot"] * rem_fraction
        rem_a_sot = pre_match["shots_on_target"]["expected_away_sot"] * rem_fraction
        rem_tot_sot = rem_h_sot + rem_a_sot

        # Final Expected Totals
        final_tot_shots = obs_tot_shots + rem_tot_shots
        final_tot_sot = obs_tot_sot + rem_tot_sot

        # Live Dynamic Markets & Early Resolution
        live_shot_markets = {}
        for line in [15.5, 17.5, 19.5, 21.5, 23.5, 25.5, 27.5]:
            k_over = f"over_{str(line).replace('.', '_')}"
            if obs_tot_shots >= math.ceil(line):
                live_shot_markets[k_over] = {
                    "probability": 1.0,
                    "status": "already_resolved",
                    "resolved_result": True
                }
            else:
                needed = math.ceil(line) - obs_tot_shots
                p = sum(_negative_binomial_pmf(k, rem_tot_shots, DEFAULT_SHOTS_DISPERSION_R) for k in range(needed, MAX_SHOT_GRID))
                live_shot_markets[k_over] = {
                    "probability": round(max(0.01, min(0.99, p)), 4),
                    "status": "in_play",
                    "resolved_result": None
                }

        return {
            "fixture_id": fixture.id,
            "status": "LIVE_ACTIVE",
            "model_version": LIVE_SHOTS_MODEL_VERSION,
            "match_minute": minute,
            "observed": {
                "home_shots": obs_h_shots,
                "away_shots": obs_a_shots,
                "total_shots": obs_tot_shots,
                "home_sot": obs_h_sot,
                "away_sot": obs_a_sot,
                "total_sot": obs_tot_sot
            },
            "remaining_expected": {
                "home_shots": round(rem_h_shots, 2),
                "away_shots": round(rem_a_shots, 2),
                "total_shots": round(rem_tot_shots, 2),
                "home_sot": round(rem_h_sot, 2),
                "away_sot": round(rem_a_sot, 2),
                "total_sot": round(rem_tot_sot, 2)
            },
            "final_expected": {
                "total_shots": round(final_tot_shots, 2),
                "total_sot": round(final_tot_sot, 2)
            },
            "markets": live_shot_markets,
            "confidence": round(pre_match.get("confidence", 0.50) * 0.85, 2),
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

    @classmethod
    def save_shot_snapshot(cls, db: Session, fixture_id: int) -> ShotPredictionSnapshot:
        """
        Creates and persists an immutable ShotPredictionSnapshot record.
        """
        pred = cls.predict_shots(db, fixture_id)

        snapshot = ShotPredictionSnapshot(
            fixture_id=fixture_id,
            model_version=SHOTS_MODEL_VERSION,
            prediction_timestamp=datetime.now(timezone.utc),
            match_minute=0,
            is_live=False,
            expected_home_shots=pred["shots"]["expected_home_shots"],
            expected_away_shots=pred["shots"]["expected_away_shots"],
            expected_total_shots=pred["shots"]["expected_total_shots"],
            expected_home_sot=pred["shots_on_target"]["expected_home_sot"],
            expected_away_sot=pred["shots_on_target"]["expected_away_sot"],
            expected_total_sot=pred["shots_on_target"]["expected_total_sot"],
            shots_probabilities_json=json.dumps(pred["shots"]["probabilities"]),
            sot_probabilities_json=json.dumps(pred["shots_on_target"]["probabilities"]),
            confidence=pred["confidence"],
            data_quality=pred["data_quality"],
            diagnostics_json=json.dumps(pred["diagnostics"]),
            created_at=datetime.now(timezone.utc)
        )
        db.add(snapshot)
        db.commit()
        return snapshot

    @classmethod
    def get_shots_readiness_status(cls, db: Session) -> Dict[str, Any]:
        """Returns empirical production validation status for shot and SoT prediction engines."""
        shot_evals = db.query(ModelEvaluation).filter(ModelEvaluation.market.contains("shots")).count()
        sot_evals = db.query(ModelEvaluation).filter(ModelEvaluation.market.contains("sot")).count()

        def _eval_gate(n: int) -> str:
            if n >= 300: return "VALIDATED"
            if n >= 100: return "VALIDATING"
            return "INSUFFICIENT_DATA"

        return {
            "shots_model": {
                "model_version": SHOTS_MODEL_VERSION,
                "sample_size": shot_evals,
                "readiness_state": _eval_gate(shot_evals),
                "required_sample": MIN_PRODUCTION_VALIDATION_SAMPLE
            },
            "sot_model": {
                "model_version": SHOTS_MODEL_VERSION,
                "sample_size": sot_evals,
                "readiness_state": _eval_gate(sot_evals),
                "required_sample": MIN_PRODUCTION_VALIDATION_SAMPLE
            },
            "ensemble_activation": "GATED (Sample < 100)" if (shot_evals < 100 or sot_evals < 100) else "ELIGIBLE",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
