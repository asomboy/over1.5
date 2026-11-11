import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import BetSlip, Fixture, HistoricalResult, MatchStatistics
    from services.market_resolution_service import MarketResolutionService
except ImportError:
    from ..models import BetSlip, Fixture, HistoricalResult, MatchStatistics
    from .market_resolution_service import MarketResolutionService


class BetSlipService:
    """
    Manages tracked bet slips, +EV edge calculations against user bookmaker odds,
    quarter-Kelly sizing, and automatic match settlement against final results.
    """

    MAX_STORED_SLIPS = 500

    DISPLAY_MARKET_ALIASES: Dict[str, str] = {
        "over 0.5": "over_0_5_goals",
        "over 0.5 goals": "over_0_5_goals",
        "over 1.5": "over_1_5_goals",
        "over 1.5 goals": "over_1_5_goals",
        "full match over 1.5": "over_1_5_goals",
        "full match over 1.5 goals": "over_1_5_goals",
        "live o1.5": "over_1_5_goals",
        "over 2.5": "over_2_5_goals",
        "over 2.5 goals": "over_2_5_goals",
        "full match over 2.5": "over_2_5_goals",
        "full match over 2.5 goals": "over_2_5_goals",
        "live o2.5": "over_2_5_goals",
        "over 3.5": "over_3_5_goals",
        "over 3.5 goals": "over_3_5_goals",
        "full match over 3.5": "over_3_5_goals",
        "full match over 3.5 goals": "over_3_5_goals",
        "under 2.5": "under_2_5_goals",
        "under 2.5 goals": "under_2_5_goals",
        "under 3.5": "under_3_5_goals",
        "under 3.5 goals": "under_3_5_goals",
        "btts yes": "btts_yes",
        "btts": "btts_yes",
        "both teams to score": "btts_yes",
        "btts no": "btts_no",
        "home win": "1x2_home",
        "1": "1x2_home",
        "draw": "1x2_draw",
        "x": "1x2_draw",
        "away win": "1x2_away",
        "2": "1x2_away",
        "1h over 0.5": "ht_over_0_5_goals",
        "1h over 0.5 goals": "ht_over_0_5_goals",
        "over 8.5 corners": "over_8_5_corners",
        "over 9.5 corners": "over_9_5_corners",
        "over 3.5 cards": "over_3_5_cards",
        "over 4.5 cards": "over_4_5_cards",
    }

    @staticmethod
    def calculate_leg_ev(
        model_prob: float, bookmaker_odds: Optional[float] = None
    ) -> Dict[str, Any]:
        """Calculates model fair odds, true +EV % against user bookmaker odds, and quarter-Kelly fraction."""
        prob = max(0.01, min(0.99, float(model_prob)))
        fair_odds = round(1.0 / prob, 2)

        if bookmaker_odds is not None and float(bookmaker_odds) > 1.0:
            eff_odds = round(float(bookmaker_odds), 2)
            edge = prob * eff_odds - 1.0
            ev_pct = round(edge * 100.0, 2)
            kelly_raw = edge / max(0.01, eff_odds - 1.0)
            kelly_quarter = round(min(0.10, max(0.0, kelly_raw * 0.25)), 4)
        else:
            eff_odds = fair_odds
            ev_pct = 0.0
            kelly_quarter = 0.0

        return {
            "model_prob": round(prob, 4),
            "fair_odds": fair_odds,
            "effective_odds": eff_odds,
            "ev_pct": ev_pct,
            "kelly_fraction": kelly_quarter,
        }

    @classmethod
    def resolve_leg_outcome(
        cls,
        market: str,
        fixture: Optional[Fixture],
        hist: Optional[HistoricalResult],
        stats: Optional[MatchStatistics],
        baseline_goals: Optional[int] = None,
    ) -> Optional[str]:
        """Grades a slip leg against factual finished match results, returning 'WON', 'LOST', 'VOID', or None."""
        if not fixture:
            return None

        st = str(fixture.status or "").upper()
        if st in ("POSTPONED", "CANCELLED", "CANCELED", "ABANDONED"):
            return "VOID"

        if st not in ("FINISHED", "FT", "AET", "PEN"):
            return None

        h_score = fixture.home_score if fixture.home_score is not None else (hist.home_score if hist else None)
        a_score = fixture.away_score if fixture.away_score is not None else (hist.away_score if hist else None)
        if h_score is None or a_score is None:
            return None

        norm_market = str(market or "").strip().lower()
        if "at least 1 more goal" in norm_market or "+1 more goal" in norm_market:
            base = int(baseline_goals or 0)
            return "WON" if (h_score + a_score) > base else "LOST"

        canonical = cls.DISPLAY_MARKET_ALIASES.get(norm_market, norm_market.replace(" ", "_"))
        res = MarketResolutionService.resolve_market_outcome(
            market=canonical,
            home_score=h_score,
            away_score=a_score,
            ht_home_score=hist.half_time_home_score if hist else None,
            ht_away_score=hist.half_time_away_score if hist else None,
            home_corners=stats.home_corners if stats else (hist.home_corners if hist else None),
            away_corners=stats.away_corners if stats else (hist.away_corners if hist else None),
            home_yellow=stats.home_yellow_cards if stats else (hist.home_yellow_cards if hist else None),
            away_yellow=stats.away_yellow_cards if stats else (hist.away_yellow_cards if hist else None),
            home_red=stats.home_red_cards if stats else (hist.home_red_cards if hist else None),
            away_red=stats.away_red_cards if stats else (hist.away_red_cards if hist else None),
        )
        if res is None:
            return None
        return "WON" if res >= 0.5 else "LOST"

    @classmethod
    def serialize_slip(cls, slip: BetSlip) -> Dict[str, Any]:
        """Serializes a BetSlip row with UTC ISO timestamps."""
        try:
            legs = json.loads(slip.legs_json or "[]")
        except Exception:
            legs = []
        created_iso = (
            slip.created_at.isoformat() + "Z"
            if slip.created_at and getattr(slip.created_at, "tzinfo", None) is None
            else (slip.created_at.isoformat() if slip.created_at else None)
        )
        settled_iso = (
            slip.settled_at.isoformat() + "Z"
            if slip.settled_at and getattr(slip.settled_at, "tzinfo", None) is None
            else (slip.settled_at.isoformat() if slip.settled_at else None)
        )
        return {
            "id": slip.id,
            "title": slip.title,
            "source": slip.source,
            "stake": round(float(slip.stake or 0.0), 2),
            "combined_odds": round(float(slip.combined_odds or 1.0), 2),
            "expected_win_prob": round(float(slip.expected_win_prob), 4) if slip.expected_win_prob is not None else None,
            "expected_value_pct": round(float(slip.expected_value_pct), 2) if slip.expected_value_pct is not None else None,
            "status": slip.status,
            "payout": round(float(slip.payout or 0.0), 2),
            "profit_loss": round(float(slip.profit_loss or 0.0), 2),
            "legs": legs if isinstance(legs, list) else [],
            "created_at": created_iso,
            "settled_at": settled_iso,
        }

    @classmethod
    def create_slip(
        cls,
        db: Session,
        title: str,
        source: str,
        stake: float,
        legs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Validates, prices, persists, and settles (if applicable) a new tracked BetSlip."""
        existing_count = db.query(BetSlip).count()
        if existing_count >= cls.MAX_STORED_SLIPS:
            oldest = db.query(BetSlip).order_by(BetSlip.created_at.asc()).limit(existing_count - cls.MAX_STORED_SLIPS + 1).all()
            for row in oldest:
                db.delete(row)

        normalized_legs: List[Dict[str, Any]] = []
        combined_odds = 1.0
        joint_prob = 1.0

        for leg in legs:
            fid = int(leg["fixture_id"])
            prob = float(leg.get("model_prob") or 0.5)
            bm_odds = leg.get("bookmaker_odds")
            ev_info = cls.calculate_leg_ev(prob, float(bm_odds) if bm_odds else None)
            combined_odds *= ev_info["effective_odds"]
            joint_prob *= ev_info["model_prob"]
            normalized_legs.append({
                "fixture_id": fid,
                "match_label": str(leg.get("match_label") or f"Fixture #{fid}"),
                "league_name": str(leg.get("league_name") or ""),
                "match_date": leg.get("match_date"),
                "market": str(leg.get("market") or "Over 1.5 Goals"),
                "model_prob": ev_info["model_prob"],
                "fair_odds": ev_info["fair_odds"],
                "bookmaker_odds": float(bm_odds) if (bm_odds and float(bm_odds) > 1.0) else None,
                "effective_odds": ev_info["effective_odds"],
                "ev_pct": ev_info["ev_pct"],
                "kelly_fraction": ev_info["kelly_fraction"],
                "baseline_goals": leg.get("baseline_goals"),
                "result": "PENDING",
                "final_score": None,
            })

        eff_stake = round(max(1.0, float(stake or 10.0)), 2)
        eff_combined = round(combined_odds, 2)
        eff_ev = round((joint_prob * eff_combined - 1.0) * 100.0, 2)

        slip = BetSlip(
            title=str(title or "Tracked Prediction Slip").strip()[:120],
            source=str(source or "manual").strip()[:32],
            stake=eff_stake,
            combined_odds=eff_combined,
            expected_win_prob=round(joint_prob, 4),
            expected_value_pct=eff_ev,
            status="PENDING",
            payout=0.0,
            profit_loss=0.0,
            legs_json=json.dumps(normalized_legs),
        )
        db.add(slip)
        db.commit()
        db.refresh(slip)

        cls.settle_open_slips(db)
        db.refresh(slip)
        return cls.serialize_slip(slip)

    @classmethod
    def settle_open_slips(cls, db: Session) -> int:
        """Resolves all PENDING bet slips whose leg fixtures have finished or been postponed."""
        pending = db.query(BetSlip).filter(BetSlip.status == "PENDING").all()
        if not pending:
            return 0

        fixture_ids = set()
        slip_legs_map: Dict[int, List[Dict[str, Any]]] = {}
        for s in pending:
            try:
                legs = json.loads(s.legs_json or "[]")
            except Exception:
                legs = []
            if not isinstance(legs, list):
                legs = []
            slip_legs_map[s.id] = legs
            for lg in legs:
                if isinstance(lg, dict) and lg.get("fixture_id") is not None:
                    fixture_ids.add(int(lg["fixture_id"]))

        fix_map: Dict[int, Fixture] = {}
        hist_map: Dict[int, HistoricalResult] = {}
        stats_map: Dict[int, MatchStatistics] = {}

        fid_list = list(fixture_ids)
        for i in range(0, len(fid_list), 500):
            chunk = fid_list[i:i + 500]
            for f in db.query(Fixture).filter(Fixture.id.in_(chunk)).all():
                fix_map[f.id] = f
            for h in db.query(HistoricalResult).filter(HistoricalResult.fixture_id.in_(chunk)).all():
                hist_map[h.fixture_id] = h
            for st in db.query(MatchStatistics).filter(MatchStatistics.fixture_id.in_(chunk)).all():
                stats_map[st.fixture_id] = st

        settled_count = 0
        dirty = False
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

        for slip in pending:
            legs = slip_legs_map.get(slip.id) or []
            if not legs:
                continue

            legs_changed = False
            any_lost = False
            any_pending = False
            active_odds_product = 1.0
            non_void_count = 0

            for lg in legs:
                fid = int(lg.get("fixture_id") or 0)
                fix = fix_map.get(fid)
                hist = hist_map.get(fid)
                stats = stats_map.get(fid)

                if fix and fix.home_score is not None and fix.away_score is not None:
                    score_str = f"{fix.home_score}-{fix.away_score}"
                    if lg.get("final_score") != score_str:
                        lg["final_score"] = score_str
                        legs_changed = True

                res = cls.resolve_leg_outcome(
                    market=str(lg.get("market") or ""),
                    fixture=fix,
                    hist=hist,
                    stats=stats,
                    baseline_goals=lg.get("baseline_goals"),
                )
                if res and lg.get("result") != res:
                    lg["result"] = res
                    legs_changed = True

                eff_res = lg.get("result", "PENDING")
                if eff_res == "LOST":
                    any_lost = True
                elif eff_res == "PENDING":
                    any_pending = True
                elif eff_res == "WON":
                    non_void_count += 1
                    active_odds_product *= float(lg.get("effective_odds") or lg.get("fair_odds") or 1.0)

            if legs_changed:
                slip.legs_json = json.dumps(legs)
                dirty = True

            if any_lost:
                slip.status = "LOST"
                slip.payout = 0.0
                slip.profit_loss = round(-float(slip.stake or 0.0), 2)
                slip.settled_at = now_naive
                settled_count += 1
                dirty = True
            elif not any_pending:
                if non_void_count == 0:
                    slip.status = "VOID"
                    slip.payout = round(float(slip.stake or 0.0), 2)
                    slip.profit_loss = 0.0
                else:
                    payout = round(float(slip.stake or 0.0) * active_odds_product, 2)
                    slip.status = "WON"
                    slip.payout = payout
                    slip.profit_loss = round(payout - float(slip.stake or 0.0), 2)
                slip.settled_at = now_naive
                settled_count += 1
                dirty = True

        if dirty:
            db.commit()
        return settled_count

    @classmethod
    def get_slips_and_summary(cls, db: Session, limit: int = 100) -> Dict[str, Any]:
        """Returns persisted slips alongside aggregate ROI, win rate, and drawdown stats."""
        cls.settle_open_slips(db)
        rows = db.query(BetSlip).order_by(BetSlip.created_at.desc(), BetSlip.id.desc()).limit(limit).all()
        slips = [cls.serialize_slip(r) for r in rows]

        all_rows = db.query(BetSlip).order_by(BetSlip.created_at.asc(), BetSlip.id.asc()).all()
        won_slips = sum(1 for r in all_rows if r.status == "WON")
        lost_slips = sum(1 for r in all_rows if r.status == "LOST")
        void_slips = sum(1 for r in all_rows if r.status == "VOID")
        pending_slips = sum(1 for r in all_rows if r.status == "PENDING")

        settled_non_void = won_slips + lost_slips
        total_staked = sum(float(r.stake or 0.0) for r in all_rows if r.status in ("WON", "LOST"))
        total_payout = sum(float(r.payout or 0.0) for r in all_rows if r.status in ("WON", "LOST"))
        net_profit = round(total_payout - total_staked, 2)
        roi_pct = round((net_profit / total_staked) * 100.0, 2) if total_staked > 0 else 0.0
        win_rate_pct = round((won_slips / settled_non_void) * 100.0, 1) if settled_non_void > 0 else 0.0

        # Cumulative equity curve drawdown across settled non-void slips
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in all_rows:
            if r.status in ("WON", "LOST"):
                running += float(r.profit_loss or 0.0)
                if running > peak:
                    peak = running
                dd = peak - running
                if dd > max_dd:
                    max_dd = dd

        return {
            "status": "ok",
            "slips": slips,
            "summary": {
                "total_slips": len(all_rows),
                "pending_slips": pending_slips,
                "settled_slips": won_slips + lost_slips + void_slips,
                "won_slips": won_slips,
                "lost_slips": lost_slips,
                "void_slips": void_slips,
                "win_rate_pct": win_rate_pct,
                "total_staked": round(total_staked, 2),
                "total_payout": round(total_payout, 2),
                "net_profit": net_profit,
                "roi_pct": roi_pct,
                "max_drawdown": round(max_dd, 2),
            },
        }

    @staticmethod
    def delete_slip(db: Session, slip_id: int) -> bool:
        """Deletes a single tracked bet slip by ID."""
        row = db.query(BetSlip).filter(BetSlip.id == slip_id).first()
        if not row:
            return False
        db.delete(row)
        db.commit()
        return True

