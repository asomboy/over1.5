from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class LiveMarketProbability(BaseModel):
    probability: float = Field(ge=0.0, le=1.0)
    status: str = Field(default="active", description="'active' | 'already_resolved'")
    resolved_result: Optional[bool] = None


class LiveMatchStateSchema(BaseModel):
    fixture_id: int
    minute: int
    added_time: int = 0
    period: str = "1H"
    status: str = "LIVE"
    home_score: int
    away_score: int
    home_corners: Optional[int] = None
    away_corners: Optional[int] = None
    home_shots: Optional[int] = None
    away_shots: Optional[int] = None
    home_shots_on_target: Optional[int] = None
    away_shots_on_target: Optional[int] = None
    home_possession: Optional[float] = None
    away_possession: Optional[float] = None
    home_fouls: Optional[int] = None
    away_fouls: Optional[int] = None
    home_yellow_cards: Optional[int] = None
    away_yellow_cards: Optional[int] = None
    home_red_cards: Optional[int] = None
    away_red_cards: Optional[int] = None
    last_updated: Optional[str] = None
    data_source: str = "live_feed"
    data_quality: str = "verified"


class LiveGoalsPrediction(BaseModel):
    pre_match_xg: Dict[str, float]
    remaining_xg: Dict[str, float]
    current_score: Dict[str, int]
    at_least_1_more_goal: LiveMarketProbability
    at_least_2_more_goals: LiveMarketProbability
    full_match_over_1_5: LiveMarketProbability
    full_match_over_2_5: LiveMarketProbability
    full_match_over_3_5: LiveMarketProbability
    full_match_over_4_5: LiveMarketProbability
    btts_yes: LiveMarketProbability
    btts_no: LiveMarketProbability
    home_to_score_again: LiveMarketProbability
    away_to_score_again: LiveMarketProbability
    next_goal: Dict[str, float]


class LiveCornersPrediction(BaseModel):
    pre_match_expected_corners: float
    current_corners: Dict[str, int]
    remaining_expected_corners: Dict[str, float]
    over_7_5: LiveMarketProbability
    over_8_5: LiveMarketProbability
    over_9_5: LiveMarketProbability
    over_10_5: LiveMarketProbability
    over_11_5: LiveMarketProbability
    next_corner: Dict[str, float]


class LiveCardsPrediction(BaseModel):
    current_cards: Dict[str, int]
    remaining_expected_cards: Dict[str, float]
    at_least_1_more_card: LiveMarketProbability
    over_current_plus_1_5: LiveMarketProbability
    over_current_plus_2_5: LiveMarketProbability
    next_card: Dict[str, float]
    any_red_card: LiveMarketProbability
    referee_tendency: Optional[Dict[str, Any]] = None


class LiveSignalItem(BaseModel):
    market: str
    category: str = Field(..., description="'goals' | 'corners' | 'cards'")
    probability: float
    confidence_score: int
    time_remaining_minutes: float
    signal_strength: str = Field(..., description="'STRONG' | 'MODERATE' | 'WATCH'")
    rationale: str


class BestLiveSignal(BaseModel):
    market: Optional[str] = None
    category: Optional[str] = None
    probability: float = 0.0
    signal_score: int = 0
    label: str = Field(default="NO_SIGNAL", description="'STRONG' | 'MODERATE' | 'WATCH' | 'NO_SIGNAL'")
    time_remaining_minutes: float = 0.0
    rationale: Optional[str] = None


class LiveConfidence(BaseModel):
    overall_confidence: int = Field(ge=0, le=100)
    pre_match_confidence: int = Field(ge=0, le=100)
    live_data_quality: int = Field(ge=0, le=100)
    statistical_coverage: int = Field(ge=0, le=100)
    model_stability: int = Field(ge=0, le=100)
    time_sensitivity: int = Field(ge=0, le=100)
    label: str = Field(..., description="'insufficient' | 'low' | 'moderate' | 'good' | 'strong'")


class LiveDiagnostics(BaseModel):
    prior_weight: float
    live_weight: float
    effective_remaining_minutes: float
    score_state_adjustment: Dict[str, Any]
    momentum_adjustment: Dict[str, Any]
    red_card_adjustment: Dict[str, Any]


class LiveIntelligenceResponse(BaseModel):
    fixture_id: int
    model_version: str = "v1_live_intelligence"
    match_state: LiveMatchStateSchema
    live_goals: LiveGoalsPrediction
    live_corners: LiveCornersPrediction
    live_cards: LiveCardsPrediction
    live_signals: List[LiveSignalItem]
    best_live_signal: BestLiveSignal
    confidence: LiveConfidence
    diagnostics: LiveDiagnostics
    events: Optional[List[Dict[str, Any]]] = None
    narrative: Optional[List[Dict[str, Any]]] = None
    data_status: Optional[str] = "FRESH"
    retrieved_at: Optional[str] = None
    observed: Optional[Dict[str, Any]] = None


class LiveFixtureSummary(BaseModel):
    fixture_id: int
    league_name: Optional[str] = None
    home_team_name: str
    away_team_name: str
    minute: int
    period: str
    home_score: int
    away_score: int
    best_signal: BestLiveSignal
    confidence: int
    data_quality: int
    last_update: Optional[str] = None


class CanonicalLiveMatchResponse(BaseModel):
    fixture: Dict[str, Any]
    live_state: Dict[str, Any]
    statistics: Dict[str, Any]
    events: List[Dict[str, Any]]
    narrative: List[Dict[str, Any]]
    data_quality: Dict[str, Any]
    provider: Dict[str, Any]
    predictions: Dict[str, Any]
    signals: List[Dict[str, Any]]
    retrieved_at: str
    status: str
