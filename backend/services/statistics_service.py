import os
import sys
from datetime import datetime, timezone
from typing import List, Optional, cast
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import League, Team, Fixture, HistoricalResult, TeamStatistics, LeagueStatistics
except ImportError:
    from ..models import League, Team, Fixture, HistoricalResult, TeamStatistics, LeagueStatistics


def calculate_team_statistics(
    db: Session, team_id: int, last_n_matches: int = 10
) -> Optional[TeamStatistics]:
    """
    Calculates average home/away goals scored and conceded for a team based on its
    most recent completed league matches (default: last 10 completed matches).
    Saves and updates the result in the TeamStatistics table.
    """
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return None

    # Retrieve last N completed home matches with historical results
    home_results = (
        db.query(HistoricalResult)
        .join(Fixture, Fixture.id == HistoricalResult.fixture_id)
        .filter(Fixture.home_team_id == team_id)
        .order_by(Fixture.match_date.desc())
        .limit(last_n_matches)
        .all()
    )

    # Retrieve last N completed away matches with historical results
    away_results = (
        db.query(HistoricalResult)
        .join(Fixture, Fixture.id == HistoricalResult.fixture_id)
        .filter(Fixture.away_team_id == team_id)
        .order_by(Fixture.match_date.desc())
        .limit(last_n_matches)
        .all()
    )

    home_count = len(home_results)
    away_count = len(away_results)

    if home_count > 0:
        avg_home_scored = round(float(sum(res.home_score for res in home_results)) / home_count, 2)
        avg_home_conceded = round(float(sum(res.away_score for res in home_results)) / home_count, 2)
    else:
        avg_home_scored = 0.0
        avg_home_conceded = 0.0

    if away_count > 0:
        avg_away_scored = round(float(sum(res.away_score for res in away_results)) / away_count, 2)
        avg_away_conceded = round(float(sum(res.home_score for res in away_results)) / away_count, 2)
    else:
        avg_away_scored = 0.0
        avg_away_conceded = 0.0

    # Fetch or calculate league statistics for strength metric comparisons
    league_avg_home = 0.0
    league_avg_away = 0.0
    if team.league_id:
        l_stats = calculate_league_statistics(db, cast(int, team.league_id))
        if l_stats:
            league_avg_home = cast(float, l_stats.avg_home_goals)
            league_avg_away = cast(float, l_stats.avg_away_goals)

    home_attack_strength = round(avg_home_scored / league_avg_home, 4) if (league_avg_home > 0 and home_count > 0) else 1.0
    home_defense_strength = round(avg_home_conceded / league_avg_away, 4) if (league_avg_away > 0 and home_count > 0) else 1.0
    away_attack_strength = round(avg_away_scored / league_avg_away, 4) if (league_avg_away > 0 and away_count > 0) else 1.0
    away_defense_strength = round(avg_away_conceded / league_avg_home, 4) if (league_avg_home > 0 and away_count > 0) else 1.0

    # Fetch existing team statistics or create a new entry
    stats = db.query(TeamStatistics).filter(TeamStatistics.team_id == team_id).first()
    if not stats:
        stats = TeamStatistics(team_id=team_id)
        db.add(stats)

    stats.matches_analyzed_home = home_count
    stats.matches_analyzed_away = away_count
    stats.avg_home_goals_scored = avg_home_scored
    stats.avg_home_goals_conceded = avg_home_conceded
    stats.avg_away_goals_scored = avg_away_scored
    stats.avg_away_goals_conceded = avg_away_conceded
    stats.home_attack_strength = home_attack_strength
    stats.home_defense_strength = home_defense_strength
    stats.away_attack_strength = away_attack_strength
    stats.away_defense_strength = away_defense_strength
    stats.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(stats)
    return stats


def calculate_all_team_statistics(
    db: Session, last_n_matches: int = 10
) -> List[TeamStatistics]:
    """
    Calculates and saves team statistics for all teams in the database.
    """
    teams = db.query(Team).all()
    calculated_stats = []
    for team in teams:
        stat = calculate_team_statistics(db, cast(int, team.id), last_n_matches=last_n_matches)
        if stat:
            calculated_stats.append(stat)
    return calculated_stats


def get_team_statistics(db: Session, team_id: int) -> Optional[TeamStatistics]:
    """
    Retrieves stored statistics for a specific team from the database.
    """
    return db.query(TeamStatistics).filter(TeamStatistics.team_id == team_id).first()


def get_all_team_statistics(db: Session) -> List[TeamStatistics]:
    """
    Retrieves stored statistics for all teams from the database.
    """
    return db.query(TeamStatistics).all()


def calculate_league_statistics(db: Session, league_id: int) -> Optional[LeagueStatistics]:
    """
    Calculates average home goals and average away goals across ALL completed matches
    for a specific league. Saves and updates the result in the LeagueStatistics table.
    """
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        return None

    # Retrieve all completed fixtures in this league with historical results
    completed_results = (
        db.query(HistoricalResult)
        .join(Fixture, Fixture.id == HistoricalResult.fixture_id)
        .filter(Fixture.league_id == league_id)
        .all()
    )

    total_matches = len(completed_results)

    if total_matches > 0:
        avg_home_goals = round(float(sum(res.home_score for res in completed_results)) / total_matches, 4)
        avg_away_goals = round(float(sum(res.away_score for res in completed_results)) / total_matches, 4)
    else:
        avg_home_goals = 0.0
        avg_away_goals = 0.0

    stats = db.query(LeagueStatistics).filter(LeagueStatistics.league_id == league_id).first()
    if not stats:
        stats = LeagueStatistics(league_id=league_id)
        db.add(stats)

    stats.total_matches_analyzed = total_matches
    stats.avg_home_goals = avg_home_goals
    stats.avg_away_goals = avg_away_goals
    stats.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(stats)
    return stats


def calculate_all_league_statistics(db: Session) -> List[LeagueStatistics]:
    """
    Calculates and saves league statistics for all leagues in the database.
    """
    leagues = db.query(League).all()
    calculated_stats = []
    for league in leagues:
        stat = calculate_league_statistics(db, cast(int, league.id))
        if stat:
            calculated_stats.append(stat)
    return calculated_stats


def get_league_statistics(db: Session, league_id: int) -> Optional[LeagueStatistics]:
    """
    Retrieves stored statistics for a specific league from the database.
    """
    return db.query(LeagueStatistics).filter(LeagueStatistics.league_id == league_id).first()


def get_all_league_statistics(db: Session) -> List[LeagueStatistics]:
    """
    Retrieves stored statistics for all leagues from the database.
    """
    return db.query(LeagueStatistics).all()

