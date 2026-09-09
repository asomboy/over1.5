import os
import sys
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Integer, String, Float, DateTime, ForeignKey, Boolean, Text, UniqueConstraint
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
    season: Mapped[str] = mapped_column(String, default="2025/2026")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    fixtures = relationship("Fixture", back_populates="league", cascade="all, delete-orphan")
    teams = relationship("Team", back_populates="league", cascade="all, delete-orphan")
    statistics = relationship("LeagueStatistics", back_populates="league", uselist=False, cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    short_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    league_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("leagues.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    league = relationship("League", back_populates="teams")
    home_fixtures = relationship("Fixture", foreign_keys="[Fixture.home_team_id]", back_populates="home_team")
    away_fixtures = relationship("Fixture", foreign_keys="[Fixture.away_team_id]", back_populates="away_team")
    statistics = relationship("TeamStatistics", back_populates="team", uselist=False, cascade="all, delete-orphan")
    elo_rating = relationship("EloRating", back_populates="team", uselist=False, cascade="all, delete-orphan")
    form_streak = relationship("TeamFormStreak", back_populates="team", uselist=False, cascade="all, delete-orphan")


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
    match_statistics = relationship("MatchStatistics", back_populates="fixture", uselist=False, cascade="all, delete-orphan")
    predictions = relationship("Prediction", back_populates="fixture", cascade="all, delete-orphan")
    corner_snapshots = relationship("CornerPredictionSnapshot", back_populates="fixture", cascade="all, delete-orphan")
    card_snapshots = relationship("CardPredictionSnapshot", back_populates="fixture", cascade="all, delete-orphan")
    referee_statistics = relationship("RefereeMatchStatistics", back_populates="fixture", uselist=False, cascade="all, delete-orphan")
    live_state = relationship("LiveMatchState", back_populates="fixture", uselist=False, cascade="all, delete-orphan")
    live_snapshots = relationship("LivePredictionSnapshot", back_populates="fixture", cascade="all, delete-orphan")
    evaluations = relationship("ModelEvaluation", back_populates="fixture", cascade="all, delete-orphan")
    feature_snapshots = relationship("FeatureSnapshot", back_populates="fixture", cascade="all, delete-orphan")
    enrichment_status = relationship("HistoricalEnrichmentStatus", back_populates="fixture", uselist=False, cascade="all, delete-orphan")
    provider_mappings = relationship("FixtureProviderMapping", back_populates="fixture", cascade="all, delete-orphan")
    observed_snapshots = relationship("LiveObservedSnapshot", back_populates="fixture", cascade="all, delete-orphan")


class HistoricalResult(Base):
    __tablename__ = "historical_results"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), unique=True, nullable=False)
    home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, nullable=False)
    half_time_home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    half_time_away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_yellow_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_yellow_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_red_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_red_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    fixture = relationship("Fixture", back_populates="historical_result")


class Referee(Base):
    """
    Referee entity tracking historical officiating records, disciplinary tendencies,
    and competition assignments.
    """
    __tablename__ = "referees"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    competition: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    referee_matches = relationship("RefereeMatchStatistics", back_populates="referee", cascade="all, delete-orphan")


class RefereeMatchStatistics(Base):
    """
    Observed match-level officiating statistics for referees.
    """
    __tablename__ = "referee_match_statistics"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    referee_id: Mapped[int] = mapped_column(Integer, ForeignKey("referees.id"), nullable=False, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), unique=True, nullable=False, index=True)
    yellow_cards: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    red_cards: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cards: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fouls: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    data_source: Mapped[str] = mapped_column(String, default="observed")
    data_quality: Mapped[str] = mapped_column(String, default="verified")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    referee = relationship("Referee", back_populates="referee_matches")
    fixture = relationship("Fixture", back_populates="referee_statistics")


class MatchStatistics(Base):
    """
    Reusable Match Detailed Statistics table storing observed match-level metrics
    such as corners, shots, possession, fouls, and cards.
    """
    __tablename__ = "match_statistics"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), unique=True, nullable=False, index=True)
    home_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_possession: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_possession: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_fouls: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_fouls: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_fouls: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_offsides: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_offsides: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_offsides: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_saves: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_saves: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_saves: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_blocked_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_blocked_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_blocked_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_inside_box_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_inside_box_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_inside_box_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_outside_box_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_outside_box_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_outside_box_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_yellow_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_yellow_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_red_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_red_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_total_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_total_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_yellow_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_red_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    referee_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    referee_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("referees.id"), nullable=True, index=True)
    data_source: Mapped[str] = mapped_column(String, default="observed")
    data_quality: Mapped[str] = mapped_column(String, default="verified")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    fixture = relationship("Fixture", back_populates="match_statistics")


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
    btts_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    most_likely_score: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    top_scorelines_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model_version: Mapped[Optional[str]] = mapped_column(String, default="v2_match_intelligence", nullable=True)
    raw_intelligence_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    fixture = relationship("Fixture", back_populates="predictions")


class CornerPredictionSnapshot(Base):
    """
    Immutable Pre-Match and Historical Prediction Snapshot storage for Corner models.
    Preserves exact model inputs and probabilities generated before kickoff for auditing,
    out-of-sample backtesting, and live performance verification.
    """
    __tablename__ = "corner_prediction_snapshots"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String, default="v1_corners_nb", index=True)
    prediction_timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    is_prematch: Mapped[bool] = mapped_column(Boolean, default=True)

    expected_home_corners: Mapped[float] = mapped_column(Float, nullable=False)
    expected_away_corners: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total_corners: Mapped[float] = mapped_column(Float, nullable=False)

    over_7_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_7_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    over_8_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_8_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    over_9_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_9_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    over_10_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_10_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    over_11_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_11_5_prob: Mapped[float] = mapped_column(Float, nullable=False)

    home_over_3_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_over_4_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_over_5_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_over_3_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_over_4_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_over_5_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    confidence_score: Mapped[int] = mapped_column(Integer, default=50)
    data_quality_score: Mapped[int] = mapped_column(Integer, default=50)
    sample_strength_score: Mapped[int] = mapped_column(Integer, default=50)
    dispersion: Mapped[float] = mapped_column(Float, default=5.5)
    dispersion_source: Mapped[str] = mapped_column(String, default="fallback")
    baseline_source: Mapped[str] = mapped_column(String, default="fallback")

    # Post-Match Verification fields (populated once match is finished and verified)
    actual_home_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_away_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_total_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    fixture = relationship("Fixture", back_populates="corner_snapshots")


class CardPredictionSnapshot(Base):
    """
    Immutable Pre-Match and Historical Prediction Snapshot storage for Cards models.
    Preserves exact model inputs and probabilities generated before kickoff for auditing,
    out-of-sample backtesting, and live performance verification.
    """
    __tablename__ = "card_prediction_snapshots"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String, default="v1_cards_nb", index=True)
    prediction_timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    is_prematch: Mapped[bool] = mapped_column(Boolean, default=True)

    expected_home_cards: Mapped[float] = mapped_column(Float, nullable=False)
    expected_away_cards: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total_cards: Mapped[float] = mapped_column(Float, nullable=False)

    over_1_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_1_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    over_2_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_2_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    over_3_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_3_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    over_4_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_4_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    over_5_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_5_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    over_6_5_prob: Mapped[float] = mapped_column(Float, nullable=False)
    under_6_5_prob: Mapped[float] = mapped_column(Float, nullable=False)

    home_over_0_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_over_1_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_over_2_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_over_3_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_over_0_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_over_1_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_over_2_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_over_3_5_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Red card risk
    any_red_card_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_red_card_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_red_card_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    confidence_score: Mapped[int] = mapped_column(Integer, default=50)
    data_quality_score: Mapped[int] = mapped_column(Integer, default=50)
    sample_strength_score: Mapped[int] = mapped_column(Integer, default=50)
    model_stability_score: Mapped[int] = mapped_column(Integer, default=50)
    referee_confidence_score: Mapped[int] = mapped_column(Integer, default=50)

    dispersion: Mapped[float] = mapped_column(Float, default=4.0)
    dispersion_source: Mapped[str] = mapped_column(String, default="fallback")
    baseline_source: Mapped[str] = mapped_column(String, default="fallback")
    referee_source: Mapped[str] = mapped_column(String, default="fallback")
    referee_sample_size: Mapped[int] = mapped_column(Integer, default=0)

    # Post-Match Verification fields (populated once match is finished and verified)
    actual_home_yellow_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_away_yellow_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_home_red_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_away_red_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_total_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    fixture = relationship("Fixture", back_populates="card_snapshots")


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


class EloRating(Base):
    __tablename__ = "elo_ratings"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), unique=True, nullable=False, index=True)
    rating: Mapped[float] = mapped_column(Float, default=1500.0, nullable=False)
    matches_played: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    home_wins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    away_wins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    draws: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    goals_scored: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    team = relationship("Team", back_populates="elo_rating")


class TeamFormStreak(Base):
    __tablename__ = "team_form_streaks"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), unique=True, nullable=False, index=True)
    last_3_results: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # JSON: ["W","D","L"]
    last_5_results: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # JSON: ["W","D","L","W","D"]
    last_10_results: Mapped[Optional[str]] = mapped_column(String, nullable=True) # JSON: ["W","D","L",...]
    goals_scored_last_5: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    goals_conceded_last_5: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clean_sheets_last_5: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_to_score_last_5: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    team = relationship("Team", back_populates="form_streak")


class LiveMatchState(Base):
    """
    Real-time observed in-game state for active football fixtures.
    Maintains the latest minute, clock period, scoreline, and live boxscores.
    """
    __tablename__ = "live_match_states"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), unique=True, nullable=False, index=True)
    minute: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    added_time: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    period: Mapped[str] = mapped_column(String, default="1H") # 1H, HT, 2H, ET, PEN, FT
    status: Mapped[str] = mapped_column(String, default="LIVE", index=True)

    home_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    home_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    home_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    home_shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    home_possession: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_possession: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    home_fouls: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_fouls: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    home_yellow_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_yellow_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    home_red_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_red_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    last_updated: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    data_source: Mapped[str] = mapped_column(String, default="live_feed")
    data_quality: Mapped[str] = mapped_column(String, default="verified")

    # Relationships
    fixture = relationship("Fixture", back_populates="live_state")


class LivePredictionSnapshot(Base):
    """
    Chronological in-play prediction snapshot archive.
    Preserves dynamic probabilities generated at specific match minutes without
    overwriting immutable pre-match predictions.
    """
    __tablename__ = "live_prediction_snapshots"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String, default="v1_live_intelligence", index=True)

    prediction_timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    match_minute: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    period: Mapped[str] = mapped_column(String, default="1H")

    home_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    goals_prediction_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corners_prediction_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cards_prediction_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    best_live_signal_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    confidence: Mapped[int] = mapped_column(Integer, default=50)
    data_quality: Mapped[int] = mapped_column(Integer, default=50)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    fixture = relationship("Fixture", back_populates="live_snapshots")


class ModelEvaluation(Base):
    """
    Central immutable record evaluating a specific model prediction market
    against verified real-world post-match outcomes.
    """
    __tablename__ = "model_evaluations"
    __table_args__ = (
        UniqueConstraint("fixture_id", "market", "model_version", "is_live", "match_minute", name="uq_model_eval"),
        {'extend_existing': True}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    prediction_snapshot_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prediction_type: Mapped[str] = mapped_column(String, nullable=False, index=True) # goals, 1x2, corners, cards, red_cards, live_goals, live_corners, live_cards
    market: Mapped[str] = mapped_column(String, nullable=False, index=True) # over_1_5_goals, over_2_5_goals, btts, 1x2_home, over_8_5_corners, etc.
    model_version: Mapped[str] = mapped_column(String, nullable=False, index=True)
    competition: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    prediction_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    match_minute: Mapped[int] = mapped_column(Integer, default=0, nullable=False) # 0 for pre-match
    period: Mapped[str] = mapped_column(String, default="PRE") # PRE, 1H, HT, 2H, FT

    predicted_probability: Mapped[float] = mapped_column(Float, nullable=False)
    actual_outcome: Mapped[float] = mapped_column(Float, nullable=False) # 1.0 or 0.0 for binary, count for regression

    confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    data_quality: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    is_live: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Precalculated atomic metric components
    brier_component: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # (p - y)^2
    log_loss_component: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # -(y*log(p) + (1-y)*log(1-p))
    absolute_error: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # |p - y|

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    fixture = relationship("Fixture", back_populates="evaluations")


class DataQualitySnapshot(Base):
    """
    Periodic data quality and coverage registry records across database, competition, season, or team.
    """
    __tablename__ = "data_quality_snapshots"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    scope_type: Mapped[str] = mapped_column(String, default="global", index=True) # global, competition, team, provider
    scope_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    metric: Mapped[str] = mapped_column(String, nullable=False, index=True) # goals_data_coverage, corner_data_coverage, card_data_coverage, referee_data_coverage, live_snapshot_coverage
    
    eligible_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    observed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    coverage_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ProviderHealth(Base):
    """
    Operational monitoring records tracking external API data providers (latency, success rates, status).
    """
    __tablename__ = "provider_health_logs"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True) # espn, football_data, api_football
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    average_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    missing_data_rate: Mapped[float] = mapped_column(Float, default=0.0)
    stale_data_rate: Mapped[float] = mapped_column(Float, default=0.0)
    
    status: Mapped[str] = mapped_column(String, default="HEALTHY", index=True) # HEALTHY, DEGRADED, UNAVAILABLE


class FeatureSnapshot(Base):
    """
    Immutable feature payload snapshot preserving exact model inputs available before kickoff or at snapshot time.
    Guarantees strict auditability, zero future-leakage verification, and model reproducibility.
    """
    __tablename__ = "feature_snapshots"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String, default="v2_match_intelligence", index=True)
    prediction_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    
    features_json: Mapped[str] = mapped_column(Text, nullable=False) # JSON payload of team/ref/competition features
    feature_coverage: Mapped[float] = mapped_column(Float, default=1.0)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    provenance_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    fixture = relationship("Fixture", back_populates="feature_snapshots")


class HistoricalEnrichmentStatus(Base):
    """
    Tracks historical data backfill progress, boxscore completeness, and retry eligibility.
    """
    __tablename__ = "historical_enrichment_status"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), unique=True, nullable=False, index=True)
    
    enrichment_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    has_boxscore_stats: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    has_referee: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    retry_eligible: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    fixture = relationship("Fixture", back_populates="enrichment_status")


class JobExecution(Base):
    """
    Persistent execution log for scheduled and manual production background jobs.
    Tracks execution status, timing, processed records, and error details.
    """
    __tablename__ = "job_executions"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_name: Mapped[str] = mapped_column(String, nullable=False, index=True) # fixture_ingestion, historical_enrichment, prematch_prediction, live_match_poll, post_match_verification, model_evaluation, provider_health, system_maintenance
    execution_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="RUNNING", index=True) # RUNNING, SUCCESS, PARTIAL, FAILED, SKIPPED
    
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    records_created: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    records_skipped: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class SystemAlert(Base):
    """
    Internal system operational alert record with deduplication and cooldown support.
    """
    __tablename__ = "system_alerts"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    alert_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String, nullable=False, index=True) # PROVIDER_FAILURE, PROVIDER_STALE, JOB_FAILURE, DATABASE_FAILURE, MODEL_DEGRADATION, DATA_QUALITY, LIVE_DATA_STALE, SYSTEM_CONFIGURATION
    severity: Mapped[str] = mapped_column(String, default="WARNING", index=True) # INFO, WARNING, CRITICAL
    
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, default="system", index=True)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class DataProvenance(Base):
    """
    Field-level audit trail tracking the exact external source, provider record ID, 
    retrieval timestamp, and verification status of historical observations.
    """
    __tablename__ = "data_provenance"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String, nullable=False, index=True) # home_score, away_score, home_corners, away_corners, home_yellow_cards, away_yellow_cards, referee_name, etc.
    value: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False, index=True) # espn, football_data, manual
    provider_record_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    source_type: Mapped[str] = mapped_column(String, default="OBSERVED") # OBSERVED, VERIFIED, RECONCILED
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class DataConflict(Base):
    """
    Records conflicting observations between different external data providers.
    Prevents silent overwriting and preserves operator visibility.
    """
    __tablename__ = "data_conflicts"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    primary_provider: Mapped[str] = mapped_column(String, nullable=False)
    primary_value: Mapped[str] = mapped_column(String, nullable=False)
    conflicting_provider: Mapped[str] = mapped_column(String, nullable=False)
    conflicting_value: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="CONFLICT", index=True) # CONFLICT, RESOLVED, IGNORED
    resolved_value: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class UnifiedMatchIntelligenceSnapshot(Base):
    """
    Immutable cross-market prediction snapshot combining Goals, Corners, Cards, 
    Referee signals, Live dynamics, and Cross-Market consistency diagnostics.
    """
    __tablename__ = "unified_match_intelligence_snapshots"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    match_status: Mapped[str] = mapped_column(String, default="SCHEDULED", index=True) # SCHEDULED, LIVE, FINISHED
    match_minute: Mapped[int] = mapped_column(Integer, default=0, index=True)
    
    unified_confidence: Mapped[float] = mapped_column(Float, default=0.50)
    match_state_tags: Mapped[str] = mapped_column(Text, default="[]") # JSON list of match state tags
    cross_market_consistency_score: Mapped[float] = mapped_column(Float, default=1.0)
    
    intelligence_payload: Mapped[str] = mapped_column(Text, nullable=False) # Complete serialized JSON payload
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ShotPredictionSnapshot(Base):
    """
    Immutable Pre-Match and In-Play Prediction Snapshot storage for Shots and Shots-on-Target models.
    Preserves model inputs, discrete distributions, and market probabilities for auditing and calibration.
    """
    __tablename__ = "shot_prediction_snapshots"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String, default="v1_shots_nb", index=True)
    prediction_timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    match_minute: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    expected_home_shots: Mapped[float] = mapped_column(Float, nullable=False)
    expected_away_shots: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total_shots: Mapped[float] = mapped_column(Float, nullable=False)

    expected_home_sot: Mapped[float] = mapped_column(Float, nullable=False)
    expected_away_sot: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total_sot: Mapped[float] = mapped_column(Float, nullable=False)

    # Serialized JSON dictionaries of discrete PMFs and Market Over/Under probabilities
    shots_probabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    sot_probabilities_json: Mapped[str] = mapped_column(Text, nullable=False)

    confidence: Mapped[float] = mapped_column(Float, default=0.50)
    data_quality: Mapped[float] = mapped_column(Float, default=0.50)
    diagnostics_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class MatchStatisticsPredictionSnapshot(Base):
    """
    Immutable Pre-Match and In-Play Prediction Snapshot storage for Match Statistics models:
    Possession, Fouls, Offsides, Saves, Blocked Shots, and Shot Locations (Inside/Outside Box).
    """
    __tablename__ = "match_statistics_prediction_snapshots"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String, default="v1_match_stats_nb", index=True)
    prediction_timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    match_minute: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    expected_home_possession: Mapped[float] = mapped_column(Float, default=50.0)
    expected_away_possession: Mapped[float] = mapped_column(Float, default=50.0)

    expected_home_fouls: Mapped[float] = mapped_column(Float, nullable=False)
    expected_away_fouls: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total_fouls: Mapped[float] = mapped_column(Float, nullable=False)

    expected_home_offsides: Mapped[float] = mapped_column(Float, nullable=False)
    expected_away_offsides: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total_offsides: Mapped[float] = mapped_column(Float, nullable=False)

    expected_home_saves: Mapped[float] = mapped_column(Float, nullable=False)
    expected_away_saves: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total_saves: Mapped[float] = mapped_column(Float, nullable=False)

    expected_home_blocked_shots: Mapped[float] = mapped_column(Float, nullable=False)
    expected_away_blocked_shots: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total_blocked_shots: Mapped[float] = mapped_column(Float, nullable=False)

    expected_home_inside_box_shots: Mapped[float] = mapped_column(Float, nullable=False)
    expected_away_inside_box_shots: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total_inside_box_shots: Mapped[float] = mapped_column(Float, nullable=False)

    expected_home_outside_box_shots: Mapped[float] = mapped_column(Float, nullable=False)
    expected_away_outside_box_shots: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total_outside_box_shots: Mapped[float] = mapped_column(Float, nullable=False)

    fouls_probabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    offsides_probabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    saves_probabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    blocked_shots_probabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    shot_location_probabilities_json: Mapped[str] = mapped_column(Text, nullable=False)

    confidence: Mapped[float] = mapped_column(Float, default=0.50)
    data_quality: Mapped[float] = mapped_column(Float, default=0.50)
    diagnostics_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class PredictionDecisionSnapshot(Base):
    """
    Immutable Phase 12 Decision Snapshot preserving normalized probabilities,
    decision confidence, decision score, risk tier, signal status, historical calibration,
    drift status, and machine-readable explainability reason codes.
    """
    __tablename__ = "prediction_decision_snapshots"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String, nullable=False, index=True)
    selection: Mapped[str] = mapped_column(String, nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String, default="v1_decision_engine", index=True)

    probability: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    decision_score: Mapped[float] = mapped_column(Float, nullable=False)

    risk_tier: Mapped[str] = mapped_column(String, default="NO_SIGNAL", index=True) # LOW, MEDIUM, HIGH, NO_SIGNAL
    signal_status: Mapped[str] = mapped_column(String, default="NO_SIGNAL", index=True) # PRODUCTION_SIGNAL, SHADOW_SIGNAL, INSUFFICIENT_DATA, DEGRADED, NO_SIGNAL, UNAVAILABLE

    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    brier_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    log_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ece: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mce: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    data_quality: Mapped[float] = mapped_column(Float, default=0.50)
    consistency_score: Mapped[float] = mapped_column(Float, default=1.0)
    drift_status: Mapped[str] = mapped_column(String, default="STABLE")
    readiness_status: Mapped[str] = mapped_column(String, default="INSUFFICIENT_DATA")

    explanation_json: Mapped[str] = mapped_column(Text, default="{}")
    diagnostics_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    match_minute: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    prediction_timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class FixtureProviderMapping(Base):
    __tablename__ = "fixture_provider_mappings"
    __table_args__ = (
        UniqueConstraint('provider_name', 'provider_event_id', name='uq_provider_event'),
        {'extend_existing': True}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    provider_name: Mapped[str] = mapped_column(String, nullable=False, index=True)  # e.g., "ESPN"
    provider_event_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider_home_team_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    provider_away_team_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    provider_competition_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    provider_kickoff: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mapping_status: Mapped[str] = mapped_column(String, default="MAPPED", index=True)  # MAPPED, VERIFIED, REJECTED, SUSPENDED
    first_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    validation_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    # Relationships
    fixture = relationship("Fixture", back_populates="provider_mappings")


class LiveObservedSnapshot(Base):
    __tablename__ = "live_observed_snapshots"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    provider_name: Mapped[str] = mapped_column(String, nullable=False, default="ESPN", index=True)
    provider_event_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    match_state: Mapped[str] = mapped_column(String, default="SCHEDULED", index=True)
    minute: Mapped[int] = mapped_column(Integer, default=0)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    statistics_json: Mapped[str] = mapped_column(Text, default="{}")
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    data_quality: Mapped[str] = mapped_column(String, default="AVAILABLE")  # AVAILABLE, PARTIAL, UNAVAILABLE, STALE
    freshness: Mapped[str] = mapped_column(String, default="FRESH")  # FRESH, DELAYED, STALE, VERY_STALE, UNAVAILABLE
    snapshot_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    # Relationships
    fixture = relationship("Fixture", back_populates="observed_snapshots")








