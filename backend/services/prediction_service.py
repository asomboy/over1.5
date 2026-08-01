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
    from models import Fixture, Prediction, TeamStatistics, LeagueStatistics, Team, HistoricalResult
    from services.statistics_service import calculate_team_statistics, calculate_league_statistics
except ImportError:
    from ..models import Fixture, Prediction, TeamStatistics, LeagueStatistics, Team, HistoricalResult
    from .statistics_service import calculate_team_statistics, calculate_league_statistics

logger = logging.getLogger(__name__)

def _poisson_pmf(k: int, mu: float) -> float:
    """Calculates Poisson probability mass function P(X=k) for mean mu."""
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    return (math.pow(mu, k) * math.exp(-mu)) / math.factorial(k)


class DixonColesPredictionEngine:
    """
    Dixon-Coles (1997) Football Prediction Engine V2.
    
    Includes:
    1. Low-score joint probability dependency correction factor tau(x, y, lambda, mu, rho).
    2. Exponential time-decay weighting phi(t) = exp(-xi * t) for historical matches.
    3. Dynamic rating variance for low-sample-size teams.
    4. Complete 1X2, Over/Under thresholds, half-time splits, and top scoreline calculations.
    """

    DEFAULT_RHO = -0.11  # Low-score correlation parameter for soccer draw adjustment
    XI_TIME_DECAY = 0.0035  # Time decay parameter (~6-month half-life in days)

    @staticmethod
    def _dixon_coles_tau(x: int, y: int, lambda_h: float, lambda_a: float, rho: float = DEFAULT_RHO) -> float:
        """
        Calculates the Dixon-Coles tau adjustment parameter for low scorelines.
        Adjusts probabilities for (0,0), (1,0), (0,1), and (1,1) scorelines to account
        for interdependence in low-scoring football matches.
        """
        if x == 0 and y == 0:
            return max(0.0, 1.0 - (lambda_h * lambda_a * rho))
        elif x == 1 and y == 0:
            return max(0.0, 1.0 + (lambda_h * rho))
        elif x == 0 and y == 1:
            return max(0.0, 1.0 + (lambda_a * rho))
        elif x == 1 and y == 1:
            return max(0.0, 1.0 - rho)
        return 1.0

    @staticmethod
    def resolve_team_ratings(team: Optional[Team]) -> Tuple[float, float, float, float]:
        """
        Derives realistic home_attack, home_defense, away_attack, away_defense strength ratings
        based on team tier/prestige and deterministic seed hashing when completed match history is missing.
        """
        if not team or not team.name:
            return 1.0, 1.0, 1.0, 1.0

        name = team.name.lower()
        seed_att = sum(ord(c) * (i * 3 + 1) for i, c in enumerate(team.name))
        seed_def = sum(ord(c) * (i * 5 + 2) for i, c in enumerate(team.name[::-1]))

        # Base multipliers for top clubs
        if any(e in name for e in ["real madrid", "barcelona", "bayern", "manchester city", "arsenal", "liverpool", "psg", "inter", "juventus", "milan", "napoli", "dortmund", "leverkusen", "atletico"]):
            base_att, base_def = 1.45, 0.70
        elif any(e in name for e in ["chelsea", "tottenham", "manchester united", "sevilla", "leipzig", "roma", "lazio", "fiorentina", "villarreal", "betis", "flamengo", "palmeiras", "river plate", "boca", "benfica", "porto", "sporting", "ajax", "psv", "feyenoord"]):
            base_att, base_def = 1.28, 0.82
        elif any(e in name for e in ["city", "united", "real", "athletic", "dynamo", "sporting", "racing", "club", "fc", "red bull", "monaco", "lyon", "marseille"]):
            base_att, base_def = 1.15, 0.90
        else:
            base_att, base_def = 0.95, 1.05

        # Inject wide team-specific variance (0.75 to 1.35) using deterministic team name hash
        att_var = 0.75 + ((seed_att % 61) / 100.0)
        def_var = 0.75 + ((seed_def % 57) / 100.0)

        h_att = round(base_att * att_var * 1.10, 2)
        h_def = round(base_def * def_var * 0.90, 2)
        a_att = round(base_att * att_var * 0.90, 2)
        a_def = round(base_def * def_var * 1.10, 2)

        return h_att, h_def, a_att, a_def

    @classmethod
    def calculate_xg(
        cls, db: Session, home_team_id: int, away_team_id: int, league_id: int, target_date: Optional[datetime] = None
    ) -> Tuple[float, float, float]:
        """
        Calculates expected goals for home team, away team, and total match xG
        using time-decay weighted historical performance metrics and league goal averages.
        """
        home_team = db.query(Team).filter(Team.id == home_team_id).first()
        away_team = db.query(Team).filter(Team.id == away_team_id).first()

        # Fetch or compute team & league statistics
        home_stats = db.query(TeamStatistics).filter(TeamStatistics.team_id == home_team_id).first()
        if not home_stats:
            home_stats = calculate_team_statistics(db, home_team_id)

        away_stats = db.query(TeamStatistics).filter(TeamStatistics.team_id == away_team_id).first()
        if not away_stats:
            away_stats = calculate_team_statistics(db, away_team_id)

        league_stats = db.query(LeagueStatistics).filter(LeagueStatistics.league_id == league_id).first()
        if not league_stats:
            league_stats = calculate_league_statistics(db, league_id)

        league_avg_home = cast(float, league_stats.avg_home_goals) if league_stats and league_stats.avg_home_goals > 0 else 1.5
        league_avg_away = cast(float, league_stats.avg_away_goals) if league_stats and league_stats.avg_away_goals > 0 else 1.2

        # Compute time-decay weighted ratings if historical results exist
        now_dt = target_date or datetime.now(timezone.utc)
        if now_dt.tzinfo is not None:
            now_dt = now_dt.astimezone(timezone.utc).replace(tzinfo=None)

        def _calc_weighted_rates(t_id: int, is_home: bool) -> Tuple[float, float, int]:
            query = (
                db.query(HistoricalResult, Fixture.match_date)
                .join(Fixture, Fixture.id == HistoricalResult.fixture_id)
                .filter(Fixture.home_team_id == t_id if is_home else Fixture.away_team_id == t_id)
                .order_by(Fixture.match_date.desc())
                .limit(10)
                .all()
            )
            if not query:
                return 0.0, 0.0, 0
            
            weight_sum = 0.0
            scored_weighted = 0.0
            conceded_weighted = 0.0

            for res, match_date in query:
                if match_date is None:
                    continue
                m_date = match_date.replace(tzinfo=None) if match_date.tzinfo else match_date
                days_diff = max(0.0, (now_dt - m_date).total_seconds() / 86400.0)
                weight = math.exp(-cls.XI_TIME_DECAY * days_diff)
                
                weight_sum += weight
                scored = float(res.home_score if is_home else res.away_score)
                conceded = float(res.away_score if is_home else res.home_score)
                scored_weighted += scored * weight
                conceded_weighted += conceded * weight

            if weight_sum > 0:
                return scored_weighted / weight_sum, conceded_weighted / weight_sum, len(query)
            return 0.0, 0.0, 0

        h_scored_w, h_conceded_w, h_count = _calc_weighted_rates(home_team_id, is_home=True)
        a_scored_w, a_conceded_w, a_count = _calc_weighted_rates(away_team_id, is_home=False)

        def_h_att, def_h_def, _, _ = cls.resolve_team_ratings(home_team)
        _, _, def_a_att, def_a_def = cls.resolve_team_ratings(away_team)

        if h_count >= 3 and h_scored_w > 0:
            home_attack = h_scored_w / league_avg_home if league_avg_home > 0 else def_h_att
            home_defense = h_conceded_w / league_avg_away if league_avg_away > 0 else def_h_def
        else:
            home_attack = cast(float, home_stats.home_attack_strength) if (home_stats and getattr(home_stats, "matches_analyzed_home", 0) >= 3 and home_stats.home_attack_strength != 1.0) else def_h_att
            home_defense = cast(float, home_stats.home_defense_strength) if (home_stats and getattr(home_stats, "matches_analyzed_home", 0) >= 3 and home_stats.home_defense_strength != 1.0) else def_h_def

        if a_count >= 3 and a_scored_w > 0:
            away_attack = a_scored_w / league_avg_away if league_avg_away > 0 else def_a_att
            away_defense = a_conceded_w / league_avg_home if league_avg_home > 0 else def_a_def
        else:
            away_attack = cast(float, away_stats.away_attack_strength) if (away_stats and getattr(away_stats, "matches_analyzed_away", 0) >= 3 and away_stats.away_attack_strength != 1.0) else def_a_att
            away_defense = cast(float, away_stats.away_defense_strength) if (away_stats and getattr(away_stats, "matches_analyzed_away", 0) >= 3 and away_stats.away_defense_strength != 1.0) else def_a_def

        # Expected goals formula with Dixon-Coles parameters
        expected_home_goals = round(home_attack * away_defense * (league_avg_home / 1.5) * 1.45, 2)
        expected_away_goals = round(away_attack * home_defense * (league_avg_away / 1.2) * 1.15, 2)
        expected_total_goals = round(expected_home_goals + expected_away_goals, 2)

        return expected_home_goals, expected_away_goals, expected_total_goals

    @classmethod
    def calculate_poisson_probabilities(
        cls, lambda_home: float, lambda_away: float, max_goals: int = 10, rho: float = DEFAULT_RHO
    ) -> Dict[str, Any]:
        """
        Calculates Dixon-Coles adjusted goal probabilities, 1X2 outcomes, Over/Under thresholds,
        most likely scoreline, and top scorelines.
        """
        lambda_h = max(lambda_home, 0.05)
        lambda_a = max(lambda_away, 0.05)

        home_pmf = [_poisson_pmf(i, lambda_h) for i in range(max_goals)]
        away_pmf = [_poisson_pmf(j, lambda_a) for j in range(max_goals)]

        raw_joint = []
        total_joint_prob = 0.0

        for i in range(max_goals):
            for j in range(max_goals):
                tau = cls._dixon_coles_tau(i, j, lambda_h, lambda_a, rho=rho)
                p_joint = max(0.0, home_pmf[i] * away_pmf[j] * tau)
                raw_joint.append((i, j, p_joint))
                total_joint_prob += p_joint

        if total_joint_prob <= 0:
            total_joint_prob = 1.0

        home_win_prob = 0.0
        draw_prob = 0.0
        away_win_prob = 0.0

        total_goals_pmf = [0.0] * (max_goals * 2)
        scorelines = []

        for i, j, p_raw in raw_joint:
            prob = p_raw / total_joint_prob

            if i > j:
                home_win_prob += prob
            elif i == j:
                draw_prob += prob
            else:
                away_win_prob += prob

            total_goals_pmf[i + j] += prob

            scorelines.append({
                "score": f"{i}-{j}",
                "home_goals": i,
                "away_goals": j,
                "probability": round(prob, 4)
            })

        over_0_5 = sum(total_goals_pmf[k] for k in range(1, len(total_goals_pmf)))
        over_1_5 = sum(total_goals_pmf[k] for k in range(2, len(total_goals_pmf)))
        over_2_5 = sum(total_goals_pmf[k] for k in range(3, len(total_goals_pmf)))
        over_3_5 = sum(total_goals_pmf[k] for k in range(4, len(total_goals_pmf)))
        over_4_5 = sum(total_goals_pmf[k] for k in range(5, len(total_goals_pmf)))
        under_2_5 = 1.0 - over_2_5

        scorelines.sort(key=lambda x: x["probability"], reverse=True)
        most_likely_score = scorelines[0]["score"] if scorelines else "1-1"
        top_5_scorelines = scorelines[:5]

        home_over_0_5 = sum(home_pmf[k] for k in range(1, len(home_pmf)))
        home_over_1_5 = sum(home_pmf[k] for k in range(2, len(home_pmf)))
        home_over_2_5 = sum(home_pmf[k] for k in range(3, len(home_pmf)))

        away_over_0_5 = sum(away_pmf[k] for k in range(1, len(away_pmf)))
        away_over_1_5 = sum(away_pmf[k] for k in range(2, len(away_pmf)))
        away_over_2_5 = sum(away_pmf[k] for k in range(3, len(away_pmf)))

        lambda_tot = lambda_h + lambda_a
        lambda_1h = max(lambda_tot * 0.45, 0.05)
        lambda_2h = max(lambda_tot * 0.55, 0.05)

        h1_over_0_5 = 1.0 - _poisson_pmf(0, lambda_1h)
        h1_over_1_5 = 1.0 - _poisson_pmf(0, lambda_1h) - _poisson_pmf(1, lambda_1h)

        h2_over_0_5 = 1.0 - _poisson_pmf(0, lambda_2h)
        h2_over_1_5 = 1.0 - _poisson_pmf(0, lambda_2h) - _poisson_pmf(1, lambda_2h)

        return {
            "home_win_probability": round(home_win_prob, 4),
            "draw_probability": round(draw_prob, 4),
            "away_win_probability": round(away_win_prob, 4),
            "over_0_5_probability": round(over_0_5, 4),
            "over_1_5_probability": round(over_1_5, 4),
            "over_2_5_probability": round(over_2_5, 4),
            "over_3_5_probability": round(over_3_5, 4),
            "over_4_5_probability": round(over_4_5, 4),
            "under_2_5_probability": round(under_2_5, 4),
            "home_over_0_5_probability": round(home_over_0_5, 4),
            "home_over_1_5_probability": round(home_over_1_5, 4),
            "home_over_2_5_probability": round(home_over_2_5, 4),
            "away_over_0_5_probability": round(away_over_0_5, 4),
            "away_over_1_5_probability": round(away_over_1_5, 4),
            "away_over_2_5_probability": round(away_over_2_5, 4),
            "first_half_xg": round(lambda_1h, 2),
            "first_half_over_0_5_probability": round(float(h1_over_0_5), 4),
            "first_half_over_1_5_probability": round(float(h1_over_1_5), 4),
            "second_half_xg": round(lambda_2h, 2),
            "second_half_over_0_5_probability": round(float(h2_over_0_5), 4),
            "second_half_over_1_5_probability": round(float(h2_over_1_5), 4),
            "most_likely_score": most_likely_score,
            "top_5_scorelines": top_5_scorelines
        }

    @classmethod
    def predict_fixture(cls, db: Session, fixture_id: int) -> Optional[Prediction]:
        """
        Calculates Dixon-Coles V2 predictions for a specific fixture and saves the result in SQLite.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return None

        # Calculate xG values with time decay
        match_date = getattr(fixture, "match_date", None)
        xg_home, xg_away, xg_total = cls.calculate_xg(
            db, cast(int, fixture.home_team_id), cast(int, fixture.away_team_id), cast(int, fixture.league_id), target_date=match_date
        )

        # Calculate Dixon-Coles adjusted probabilities
        probs = cls.calculate_poisson_probabilities(xg_home, xg_away)

        # Check existing Prediction or create new entry
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
        prediction.most_likely_score = probs["most_likely_score"]
        prediction.top_scorelines_json = json.dumps(probs["top_5_scorelines"])
        prediction.created_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(prediction)
        return prediction

    @classmethod
    def predict_all_upcoming_fixtures(cls, db: Session) -> List[Prediction]:
        """
        Calculates and stores predictions for all non-finished fixtures in the database.
        """
        fixtures = db.query(Fixture).filter(Fixture.status != "FINISHED").all()
        predictions = []
        for f in fixtures:
            pred = cls.predict_fixture(db, cast(int, f.id))
            if pred:
                predictions.append(pred)
        return predictions


# Compatibility alias for backward compatibility across endpoints and services
PoissonPredictionEngine = DixonColesPredictionEngine
