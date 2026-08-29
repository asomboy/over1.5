import os
import sys
import logging
from typing import Dict, Any, List, Optional, Tuple

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logger = logging.getLogger(__name__)


class MarketResolutionService:
    """
    Universal market resolution engine mapping verified final football match scores,
    corner boxscores, and card counts into ground-truth evaluation outcomes (1.0 / 0.0 or continuous values).
    """

    @classmethod
    def resolve_market_outcome(
        cls,
        market: str,
        home_score: Optional[int],
        away_score: Optional[int],
        ht_home_score: Optional[int] = None,
        ht_away_score: Optional[int] = None,
        home_corners: Optional[int] = None,
        away_corners: Optional[int] = None,
        home_yellow: Optional[int] = None,
        away_yellow: Optional[int] = None,
        home_red: Optional[int] = None,
        away_red: Optional[int] = None
    ) -> Optional[float]:
        """
        Determines binary (1.0 / 0.0) or count outcome for any standard football prediction market.
        Returns None if required statistical data is absent.
        """
        # 1. Goals Markets (requires home_score & away_score)
        if home_score is not None and away_score is not None:
            tot_goals = home_score + away_score

            if market in ["over_0_5", "over_0_5_goals"]:
                return 1.0 if tot_goals >= 1 else 0.0
            elif market in ["over_1_5", "over_1_5_goals"]:
                return 1.0 if tot_goals >= 2 else 0.0
            elif market in ["over_2_5", "over_2_5_goals"]:
                return 1.0 if tot_goals >= 3 else 0.0
            elif market in ["over_3_5", "over_3_5_goals"]:
                return 1.0 if tot_goals >= 4 else 0.0
            elif market in ["over_4_5", "over_4_5_goals"]:
                return 1.0 if tot_goals >= 5 else 0.0
            elif market in ["under_1_5", "under_1_5_goals"]:
                return 1.0 if tot_goals <= 1 else 0.0
            elif market in ["under_2_5", "under_2_5_goals"]:
                return 1.0 if tot_goals <= 2 else 0.0
            elif market in ["under_3_5", "under_3_5_goals"]:
                return 1.0 if tot_goals <= 3 else 0.0
            elif market in ["btts", "btts_yes", "both_teams_to_score"]:
                return 1.0 if (home_score >= 1 and away_score >= 1) else 0.0
            elif market == "btts_no":
                return 1.0 if (home_score == 0 or away_score == 0) else 0.0
            elif market in ["1x2_home", "home_win"]:
                return 1.0 if home_score > away_score else 0.0
            elif market in ["1x2_draw", "draw"]:
                return 1.0 if home_score == away_score else 0.0
            elif market in ["1x2_away", "away_win"]:
                return 1.0 if away_score > home_score else 0.0
            elif market in ["home_over_0_5", "home_to_score"]:
                return 1.0 if home_score >= 1 else 0.0
            elif market in ["home_over_1_5", "home_over_1_5_goals"]:
                return 1.0 if home_score >= 2 else 0.0
            elif market in ["away_over_0_5", "away_to_score"]:
                return 1.0 if away_score >= 1 else 0.0
            elif market in ["away_over_1_5", "away_over_1_5_goals"]:
                return 1.0 if away_score >= 2 else 0.0
            elif market == "expected_goals_home":
                return float(home_score)
            elif market == "expected_goals_away":
                return float(away_score)
            elif market == "expected_goals_total":
                return float(tot_goals)

        # 2. Half-Time Goals Markets (requires ht_home & ht_away)
        if ht_home_score is not None and ht_away_score is not None:
            ht_tot = ht_home_score + ht_away_score
            if market in ["ht_over_0_5", "ht_over_0_5_goals"]:
                return 1.0 if ht_tot >= 1 else 0.0
            elif market in ["ht_over_1_5", "ht_over_1_5_goals"]:
                return 1.0 if ht_tot >= 2 else 0.0
            elif market in ["ht_under_1_5", "ht_under_1_5_goals"]:
                return 1.0 if ht_tot <= 1 else 0.0

        # 3. Corners Markets (requires home_corners & away_corners)
        if home_corners is not None and away_corners is not None:
            tot_corners = home_corners + away_corners
            if market in ["over_7_5_corners", "over_7_5"]:
                return 1.0 if tot_corners >= 8 else 0.0
            elif market in ["over_8_5_corners", "over_8_5"]:
                return 1.0 if tot_corners >= 9 else 0.0
            elif market in ["over_9_5_corners", "over_9_5"]:
                return 1.0 if tot_corners >= 10 else 0.0
            elif market in ["over_10_5_corners", "over_10_5"]:
                return 1.0 if tot_corners >= 11 else 0.0
            elif market in ["over_11_5_corners", "over_11_5"]:
                return 1.0 if tot_corners >= 12 else 0.0
            elif market in ["under_8_5_corners", "under_8_5"]:
                return 1.0 if tot_corners <= 8 else 0.0
            elif market in ["under_9_5_corners", "under_9_5"]:
                return 1.0 if tot_corners <= 9 else 0.0
            elif market in ["under_10_5_corners", "under_10_5"]:
                return 1.0 if tot_corners <= 10 else 0.0
            elif market == "expected_corners_total":
                return float(tot_corners)

        # 4. Disciplinary / Cards Markets (requires yellow / red cards)
        if home_yellow is not None and away_yellow is not None:
            tot_yellow = home_yellow + away_yellow
            tot_red = (home_red or 0) + (away_red or 0)
            tot_cards = tot_yellow + tot_red

            if market in ["over_2_5_cards", "over_2_5"]:
                return 1.0 if tot_cards >= 3 else 0.0
            elif market in ["over_3_5_cards", "over_3_5"]:
                return 1.0 if tot_cards >= 4 else 0.0
            elif market in ["over_4_5_cards", "over_4_5"]:
                return 1.0 if tot_cards >= 5 else 0.0
            elif market in ["over_5_5_cards", "over_5_5"]:
                return 1.0 if tot_cards >= 6 else 0.0
            elif market in ["over_6_5_cards", "over_6_5"]:
                return 1.0 if tot_cards >= 7 else 0.0
            elif market in ["under_4_5_cards", "under_4_5"]:
                return 1.0 if tot_cards <= 4 else 0.0
            elif market in ["any_red_card", "red_card"]:
                return 1.0 if tot_red >= 1 else 0.0
            elif market == "expected_cards_total":
                return float(tot_cards)

        return None
