import os
import sys
import json
import math
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict, Any
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import Team, Fixture, HistoricalResult, EloRating, TeamFormStreak, TeamStatistics, LeagueStatistics
except ImportError:
    from ..models import Team, Fixture, HistoricalResult, EloRating, TeamFormStreak, TeamStatistics, LeagueStatistics

logger = logging.getLogger(__name__)


class EloRatingService:
    """
    Elo Rating Service for football team strength estimation.
    
    Based on the Elo rating system used by FIFA and FiveThirtyEight:
    1. Initial rating: 1500 for all teams
    2. K-factor: 40 for teams with < 30 matches, 20 for established teams
    3. Home advantage: +50 Elo points for the home team
    4. Expected score: E = 1 / (1 + 10^((Rb - Ra) / 400))
    5. Rating update: Rn = Ro + K * (S - E)
    """

    DEFAULT_RATING = 1500.0
    HOME_ADVANTAGE = 50.0
    K_FACTOR_NEW = 40.0      # Teams with < 30 matches
    K_FACTOR_ESTABLISHED = 20.0  # Teams with >= 30 matches

    @classmethod
    def get_or_create_elo(cls, db: Session, team_id: int) -> EloRating:
        """Retrieve or create EloRating entry for a team."""
        elo = db.query(EloRating).filter(EloRating.team_id == team_id).first()
        if not elo:
            elo = EloRating(team_id=team_id, rating=cls.DEFAULT_RATING)
            db.add(elo)
            db.flush()
        return elo

    @classmethod
    def calculate_expected_score(cls, rating_a: float, rating_b: float) -> float:
        """Calculate expected score (win probability) for team A against team B."""
        return 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / 400.0))

    @classmethod
    def get_k_factor(cls, elo: EloRating) -> float:
        """Return K-factor based on team experience."""
        return cls.K_FACTOR_NEW if elo.matches_played < 30 else cls.K_FACTOR_ESTABLISHED

    @classmethod
    def update_elo_for_fixture(
        cls,
        db: Session,
        fixture_id: int,
        home_team_id: int,
        away_team_id: int,
        home_score: int,
        away_score: int,
        commit: bool = True
    ) -> Tuple[EloRating, EloRating]:
        """
        Update Elo ratings after a match result.
        Returns (home_elo, away_elo) after update.
        """
        home_elo = cls.get_or_create_elo(db, home_team_id)
        away_elo = cls.get_or_create_elo(db, away_team_id)

        # Determine actual scores (1.0 for win, 0.5 for draw, 0.0 for loss)
        if home_score > away_score:
            home_actual = 1.0
            away_actual = 0.0
            home_elo.home_wins += 1
            away_elo.away_wins += 1
        elif home_score < away_score:
            home_actual = 0.0
            away_actual = 1.0
            away_elo.away_wins += 1
        else:
            home_actual = 0.5
            away_actual = 0.5
            home_elo.draws += 1
            away_elo.draws += 1

        # Apply home advantage
        home_rating_adj = home_elo.rating + cls.HOME_ADVANTAGE
        away_rating_adj = away_elo.rating

        # Calculate expected scores
        home_expected = cls.calculate_expected_score(home_rating_adj, away_rating_adj)
        away_expected = cls.calculate_expected_score(away_rating_adj, home_rating_adj)

        # Get K-factors
        home_k = cls.get_k_factor(home_elo)
        away_k = cls.get_k_factor(away_elo)

        # Update ratings
        home_elo.rating = round(home_elo.rating + home_k * (home_actual - home_expected), 2)
        away_elo.rating = round(away_elo.rating + away_k * (away_actual - away_expected), 2)

        # Update stats
        home_elo.matches_played += 1
        away_elo.matches_played += 1
        home_elo.goals_scored += home_score
        home_elo.goals_conceded += away_score
        away_elo.goals_scored += away_score
        away_elo.goals_conceded += home_score
        home_elo.last_updated = datetime.now(timezone.utc)
        away_elo.last_updated = datetime.now(timezone.utc)

        if commit:
            db.commit()
            db.refresh(home_elo)
            db.refresh(away_elo)

        return home_elo, away_elo

    @classmethod
    def recalculate_all_elo(cls, db: Session, commit: bool = True) -> List[EloRating]:
        """
        Recalculate all Elo ratings from scratch based on all completed matches.
        Useful for initial setup or after data purges.
        """
        # Reset all ratings
        all_elos = db.query(EloRating).all()
        for elo in all_elos:
            elo.rating = cls.DEFAULT_RATING
            elo.matches_played = 0
            elo.home_wins = 0
            elo.away_wins = 0
            elo.draws = 0
            elo.goals_scored = 0
            elo.goals_conceded = 0

        # Get all completed matches in chronological order
        completed = (
            db.query(Fixture, HistoricalResult)
            .join(HistoricalResult, Fixture.id == HistoricalResult.fixture_id)
            .filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
            .order_by(Fixture.match_date.asc())
            .all()
        )

        for fixture, result in completed:
            cls.update_elo_for_fixture(
                db, fixture.id, fixture.home_team_id, fixture.away_team_id,
                result.home_score, result.away_score, commit=False
            )

        if commit:
            db.commit()
            for elo in all_elos:
                db.refresh(elo)

        return all_elos

    @classmethod
    def get_team_elo(cls, db: Session, team_id: int) -> float:
        """Get current Elo rating for a team, defaulting to 1500."""
        elo = cls.get_or_create_elo(db, team_id)
        return elo.rating

    @classmethod
    def get_elo_difference(cls, db: Session, home_team_id: int, away_team_id: int) -> float:
        """Get Elo difference (home - away) including home advantage."""
        home_elo = cls.get_team_elo(db, home_team_id)
        away_elo = cls.get_team_elo(db, away_team_id)
        return (home_elo + cls.HOME_ADVANTAGE) - away_elo

    @classmethod
    def get_win_probability(cls, db: Session, home_team_id: int, away_team_id: int) -> float:
        """Get probability of home team winning based on Elo."""
        home_elo = cls.get_team_elo(db, home_team_id) + cls.HOME_ADVANTAGE
        away_elo = cls.get_team_elo(db, away_team_id)
        return cls.calculate_expected_score(home_elo, away_elo)


class TeamFormService:
    """
    Team Form Streak Service.
    
    Calculates recent form (W/D/L streaks) and goal statistics
    for the last 3, 5, and 10 matches.
    """

    @classmethod
    def get_or_create_streak(cls, db: Session, team_id: int) -> TeamFormStreak:
        """Retrieve or create TeamFormStreak entry for a team."""
        streak = db.query(TeamFormStreak).filter(TeamFormStreak.team_id == team_id).first()
        if not streak:
            streak = TeamFormStreak(team_id=team_id)
            db.add(streak)
            db.flush()
        return streak

    @classmethod
    def calculate_form_streaks(cls, db: Session, team_id: int, commit: bool = True) -> TeamFormStreak:
        """
        Calculate form streaks for a team based on last 10 completed matches.
        """
        # Get last 10 completed matches for this team
        home_matches = (
            db.query(Fixture, HistoricalResult)
            .join(HistoricalResult, Fixture.id == HistoricalResult.fixture_id)
            .filter(Fixture.home_team_id == team_id)
            .filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
            .order_by(Fixture.match_date.desc())
            .limit(10)
            .all()
        )

        away_matches = (
            db.query(Fixture, HistoricalResult)
            .join(HistoricalResult, Fixture.id == HistoricalResult.fixture_id)
            .filter(Fixture.away_team_id == team_id)
            .filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
            .order_by(Fixture.match_date.desc())
            .limit(10)
            .all()
        )

        # Combine and sort by date desc
        all_matches = []
        for fixture, result in home_matches:
            all_matches.append((fixture, result, True))  # True = home
        for fixture, result in away_matches:
            all_matches.append((fixture, result, False))  # False = away

        all_matches.sort(key=lambda x: x[0].match_date, reverse=True)
        all_matches = all_matches[:10]

        # Calculate results
        results = []
        goals_scored = 0
        goals_conceded = 0
        clean_sheets = 0
        failed_to_score = 0

        for fixture, result, is_home in all_matches:
            team_goals = result.home_score if is_home else result.away_score
            opp_goals = result.away_score if is_home else result.home_score

            goals_scored += team_goals
            goals_conceded += opp_goals

            if opp_goals == 0:
                clean_sheets += 1
            if team_goals == 0:
                failed_to_score += 1

            if team_goals > opp_goals:
                results.append("W")
            elif team_goals < opp_goals:
                results.append("L")
            else:
                results.append("D")

        last_3 = results[:3]
        last_5 = results[:5]
        last_10 = results[:10]

        streak = cls.get_or_create_streak(db, team_id)
        streak.last_3_results = json.dumps(last_3) if last_3 else None
        streak.last_5_results = json.dumps(last_5) if last_5 else None
        streak.last_10_results = json.dumps(last_10) if last_10 else None
        streak.goals_scored_last_5 = sum(1 for r in all_matches[:5] if r[1].home_score > r[1].away_score or r[1].away_score > r[1].home_score)  # This is wrong, let me fix
        # Actually, let me recalculate properly
        streak.goals_scored_last_5 = 0
        streak.goals_conceded_last_5 = 0
        streak.clean_sheets_last_5 = 0
        streak.failed_to_score_last_5 = 0

        for fixture, result, is_home in all_matches[:5]:
            team_goals = result.home_score if is_home else result.away_score
            opp_goals = result.away_score if is_home else result.home_score
            streak.goals_scored_last_5 += team_goals
            streak.goals_conceded_last_5 += opp_goals
            if opp_goals == 0:
                streak.clean_sheets_last_5 += 1
            if team_goals == 0:
                streak.failed_to_score_last_5 += 1

        streak.updated_at = datetime.now(timezone.utc)

        if commit:
            db.commit()
            db.refresh(streak)

        return streak

    @classmethod
    def get_form_score(cls, streak: Optional[TeamFormStreak], window: int = 5) -> float:
        """
        Calculate a form score from 0.0 (terrible) to 1.0 (perfect).
        Win = 1.0, Draw = 0.5, Loss = 0.0
        """
        if not streak:
            return 0.5  # Neutral

        results_json = getattr(streak, f"last_{window}_results", None)
        if not results_json:
            return 0.5

        try:
            results = json.loads(results_json)
        except (json.JSONDecodeError, TypeError):
            return 0.5

        if not results:
            return 0.5

        score_map = {"W": 1.0, "D": 0.5, "L": 0.0}
        scores = [score_map.get(r, 0.5) for r in results]
        return sum(scores) / len(scores)

    @classmethod
    def get_goal_form_multiplier(cls, streak: Optional[TeamFormStreak], is_home: bool) -> float:
        """
        Calculate a multiplier for expected goals based on recent goal form.
        > 1.0 means team is scoring/conceding more than average recently.
        """
        if not streak:
            return 1.0

        goals_scored = streak.goals_scored_last_5
        goals_conceded = streak.goals_conceded_last_5
        matches = 5

        if matches == 0:
            return 1.0

        avg_scored = goals_scored / matches
        avg_conceded = goals_conceded / matches

        # League average is roughly 1.4 goals per team per match
        # Form multiplier: if scoring more than 1.4, boost; if less, reduce
        if is_home:
            # Home teams average ~1.6 goals
            scored_mult = max(0.7, min(1.4, avg_scored / 1.6))
            conceded_mult = max(0.7, min(1.4, avg_conceded / 1.2))
        else:
            # Away teams average ~1.2 goals
            scored_mult = max(0.7, min(1.4, avg_scored / 1.2))
            conceded_mult = max(0.7, min(1.4, avg_conceded / 1.5))

        return round((scored_mult + (2.0 - conceded_mult)) / 2.0, 3)


class HeadToHeadService:
    """
    Head-to-Head History Service.
    
    Calculates historical matchup statistics between two teams,
    weighted by recency and match importance.
    """

    @classmethod
    def get_h2h_matches(
        cls, db: Session, team_a_id: int, team_b_id: int, limit: int = 10
    ) -> List[Tuple[Fixture, HistoricalResult, bool]]:
        """
        Get recent head-to-head matches between two teams.
        Returns list of (fixture, result, team_a_is_home) tuples.
        """
        # Team A home vs Team B away
        a_home = (
            db.query(Fixture, HistoricalResult)
            .join(HistoricalResult, Fixture.id == HistoricalResult.fixture_id)
            .filter(Fixture.home_team_id == team_a_id)
            .filter(Fixture.away_team_id == team_b_id)
            .filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
            .order_by(Fixture.match_date.desc())
            .limit(limit)
            .all()
        )

        # Team B home vs Team A away
        b_home = (
            db.query(Fixture, HistoricalResult)
            .join(HistoricalResult, Fixture.id == HistoricalResult.fixture_id)
            .filter(Fixture.home_team_id == team_b_id)
            .filter(Fixture.away_team_id == team_a_id)
            .filter(Fixture.status.in_(["FINISHED", "FT", "AET", "PEN"]))
            .order_by(Fixture.match_date.desc())
            .limit(limit)
            .all()
        )

        results = []
        for fixture, result in a_home:
            results.append((fixture, result, True))  # team_a is home
        for fixture, result in b_home:
            results.append((fixture, result, False))  # team_a is away

        # Sort by date desc and limit
        results.sort(key=lambda x: x[0].match_date, reverse=True)
        return results[:limit]

    @classmethod
    def get_h2h_goal_stats(
        cls, db: Session, team_a_id: int, team_b_id: int, limit: int = 10
    ) -> Dict[str, Any]:
        """
        Calculate goal statistics from H2H matches.
        Returns dict with avg goals, total matches, team_a wins/draws/losses.
        """
        matches = cls.get_h2h_matches(db, team_a_id, team_b_id, limit)

        if not matches:
            return {
                "total_matches": 0,
                "team_a_wins": 0,
                "draws": 0,
                "team_b_wins": 0,
                "avg_total_goals": 0.0,
                "avg_team_a_goals": 0.0,
                "avg_team_b_goals": 0.0,
                "over_1_5_rate": 0.0,
                "over_2_5_rate": 0.0,
                "btts_rate": 0.0,
            }

        total_matches = len(matches)
        team_a_wins = 0
        draws = 0
        team_b_wins = 0
        total_goals = 0
        team_a_goals = 0
        team_b_goals = 0
        over_1_5 = 0
        over_2_5 = 0
        btts = 0

        for fixture, result, team_a_is_home in matches:
            a_goals = result.home_score if team_a_is_home else result.away_score
            b_goals = result.away_score if team_a_is_home else result.home_score

            team_a_goals += a_goals
            team_b_goals += b_goals
            total_goals += a_goals + b_goals

            if a_goals > b_goals:
                team_a_wins += 1
            elif a_goals < b_goals:
                team_b_wins += 1
            else:
                draws += 1

            if a_goals + b_goals > 1:
                over_1_5 += 1
            if a_goals + b_goals > 2:
                over_2_5 += 1
            if a_goals > 0 and b_goals > 0:
                btts += 1

        return {
            "total_matches": total_matches,
            "team_a_wins": team_a_wins,
            "draws": draws,
            "team_b_wins": team_b_wins,
            "avg_total_goals": round(total_goals / total_matches, 2) if total_matches > 0 else 0.0,
            "avg_team_a_goals": round(team_a_goals / total_matches, 2) if total_matches > 0 else 0.0,
            "avg_team_b_goals": round(team_b_goals / total_matches, 2) if total_matches > 0 else 0.0,
            "over_1_5_rate": round(over_1_5 / total_matches, 4) if total_matches > 0 else 0.0,
            "over_2_5_rate": round(over_2_5 / total_matches, 4) if total_matches > 0 else 0.0,
            "btts_rate": round(btts / total_matches, 4) if total_matches > 0 else 0.0,
        }
