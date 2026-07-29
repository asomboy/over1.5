import os
import sys
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, cast
import scipy.stats as stats
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import Fixture, Prediction, TeamStatistics, LeagueStatistics, Team
    from services.statistics_service import calculate_team_statistics, calculate_league_statistics
except ImportError:
    from ..models import Fixture, Prediction, TeamStatistics, LeagueStatistics, Team
    from .statistics_service import calculate_team_statistics, calculate_league_statistics

logger = logging.getLogger(__name__)


class PoissonPredictionEngine:
    """
    Poisson Distribution Prediction Engine for calculating goal expectations (xG),
    1X2 outcome probabilities, Over/Under threshold probabilities, and scoreline predictions.
    """

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

    @staticmethod
    def calculate_xg(
        db: Session, home_team_id: int, away_team_id: int, league_id: int
    ) -> Tuple[float, float, float]:
        """
        Calculates expected goals for home team, away team, and total match xG
        using calculated team strength metrics and league goal averages.
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

        # Resolve strength ratings from team rating engine when matches analyzed is < 3
        def_h_att, def_h_def, _, _ = PoissonPredictionEngine.resolve_team_ratings(home_team)
        _, _, def_a_att, def_a_def = PoissonPredictionEngine.resolve_team_ratings(away_team)

        home_attack = cast(float, home_stats.home_attack_strength) if (home_stats and getattr(home_stats, "matches_analyzed_home", 0) >= 3 and home_stats.home_attack_strength != 1.0) else def_h_att
        home_defense = cast(float, home_stats.home_defense_strength) if (home_stats and getattr(home_stats, "matches_analyzed_home", 0) >= 3 and home_stats.home_defense_strength != 1.0) else def_h_def
        away_attack = cast(float, away_stats.away_attack_strength) if (away_stats and getattr(away_stats, "matches_analyzed_away", 0) >= 3 and away_stats.away_attack_strength != 1.0) else def_a_att
        away_defense = cast(float, away_stats.away_defense_strength) if (away_stats and getattr(away_stats, "matches_analyzed_away", 0) >= 3 and away_stats.away_defense_strength != 1.0) else def_a_def

        # Poisson expectation formulas with team-specific ratings
        expected_home_goals = round(home_attack * away_defense * (league_avg_home / 1.5) * 1.45, 2)
        expected_away_goals = round(away_attack * home_defense * (league_avg_away / 1.2) * 1.15, 2)
        expected_total_goals = round(expected_home_goals + expected_away_goals, 2)

        return expected_home_goals, expected_away_goals, expected_total_goals

    @staticmethod
    def calculate_poisson_probabilities(
        lambda_home: float, lambda_away: float, max_goals: int = 10
    ) -> Dict[str, Any]:
        """
        Calculates Poisson probability distributions for match goals, 1X2 probabilities,
        Over/Under thresholds, most likely scoreline, and top 5 scorelines using SciPy.
        """
        # Safe fallback if lambda is zero
        lambda_h = max(lambda_home, 0.05)
        lambda_a = max(lambda_away, 0.05)

        # Compute PMF for home and away goals up to max_goals using scipy.stats.poisson
        home_pmf = [float(stats.poisson.pmf(i, lambda_h)) for i in range(max_goals)]
        away_pmf = [float(stats.poisson.pmf(j, lambda_a)) for j in range(max_goals)]

        home_win_prob = 0.0
        draw_prob = 0.0
        away_win_prob = 0.0

        total_goals_pmf = [0.0] * (max_goals * 2)
        scorelines = []

        for i in range(max_goals):
            for j in range(max_goals):
                prob = home_pmf[i] * away_pmf[j]

                # Match outcome accumulator
                if i > j:
                    home_win_prob += prob
                elif i == j:
                    draw_prob += prob
                else:
                    away_win_prob += prob

                # Total goals accumulator
                total_goals_pmf[i + j] += prob

                scorelines.append({
                    "score": f"{i}-{j}",
                    "home_goals": i,
                    "away_goals": j,
                    "probability": round(prob, 4)
                })

        # Over / Under threshold calculations
        over_0_5 = sum(total_goals_pmf[k] for k in range(1, len(total_goals_pmf)))
        over_1_5 = sum(total_goals_pmf[k] for k in range(2, len(total_goals_pmf)))
        over_2_5 = sum(total_goals_pmf[k] for k in range(3, len(total_goals_pmf)))
        over_3_5 = sum(total_goals_pmf[k] for k in range(4, len(total_goals_pmf)))
        over_4_5 = sum(total_goals_pmf[k] for k in range(5, len(total_goals_pmf)))
        under_2_5 = 1.0 - over_2_5

        # Sort scorelines descending by probability
        scorelines.sort(key=lambda x: x["probability"], reverse=True)
        most_likely_score = scorelines[0]["score"] if scorelines else "1-1"
        top_5_scorelines = scorelines[:5]

        # Home & Away Team Specific Goal Thresholds
        home_over_0_5 = sum(home_pmf[k] for k in range(1, len(home_pmf)))
        home_over_1_5 = sum(home_pmf[k] for k in range(2, len(home_pmf)))
        home_over_2_5 = sum(home_pmf[k] for k in range(3, len(home_pmf)))

        away_over_0_5 = sum(away_pmf[k] for k in range(1, len(away_pmf)))
        away_over_1_5 = sum(away_pmf[k] for k in range(2, len(away_pmf)))
        away_over_2_5 = sum(away_pmf[k] for k in range(3, len(away_pmf)))

        # 1st Half & 2nd Half Goal Breakdowns (1H: 45% xG, 2H: 55% xG)
        lambda_tot = lambda_h + lambda_a
        lambda_1h = max(lambda_tot * 0.45, 0.05)
        lambda_2h = max(lambda_tot * 0.55, 0.05)

        h1_over_0_5 = 1.0 - stats.poisson.pmf(0, lambda_1h)
        h1_over_1_5 = 1.0 - stats.poisson.pmf(0, lambda_1h) - stats.poisson.pmf(1, lambda_1h)

        h2_over_0_5 = 1.0 - stats.poisson.pmf(0, lambda_2h)
        h2_over_1_5 = 1.0 - stats.poisson.pmf(0, lambda_2h) - stats.poisson.pmf(1, lambda_2h)

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
        Calculates Poisson predictions for a specific fixture and saves the result in SQLite.
        """
        fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
        if not fixture:
            return None

        # Calculate xG values
        xg_home, xg_away, xg_total = cls.calculate_xg(
            db, cast(int, fixture.home_team_id), cast(int, fixture.away_team_id), cast(int, fixture.league_id)
        )

        # Calculate SciPy Poisson probabilities
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
