import os
import sys
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class LiveNarrativeEngine:
    """
    Generates fixture-specific factual live narrative statements from observed changes.
    Never manufactures fictional commentary. Every statement is directly traceable to
    provider-observed facts and previous snapshots.
    """

    @classmethod
    def generate_narrative(
        cls,
        fixture_id: int,
        home_team: str,
        away_team: str,
        current_state: Dict[str, Any],
        previous_state: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Compares current live snapshot with previous snapshot to produce verified factual updates.
        Returns a list of structured narrative items:
        [{
            "id": "...",
            "minute": int,
            "category": "SCORE" | "ATTACK" | "DISCIPLINE" | "CONTROL" | "STATUS",
            "statement": str,
            "field": str,
            "observed_value": Any,
            "retrieved_at": str
        }]
        """
        statements: List[Dict[str, Any]] = []
        retrieved_at = current_state.get("retrieved_at", datetime.now(timezone.utc).isoformat())
        curr_min = current_state.get("minute", 0)
        curr_clock = current_state.get("display_clock", f"{curr_min}'")

        curr_score = current_state.get("score", {})
        curr_h_score = curr_score.get("home", 0)
        curr_a_score = curr_score.get("away", 0)

        stats = current_state.get("statistics", {})
        shots_stat = stats.get("shots", {})
        sot_stat = stats.get("shots_on_target", {})
        corn_stat = stats.get("corners", {})
        cards_stat = stats.get("cards", {})
        poss_stat = stats.get("possession", {})
        fouls_stat = stats.get("fouls", {})

        h_shots = shots_stat.get("home")
        a_shots = shots_stat.get("away")
        h_sot = sot_stat.get("home")
        a_sot = sot_stat.get("away")
        h_corn = corn_stat.get("home")
        a_corn = corn_stat.get("away")
        h_poss = poss_stat.get("home")
        a_poss = poss_stat.get("away")
        h_yellow = cards_stat.get("home_yellow")
        a_yellow = cards_stat.get("away_yellow")
        h_red = cards_stat.get("home_red")
        a_red = cards_stat.get("away_red")

        # 1. State change analysis against previous state
        if previous_state:
            prev_score = previous_state.get("score", {})
            prev_h_score = prev_score.get("home", 0)
            prev_a_score = prev_score.get("away", 0)

            # Score change
            if curr_h_score > prev_h_score:
                diff = curr_h_score - prev_h_score
                statements.append({
                    "id": f"narr_goal_h_{curr_min}_{diff}",
                    "minute": curr_min,
                    "category": "SCORE",
                    "statement": f"GOAL! {home_team} scores to make it {curr_h_score}–{curr_a_score}.",
                    "field": "score.home",
                    "observed_value": curr_h_score,
                    "retrieved_at": retrieved_at
                })
            if curr_a_score > prev_a_score:
                diff = curr_a_score - prev_a_score
                statements.append({
                    "id": f"narr_goal_a_{curr_min}_{diff}",
                    "minute": curr_min,
                    "category": "SCORE",
                    "statement": f"GOAL! {away_team} scores to make it {curr_h_score}–{curr_a_score}.",
                    "field": "score.away",
                    "observed_value": curr_a_score,
                    "retrieved_at": retrieved_at
                })

            # Shot changes
            prev_stats = previous_state.get("statistics", {})
            prev_h_shots = prev_stats.get("shots", {}).get("home")
            prev_a_shots = prev_stats.get("shots", {}).get("away")

            if h_shots is not None and prev_h_shots is not None and h_shots > prev_h_shots:
                s_diff = int(h_shots - prev_h_shots)
                statements.append({
                    "id": f"narr_shots_h_{curr_min}",
                    "minute": curr_min,
                    "category": "ATTACK",
                    "statement": f"{home_team} has recorded {s_diff} shot{'s' if s_diff > 1 else ''} in the latest verified interval.",
                    "field": "statistics.shots.home",
                    "observed_value": h_shots,
                    "retrieved_at": retrieved_at
                })
            if a_shots is not None and prev_a_shots is not None and a_shots > prev_a_shots:
                s_diff = int(a_shots - prev_a_shots)
                statements.append({
                    "id": f"narr_shots_a_{curr_min}",
                    "minute": curr_min,
                    "category": "ATTACK",
                    "statement": f"{away_team} has recorded {s_diff} shot{'s' if s_diff > 1 else ''} in the latest verified interval.",
                    "field": "statistics.shots.away",
                    "observed_value": a_shots,
                    "retrieved_at": retrieved_at
                })

            # Corner changes
            prev_h_corn = prev_stats.get("corners", {}).get("home")
            prev_a_corn = prev_stats.get("corners", {}).get("away")
            if h_corn is not None and prev_h_corn is not None and h_corn > prev_h_corn:
                c_diff = int(h_corn - prev_h_corn)
                statements.append({
                    "id": f"narr_corn_h_{curr_min}",
                    "minute": curr_min,
                    "category": "ATTACK",
                    "statement": f"{home_team} was awarded {c_diff} new corner{'s' if c_diff > 1 else ''}.",
                    "field": "statistics.corners.home",
                    "observed_value": h_corn,
                    "retrieved_at": retrieved_at
                })
            if a_corn is not None and prev_a_corn is not None and a_corn > prev_a_corn:
                c_diff = int(a_corn - prev_a_corn)
                statements.append({
                    "id": f"narr_corn_a_{curr_min}",
                    "minute": curr_min,
                    "category": "ATTACK",
                    "statement": f"{away_team} was awarded {c_diff} new corner{'s' if c_diff > 1 else ''}.",
                    "field": "statistics.corners.away",
                    "observed_value": a_corn,
                    "retrieved_at": retrieved_at
                })

            # Red card changes
            prev_h_red = prev_stats.get("cards", {}).get("home_red")
            prev_a_red = prev_stats.get("cards", {}).get("away_red")
            if h_red is not None and prev_h_red is not None and h_red > prev_h_red:
                statements.append({
                    "id": f"narr_red_h_{curr_min}",
                    "minute": curr_min,
                    "category": "DISCIPLINE",
                    "statement": f"RED CARD! {home_team} is reduced to {11 - int(h_red)} players.",
                    "field": "statistics.cards.home_red",
                    "observed_value": h_red,
                    "retrieved_at": retrieved_at
                })
            if a_red is not None and prev_a_red is not None and a_red > prev_a_red:
                statements.append({
                    "id": f"narr_red_a_{curr_min}",
                    "minute": curr_min,
                    "category": "DISCIPLINE",
                    "statement": f"RED CARD! {away_team} is reduced to {11 - int(a_red)} players.",
                    "field": "statistics.cards.away_red",
                    "observed_value": a_red,
                    "retrieved_at": retrieved_at
                })

        # 2. Cumulative factual summaries if few change statements
        if h_sot is not None and a_sot is not None and (h_sot > 0 or a_sot > 0):
            if h_sot > a_sot:
                statements.append({
                    "id": f"narr_sot_lead_h_{curr_min}",
                    "minute": curr_min,
                    "category": "ATTACK",
                    "statement": f"{home_team} currently leads shots on target {int(h_sot)}–{int(a_sot)}.",
                    "field": "statistics.shots_on_target",
                    "observed_value": {"home": h_sot, "away": a_sot},
                    "retrieved_at": retrieved_at
                })
            elif a_sot > h_sot:
                statements.append({
                    "id": f"narr_sot_lead_a_{curr_min}",
                    "minute": curr_min,
                    "category": "ATTACK",
                    "statement": f"{away_team} currently leads shots on target {int(a_sot)}–{int(h_sot)}.",
                    "field": "statistics.shots_on_target",
                    "observed_value": {"home": h_sot, "away": a_sot},
                    "retrieved_at": retrieved_at
                })

        # Possession dominance (> 60%)
        if h_poss is not None and a_poss is not None:
            if h_poss >= 60.0:
                statements.append({
                    "id": f"narr_poss_h_{curr_min}",
                    "minute": curr_min,
                    "category": "CONTROL",
                    "statement": f"{home_team} currently dominates possession with {round(h_poss, 1)}%.",
                    "field": "statistics.possession.home",
                    "observed_value": h_poss,
                    "retrieved_at": retrieved_at
                })
            elif a_poss >= 60.0:
                statements.append({
                    "id": f"narr_poss_a_{curr_min}",
                    "minute": curr_min,
                    "category": "CONTROL",
                    "statement": f"{away_team} currently dominates possession with {round(a_poss, 1)}%.",
                    "field": "statistics.possession.away",
                    "observed_value": a_poss,
                    "retrieved_at": retrieved_at
                })

        # Total corners recorded
        if h_corn is not None and a_corn is not None and (h_corn + a_corn) >= 4:
            statements.append({
                "id": f"narr_corn_tot_{curr_min}",
                "minute": curr_min,
                "category": "ATTACK",
                "statement": f"{int(h_corn + a_corn)} corners recorded ({home_team} {int(h_corn)} — {away_team} {int(a_corn)}).",
                "field": "statistics.corners",
                "observed_value": h_corn + a_corn,
                "retrieved_at": retrieved_at
            })

        # Completed match statement
        if current_state.get("is_completed"):
            statements.insert(0, {
                "id": f"narr_ft_{curr_min}",
                "minute": curr_min,
                "category": "STATUS",
                "statement": f"Full Time. Verified final score: {home_team} {curr_h_score}–{curr_a_score} {away_team}.",
                "field": "status",
                "observed_value": "FINISHED",
                "retrieved_at": retrieved_at
            })

        # Default fallback if nothing material has occurred
        if not statements:
            statements.append({
                "id": f"narr_neutral_{curr_min}",
                "minute": curr_min,
                "category": "STATUS",
                "statement": "No material verified change since the previous update.",
                "field": "status",
                "observed_value": "STABLE",
                "retrieved_at": retrieved_at
            })

        return statements
