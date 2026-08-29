import os
import sys
import json
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, cast
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import Fixture, Prediction, TeamStatistics, LeagueStatistics, Team, HistoricalResult, TeamFormStreak, EloRating
    from services.statistics_service import calculate_team_statistics, calculate_league_statistics
    from services.elo_service import EloRatingService, TeamFormService, HeadToHeadService
    from services.weather_service import WeatherService
    from schemas.prediction_schema import MatchIntelligencePrediction
except ImportError:
    from ..models import Fixture, Prediction, TeamStatistics, LeagueStatistics, Team, HistoricalResult, TeamFormStreak, EloRating
    from .statistics_service import calculate_team_statistics, calculate_league_statistics
    from .elo_service import EloRatingService, TeamFormService, HeadToHeadService
    from .weather_service import WeatherService
    from ..schemas.prediction_schema import MatchIntelligencePrediction

logger = logging.getLogger(__name__)

MODEL_VERSION = "v2_match_intelligence"

# Dixon-Coles Rho Strategy Configuration
DEFAULT_FALLBACK_RHO = -0.11
MIN_RHO = -0.25
MAX_RHO = 0.05

# Score Matrix Adaptive Grid Configuration
MIN_SCORE_GRID = 10
TARGET_CAPTURED_MASS = 0.999
MAX_SCORE_GRID = 25

# Best Model Signal Whitelist & Threshold Configuration
SIGNAL_CANDIDATE_MARKETS = [
    # (Market Name, Category Key, Sub-Key, Min Probability Threshold)
    ("Over 1.5 Goals", "goals", "over_1_5", 0.65),
    ("Over 2.5 Goals", "goals", "over_2_5", 0.52),
    ("Over 3.5 Goals", "goals", "over_3_5", 0.38),
    ("Both Teams To Score", "btts", "yes", 0.52),
    ("Both Teams Not To Score", "btts", "no", 0.55),
    ("Home Win", "result", "home_win", 0.45),
    ("Away Win", "result", "away_win", 0.40),
    ("Draw", "result", "draw", 0.32),
    ("Home Team Over 1.5", "home_team_goals", "over_1_5", 0.46),
    ("Away Team Over 1.5", "away_team_goals", "over_1_5", 0.40),
]

MIN_SIGNAL_CONFIDENCE_OVERALL = 40
MIN_SIGNAL_DATA_QUALITY = 35
MIN_SIGNAL_MODEL_STABILITY = 35
MIN_SIGNAL_QUALIFYING_SCORE = 60


def _poisson_pmf(k: int, mu: float) -> float:
    """Calculates Poisson probability mass function P(X=k) for mean mu with numerical safeguards."""
    if math.isnan(mu) or math.isinf(mu) or mu <= 0:
        return 1.0 if k == 0 else 0.0
    if k < 0:
        return 0.0
    try:
        return (math.pow(mu, k) * math.exp(-mu)) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _negative_binomial_pmf(k: int, mu: float, r: float = 3.0) -> float:
    """
    Calculates Negative Binomial probability mass function.
    PMF(k; r, p) = C(k+r-1, k) * p^r * (1-p)^k where p = r / (r + mu).
    """
    if math.isnan(mu) or math.isinf(mu) or mu <= 0:
        return 1.0 if k == 0 else 0.0
    if r <= 0 or math.isnan(r) or math.isinf(r):
        return _poisson_pmf(k, mu)

    try:
        p = r / (r + mu)
        log_pmf = (math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r)) + \
                   (r * math.log(p)) + (k * math.log(1 - p))
        return math.exp(log_pmf)
    except (OverflowError, ValueError):
        return _poisson_pmf(k, mu)


def _calculate_overdispersion_parameter(db: Session, league_id: int) -> Optional[float]:
    """
    Calculate the Negative Binomial dispersion parameter r from historical data.
    r = mean^2 / (variance - mean)
    """
    try:
        results = (
            db.query(HistoricalResult)
            .join(Fixture, Fixture.id == HistoricalResult.fixture_id)
            .filter(Fixture.league_id == league_id)
            .filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
            .order_by(Fixture.match_date.desc())
            .limit(300)
            .all()
        )

        if len(results) < 15:
            return None

        total_goals = [res.home_score + res.away_score for res in results]
        n = len(total_goals)
        mean = sum(total_goals) / n
        variance = sum((g - mean) ** 2 for g in total_goals) / n

        if variance <= mean:
            return None

        r = (mean ** 2) / (variance - mean)
        if r <= 1.0:
            return None
        return round(min(8.0, max(1.5, r)), 2)
    except Exception:
        return None


def _shrink_to_prior(observed: float, prior: float, weight: float) -> float:
    """Shrinks an observed value toward a prior based on weight [0.0, 1.0]."""
    if weight <= 0.0:
        return float(prior)
    if weight >= 1.0:
        return float(observed)
    return float(observed * weight + prior * (1.0 - weight))


def _clamp(value: float, low: float, high: float) -> float:
    if math.isnan(value):
        return low
    return max(low, min(high, value))


def _form_goal_multipliers(streak: Optional[TeamFormStreak], league_avg_scored: float, league_avg_conceded: float) -> Tuple[float, float]:
    """Returns (scored_multiplier, conceded_multiplier) in [0.8, 1.25] derived from recent goal form."""
    if not streak or (streak.goals_scored_last_5 == 0 and streak.goals_conceded_last_5 == 0):
        return 1.0, 1.0

    scored_mult = 1.0
    conceded_mult = 1.0
    if streak.goals_scored_last_5 > 0 and league_avg_scored > 0:
        scored_mult = _clamp((streak.goals_scored_last_5 / 5.0) / league_avg_scored, 0.8, 1.25)
    if streak.goals_conceded_last_5 > 0 and league_avg_conceded > 0:
        conceded_mult = _clamp((streak.goals_conceded_last_5 / 5.0) / league_avg_conceded, 0.8, 1.25)

    return scored_mult, conceded_mult


class HalfPredictionEngine:
    """
    Half-by-Half Goal Expectation & Probability Engine.
    Estimates first-half and second-half goal distributions with strict mathematical
    sum consistency: expected_first_half + expected_second_half == expected_full_match.
    """
    DEFAULT_FIRST_HALF_RATIO = 0.45
    DEFAULT_SECOND_HALF_RATIO = 0.55

    @classmethod
    def calculate_half_proportions(
        cls, db: Optional[Session], league_id: Optional[int], home_team_id: Optional[int], away_team_id: Optional[int]
    ) -> Tuple[float, float, float, float]:
        """
        Calculates (home_1h_ratio, home_2h_ratio, away_1h_ratio, away_2h_ratio).
        Guarantees that home_1h + home_2h == 1.0 and away_1h + away_2h == 1.0.
        """
        league_1h_ratio = cls.DEFAULT_FIRST_HALF_RATIO
        
        if db and league_id:
            try:
                league_ht_matches = (
                    db.query(HistoricalResult)
                    .join(Fixture, Fixture.id == HistoricalResult.fixture_id)
                    .filter(
                        Fixture.league_id == league_id,
                        HistoricalResult.half_time_home_score.isnot(None),
                        HistoricalResult.half_time_away_score.isnot(None)
                    )
                    .limit(100)
                    .all()
                )
                if len(league_ht_matches) >= 10:
                    tot_ht_goals = sum((r.half_time_home_score or 0) + (r.half_time_away_score or 0) for r in league_ht_matches)
                    tot_ft_goals = sum(r.total_goals for r in league_ht_matches)
                    if tot_ft_goals > 0:
                        obs_ratio = tot_ht_goals / tot_ft_goals
                        league_1h_ratio = _clamp(obs_ratio, 0.38, 0.52)
            except Exception as e:
                logger.debug(f"Error calculating league HT proportions: {e}")

        def get_team_ht_ratio(team_id: Optional[int], is_home: bool) -> float:
            if not db or not team_id:
                return league_1h_ratio
            try:
                filter_cond = (Fixture.home_team_id == team_id) if is_home else (Fixture.away_team_id == team_id)
                team_ht_matches = (
                    db.query(HistoricalResult)
                    .join(Fixture, Fixture.id == HistoricalResult.fixture_id)
                    .filter(
                        filter_cond,
                        HistoricalResult.half_time_home_score.isnot(None),
                        HistoricalResult.half_time_away_score.isnot(None)
                    )
                    .order_by(Fixture.match_date.desc())
                    .limit(12)
                    .all()
                )
                n = len(team_ht_matches)
                if n >= 5:
                    team_ht_scored = sum((r.half_time_home_score or 0) if is_home else (r.half_time_away_score or 0) for r in team_ht_matches)
                    team_ft_scored = sum(r.home_score if is_home else r.away_score for r in team_ht_matches)
                    if team_ft_scored > 0:
                        obs_team_ratio = _clamp(team_ht_scored / team_ft_scored, 0.30, 0.60)
                        weight = min(0.6, n / 15.0)
                        return _shrink_to_prior(obs_team_ratio, league_1h_ratio, weight)
            except Exception as e:
                logger.debug(f"Error calculating team HT proportions: {e}")
            return league_1h_ratio

        home_1h = get_team_ht_ratio(home_team_id, is_home=True)
        away_1h = get_team_ht_ratio(away_team_id, is_home=False)

        return (
            home_1h,
            round(1.0 - home_1h, 4),
            away_1h,
            round(1.0 - away_1h, 4)
        )

    @classmethod
    def calculate_half_probabilities(
        cls, lambda_home: float, lambda_away: float, half_props: Tuple[float, float, float, float]
    ) -> Dict[str, float]:
        """
        Derives first-half and second-half expected goals and probabilities with exact sum consistency:
        lambda_1h + lambda_2h == lambda_home + lambda_away.
        """
        h_1h_prop, h_2h_prop, a_1h_prop, a_2h_prop = half_props
        
        # Exact team-split expected goals for each half
        lambda_h_1h = lambda_home * h_1h_prop
        lambda_h_2h = lambda_home * h_2h_prop
        lambda_a_1h = lambda_away * a_1h_prop
        lambda_a_2h = lambda_away * a_2h_prop

        lambda_1h = max(0.02, lambda_h_1h + lambda_a_1h)
        lambda_2h = max(0.02, lambda_h_2h + lambda_a_2h)

        # Scale slightly if small rounding differences occur to guarantee exact sum
        total_full = max(0.04, lambda_home + lambda_away)
        half_sum = lambda_1h + lambda_2h
        if abs(half_sum - total_full) > 0.001 and half_sum > 0:
            scale = total_full / half_sum
            lambda_1h *= scale
            lambda_2h *= scale

        # First half Poisson distribution approximation
        h1_over_0_5 = round(1.0 - math.exp(-lambda_1h), 4)
        h1_over_1_5 = round(1.0 - math.exp(-lambda_1h) * (1.0 + lambda_1h), 4)

        # Second half Poisson distribution approximation
        h2_over_0_5 = round(1.0 - math.exp(-lambda_2h), 4)
        h2_over_1_5 = round(1.0 - math.exp(-lambda_2h) * (1.0 + lambda_2h), 4)

        # Enforce logical bounds and monotonicity
        h1_over_0_5 = max(0.0, min(1.0, h1_over_0_5))
        h1_over_1_5 = max(0.0, min(h1_over_0_5, h1_over_1_5))
        h2_over_0_5 = max(0.0, min(1.0, h2_over_0_5))
        h2_over_1_5 = max(0.0, min(h2_over_0_5, h2_over_1_5))

        return {
            "first_half_over_0_5": h1_over_0_5,
            "first_half_over_1_5": h1_over_1_5,
            "second_half_over_0_5": h2_over_0_5,
            "second_half_over_1_5": h2_over_1_5,
            "first_half_xg": round(lambda_1h, 2),
            "second_half_xg": round(lambda_2h, 2)
        }


class DixonColesPredictionEngine:
    """
    Dixon-Coles Match Intelligence Core Prediction Engine V2.
    
    Hardened Features:
    1. Configurable, hierarchical Dixon-Coles rho estimation with safety clamping and source tracking.
    2. Adaptive score matrix grid expansion ensuring >= 0.999 probability mass capture before normalization.
    3. Transparent Best Model Signal scoring with whitelisted candidates and explicit no-signal handling.
    4. Exact half-by-half mathematical expectation consistency.
    5. Multi-dimensional model confidence and data quality tracking.
    """

    @classmethod
    def resolve_rho_strategy(cls, db: Optional[Session], league_id: Optional[int]) -> Tuple[float, str]:
        """
        Hierarchical Dixon-Coles rho estimation strategy:
        A. Competition-specific estimated rho (n >= 30 historical matches in league)
        B. Shrunk competition rho (10 <= n < 30 matches, shrunk toward global)
        C. Global estimated rho (n >= 50 matches across all leagues)
        D. Configurable fallback rho (-0.11)

        Returns (rho_value, rho_source).
        """
        if not db:
            return DEFAULT_FALLBACK_RHO, "fallback"

        # Global estimate helper
        def compute_global_rho() -> Tuple[float, str]:
            try:
                all_results = (
                    db.query(HistoricalResult)
                    .join(Fixture, Fixture.id == HistoricalResult.fixture_id)
                    .filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
                    .limit(500)
                    .all()
                )
                n = len(all_results)
                if n >= 50:
                    rho_est = cls._estimate_rho_from_matches(all_results)
                    return _clamp(rho_est, MIN_RHO, MAX_RHO), "global"
            except Exception as e:
                logger.debug(f"Global rho estimation error: {e}")
            return DEFAULT_FALLBACK_RHO, "fallback"

        if not league_id:
            return compute_global_rho()

        # Competition-specific estimation
        try:
            league_results = (
                db.query(HistoricalResult)
                .join(Fixture, Fixture.id == HistoricalResult.fixture_id)
                .filter(Fixture.league_id == league_id, Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
                .limit(200)
                .all()
            )
            n_league = len(league_results)

            if n_league >= 30:
                raw_rho = cls._estimate_rho_from_matches(league_results)
                return _clamp(raw_rho, MIN_RHO, MAX_RHO), "competition"
            elif n_league >= 10:
                raw_comp = cls._estimate_rho_from_matches(league_results)
                glob_rho, _ = compute_global_rho()
                weight = n_league / 30.0
                shrunk_rho = _shrink_to_prior(raw_comp, glob_rho, weight)
                return _clamp(shrunk_rho, MIN_RHO, MAX_RHO), "shrunk_competition"
            else:
                return compute_global_rho()
        except Exception as e:
            logger.warning(f"Competition rho resolution failed for league {league_id}: {e}. Falling back to default.")
            return compute_global_rho()

    @staticmethod
    def _estimate_rho_from_matches(results: List[HistoricalResult]) -> float:
        """
        Estimates the Dixon-Coles rho parameter via empirical low-score correlation frequencies.
        tau(1,1) = 1 - rho  =>  rho ~ 1 - (obs_11 / exp_11)
        tau(0,0) = 1 - lambda_h * lambda_a * rho
        """
        n = len(results)
        if n == 0:
            return DEFAULT_FALLBACK_RHO

        home_goals = [r.home_score for r in results]
        away_goals = [r.away_score for r in results]
        mean_h = max(0.5, sum(home_goals) / float(n))
        mean_a = max(0.5, sum(away_goals) / float(n))

        n_00 = sum(1 for r in results if r.home_score == 0 and r.away_score == 0)
        n_11 = sum(1 for r in results if r.home_score == 1 and r.away_score == 1)
        n_10 = sum(1 for r in results if r.home_score == 1 and r.away_score == 0)
        n_01 = sum(1 for r in results if r.home_score == 0 and r.away_score == 1)

        exp_00 = n * math.exp(-mean_h - mean_a)
        exp_11 = n * (mean_h * math.exp(-mean_h)) * (mean_a * math.exp(-mean_a))
        exp_10 = n * (mean_h * math.exp(-mean_h)) * (math.exp(-mean_a))
        exp_01 = n * (math.exp(-mean_h)) * (mean_a * math.exp(-mean_a))

        rho_11 = 1.0 - (n_11 / max(1.0, exp_11)) if exp_11 > 0 else DEFAULT_FALLBACK_RHO
        rho_00 = (1.0 - (n_00 / max(1.0, exp_00))) / max(0.25, mean_h * mean_a) if exp_00 > 0 else DEFAULT_FALLBACK_RHO
        rho_10 = ((n_10 / max(1.0, exp_10)) - 1.0) / max(0.5, mean_h) if exp_10 > 0 else DEFAULT_FALLBACK_RHO
        rho_01 = ((n_01 / max(1.0, exp_01)) - 1.0) / max(0.5, mean_a) if exp_01 > 0 else DEFAULT_FALLBACK_RHO

        # Composite weighted estimate
        composite = 0.40 * rho_11 + 0.30 * rho_00 + 0.15 * rho_10 + 0.15 * rho_01
        return round(_clamp(composite, MIN_RHO, MAX_RHO), 4)

    @staticmethod
    def _dixon_coles_tau(x: int, y: int, lambda_h: float, lambda_a: float, rho: float = DEFAULT_FALLBACK_RHO) -> float:
        """
        Calculates the Dixon-Coles tau adjustment parameter for low scorelines with positivity safeguards.
        Guarantees tau >= 0.001 to prevent negative probabilities or division anomalies.
        """
        safe_rho = _clamp(rho, MIN_RHO, MAX_RHO)
        if x == 0 and y == 0:
            return max(0.001, 1.0 - (lambda_h * lambda_a * safe_rho))
        elif x == 1 and y == 0:
            return max(0.001, 1.0 + (lambda_h * safe_rho))
        elif x == 0 and y == 1:
            return max(0.001, 1.0 + (lambda_a * safe_rho))
        elif x == 1 and y == 1:
            return max(0.001, 1.0 - safe_rho)
        return 1.0

    @staticmethod
    def resolve_team_ratings(team: Optional[Team]) -> Tuple[float, float, float, float]:
        """Derives mild team strength priors based on club tier and deterministic seed variance."""
        if not team or not team.name:
            return 1.0, 1.0, 1.0, 1.0

        name = team.name.lower()
        seed_att = sum(ord(c) * (i * 3 + 1) for i, c in enumerate(team.name))
        seed_def = sum(ord(c) * (i * 5 + 2) for i, c in enumerate(team.name[::-1]))

        if any(e in name for e in ["real madrid", "barcelona", "bayern", "manchester city", "arsenal", "liverpool", "psg", "inter", "juventus", "milan", "napoli", "dortmund", "leverkusen", "atletico"]):
            base_att, base_def = 1.10, 0.94
        elif any(e in name for e in ["chelsea", "tottenham", "manchester united", "sevilla", "leipzig", "roma", "lazio", "fiorentina", "villarreal", "betis", "flamengo", "palmeiras", "river plate", "boca", "benfica", "porto", "sporting", "ajax", "psv", "feyenoord"]):
            base_att, base_def = 1.05, 0.97
        else:
            base_att, base_def = 1.00, 1.00

        att_var = 0.90 + ((seed_att % 21) / 100.0)
        def_var = 0.90 + ((seed_def % 21) / 100.0)

        h_att = round(base_att * att_var, 3)
        h_def = round(base_def * def_var, 3)
        a_att = round(base_att * att_var * 0.97, 3)
        a_def = round(base_def * def_var * 1.03, 3)

        return h_att, h_def, a_att, a_def

    @classmethod
    def calculate_confidence_details(
        cls, db: Optional[Session], home_team_id: Optional[int], away_team_id: Optional[int], league_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Computes deterministic multi-factor model confidence metrics (0-100):
        - data_quality: sample size, match recency, team history completeness
        - model_stability: Elo divergence, form streak variance, league density
        - overall: combined weighted confidence score
        - sample_quality: categorical label
        """
        if not db or not home_team_id or not away_team_id:
            return {
                "overall": 50,
                "data_quality": 50,
                "model_stability": 50,
                "sample_quality": "moderate"
            }

        try:
            home_stats = db.query(TeamStatistics).filter(TeamStatistics.team_id == home_team_id).first()
            away_stats = db.query(TeamStatistics).filter(TeamStatistics.team_id == away_team_id).first()

            h_matches = (home_stats.matches_analyzed_home + home_stats.matches_analyzed_away) if home_stats else 0
            a_matches = (away_stats.matches_analyzed_home + away_stats.matches_analyzed_away) if away_stats else 0
            total_matches = h_matches + a_matches

            if total_matches >= 16:
                dq_base = 78
            elif total_matches >= 10:
                dq_base = 68
            elif total_matches >= 6:
                dq_base = 54
            elif total_matches >= 2:
                dq_base = 38
            else:
                dq_base = 22

            home_elo = db.query(EloRating).filter(EloRating.team_id == home_team_id).first()
            away_elo = db.query(EloRating).filter(EloRating.team_id == away_team_id).first()
            min_elo_m = min(home_elo.matches_played if home_elo else 0, away_elo.matches_played if away_elo else 0)

            if min_elo_m >= 10:
                dq_base += 12
            elif min_elo_m >= 4:
                dq_base += 7
            elif min_elo_m >= 1:
                dq_base += 3

            home_streak = db.query(TeamFormStreak).filter(TeamFormStreak.team_id == home_team_id).first()
            away_streak = db.query(TeamFormStreak).filter(TeamFormStreak.team_id == away_team_id).first()
            if home_streak and away_streak:
                dq_base += 8

            data_quality = int(round(_clamp(dq_base, 15, 98)))

            stab_base = 72
            if league_id:
                l_stats = db.query(LeagueStatistics).filter(LeagueStatistics.league_id == league_id).first()
                tot_l_matches = l_stats.total_matches_analyzed if l_stats else 0
                if tot_l_matches >= 30:
                    stab_base += 12
                elif tot_l_matches >= 10:
                    stab_base += 6
                elif tot_l_matches < 3:
                    stab_base -= 12

            if home_streak and home_stats and home_stats.avg_home_goals_scored > 0:
                recent_avg = home_streak.goals_scored_last_5 / 5.0
                if abs(recent_avg - home_stats.avg_home_goals_scored) > 1.8:
                    stab_base -= 8

            if away_streak and away_stats and away_stats.avg_away_goals_scored > 0:
                recent_avg = away_streak.goals_scored_last_5 / 5.0
                if abs(recent_avg - away_stats.avg_away_goals_scored) > 1.8:
                    stab_base -= 8

            model_stability = int(round(_clamp(stab_base, 15, 98)))
            overall = int(round(0.50 * data_quality + 0.50 * model_stability))

            if overall >= 85:
                sample_quality = "strong"
            elif overall >= 70:
                sample_quality = "good"
            elif overall >= 55:
                sample_quality = "moderate"
            elif overall >= 40:
                sample_quality = "low"
            else:
                sample_quality = "insufficient"

            return {
                "overall": overall,
                "data_quality": data_quality,
                "model_stability": model_stability,
                "sample_quality": sample_quality
            }
        except Exception as e:
            logger.error(f"Error calculating confidence details: {e}")
            return {
                "overall": 50,
                "data_quality": 50,
                "model_stability": 50,
                "sample_quality": "moderate"
            }

    @classmethod
    def calculate_confidence_score(cls, db: Session, home_team_id: int, away_team_id: int) -> float:
        """Legacy compatibility helper returning normalized 0.0-1.0 confidence float."""
        details = cls.calculate_confidence_details(db, home_team_id, away_team_id)
        return round(details["overall"] / 100.0, 2)

    @classmethod
    def calculate_xg(
        cls, db: Session, home_team_id: int, away_team_id: int, league_id: int, target_date: Optional[datetime] = None, venue: Optional[str] = None
    ) -> Tuple[float, float, float]:
        """Calculates expected goals for home team, away team, and total match xG."""
        home_team = db.query(Team).filter(Team.id == home_team_id).first()
        away_team = db.query(Team).filter(Team.id == away_team_id).first()

        home_stats = db.query(TeamStatistics).filter(TeamStatistics.team_id == home_team_id).first()
        if not home_stats:
            home_stats = calculate_team_statistics(db, home_team_id)

        away_stats = db.query(TeamStatistics).filter(TeamStatistics.team_id == away_team_id).first()
        if not away_stats:
            away_stats = calculate_team_statistics(db, away_team_id)

        league_stats = db.query(LeagueStatistics).filter(LeagueStatistics.league_id == league_id).first()
        if not league_stats:
            league_stats = calculate_league_statistics(db, league_id)

        league_avg_home = cast(float, league_stats.avg_home_goals) if league_stats and league_stats.avg_home_goals > 0 else 1.45
        league_avg_away = cast(float, league_stats.avg_away_goals) if league_stats and league_stats.avg_away_goals > 0 else 1.15

        def_h_att, def_h_def, _, _ = cls.resolve_team_ratings(home_team)
        _, _, def_a_att, def_a_def = cls.resolve_team_ratings(away_team)

        n_home_m = int(home_stats.matches_analyzed_home) if home_stats else 0
        n_away_a = int(away_stats.matches_analyzed_away) if away_stats else 0

        if n_home_m < 2:
            def_h_att = _clamp(def_h_att, 0.95, 1.05)
            def_h_def = _clamp(def_h_def, 0.95, 1.05)
        if n_away_a < 2:
            def_a_att = _clamp(def_a_att, 0.95, 1.05)
            def_a_def = _clamp(def_a_def, 0.95, 1.05)

        w_h_att = min(1.0, n_home_m / 5.0)
        w_h_def = min(1.0, n_home_m / 5.0)
        w_a_att = min(1.0, n_away_a / 5.0)
        w_a_def = min(1.0, n_away_a / 5.0)

        home_attack = _shrink_to_prior(cast(float, home_stats.home_attack_strength) if home_stats else 1.0, def_h_att, w_h_att)
        home_defense = _shrink_to_prior(cast(float, home_stats.home_defense_strength) if home_stats else 1.0, def_h_def, w_h_def)
        away_attack = _shrink_to_prior(cast(float, away_stats.away_attack_strength) if away_stats else 1.0, def_a_att, w_a_att)
        away_defense = _shrink_to_prior(cast(float, away_stats.away_defense_strength) if away_stats else 1.0, def_a_def, w_a_def)

        raw_home = home_attack * away_defense * league_avg_home
        raw_away = away_attack * home_defense * league_avg_away

        # Venue Goal Multiplier
        if venue and venue.strip():
            try:
                venue_results = (
                    db.query(HistoricalResult)
                    .join(Fixture, Fixture.id == HistoricalResult.fixture_id)
                    .filter(Fixture.venue == venue, Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
                    .all()
                )
                tot_league_avg = league_avg_home + league_avg_away
                if len(venue_results) >= 2 and tot_league_avg > 0:
                    v_avg = sum(res.total_goals for res in venue_results) / len(venue_results)
                    raw_v_mult = _clamp(v_avg / tot_league_avg, 0.85, 1.20)
                    v_mult = 0.5 + 0.5 * raw_v_mult
                    raw_home *= v_mult
                    raw_away *= v_mult
            except Exception:
                pass

        # Form adjustment
        home_streak = db.query(TeamFormStreak).filter(TeamFormStreak.team_id == home_team_id).first()
        away_streak = db.query(TeamFormStreak).filter(TeamFormStreak.team_id == away_team_id).first()
        if home_streak:
            h_scored_m, h_conceded_m = _form_goal_multipliers(home_streak, league_avg_home, league_avg_away)
            raw_home *= (0.5 + 0.5 * h_scored_m)
            raw_away *= (0.5 + 0.5 * h_conceded_m)
        if away_streak:
            a_scored_m, a_conceded_m = _form_goal_multipliers(away_streak, league_avg_away, league_avg_home)
            raw_away *= (0.5 + 0.5 * a_scored_m)
            raw_home *= (0.5 + 0.5 * a_conceded_m)

        # Elo adjustment
        try:
            home_elo_obj = EloRatingService.get_or_create_elo(db, home_team_id)
            away_elo_obj = EloRatingService.get_or_create_elo(db, away_team_id)
            elo_diff = (home_elo_obj.rating + EloRatingService.HOME_ADVANTAGE) - away_elo_obj.rating
            min_elo_matches = min(home_elo_obj.matches_played, away_elo_obj.matches_played)
            elo_weight = min(1.0, min_elo_matches / 15.0)
            goal_margin = _clamp((elo_diff / 400.0) * 1.6, -1.2, 1.2) * elo_weight
            raw_home = max(0.4, raw_home + goal_margin / 2.0)
            raw_away = max(0.3, raw_away - goal_margin / 2.0)
        except Exception:
            pass

        # H2H goal prior
        try:
            h2h = HeadToHeadService.get_h2h_goal_stats(db, home_team_id, away_team_id, limit=10)
            h2h_n = int(h2h.get("total_matches") or 0)
            h2h_avg_total = float(h2h.get("avg_total_goals") or 0.0)
            if h2h_n >= 2 and h2h_avg_total > 0:
                h2h_weight = min(0.25, h2h_n * 0.05)
                target_total = raw_home + raw_away
                if target_total > 0:
                    scale = (h2h_weight * h2h_avg_total + (1.0 - h2h_weight) * target_total) / target_total
                    raw_home *= scale
                    raw_away *= scale
        except Exception:
            pass

        raw_home = _clamp(raw_home, 0.6, 2.5)
        raw_away = _clamp(raw_away, 0.4, 2.2)

        tot_xg = raw_home + raw_away
        if tot_xg < 1.8:
            scale = 1.8 / tot_xg
            raw_home *= scale
            raw_away *= scale
        elif tot_xg > 4.0:
            scale = 4.0 / tot_xg
            raw_home *= scale
            raw_away *= scale

        expected_home_goals = round(raw_home, 2)
        expected_away_goals = round(raw_away, 2)
        expected_total_goals = round(expected_home_goals + expected_away_goals, 2)

        return expected_home_goals, expected_away_goals, expected_total_goals

    @classmethod
    def calculate_score_matrix(
        cls, lambda_home: float, lambda_away: float, max_goals: Optional[int] = None, rho: float = DEFAULT_FALLBACK_RHO
    ) -> Tuple[List[List[float]], int, Dict[str, Any]]:
        """
        Adaptive Score Matrix Grid Expansion:
        Calculates bivariate Dixon-Coles probabilities and iteratively expands score grid size
        (from MIN_SCORE_GRID=10 up to MAX_SCORE_GRID=25) until raw captured probability mass >= 0.999.
        Only normalizes after capturing target probability mass to eliminate float rounding errors.

        Returns (normalized_matrix, final_grid_size, diagnostics).
        """
        lambda_h = _clamp(lambda_home, 0.05, 10.0)
        lambda_a = _clamp(lambda_away, 0.05, 10.0)
        safe_rho = _clamp(rho, MIN_RHO, MAX_RHO)

        # Initial dynamic grid estimate
        initial_max = max(lambda_h, lambda_a)
        calc_initial = int(math.ceil(initial_max + 4.5 * math.sqrt(initial_max)))
        grid_size = max(MIN_SCORE_GRID, calc_initial)
        if max_goals is not None and max_goals > grid_size:
            grid_size = max_goals
        grid_size = min(grid_size, MAX_SCORE_GRID)

        raw_matrix: List[List[float]] = []
        captured_mass = 0.0

        # Iterative expansion loop
        while True:
            home_pmf = [_poisson_pmf(i, lambda_h) for i in range(grid_size)]
            away_pmf = [_poisson_pmf(j, lambda_a) for j in range(grid_size)]

            raw_matrix = [[0.0 for _ in range(grid_size)] for _ in range(grid_size)]
            captured_mass = 0.0

            for i in range(grid_size):
                for j in range(grid_size):
                    tau = cls._dixon_coles_tau(i, j, lambda_h, lambda_a, rho=safe_rho)
                    p = max(0.0, home_pmf[i] * away_pmf[j] * tau)
                    raw_matrix[i][j] = p
                    captured_mass += p

            # Check if target captured mass (0.999) is met or max grid is reached
            if captured_mass >= TARGET_CAPTURED_MASS or grid_size >= MAX_SCORE_GRID:
                break
            grid_size = min(MAX_SCORE_GRID, grid_size + 3)

        tail_mass = max(0.0, 1.0 - captured_mass)
        target_met = captured_mass >= TARGET_CAPTURED_MASS

        if not target_met:
            logger.warning(
                f"Score matrix adaptive expansion hit MAX_SCORE_GRID={MAX_SCORE_GRID} with captured_mass={captured_mass:.5f} (tail_mass={tail_mass:.5f}, target={TARGET_CAPTURED_MASS})"
            )

        # Exact normalization after capturing >= 99.9% probability mass
        norm_factor = captured_mass if captured_mass > 0 else 1.0
        normalized_matrix = [[raw_matrix[i][j] / norm_factor for j in range(grid_size)] for i in range(grid_size)]

        diagnostics = {
            "captured_mass": round(captured_mass, 6),
            "tail_mass": round(tail_mass, 6),
            "grid_size": grid_size,
            "target_met": target_met,
            "rho_used": safe_rho
        }

        return normalized_matrix, grid_size, diagnostics

    @classmethod
    def derive_markets_from_matrix(cls, matrix: List[List[float]], grid_size: int) -> Dict[str, Any]:
        """
        Mathematically derives all prediction markets directly from the normalized score matrix.
        Guarantees exact sum-to-1 consistency across mutually exclusive probability sets.
        """
        # 1. 1X2 Result Markets
        p_home_win = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size) if i > j)
        p_draw = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size) if i == j)
        p_away_win = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size) if i < j)

        # 2. Total Goals Over / Under Markets (0.5, 1.5, 2.5, 3.5, 4.5)
        p_o05 = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size) if i + j >= 1)
        p_o15 = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size) if i + j >= 2)
        p_o25 = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size) if i + j >= 3)
        p_o35 = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size) if i + j >= 4)
        p_o45 = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size) if i + j >= 5)

        # 3. Both Teams To Score (BTTS)
        p_btts_yes = sum(matrix[i][j] for i in range(grid_size) for j in range(grid_size) if i >= 1 and j >= 1)
        p_btts_no = 1.0 - p_btts_yes

        # 4. Home Team Goals Over / Under
        p_h_o05 = sum(matrix[i][j] for i in range(1, grid_size) for j in range(grid_size))
        p_h_o15 = sum(matrix[i][j] for i in range(2, grid_size) for j in range(grid_size))
        p_h_o25 = sum(matrix[i][j] for i in range(3, grid_size) for j in range(grid_size))

        # 5. Away Team Goals Over / Under
        p_a_o05 = sum(matrix[i][j] for i in range(grid_size) for j in range(1, grid_size))
        p_a_o15 = sum(matrix[i][j] for i in range(grid_size) for j in range(2, grid_size))
        p_a_o25 = sum(matrix[i][j] for i in range(grid_size) for j in range(3, grid_size))

        # 6. Exact Scorelines
        exact_scores = []
        for i in range(grid_size):
            for j in range(grid_size):
                prob = matrix[i][j]
                if prob > 0.0001:
                    exact_scores.append({
                        "home": i,
                        "away": j,
                        "score": f"{i}-{j}",
                        "probability": round(prob, 4)
                    })
        exact_scores.sort(key=lambda x: x["probability"], reverse=True)
        most_likely_score = exact_scores[0]["score"] if exact_scores else "1-1"

        return {
            "result": {
                "home_win": round(p_home_win, 4),
                "draw": round(p_draw, 4),
                "away_win": round(p_away_win, 4)
            },
            "goals": {
                "over_0_5": round(p_o05, 4),
                "under_0_5": round(1.0 - p_o05, 4),
                "over_1_5": round(p_o15, 4),
                "under_1_5": round(1.0 - p_o15, 4),
                "over_2_5": round(p_o25, 4),
                "under_2_5": round(1.0 - p_o25, 4),
                "over_3_5": round(p_o35, 4),
                "under_3_5": round(1.0 - p_o35, 4),
                "over_4_5": round(p_o45, 4)
            },
            "btts": {
                "yes": round(p_btts_yes, 4),
                "no": round(p_btts_no, 4)
            },
            "home_team_goals": {
                "over_0_5": round(p_h_o05, 4),
                "under_0_5": round(1.0 - p_h_o05, 4),
                "over_1_5": round(p_h_o15, 4),
                "under_1_5": round(1.0 - p_h_o15, 4),
                "over_2_5": round(p_h_o25, 4),
                "under_2_5": round(1.0 - p_h_o25, 4)
            },
            "away_team_goals": {
                "over_0_5": round(p_a_o05, 4),
                "under_0_5": round(1.0 - p_a_o05, 4),
                "over_1_5": round(p_a_o15, 4),
                "under_1_5": round(1.0 - p_a_o15, 4),
                "over_2_5": round(p_a_o25, 4),
                "under_2_5": round(1.0 - p_a_o25, 4)
            },
            "most_likely_score": most_likely_score,
            "exact_scores": exact_scores
        }

    @classmethod
    def determine_best_market_signal(
        cls, markets: Dict[str, Any], confidence: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluates whitelisted candidate markets against confidence thresholds.
        Trivial markets (Over 0.5, Home Over 0.5, Away Over 0.5) are strictly excluded from selection.

        Formula:
        signal_score = round(0.40 * (prob * 100) + 0.30 * confidence.overall + 0.15 * data_quality + 0.15 * model_stability)

        Returns explicit no-signal structure if confidence is inadequate or no market qualifies.
        """
        conf_overall = confidence.get("overall", 50)
        data_qual = confidence.get("data_quality", 50)
        model_stab = confidence.get("model_stability", 50)

        # Check minimum model validity thresholds
        if (
            conf_overall < MIN_SIGNAL_CONFIDENCE_OVERALL or
            data_qual < MIN_SIGNAL_DATA_QUALITY or
            model_stab < MIN_SIGNAL_MODEL_STABILITY
        ):
            return {
                "market": None,
                "probability": 0.0,
                "signal_score": 0,
                "label": "Watch"
            }

        best_market: Optional[str] = None
        best_prob = 0.0
        best_score = 0

        for m_name, cat_key, sub_key, min_prob in SIGNAL_CANDIDATE_MARKETS:
            prob = markets.get(cat_key, {}).get(sub_key, 0.0)
            if prob < min_prob:
                continue

            raw_score = (0.40 * (prob * 100.0)) + (0.30 * conf_overall) + (0.15 * data_qual) + (0.15 * model_stab)
            signal_score = int(round(_clamp(raw_score, 0.0, 99.0)))

            if signal_score > best_score:
                best_score = signal_score
                best_market = m_name
                best_prob = prob

        if not best_market or best_score < MIN_SIGNAL_QUALIFYING_SCORE:
            return {
                "market": None,
                "probability": 0.0,
                "signal_score": 0,
                "label": "Watch"
            }

        label = "Strong" if best_score >= 80 else ("Moderate" if best_score >= 65 else "Watch")

        return {
            "market": best_market,
            "probability": round(best_prob, 4),
            "signal_score": best_score,
            "label": label
        }

    @classmethod
    def calculate_poisson_probabilities(
        cls, lambda_home: float, lambda_away: float, max_goals: int = 10, rho: Optional[float] = None,
        overdispersion_r: Optional[float] = None, db: Optional[Session] = None,
        home_team_id: Optional[int] = None, away_team_id: Optional[int] = None, league_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Unified Match Intelligence calculator with adaptive score grid and hierarchical rho strategy.
        """
        # Resolve rho strategy
        if rho is not None:
            safe_rho = _clamp(rho, MIN_RHO, MAX_RHO)
            rho_source = "custom"
        else:
            safe_rho, rho_source = cls.resolve_rho_strategy(db, league_id)

        matrix, grid_size, tail_diag = cls.calculate_score_matrix(lambda_home, lambda_away, max_goals=max_goals, rho=safe_rho)
        derived = cls.derive_markets_from_matrix(matrix, grid_size)

        # Half-by-Half goal predictions with exact sum consistency
        half_props = HalfPredictionEngine.calculate_half_proportions(db, league_id, home_team_id, away_team_id)
        halves = HalfPredictionEngine.calculate_half_probabilities(lambda_home, lambda_away, half_props)

        # Confidence system
        confidence = cls.calculate_confidence_details(db, home_team_id, away_team_id, league_id)

        # Best Model Signal
        best_signal = cls.determine_best_market_signal(derived, confidence)

        res_dict = {
            # Legacy backward-compatible keys
            "home_win_probability": derived["result"]["home_win"],
            "draw_probability": derived["result"]["draw"],
            "away_win_probability": derived["result"]["away_win"],
            "over_0_5_probability": derived["goals"]["over_0_5"],
            "over_1_5_probability": derived["goals"]["over_1_5"],
            "over_2_5_probability": derived["goals"]["over_2_5"],
            "over_3_5_probability": derived["goals"]["over_3_5"],
            "over_4_5_probability": derived["goals"]["over_4_5"],
            "under_2_5_probability": derived["goals"]["under_2_5"],
            "btts_probability": derived["btts"]["yes"],
            "home_over_0_5_probability": derived["home_team_goals"]["over_0_5"],
            "home_over_1_5_probability": derived["home_team_goals"]["over_1_5"],
            "home_over_2_5_probability": derived["home_team_goals"]["over_2_5"],
            "away_over_0_5_probability": derived["away_team_goals"]["over_0_5"],
            "away_over_1_5_probability": derived["away_team_goals"]["over_1_5"],
            "away_over_2_5_probability": derived["away_team_goals"]["over_2_5"],
            "first_half_xg": halves["first_half_xg"],
            "first_half_over_0_5_probability": halves["first_half_over_0_5"],
            "first_half_over_1_5_probability": halves["first_half_over_1_5"],
            "second_half_xg": halves["second_half_xg"],
            "second_half_over_0_5_probability": halves["second_half_over_0_5"],
            "second_half_over_1_5_probability": halves["second_half_over_1_5"],
            "most_likely_score": derived["most_likely_score"],
            "top_5_scorelines": derived["exact_scores"][:5],

            # Match Intelligence Core unified schema
            "model": {
                "version": MODEL_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "rho": safe_rho,
                "rho_source": rho_source
            },
            "expected_goals": {
                "home": round(lambda_home, 2),
                "away": round(lambda_away, 2),
                "total": round(lambda_home + lambda_away, 2)
            },
            "result": derived["result"],
            "goals": derived["goals"],
            "btts": derived["btts"],
            "home_team_goals": derived["home_team_goals"],
            "away_team_goals": derived["away_team_goals"],
            "halves": halves,
            "exact_scores": derived["exact_scores"][:10],
            "confidence": confidence,
            "best_signal": best_signal,
            "diagnostics": {
                "score_matrix": tail_diag
            }
        }

        return res_dict

    @classmethod
    def generate_match_intelligence_prediction(
        cls, db: Session, fixture_id: int
    ) -> Optional[Dict[str, Any]]:
        """Generates the complete validated Match Intelligence Core prediction payload for a fixture."""
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return None

        match_date = getattr(fixture, "match_date", None)
        venue_str = getattr(fixture, "venue", None)
        xg_home, xg_away, xg_total = cls.calculate_xg(
            db, cast(int, fixture.home_team_id), cast(int, fixture.away_team_id), cast(int, fixture.league_id), target_date=match_date, venue=venue_str
        )

        weather_info = WeatherService.get_weather_for_venue(venue_str)
        if weather_info and weather_info.get("xg_modifier"):
            xg_home = round(xg_home * weather_info["xg_modifier"], 2)
            xg_away = round(xg_away * weather_info["xg_modifier"], 2)
            xg_total = round(xg_home + xg_away, 2)

        overdispersion_r = _calculate_overdispersion_parameter(db, cast(int, fixture.league_id))
        probs = cls.calculate_poisson_probabilities(
            xg_home, xg_away, overdispersion_r=overdispersion_r,
            db=db, home_team_id=cast(int, fixture.home_team_id),
            away_team_id=cast(int, fixture.away_team_id),
            league_id=cast(int, fixture.league_id)
        )

        # Corners Prediction Engine integration (Phase 2)
        try:
            from services.corners_service import CornersPredictionEngine
            corners_payload = CornersPredictionEngine.predict_corners(db, fixture_id, target_date=match_date)
        except Exception as e:
            logger.debug(f"Corners prediction generation error: {e}")
            corners_payload = {
                "available": False,
                "reason": "Corner prediction service unavailable.",
                "expected": None,
                "total_markets": None,
                "home_team": None,
                "away_team": None,
                "confidence": {"overall": 0, "data_quality": 0, "sample_strength": 0, "model_stability": 0, "label": "insufficient"},
                "model": {"version": "v1_corners_nb", "dispersion": 5.5, "dispersion_source": "fallback"}
            }

        full_payload = {
            "fixture_id": fixture_id,
            "model": probs["model"],
            "expected_goals": probs["expected_goals"],
            "result": probs["result"],
            "goals": probs["goals"],
            "btts": probs["btts"],
            "home_team_goals": probs["home_team_goals"],
            "away_team_goals": probs["away_team_goals"],
            "halves": probs["halves"],
            "exact_scores": probs["exact_scores"],
            "confidence": probs["confidence"],
            "best_signal": probs["best_signal"],
            "corners": corners_payload
        }

        return full_payload

    @classmethod
    def predict_fixture(cls, db: Session, fixture_id: int, commit: bool = True) -> Optional[Prediction]:
        """Calculates Dixon-Coles Match Intelligence V2 predictions for a specific fixture and saves in SQLite."""
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return None

        match_date = getattr(fixture, "match_date", None)
        venue_str = getattr(fixture, "venue", None)
        xg_home, xg_away, xg_total = cls.calculate_xg(
            db, cast(int, fixture.home_team_id), cast(int, fixture.away_team_id), cast(int, fixture.league_id), target_date=match_date, venue=venue_str
        )

        weather_info = WeatherService.get_weather_for_venue(venue_str)
        if weather_info and weather_info.get("xg_modifier"):
            xg_home = round(xg_home * weather_info["xg_modifier"], 2)
            xg_away = round(xg_away * weather_info["xg_modifier"], 2)
            xg_total = round(xg_home + xg_away, 2)

        overdispersion_r = _calculate_overdispersion_parameter(db, cast(int, fixture.league_id))
        probs = cls.calculate_poisson_probabilities(
            xg_home, xg_away, overdispersion_r=overdispersion_r,
            db=db, home_team_id=cast(int, fixture.home_team_id),
            away_team_id=cast(int, fixture.away_team_id),
            league_id=cast(int, fixture.league_id)
        )

        # Corners Prediction Engine integration (Phase 2)
        try:
            from services.corners_service import CornersPredictionEngine
            corners_payload = CornersPredictionEngine.predict_corners(db, fixture_id, target_date=match_date)
        except Exception as e:
            logger.debug(f"Corners prediction generation error: {e}")
            corners_payload = {
                "available": False,
                "reason": "Corner prediction service unavailable.",
                "expected": None,
                "total_markets": None,
                "home_team": None,
                "away_team": None,
                "confidence": {"overall": 0, "data_quality": 0, "sample_strength": 0, "model_stability": 0, "label": "insufficient"},
                "model": {"version": "v1_corners_nb", "dispersion": 5.5, "dispersion_source": "fallback"}
            }

        full_intelligence = {
            "fixture_id": fixture_id,
            "model": probs["model"],
            "expected_goals": probs["expected_goals"],
            "result": probs["result"],
            "goals": probs["goals"],
            "btts": probs["btts"],
            "home_team_goals": probs["home_team_goals"],
            "away_team_goals": probs["away_team_goals"],
            "halves": probs["halves"],
            "exact_scores": probs["exact_scores"],
            "confidence": probs["confidence"],
            "best_signal": probs["best_signal"],
            "corners": corners_payload
        }

        prediction = db.query(Prediction).filter(Prediction.fixture_id == fixture_id).first()
        if not prediction:
            prediction = Prediction(fixture_id=fixture_id)
            db.add(prediction)

        prediction.predicted_home_score = xg_home
        prediction.predicted_away_score = xg_away
        prediction.expected_goals_xg = xg_total
        prediction.home_win_probability = probs["home_win_probability"]
        prediction.draw_probability = probs["draw_probability"]
        prediction.away_win_probability = probs["away_win_probability"]
        prediction.over_0_5_probability = probs["over_0_5_probability"]
        prediction.over_1_5_probability = probs["over_1_5_probability"]
        prediction.over_2_5_probability = probs["over_2_5_probability"]
        prediction.over_3_5_probability = probs["over_3_5_probability"]
        prediction.over_4_5_probability = probs["over_4_5_probability"]
        prediction.under_2_5_probability = probs["under_2_5_probability"]
        prediction.btts_probability = probs["btts_probability"]
        prediction.confidence_score = probs["confidence"]["overall"] / 100.0
        prediction.most_likely_score = probs["most_likely_score"]
        prediction.top_scorelines_json = json.dumps(probs["top_5_scorelines"])
        prediction.model_version = MODEL_VERSION
        prediction.raw_intelligence_json = json.dumps(full_intelligence)
        prediction.created_at = datetime.now(timezone.utc)

        if commit:
            db.commit()
            db.refresh(prediction)
        else:
            db.flush()
        return prediction

    @classmethod
    def predict_all_upcoming_fixtures(cls, db: Session) -> List[Prediction]:
        """Calculates and stores Match Intelligence predictions for all non-finished fixtures in SQLite."""
        fixtures = db.query(Fixture).filter(Fixture.status != "FINISHED").all()
        predictions = []
        for f in fixtures:
            pred = cls.predict_fixture(db, cast(int, f.id), commit=False)
            if pred:
                predictions.append(pred)
        db.commit()
        for p in predictions:
            try:
                db.refresh(p)
            except Exception:
                pass
        return predictions


# Compatibility alias for backward compatibility across endpoints and services
PoissonPredictionEngine = DixonColesPredictionEngine
