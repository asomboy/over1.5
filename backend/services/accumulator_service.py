import os
import sys
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session, joinedload

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import Fixture, Prediction, League, Team
except ImportError:
    from ..models import Fixture, Prediction, League, Team

logger = logging.getLogger(__name__)


class AccumulatorGeneratorService:
    """
    Smart Accumulator Generator Service.
    
    Constructs high-probability, diversified betting accumulators (slips)
    from upcoming fixture goal predictions while enforcing cross-league diversification.
    """

    @classmethod
    def generate_accumulators(cls, db: Session, match_day: Optional[str] = None) -> Dict[str, Any]:
        """
        Generates 3 curated accumulator options:
        1. Safe Double (2 picks, Over 1.5, highest probability)
        2. Over 1.5 5-Fold (5 picks, Over 1.5, max 1 pick per league)
        3. High Yield 3-Fold (3 picks, Over 2.5 / BTTS)
        """
        now = datetime.now(timezone.utc)
        now_cutoff = (now - timedelta(hours=2)).replace(tzinfo=None)

        query = (
            db.query(Fixture)
            .options(
                joinedload(Fixture.league),
                joinedload(Fixture.home_team),
                joinedload(Fixture.away_team),
                joinedload(Fixture.predictions)
            )
            .filter(Fixture.status != "FINISHED", Fixture.match_date >= now_cutoff)
            .order_by(Fixture.match_date.asc())
        )

        fixtures = query.all()

        # Parse valid candidate fixtures with predictions
        candidates = []
        for fix in fixtures:
            pred = fix.predictions[0] if fix.predictions else None
            if not pred:
                continue

            o15 = float(pred.over_1_5_probability or 0.0)
            o25 = float(pred.over_2_5_probability or 0.0)
            btts = float(pred.btts_probability or 0.0)
            conf = float(pred.confidence_score or 0.50)
            h_xg = float(pred.predicted_home_score or 1.45)
            a_xg = float(pred.predicted_away_score or 1.15)
            tot_xg = round(h_xg + a_xg, 2)

            match_date_str = fix.match_date.strftime("%Y-%m-%d") if fix.match_date else ""

            candidates.append({
                "fixture_id": fix.id,
                "home_team": fix.home_team.name if fix.home_team else "Home Team",
                "away_team": fix.away_team.name if fix.away_team else "Away Team",
                "league": fix.league.name if fix.league else "League",
                "league_id": fix.league_id,
                "match_date": fix.match_date.isoformat() if fix.match_date else "",
                "match_date_key": match_date_str,
                "over_1_5_prob": o15,
                "over_2_5_prob": o25,
                "btts_prob": btts,
                "confidence_score": conf,
                "expected_goals": tot_xg,
                "most_likely_score": pred.most_likely_score or "2-1"
            })

        if match_day and match_day != 'ALL_DAYS':
            candidates = [c for c in candidates if c["match_date_key"] == match_day]

        # 1. Safe Double (2 legs, top Over 1.5, different leagues)
        safe_double = cls._build_acca(candidates, target_legs=2, market="over_1_5", min_prob=0.75, max_per_league=1)
        
        # 2. Over 1.5 5-Fold (5 legs, max 1 per league)
        over15_5fold = cls._build_acca(candidates, target_legs=5, market="over_1_5", min_prob=0.72, max_per_league=1)

        # 3. High Yield 3-Fold (3 legs, Over 2.5 or BTTS, max 1 per league)
        high_yield_3fold = cls._build_acca(candidates, target_legs=3, market="over_2_5", min_prob=0.52, max_per_league=1)

        return {
            "status": "ok",
            "total_candidates_analyzed": len(candidates),
            "accumulators": {
                "safe_double": safe_double,
                "over_1_5_5fold": over15_5fold,
                "high_yield_3fold": high_yield_3fold
            }
        }

    @classmethod
    def _build_acca(
        cls,
        candidates: List[Dict[str, Any]],
        target_legs: int,
        market: str = "over_1_5",
        min_prob: float = 0.70,
        max_per_league: int = 1
    ) -> Dict[str, Any]:
        """Builds an accumulator slip adhering to target leg counts and league diversity."""
        key_name = f"{market}_prob"
        
        # Sort candidates by target market probability descending
        sorted_candidates = sorted(candidates, key=lambda x: x.get(key_name, 0.0), reverse=True)
        filtered = [c for c in sorted_candidates if c.get(key_name, 0.0) >= min_prob]

        # Fallback if filtered list is too small
        if len(filtered) < target_legs:
            filtered = sorted_candidates

        selected_legs = []
        league_counts: Dict[int, int] = {}

        for c in filtered:
            if len(selected_legs) >= target_legs:
                break
            l_id = c["league_id"]
            if league_counts.get(l_id, 0) < max_per_league:
                # Format market label & implied odds
                prob = c.get(key_name, 0.70)
                implied_odds = round(min(3.50, max(1.15, 1.0 / max(0.10, prob))), 2)

                leg_market_label = "Over 1.5 Goals"
                if market == "over_2_5":
                    leg_market_label = "Over 2.5 Goals" if c.get("over_2_5_prob", 0) >= c.get("btts_prob", 0) else "Both Teams To Score"

                selected_legs.append({
                    "fixture_id": c["fixture_id"],
                    "match": f"{c['home_team']} vs {c['away_team']}",
                    "home_team": c["home_team"],
                    "away_team": c["away_team"],
                    "league": c["league"],
                    "match_date": c["match_date"],
                    "market": leg_market_label,
                    "probability": round(prob * 100, 1),
                    "implied_odds": implied_odds,
                    "expected_goals": c["expected_goals"],
                    "most_likely_score": c["most_likely_score"]
                })
                league_counts[l_id] = league_counts.get(l_id, 0) + 1

        if not selected_legs:
            return {"legs": [], "total_odds": 1.0, "combined_prob": 0.0, "status": "insufficient_data"}

        total_odds = 1.0
        combined_prob = 1.0
        for leg in selected_legs:
            total_odds *= leg["implied_odds"]
            combined_prob *= (leg["probability"] / 100.0)

        return {
            "legs_count": len(selected_legs),
            "legs": selected_legs,
            "total_odds": round(total_odds, 2),
            "combined_probability": round(combined_prob * 100, 1),
            "status": "ready"
        }
