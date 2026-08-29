import os
import sys
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ModelMetadata(BaseModel):
    version: str = "v2_match_intelligence"
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rho: float = Field(default=-0.11, description="Dixon-Coles low-score dependence parameter")
    rho_source: str = Field(default="fallback", description="'competition' | 'shrunk_competition' | 'global' | 'fallback'")


class ExpectedGoals(BaseModel):
    home: float = Field(..., description="Home team expected goals (lambda_home)")
    away: float = Field(..., description="Away team expected goals (lambda_away)")
    total: float = Field(..., description="Total match expected goals (lambda_total)")


class ResultMarket(BaseModel):
    home_win: float = Field(..., description="Probability of Home Win (sum where home > away)")
    draw: float = Field(..., description="Probability of Draw (sum where home == away)")
    away_win: float = Field(..., description="Probability of Away Win (sum where home < away)")


class GoalsMarket(BaseModel):
    over_0_5: float
    under_0_5: float
    over_1_5: float
    under_1_5: float
    over_2_5: float
    under_2_5: float
    over_3_5: float
    under_3_5: float
    over_4_5: Optional[float] = None


class BTTSMarket(BaseModel):
    yes: float = Field(..., description="Probability both teams score (home >= 1 and away >= 1)")
    no: float = Field(..., description="Probability at least one team fails to score")


class TeamGoalThresholds(BaseModel):
    over_0_5: float
    under_0_5: float
    over_1_5: float
    under_1_5: float
    over_2_5: float
    under_2_5: float


class HalvesMarket(BaseModel):
    first_half_over_0_5: float
    first_half_over_1_5: float
    second_half_over_0_5: float
    second_half_over_1_5: float


class ExactScore(BaseModel):
    home: int
    away: int
    probability: float
    score: Optional[str] = None


class ConfidenceDetails(BaseModel):
    overall: int = Field(ge=0, le=100, description="Overall multi-factor model confidence score 0-100")
    data_quality: int = Field(ge=0, le=100, description="Data volume and completeness score 0-100")
    model_stability: int = Field(ge=0, le=100, description="Parameter stability & form consistency score 0-100")
    sample_quality: str = Field(..., description="'insufficient' | 'low' | 'moderate' | 'good' | 'strong'")


class BestModelSignal(BaseModel):
    market: Optional[str] = None
    probability: float
    signal_score: int = Field(ge=0, le=100)
    label: str = Field(..., description="'Watch' | 'Moderate' | 'Strong'")


# ==========================================
# PHASE 2: CORNERS PREDICTION SCHEMAS
# ==========================================

class ExpectedCorners(BaseModel):
    home: float = Field(..., description="Home expected corners (lambda_home_corners)")
    away: float = Field(..., description="Away expected corners (lambda_away_corners)")
    total: float = Field(..., description="Total match expected corners (lambda_total_corners)")


class TotalCornersMarket(BaseModel):
    over_7_5: float
    under_7_5: float
    over_8_5: float
    under_8_5: float
    over_9_5: float
    under_9_5: float
    over_10_5: float
    under_10_5: float
    over_11_5: float
    under_11_5: float


class TeamCornersThresholds(BaseModel):
    over_3_5: float
    over_4_5: float
    over_5_5: float


class CornerConfidence(BaseModel):
    overall: int = Field(ge=0, le=100)
    data_quality: int = Field(ge=0, le=100)
    sample_strength: int = Field(ge=0, le=100)
    model_stability: int = Field(ge=0, le=100)
    label: str = Field(..., description="'insufficient' | 'low' | 'moderate' | 'good' | 'strong'")


class CornerModelMetadata(BaseModel):
    version: str = "v1_corners_nb"
    dispersion: float
    dispersion_source: str = Field(..., description="'competition' | 'shrunk_competition' | 'global' | 'fallback'")


class CornersPrediction(BaseModel):
    available: bool = Field(..., description="True if sufficient historical corner data exists")
    reason: Optional[str] = None
    expected: Optional[ExpectedCorners] = None
    total_markets: Optional[TotalCornersMarket] = None
    home_team: Optional[TeamCornersThresholds] = None
    away_team: Optional[TeamCornersThresholds] = None
    confidence: Optional[CornerConfidence] = None
    model: Optional[CornerModelMetadata] = None
    diagnostics: Optional[Dict[str, Any]] = None


# ==========================================
# PHASE 3: CARDS & REFEREE INTELLIGENCE SCHEMAS
# ==========================================

class ExpectedCards(BaseModel):
    home: float = Field(..., description="Home expected total cards (lambda_home_cards)")
    away: float = Field(..., description="Away expected total cards (lambda_away_cards)")
    total: float = Field(..., description="Total match expected cards (lambda_total_cards)")
    home_yellow: Optional[float] = None
    away_yellow: Optional[float] = None
    total_yellow: Optional[float] = None


class TotalCardsMarket(BaseModel):
    over_1_5: float
    under_1_5: float
    over_2_5: float
    under_2_5: float
    over_3_5: float
    under_3_5: float
    over_4_5: float
    under_4_5: float
    over_5_5: float
    under_5_5: float
    over_6_5: float
    under_6_5: float


class TeamCardsThresholds(BaseModel):
    over_0_5: float
    over_1_5: float
    over_2_5: float
    over_3_5: float


class RedCardRisk(BaseModel):
    available: bool
    any_red_prob: float = Field(..., description="Probability of at least 1 red card in match")
    home_red_prob: float = Field(..., description="Probability of Home team receiving a red card")
    away_red_prob: float = Field(..., description="Probability of Away team receiving a red card")
    risk_level: str = Field(..., description="'Low' | 'Moderate' | 'High'")


class RefereeIntelligence(BaseModel):
    available: bool
    referee_name: Optional[str] = None
    sample_size: int = 0
    average_cards: Optional[float] = None
    influence_factor: float = 1.0
    influence_label: str = "Neutral"
    source: str = Field(default="fallback", description="'referee' | 'shrunk_referee' | 'competition' | 'global' | 'fallback'")


class CardConfidence(BaseModel):
    overall: int = Field(ge=0, le=100)
    data_quality: int = Field(ge=0, le=100)
    sample_strength: int = Field(ge=0, le=100)
    model_stability: int = Field(ge=0, le=100)
    referee_confidence: int = Field(ge=0, le=100)
    label: str = Field(..., description="'insufficient' | 'low' | 'moderate' | 'good' | 'strong'")


class CardModelMetadata(BaseModel):
    version: str = "v1_cards_nb"
    dispersion: float
    dispersion_source: str = Field(..., description="'competition' | 'shrunk_competition' | 'global' | 'fallback'")
    baseline_source: str = Field(..., description="'competition' | 'shrunk_competition' | 'global' | 'fallback'")
    referee_source: str = Field(..., description="'referee' | 'shrunk_referee' | 'competition' | 'global' | 'fallback'")


class CardsPrediction(BaseModel):
    available: bool = Field(..., description="True if sufficient historical card data exists")
    reason: Optional[str] = None
    expected: Optional[ExpectedCards] = None
    total_markets: Optional[TotalCardsMarket] = None
    home_team: Optional[TeamCardsThresholds] = None
    away_team: Optional[TeamCardsThresholds] = None
    red_card_risk: Optional[RedCardRisk] = None
    referee: Optional[RefereeIntelligence] = None
    confidence: Optional[CardConfidence] = None
    model: Optional[CardModelMetadata] = None
    diagnostics: Optional[Dict[str, Any]] = None


class MatchIntelligencePrediction(BaseModel):
    fixture_id: int
    model: ModelMetadata
    expected_goals: ExpectedGoals
    result: ResultMarket
    goals: GoalsMarket
    btts: BTTSMarket
    home_team_goals: TeamGoalThresholds
    away_team_goals: TeamGoalThresholds
    halves: HalvesMarket
    exact_scores: List[ExactScore]
    confidence: ConfidenceDetails
    best_signal: BestModelSignal
    corners: Optional[CornersPrediction] = None
    cards: Optional[CardsPrediction] = None

