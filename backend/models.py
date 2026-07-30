import os
import sys
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship, Mapped, mapped_column

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from database import Base
except ImportError:
    from .database import Base


class League(Base):
    __tablename__ = "leagues"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    country: Mapped[str] = mapped_column(String, nullable=False)
    season: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    teams = relationship("Team", back_populates="league", cascade="all, delete-orphan")
    fixtures = relationship("Fixture", back_populates="league", cascade="all, delete-orphan")
    statistics = relationship("LeagueStatistics", back_populates="league", uselist=False, cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    short_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    league_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("leagues.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    league = relationship("League", back_populates="teams")
    home_fixtures = relationship("Fixture", foreign_keys="[Fixture.home_team_id]", back_populates="home_team")
    away_fixtures = relationship("Fixture", foreign_keys="[Fixture.away_team_id]", back_populates="away_team")
    statistics = relationship("TeamStatistics", back_populates="team", uselist=False, cascade="all, delete-orphan")


class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True, index=True)
    league_id: Mapped[int] = mapped_column(Integer, ForeignKey("leagues.id"), nullable=False)
    home_team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    match_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, default="SCHEDULED", index=True) # SCHEDULED, LIVE, FINISHED, POSTPONED
    venue: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    live_clock: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    league = relationship("League", back_populates="fixtures")
    home_team = relationship("Team", foreign_keys=[home_team_id], back_populates="home_fixtures")
    away_team = relationship("Team", foreign_keys=[away_team_id], back_populates="away_fixtures")
    historical_result = relationship("HistoricalResult", back_populates="fixture", uselist=False, cascade="all, delete-orphan")
    predictions = relationship("Prediction", back_populates="fixture", cascade="all, delete-orphan")


class HistoricalResult(Base):
    __tablename__ = "historical_results"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), unique=True, nullable=False)
    home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, nullable=False)
    half_time_home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    half_time_away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    fixture = relationship("Fixture", back_populates="historical_result")


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False)
    predicted_home_score: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_away_score: Mapped[float] = mapped_column(Float, nullable=False)
    expected_goals_xg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    draw_probability: Mapped[float] = mapped_column(Float, nullable=False)
    away_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    over_0_5_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    over_1_5_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    over_2_5_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    over_3_5_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    over_4_5_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    under_2_5_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    most_likely_score: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    top_scorelines_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    fixture = relationship("Fixture", back_populates="predictions")


class TeamStatistics(Base):
    __tablename__ = "team_statistics"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), unique=True, nullable=False, index=True)
    matches_analyzed_home: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matches_analyzed_away: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_home_goals_scored: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_home_goals_conceded: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_away_goals_scored: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_away_goals_conceded: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    home_attack_strength: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    home_defense_strength: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    away_attack_strength: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    away_defense_strength: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    team = relationship("Team", back_populates="statistics")


class LeagueStatistics(Base):
    __tablename__ = "league_statistics"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    league_id: Mapped[int] = mapped_column(Integer, ForeignKey("leagues.id"), unique=True, nullable=False, index=True)
    total_matches_analyzed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_home_goals: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_away_goals: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    league = relationship("League", back_populates="statistics")
