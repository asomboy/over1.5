import os
import sys
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, cast
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import Fixture, HistoricalResult, MatchStatistics, League, Team
    from services.prediction_service import _negative_binomial_pmf, _shrink_to_prior, _clamp
    from schemas.prediction_schema import CornersPrediction
except ImportError:
    from ..models import Fixture, HistoricalResult, MatchStatistics, League, Team
    from .prediction_service import _negative_binomial_pmf, _shrink_to_prior, _clamp
    from ..schemas.prediction_schema import CornersPrediction

logger = logging.getLogger(__name__)

CORNER_MODEL_VERSION = "v1_corners_nb"
DEFAULT_FALLBACK_DISPERSION = 5.5
MIN_DISPERSION = 1.5
MAX_DISPERSION = 15.0

# League Default Baselines when historical corner records are unpopulated
DEFAULT_LEAGUE_HOME_CORNERS = 5.40
DEFAULT_LEAGUE_AWAY_CORNERS = 4.60
DEFAULT_LEAGUE_TOTAL_CORNERS = 10.00


class CornersPredictionEngine:
    """
    Negative Binomial Corners Prediction Engine V1.
    
    Predicts:
    1. Expected Home Corners (lambda_home)
    2. Expected Away Corners (lambda_away)
    3. Expected Total Corners (lambda_total = lambda_home + lambda_away)
    4. Total Corners Markets (Over/Under 7.5, 8.5, 9.5, 10.5, 11.5)
    5. Team Corners Markets (Home & Away Over 3.5, 4.5, 5.5)
    6. Multi-Factor Corner Confidence and Data Coverage tracking
    7. Dispersion estimation hierarchy (competition, shrunk_competition, global, fallback)
    """

    XI_TIME_DECAY = 0.0035

    @classmethod
    def resolve_dispersion(cls, db: Optional[Session], league_id: Optional[int]) -> Tuple[float, str]:
        """
        Estimates the Negative Binomial dispersion parameter r from historical corner data.
        Hierarchy:
        A. Competition-specific (n >= 20 corner matches in league)
        B. Shrunk competition (8 <= n < 20 matches, shrunk toward global)
        C. Global dispersion (n >= 30 matches across all leagues in DB)
        D. Fallback dispersion (5.5)

        Returns (dispersion_r, dispersion_source).
        """
        if not db:
            return DEFAULT_FALLBACK_DISPERSION, "fallback"

        def compute_global_dispersion() -> Tuple[float, str]:
            try:
                matches = (
                    db.query(MatchStatistics)
                    .filter(MatchStatistics.total_corners.isnot(None))
                    .limit(300)
                    .all()
                )
                if len(matches) >= 30:
                    corners = [m.total_corners for m in matches if m.total_corners is not None]
                    n = len(corners)
                    mean_val = sum(corners) / float(n)
                    var_val = sum((c - mean_val) ** 2 for c in corners) / float(n)
                    if var_val > mean_val:
                        r = (mean_val ** 2) / (var_val - mean_val)
                        return round(_clamp(r, MIN_DISPERSION, MAX_DISPERSION), 2), "global"
            except Exception as e:
                logger.debug(f"Global corner dispersion estimation error: {e}")
            return DEFAULT_FALLBACK_DISPERSION, "fallback"

        if not league_id:
            return compute_global_dispersion()

        try:
            league_matches = (
                db.query(MatchStatistics)
                .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                .filter(Fixture.league_id == league_id, MatchStatistics.total_corners.isnot(None))
                .limit(200)
                .all()
            )
            n_comp = len(league_matches)

            if n_comp >= 20:
                corners = [m.total_corners for m in league_matches if m.total_corners is not None]
                mean_val = sum(corners) / float(n_comp)
                var_val = sum((c - mean_val) ** 2 for c in corners) / float(n_comp)
                if var_val > mean_val:
                    r = (mean_val ** 2) / (var_val - mean_val)
                    return round(_clamp(r, MIN_DISPERSION, MAX_DISPERSION), 2), "competition"
                else:
                    return MAX_DISPERSION, "competition"
            elif n_comp >= 8:
                corners = [m.total_corners for m in league_matches if m.total_corners is not None]
                mean_val = sum(corners) / float(n_comp)
                var_val = sum((c - mean_val) ** 2 for c in corners) / float(n_comp)
                raw_comp = (mean_val ** 2) / max(0.1, (var_val - mean_val)) if var_val > mean_val else MAX_DISPERSION
                glob_r, _ = compute_global_dispersion()
                w = n_comp / 20.0
                shrunk_r = _shrink_to_prior(raw_comp, glob_r, w)
                return round(_clamp(shrunk_r, MIN_DISPERSION, MAX_DISPERSION), 2), "shrunk_competition"
            else:
                return compute_global_dispersion()
        except Exception as e:
            logger.warning(f"Error calculating corner dispersion for league {league_id}: {e}")
            return compute_global_dispersion()

    @classmethod
    def get_team_corner_metrics(
        cls, db: Session, team_id: int, is_home: bool, target_date: Optional[datetime] = None
    ) -> Tuple[float, float, int]:
        """
        Calculates time-decay weighted average corners scored and corners conceded for a team.
        Returns (avg_corners_for, avg_corners_against, sample_size).
        Strictly excludes matches occurring on or after target_date for chronological backtesting safety.
        """
        try:
            query = (
                db.query(Fixture, MatchStatistics)
                .join(MatchStatistics, MatchStatistics.fixture_id == Fixture.id)
                .filter(
                    Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                    MatchStatistics.home_corners.isnot(None),
                    MatchStatistics.away_corners.isnot(None)
                )
            )

            if target_date:
                naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if target_date.tzinfo else target_date
                query = query.filter(Fixture.match_date < naive_target)

            if is_home:
                query = query.filter(Fixture.home_team_id == team_id)
            else:
                query = query.filter(Fixture.away_team_id == team_id)

            records = query.order_by(Fixture.match_date.desc()).limit(25).all()

            if not records:
                return (DEFAULT_LEAGUE_HOME_CORNERS if is_home else DEFAULT_LEAGUE_AWAY_CORNERS), \
                       (DEFAULT_LEAGUE_AWAY_CORNERS if is_home else DEFAULT_LEAGUE_HOME_CORNERS), 0

            ref_date = target_date or datetime.now(timezone.utc)
            if hasattr(ref_date, 'tzinfo') and ref_date.tzinfo:
                ref_date = ref_date.astimezone(timezone.utc).replace(tzinfo=None)

            weighted_for = 0.0
            weighted_against = 0.0
            weight_sum = 0.0

            for f, stats in records:
                days_diff = max(0, (ref_date - f.match_date).total_seconds() / 86400.0) if f.match_date else 0
                w = math.exp(-cls.XI_TIME_DECAY * days_diff)

                c_for = (stats.home_corners or 0) if is_home else (stats.away_corners or 0)
                c_against = (stats.away_corners or 0) if is_home else (stats.home_corners or 0)

                weighted_for += c_for * w
                weighted_against += c_against * w
                weight_sum += w

            avg_for = weighted_for / weight_sum if weight_sum > 0 else (DEFAULT_LEAGUE_HOME_CORNERS if is_home else DEFAULT_LEAGUE_AWAY_CORNERS)
            avg_against = weighted_against / weight_sum if weight_sum > 0 else (DEFAULT_LEAGUE_AWAY_CORNERS if is_home else DEFAULT_LEAGUE_HOME_CORNERS)

            return round(avg_for, 2), round(avg_against, 2), len(records)
        except Exception as e:
            logger.debug(f"Error fetching team corner metrics for team {team_id}: {e}")
            return (DEFAULT_LEAGUE_HOME_CORNERS if is_home else DEFAULT_LEAGUE_AWAY_CORNERS), \
                   (DEFAULT_LEAGUE_AWAY_CORNERS if is_home else DEFAULT_LEAGUE_HOME_CORNERS), 0

    @classmethod
    def calculate_expected_corners(
        cls, db: Session, home_team_id: int, away_team_id: int, league_id: Optional[int], target_date: Optional[datetime] = None
    ) -> Tuple[float, float, float, Dict[str, Any]]:
        """
        Calculates expected corners (lambda_home, lambda_away, lambda_total) using
        time-decay team strengths, concession rates, league baselines, and sample-size shrinkage.
        """
        # 1. League corner baselines
        league_home_avg = DEFAULT_LEAGUE_HOME_CORNERS
        league_away_avg = DEFAULT_LEAGUE_AWAY_CORNERS
        league_sample_size = 0

        if league_id:
            try:
                l_query = (
                    db.query(MatchStatistics)
                    .join(Fixture, Fixture.id == MatchStatistics.fixture_id)
                    .filter(
                        Fixture.league_id == league_id,
                        Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                        MatchStatistics.home_corners.isnot(None),
                        MatchStatistics.away_corners.isnot(None)
                    )
                )
                if target_date:
                    naive_target = target_date.astimezone(timezone.utc).replace(tzinfo=None) if target_date.tzinfo else target_date
                    l_query = l_query.filter(Fixture.match_date < naive_target)

                l_matches = l_query.limit(100).all()
                league_sample_size = len(l_matches)

                if league_sample_size >= 10:
                    tot_h = sum(m.home_corners or 0 for m in l_matches)
                    tot_a = sum(m.away_corners or 0 for m in l_matches)
                    league_home_avg = round(tot_h / float(league_sample_size), 2)
                    league_away_avg = round(tot_a / float(league_sample_size), 2)
            except Exception as e:
                logger.debug(f"League corner baseline error: {e}")

        # 2. Team corner metrics
        h_for, h_conceded, h_sample = cls.get_team_corner_metrics(db, home_team_id, is_home=True, target_date=target_date)
        a_for, a_conceded, a_sample = cls.get_team_corner_metrics(db, away_team_id, is_home=False, target_date=target_date)

        # 3. Attacking and defensive concession strengths
        raw_h_att = h_for / max(1.0, league_home_avg)
        raw_h_def = h_conceded / max(1.0, league_away_avg)
        raw_a_att = a_for / max(1.0, league_away_avg)
        raw_a_def = a_conceded / max(1.0, league_home_avg)

        # 4. Shrinkage toward 1.0 based on sample size
        w_h = min(1.0, h_sample / 6.0)
        w_a = min(1.0, a_sample / 6.0)

        h_att = _shrink_to_prior(raw_h_att, 1.0, w_h)
        h_def = _shrink_to_prior(raw_h_def, 1.0, w_h)
        a_att = _shrink_to_prior(raw_a_att, 1.0, w_a)
        a_def = _shrink_to_prior(raw_a_def, 1.0, w_a)

        # 5. Expected corner rates
        lambda_h = _clamp(league_home_avg * h_att * a_def, 1.5, 12.0)
        lambda_a = _clamp(league_away_avg * a_att * h_def, 1.2, 11.0)
        lambda_tot = round(lambda_h + lambda_a, 2)

        diagnostics = {
            "home_sample_size": h_sample,
            "away_sample_size": a_sample,
            "league_sample_size": league_sample_size,
            "league_home_avg": league_home_avg,
            "league_away_avg": league_away_avg,
            "home_attack_strength": round(h_att, 3),
            "away_concession_strength": round(a_def, 3),
            "away_attack_strength": round(a_att, 3),
            "home_concession_strength": round(h_def, 3),
        }

        return round(lambda_h, 2), round(lambda_a, 2), lambda_tot, diagnostics

    @classmethod
    def calculate_corner_probabilities(
        cls, lambda_home: float, lambda_away: float, dispersion: float = DEFAULT_FALLBACK_DISPERSION
    ) -> Dict[str, Any]:
        """
        Derives comprehensive corner probability markets using separate home/away
        Negative Binomial distributions combined via discrete convolution.
        """
        lambda_h = max(0.5, lambda_home)
        lambda_a = max(0.5, lambda_away)
        lambda_tot = lambda_h + lambda_a
        safe_r = _clamp(dispersion, MIN_DISPERSION, MAX_DISPERSION)

        # Team dispersion parameters proportional to mean
        r_h = max(1.0, safe_r * (lambda_h / lambda_tot))
        r_a = max(1.0, safe_r * (lambda_a / lambda_tot))

        max_team_corners = 25
        home_pmf = [_negative_binomial_pmf(k, lambda_h, r_h) for k in range(max_team_corners)]
        away_pmf = [_negative_binomial_pmf(j, lambda_a, r_a) for j in range(max_team_corners)]

        # Discrete Convolution for total corners (0..50)
        max_total_corners = 50
        total_pmf = [0.0 for _ in range(max_total_corners)]
        for i in range(max_team_corners):
            for j in range(max_team_corners):
                tot = i + j
                if tot < max_total_corners:
                    total_pmf[tot] += home_pmf[i] * away_pmf[j]

        # Normalize total PMF
        sum_total = sum(total_pmf)
        if sum_total > 0:
            total_pmf = [p / sum_total for p in total_pmf]

        # Total Markets (Over / Under 7.5, 8.5, 9.5, 10.5, 11.5)
        p_o75 = sum(total_pmf[k] for k in range(8, max_total_corners))
        p_o85 = sum(total_pmf[k] for k in range(9, max_total_corners))
        p_o95 = sum(total_pmf[k] for k in range(10, max_total_corners))
        p_o105 = sum(total_pmf[k] for k in range(11, max_total_corners))
        p_o115 = sum(total_pmf[k] for k in range(12, max_total_corners))

        # Home Team Markets (Over 3.5, 4.5, 5.5)
        sum_h = sum(home_pmf)
        h_pmf_norm = [p / sum_h for p in home_pmf] if sum_h > 0 else home_pmf
        p_h_o35 = sum(h_pmf_norm[k] for k in range(4, max_team_corners))
        p_h_o45 = sum(h_pmf_norm[k] for k in range(5, max_team_corners))
        p_h_o55 = sum(h_pmf_norm[k] for k in range(6, max_team_corners))

        # Away Team Markets (Over 3.5, 4.5, 5.5)
        sum_a = sum(away_pmf)
        a_pmf_norm = [p / sum_a for p in away_pmf] if sum_a > 0 else away_pmf
        p_a_o35 = sum(a_pmf_norm[j] for j in range(4, max_team_corners))
        p_a_o45 = sum(a_pmf_norm[j] for j in range(5, max_team_corners))
        p_a_o55 = sum(a_pmf_norm[j] for j in range(6, max_team_corners))

        return {
            "total_markets": {
                "over_7_5": round(p_o75, 4),
                "under_7_5": round(max(0.0, 1.0 - p_o75), 4),
                "over_8_5": round(p_o85, 4),
                "under_8_5": round(max(0.0, 1.0 - p_o85), 4),
                "over_9_5": round(p_o95, 4),
                "under_9_5": round(max(0.0, 1.0 - p_o95), 4),
                "over_10_5": round(p_o105, 4),
                "under_10_5": round(max(0.0, 1.0 - p_o105), 4),
                "over_11_5": round(p_o115, 4),
                "under_11_5": round(max(0.0, 1.0 - p_o115), 4),
            },
            "home_team": {
                "over_3_5": round(p_h_o35, 4),
                "over_4_5": round(p_h_o45, 4),
                "over_5_5": round(p_h_o55, 4),
            },
            "away_team": {
                "over_3_5": round(p_a_o35, 4),
                "over_4_5": round(p_a_o45, 4),
                "over_5_5": round(p_a_o55, 4),
            }
        }

    @classmethod
    def calculate_corner_confidence(
        cls, home_sample: int, away_sample: int, league_sample: int, coverage: float
    ) -> Dict[str, Any]:
        """Calculates multi-dimensional corner confidence score (0-100)."""
        # 1. Data Quality
        tot_team_samples = home_sample + away_sample
        if tot_team_samples >= 16 and coverage >= 0.70:
            dq_base = 82
        elif tot_team_samples >= 10:
            dq_base = 70
        elif tot_team_samples >= 4:
            dq_base = 52
        elif tot_team_samples >= 2:
            dq_base = 36
        else:
            dq_base = 22

        data_quality = int(round(_clamp(dq_base, 15, 95)))

        # 2. Sample Strength
        min_team_s = min(home_sample, away_sample)
        if min_team_s >= 8:
            ss_base = 85
        elif min_team_s >= 4:
            ss_base = 68
        elif min_team_s >= 2:
            ss_base = 48
        else:
            ss_base = 25
        sample_strength = int(round(_clamp(ss_base, 15, 95)))

        # 3. Model Stability
        stab_base = 65
        if league_sample >= 25:
            stab_base += 15
        elif league_sample >= 10:
            stab_base += 8
        elif league_sample < 3:
            stab_base -= 15
        model_stability = int(round(_clamp(stab_base, 15, 95)))

        overall = int(round(0.40 * data_quality + 0.35 * sample_strength + 0.25 * model_stability))

        if overall >= 80:
            label = "strong"
        elif overall >= 65:
            label = "good"
        elif overall >= 50:
            label = "moderate"
        elif overall >= 35:
            label = "low"
        else:
            label = "insufficient"

        return {
            "overall": overall,
            "data_quality": data_quality,
            "sample_strength": sample_strength,
            "model_stability": model_stability,
            "label": label
        }

    @classmethod
    def predict_corners(
        cls, db: Session, fixture_id: int, target_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Executes full Corner Prediction pipeline for a fixture.
        Returns complete CornersPrediction structure.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return {
                "available": False,
                "reason": f"Fixture {fixture_id} not found in database.",
                "expected": None,
                "total_markets": None,
                "home_team": None,
                "away_team": None,
                "confidence": {"overall": 0, "data_quality": 0, "sample_strength": 0, "model_stability": 0, "label": "insufficient"},
                "model": {"version": CORNER_MODEL_VERSION, "dispersion": DEFAULT_FALLBACK_DISPERSION, "dispersion_source": "fallback"}
            }

        # Check data eligibility
        xg_h, xg_a, xg_tot, diag = cls.calculate_expected_corners(
            db, cast(int, fixture.home_team_id), cast(int, fixture.away_team_id), fixture.league_id, target_date=target_date
        )

        h_sample = diag["home_sample_size"]
        a_sample = diag["away_sample_size"]
        l_sample = diag["league_sample_size"]
        tot_eligible = h_sample + a_sample
        coverage = tot_eligible / max(1.0, float(tot_eligible + 5))

        # Check if enough minimum data exists (e.g. at least 1 match with corner data)
        if h_sample == 0 and a_sample == 0 and l_sample == 0:
            return {
                "available": False,
                "reason": "Insufficient verified corner history for a reliable prediction.",
                "expected": None,
                "total_markets": None,
                "home_team": None,
                "away_team": None,
                "confidence": {
                    "overall": 20,
                    "data_quality": 20,
                    "sample_strength": 20,
                    "model_stability": 20,
                    "label": "insufficient"
                },
                "model": {
                    "version": CORNER_MODEL_VERSION,
                    "dispersion": DEFAULT_FALLBACK_DISPERSION,
                    "dispersion_source": "fallback"
                },
                "diagnostics": {
                    "home_sample_size": 0,
                    "away_sample_size": 0,
                    "league_sample_size": 0,
                    "corner_data_coverage": 0.0
                }
            }

        # Resolve Negative Binomial dispersion
        dispersion_r, dispersion_source = cls.resolve_dispersion(db, fixture.league_id)

        # Calculate probabilities
        probs = cls.calculate_corner_probabilities(xg_h, xg_a, dispersion=dispersion_r)

        # Calculate confidence
        conf = cls.calculate_corner_confidence(h_sample, a_sample, l_sample, coverage)

        return {
            "available": True,
            "reason": None,
            "expected": {
                "home": xg_h,
                "away": xg_a,
                "total": xg_tot
            },
            "total_markets": probs["total_markets"],
            "home_team": probs["home_team"],
            "away_team": probs["away_team"],
            "confidence": conf,
            "model": {
                "version": CORNER_MODEL_VERSION,
                "dispersion": dispersion_r,
                "dispersion_source": dispersion_source
            },
            "diagnostics": {
                "home_sample_size": h_sample,
                "away_sample_size": a_sample,
                "league_sample_size": l_sample,
                "corner_data_coverage": round(coverage, 3),
                **diag
            }
        }


class CornersBacktestService:
    """
    Chronological Backtesting Engine for the Corners Prediction Model.
    Evaluates Brier Score, Log Loss, Calibration, and Market Accuracy
    with zero future data leakage.
    """

    @classmethod
    def run_chronological_backtest(cls, db: Session, min_samples: int = 5) -> Dict[str, Any]:
        """
        Performs a chronological backtest across all historical finished fixtures
        containing verified observed corner counts.
        """
        fixtures = (
            db.query(Fixture, MatchStatistics)
            .join(MatchStatistics, MatchStatistics.fixture_id == Fixture.id)
            .filter(
                Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]),
                MatchStatistics.home_corners.isnot(None),
                MatchStatistics.away_corners.isnot(None)
            )
            .order_by(Fixture.match_date.asc())
            .all()
        )

        n_matches = len(fixtures)
        if n_matches < min_samples:
            return {
                "status": "insufficient_data",
                "message": f"Only {n_matches} completed matches with corner data found. Minimum {min_samples} required for backtesting.",
                "matches_evaluated": n_matches,
                "metrics": None
            }

        market_keys = ["over_7_5", "over_8_5", "over_9_5", "over_10_5", "over_11_5"]
        thresholds = [7.5, 8.5, 9.5, 10.5, 11.5]

        brier_accum = {m: 0.0 for m in market_keys}
        log_loss_accum = {m: 0.0 for m in market_keys}
        correct_accum = {m: 0 for m in market_keys}

        # Calibration buckets: 0.5-0.6, 0.6-0.7, 0.7-0.8, 0.8-0.9, 0.9-1.0
        calibration_buckets = {
            "50-60%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "60-70%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "70-80%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "80-90%": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
            "90%+": {"count": 0, "pred_sum": 0.0, "actual_hits": 0},
        }

        evaluated_count = 0

        for f, stats in fixtures:
            actual_home = stats.home_corners or 0
            actual_away = stats.away_corners or 0
            actual_total = actual_home + actual_away

            # Predict corners using ONLY matches strictly before f.match_date
            pred = CornersPredictionEngine.predict_corners(db, f.id, target_date=f.match_date)
            if not pred["available"] or not pred["total_markets"]:
                continue

            evaluated_count += 1
            tot_markets = pred["total_markets"]

            for m_key, thresh in zip(market_keys, thresholds):
                p = tot_markets[m_key]
                y = 1.0 if actual_total > thresh else 0.0

                # Brier score contribution: (p - y)^2
                brier_accum[m_key] += (p - y) ** 2

                # Log loss contribution: -[y*log(p) + (1-y)*log(1-p)]
                p_safe = _clamp(p, 1e-6, 1.0 - 1e-6)
                ll = -(y * math.log(p_safe) + (1.0 - y) * math.log(1.0 - p_safe))
                log_loss_accum[m_key] += ll

                # Binary classification accuracy at threshold 0.50
                if (p >= 0.50 and y == 1.0) or (p < 0.50 and y == 0.0):
                    correct_accum[m_key] += 1

                # Calibration tracking for Over 8.5 and 9.5
                if m_key in ["over_8_5", "over_9_5"]:
                    if 0.50 <= p < 0.60:
                        b = calibration_buckets["50-60%"]
                    elif 0.60 <= p < 0.70:
                        b = calibration_buckets["60-70%"]
                    elif 0.70 <= p < 0.80:
                        b = calibration_buckets["70-80%"]
                    elif 0.80 <= p < 0.90:
                        b = calibration_buckets["80-90%"]
                    elif p >= 0.90:
                        b = calibration_buckets["90%+"]
                    else:
                        b = None

                    if b:
                        b["count"] += 1
                        b["pred_sum"] += p
                        b["actual_hits"] += int(y)

        if evaluated_count == 0:
            return {
                "status": "insufficient_evaluations",
                "message": "Zero fixtures could be evaluated with training history.",
                "matches_evaluated": 0,
                "metrics": None
            }

        # Summarize metrics
        market_metrics = {}
        for m_key in market_keys:
            market_metrics[m_key] = {
                "brier_score": round(brier_accum[m_key] / float(evaluated_count), 4),
                "log_loss": round(log_loss_accum[m_key] / float(evaluated_count), 4),
                "accuracy": round(correct_accum[m_key] / float(evaluated_count), 4),
                "sample_size": evaluated_count
            }

        calibration_summary = {}
        for b_name, b_data in calibration_buckets.items():
            c = b_data["count"]
            avg_p = round(b_data["pred_sum"] / float(c), 4) if c > 0 else 0.0
            act_rate = round(b_data["actual_hits"] / float(c), 4) if c > 0 else 0.0
            calibration_summary[b_name] = {
                "count": c,
                "avg_predicted_prob": avg_p,
                "actual_event_rate": act_rate
            }

        return {
            "status": "ok",
            "matches_evaluated": evaluated_count,
            "overall_brier_score": round(sum(m["brier_score"] for m in market_metrics.values()) / len(market_keys), 4),
            "markets": market_metrics,
            "calibration": calibration_summary
        }
