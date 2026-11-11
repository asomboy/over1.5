import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from sqlalchemy.orm import Session, joinedload

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

try:
    from models import CardPredictionSnapshot, CornerPredictionSnapshot, Fixture
    from services.canonical_competition_service import CanonicalCompetitionService
except ImportError:
    from ..models import CardPredictionSnapshot, CornerPredictionSnapshot, Fixture
    from .canonical_competition_service import CanonicalCompetitionService


BOOKMAKER_PROFILES: Dict[str, Dict[str, Any]] = {
    "sportybet": {
        "id": "sportybet",
        "name": "SportyBet (NG)",
        "sportsbook_url": "https://www.sportybet.com/ng/sport/football",
        "accent": "rose",
        "booking_note": "Add these picks on SportyBet, tap Share/Book to generate your Booking Code, and save it on your ledger slip.",
    },
    "bet9ja": {
        "id": "bet9ja",
        "name": "Bet9ja",
        "sportsbook_url": "https://web.bet9ja.com/Sport/Default.aspx",
        "accent": "emerald",
        "booking_note": "Add these picks on Bet9ja, tap Book to generate your Bet9ja Code, and attach it to your ledger slip.",
    },
}


MARKET_TRANSLATIONS: Dict[str, Dict[str, Dict[str, str]]] = {
    "sportybet": {
        "over_0_5_goals": {
            "section": "Goals",
            "market": "Over/Under",
            "pick": "Over 0.5",
            "hint": "Goals -> Over/Under -> Over 0.5",
        },
        "over_1_5_goals": {
            "section": "Goals",
            "market": "Over/Under",
            "pick": "Over 1.5",
            "hint": "Goals -> Over/Under -> Over 1.5",
        },
        "over_2_5_goals": {
            "section": "Goals",
            "market": "Over/Under",
            "pick": "Over 2.5",
            "hint": "Goals -> Over/Under -> Over 2.5",
        },
        "over_3_5_goals": {
            "section": "Goals",
            "market": "Over/Under",
            "pick": "Over 3.5",
            "hint": "Goals -> Over/Under -> Over 3.5",
        },
        "under_2_5_goals": {
            "section": "Goals",
            "market": "Over/Under",
            "pick": "Under 2.5",
            "hint": "Goals -> Over/Under -> Under 2.5",
        },
        "under_3_5_goals": {
            "section": "Goals",
            "market": "Over/Under",
            "pick": "Under 3.5",
            "hint": "Goals -> Over/Under -> Under 3.5",
        },
        "btts_yes": {
            "section": "Goals",
            "market": "GG/NG",
            "pick": "Yes (GG)",
            "hint": "Goals -> GG/NG -> Yes",
        },
        "btts_no": {
            "section": "Goals",
            "market": "GG/NG",
            "pick": "No (NG)",
            "hint": "Goals -> GG/NG -> No",
        },
        "1x2_home": {
            "section": "Main",
            "market": "1X2",
            "pick": "Home (1)",
            "hint": "Main -> 1X2 -> Home",
        },
        "1x2_draw": {
            "section": "Main",
            "market": "1X2",
            "pick": "Draw (X)",
            "hint": "Main -> 1X2 -> Draw",
        },
        "1x2_away": {
            "section": "Main",
            "market": "1X2",
            "pick": "Away (2)",
            "hint": "Main -> 1X2 -> Away",
        },
        "dc_1x": {
            "section": "Main",
            "market": "Double Chance",
            "pick": "1X (Home or Draw)",
            "hint": "Main -> Double Chance -> 1X",
        },
        "dc_x2": {
            "section": "Main",
            "market": "Double Chance",
            "pick": "X2 (Draw or Away)",
            "hint": "Main -> Double Chance -> X2",
        },
        "dc_12": {
            "section": "Main",
            "market": "Double Chance",
            "pick": "12 (Home or Away)",
            "hint": "Main -> Double Chance -> 12",
        },
        "ht_over_0_5_goals": {
            "section": "Half",
            "market": "1st Half - Over/Under",
            "pick": "Over 0.5",
            "hint": "Half -> 1st Half O/U -> Over 0.5",
        },
        "over_8_5_corners": {
            "section": "Corners",
            "market": "Corners - Over/Under",
            "pick": "Over 8.5",
            "hint": "Corners -> Over/Under -> Over 8.5",
        },
        "over_9_5_corners": {
            "section": "Corners",
            "market": "Corners - Over/Under",
            "pick": "Over 9.5",
            "hint": "Corners -> Over/Under -> Over 9.5",
        },
        "over_3_5_cards": {
            "section": "Cards",
            "market": "Bookings - Over/Under",
            "pick": "Over 3.5",
            "hint": "Cards -> Bookings O/U -> Over 3.5",
        },
        "at_least_1_more_goal": {
            "section": "Live Goals",
            "market": "Next Goal / Total Goals Over Line",
            "pick": "+1 More Goal",
            "hint": "Live -> Total Goals -> Over Current + 0.5",
        },
    },
    "bet9ja": {
        "over_0_5_goals": {
            "section": "Goals",
            "market": "Over/Under 0.5",
            "pick": "Over (0.5)",
            "hint": "Goals -> O/U 0.5 -> Over",
        },
        "over_1_5_goals": {
            "section": "Goals",
            "market": "Over/Under 1.5",
            "pick": "Over (1.5)",
            "hint": "Goals -> O/U 1.5 -> Over",
        },
        "over_2_5_goals": {
            "section": "Goals",
            "market": "Over/Under 2.5",
            "pick": "Over (2.5)",
            "hint": "Goals -> O/U 2.5 -> Over",
        },
        "over_3_5_goals": {
            "section": "Goals",
            "market": "Over/Under 3.5",
            "pick": "Over (3.5)",
            "hint": "Goals -> O/U 3.5 -> Over",
        },
        "under_2_5_goals": {
            "section": "Goals",
            "market": "Over/Under 2.5",
            "pick": "Under (2.5)",
            "hint": "Goals -> O/U 2.5 -> Under",
        },
        "under_3_5_goals": {
            "section": "Goals",
            "market": "Over/Under 3.5",
            "pick": "Under (3.5)",
            "hint": "Goals -> O/U 3.5 -> Under",
        },
        "btts_yes": {
            "section": "Goals",
            "market": "Goal / No Goal",
            "pick": "GG (Yes)",
            "hint": "Goals -> GG/NG -> GG",
        },
        "btts_no": {
            "section": "Goals",
            "market": "Goal / No Goal",
            "pick": "NG (No)",
            "hint": "Goals -> GG/NG -> NG",
        },
        "1x2_home": {
            "section": "Main",
            "market": "1X2",
            "pick": "1 (Home Win)",
            "hint": "Main -> 1X2 -> 1",
        },
        "1x2_draw": {
            "section": "Main",
            "market": "1X2",
            "pick": "X (Draw)",
            "hint": "Main -> 1X2 -> X",
        },
        "1x2_away": {
            "section": "Main",
            "market": "1X2",
            "pick": "2 (Away Win)",
            "hint": "Main -> 1X2 -> 2",
        },
        "dc_1x": {
            "section": "Main",
            "market": "Double Chance",
            "pick": "1X",
            "hint": "Main -> DC -> 1X",
        },
        "dc_x2": {
            "section": "Main",
            "market": "Double Chance",
            "pick": "X2",
            "hint": "Main -> DC -> X2",
        },
        "dc_12": {
            "section": "Main",
            "market": "Double Chance",
            "pick": "12",
            "hint": "Main -> DC -> 12",
        },
        "ht_over_0_5_goals": {
            "section": "1st Half",
            "market": "1st Half O/U 0.5",
            "pick": "Over (0.5)",
            "hint": "1st Half -> O/U 0.5 -> Over",
        },
        "over_8_5_corners": {
            "section": "Corners",
            "market": "Total Corners O/U",
            "pick": "Over 8.5",
            "hint": "Corners -> Total Corners -> Over 8.5",
        },
        "over_9_5_corners": {
            "section": "Corners",
            "market": "Total Corners O/U",
            "pick": "Over 9.5",
            "hint": "Corners -> Total Corners -> Over 9.5",
        },
        "over_3_5_cards": {
            "section": "Cards",
            "market": "Total Cards O/U",
            "pick": "Over 3.5",
            "hint": "Cards -> Total Cards -> Over 3.5",
        },
        "at_least_1_more_goal": {
            "section": "Live Goals",
            "market": "Live Total Goals Over Line",
            "pick": "+1 More Goal",
            "hint": "Live -> Total Goals -> Over Current + 0.5",
        },
    },
}


DISPLAY_TO_CANONICAL: Dict[str, str] = {
    "over 0.5": "over_0_5_goals",
    "over 0.5 goals": "over_0_5_goals",
    "over 1.5": "over_1_5_goals",
    "over 1.5 goals": "over_1_5_goals",
    "full match over 1.5": "over_1_5_goals",
    "full match over 1.5 goals": "over_1_5_goals",
    "over 2.5": "over_2_5_goals",
    "over 2.5 goals": "over_2_5_goals",
    "full match over 2.5": "over_2_5_goals",
    "full match over 2.5 goals": "over_2_5_goals",
    "over 3.5": "over_3_5_goals",
    "over 3.5 goals": "over_3_5_goals",
    "full match over 3.5": "over_3_5_goals",
    "under 2.5": "under_2_5_goals",
    "under 2.5 goals": "under_2_5_goals",
    "under 3.5": "under_3_5_goals",
    "under 3.5 goals": "under_3_5_goals",
    "btts yes": "btts_yes",
    "btts": "btts_yes",
    "both teams to score": "btts_yes",
    "btts no": "btts_no",
    "home win": "1x2_home",
    "draw": "1x2_draw",
    "away win": "1x2_away",
    "double chance 1x": "dc_1x",
    "dc 1x": "dc_1x",
    "1x": "dc_1x",
    "double chance x2": "dc_x2",
    "dc x2": "dc_x2",
    "x2": "dc_x2",
    "double chance 12": "dc_12",
    "dc 12": "dc_12",
    "12": "dc_12",
    "1h over 0.5": "ht_over_0_5_goals",
    "1h over 0.5 goals": "ht_over_0_5_goals",
    "over 8.5 corners": "over_8_5_corners",
    "over 9.5 corners": "over_9_5_corners",
    "over 3.5 cards": "over_3_5_cards",
    "at least 1 more goal": "at_least_1_more_goal",
}

CANONICAL_DISPLAY_LABELS: Dict[str, str] = {
    "over_0_5_goals": "Over 0.5 Goals",
    "over_1_5_goals": "Over 1.5 Goals",
    "over_2_5_goals": "Over 2.5 Goals",
    "over_3_5_goals": "Over 3.5 Goals",
    "under_2_5_goals": "Under 2.5 Goals",
    "under_3_5_goals": "Under 3.5 Goals",
    "btts_yes": "BTTS Yes",
    "btts_no": "BTTS No",
    "1x2_home": "Home Win",
    "1x2_draw": "Draw",
    "1x2_away": "Away Win",
    "dc_1x": "Double Chance 1X",
    "dc_x2": "Double Chance X2",
    "dc_12": "Double Chance 12",
    "ht_over_0_5_goals": "1H Over 0.5 Goals",
    "over_8_5_corners": "Over 8.5 Corners",
    "over_9_5_corners": "Over 9.5 Corners",
    "over_3_5_cards": "Over 3.5 Cards",
    "at_least_1_more_goal": "At Least 1 More Goal",
}


class BookmakerSlipService:
    """
    Constructs SportyBet & Bet9ja formatted target-odds betting slips using verified
    Poisson, Dixon-Coles, Corners, and Cards predictions, with exact bookmaker market
    translations and shareable ticket text.
    """

    @staticmethod
    def normalize_bookmaker(bookmaker: Optional[str]) -> str:
        bm = str(bookmaker or "sportybet").strip().lower()
        return bm if bm in BOOKMAKER_PROFILES else "sportybet"

    @classmethod
    def translate_market(cls, bookmaker: str, market_name: str) -> Dict[str, str]:
        bm = cls.normalize_bookmaker(bookmaker)
        raw = str(market_name or "Over 1.5 Goals").strip()
        canonical = DISPLAY_TO_CANONICAL.get(raw.lower(), raw.lower().replace(" ", "_"))
        bm_map = MARKET_TRANSLATIONS.get(bm, MARKET_TRANSLATIONS["sportybet"])
        mapped = bm_map.get(canonical)
        if not mapped:
            return {
                "canonical_market": canonical,
                "section": "Match Markets",
                "market": raw,
                "pick": raw,
                "hint": f"{bm.upper()} -> {raw}",
            }
        return {"canonical_market": canonical, **mapped}

    @staticmethod
    def _london_tz():
        if ZoneInfo is not None:
            try:
                return ZoneInfo("Europe/London")
            except Exception:
                pass
        return timezone(timedelta(hours=1))

    @classmethod
    def _format_gmt1_parts(cls, dt: Optional[datetime]) -> tuple:
        if not dt:
            return ("", "", "")
        dt_utc = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        iso_z = dt_utc.replace(tzinfo=None).isoformat() + "Z"
        local_dt = dt_utc.astimezone(cls._london_tz())
        return (iso_z, local_dt.strftime("%Y-%m-%d"), local_dt.strftime("%a %b %d, %H:%M GMT+1"))

    @staticmethod
    def _build_sportsbook_match_url(bookmaker: str, home: str, away: str) -> str:
        query = quote_plus(f"{home} {away}".strip())
        if bookmaker == "bet9ja":
            return f"https://web.bet9ja.com/Sport/Default.aspx?Search={query}"
        return f"https://www.sportybet.com/ng/sport/football?search={query}"

    @classmethod
    def _extract_fixture_markets(
        cls,
        fix: Fixture,
        corner_snap: Optional[CornerPredictionSnapshot],
        card_snap: Optional[CardPredictionSnapshot],
    ) -> List[Dict[str, Any]]:
        pred = fix.predictions[0] if fix.predictions else None
        if not pred or pred.over_1_5_probability is None:
            return []

        h_xg = float(pred.predicted_home_score or 0.0)
        a_xg = float(pred.predicted_away_score or 0.0)
        tot_xg = round(float(pred.expected_goals_xg or (h_xg + a_xg)), 2)

        o15 = float(pred.over_1_5_probability)
        o25 = float(pred.over_2_5_probability) if pred.over_2_5_probability is not None else None
        btts = float(pred.btts_probability) if pred.btts_probability is not None else None
        hw = float(pred.home_win_probability or 0.0)
        dw = float(pred.draw_probability or 0.0)
        aw = float(pred.away_win_probability or 0.0)
        ht_o05 = round(1.0 - math.exp(-(tot_xg * 0.45)), 4) if tot_xg > 0 else None

        candidates: List[tuple] = [
            ("over_1_5_goals", o15),
            ("dc_1x", round(min(0.98, hw + dw), 4)),
            ("dc_x2", round(min(0.98, aw + dw), 4)),
            ("1x2_home", hw),
            ("1x2_away", aw),
        ]
        if o25 is not None:
            candidates.append(("over_2_5_goals", o25))
        if btts is not None:
            candidates.append(("btts_yes", btts))
        if ht_o05 is not None:
            candidates.append(("ht_over_0_5_goals", ht_o05))
        if corner_snap and corner_snap.over_8_5_prob is not None:
            candidates.append(("over_8_5_corners", float(corner_snap.over_8_5_prob)))
        if card_snap and card_snap.over_3_5_prob is not None:
            candidates.append(("over_3_5_cards", float(card_snap.over_3_5_prob)))

        iso_z, day_key, gmt1_label = cls._format_gmt1_parts(fix.match_date)
        home_name = fix.home_team.name if fix.home_team else "Home"
        away_name = fix.away_team.name if fix.away_team else "Away"
        league_name = fix.league.name if fix.league else "League"
        league_country = fix.league.country if fix.league else ""

        out = []
        for canonical_mkt, prob in candidates:
            if prob is None or prob <= 0.15 or prob >= 0.99:
                continue
            fair_odds = round(min(4.50, max(1.12, 1.0 / prob)), 2)
            out.append({
                "fixture_id": fix.id,
                "league_id": fix.league_id,
                "home_team": home_name,
                "away_team": away_name,
                "match": f"{home_name} vs {away_name}",
                "league": league_name,
                "country": league_country,
                "match_date": iso_z,
                "match_date_key": day_key,
                "kickoff_gmt1": gmt1_label,
                "canonical_market": canonical_mkt,
                "market": CANONICAL_DISPLAY_LABELS.get(canonical_mkt, canonical_mkt),
                "model_prob": round(prob, 4),
                "probability": round(prob * 100.0, 1),
                "estimated_fair_odds": fair_odds,
                "expected_goals": tot_xg,
                "most_likely_score": pred.most_likely_score or "2-1",
            })
        return out

    @classmethod
    def _assemble_preset_slip(
        cls,
        *,
        bookmaker: str,
        preset_key: str,
        label: str,
        description: str,
        pool: List[Dict[str, Any]],
        allowed_markets: set,
        target_odds: float,
        min_prob: float,
        max_legs: int,
        prefer_diverse_leagues: bool = True,
    ) -> Dict[str, Any]:
        eligible = [
            item for item in pool
            if item["canonical_market"] in allowed_markets and item["model_prob"] >= min_prob
        ]
        # Rank by highest model probability, breaking ties cleanly
        eligible.sort(key=lambda x: (x["model_prob"], -x["estimated_fair_odds"]), reverse=True)

        picked: List[Dict[str, Any]] = []
        used_fixtures = set()
        used_leagues = set()
        running_odds = 1.0

        def _try_fill(enforce_league_uniqueness: bool) -> None:
            nonlocal running_odds
            for item in eligible:
                if len(picked) >= max_legs:
                    break
                if running_odds >= target_odds and len(picked) >= 2:
                    break
                fid = item["fixture_id"]
                lid = item["league_id"]
                if fid in used_fixtures:
                    continue
                if enforce_league_uniqueness and lid in used_leagues:
                    continue
                used_fixtures.add(fid)
                used_leagues.add(lid)
                translation = cls.translate_market(bookmaker, item["canonical_market"])
                enriched = {
                    **item,
                    "bookmaker_section": translation["section"],
                    "bookmaker_market": translation["market"],
                    "bookmaker_pick": translation["pick"],
                    "placement_hint": translation["hint"],
                    "sportsbook_search_url": cls._build_sportsbook_match_url(
                        bookmaker, item["home_team"], item["away_team"]
                    ),
                }
                picked.append(enriched)
                running_odds *= item["estimated_fair_odds"]

        if prefer_diverse_leagues:
            _try_fill(True)
        if len(picked) < 2 or running_odds < target_odds:
            _try_fill(False)

        if not picked:
            return {
                "preset": preset_key,
                "label": label,
                "description": description,
                "target_odds": target_odds,
                "legs_count": 0,
                "total_fair_odds": 1.0,
                "combined_probability": 0.0,
                "legs": [],
                "share_text": "",
                "status": "insufficient_data",
            }

        tot_odds = 1.0
        joint_prob = 1.0
        for lg in picked:
            tot_odds *= lg["estimated_fair_odds"]
            joint_prob *= lg["model_prob"]

        bm_name = BOOKMAKER_PROFILES[bookmaker]["name"]
        lines = [
            f"⚽ {bm_name.upper()} SLIP — {label.upper()}",
            f"Est. Model Fair Odds: {round(tot_odds, 2)}x | Joint Model Prob: {round(joint_prob * 100.0, 1)}%",
            "",
        ]
        for idx, lg in enumerate(picked, 1):
            lines.append(
                f"{idx}. {lg['match']} ({lg['league']} · {lg['kickoff_gmt1']})"
            )
            lines.append(
                f"   Pick: {lg['placement_hint']} ({lg['probability']}% | Fair Odds ~{lg['estimated_fair_odds']})"
            )
        lines.append("")
        lines.append(f"Open {bm_name} -> Search match -> Enter selections -> Book slip")

        return {
            "preset": preset_key,
            "label": label,
            "description": description,
            "target_odds": target_odds,
            "legs_count": len(picked),
            "total_fair_odds": round(tot_odds, 2),
            "combined_probability": round(joint_prob * 100.0, 1),
            "legs": picked,
            "share_text": "\n".join(lines),
            "status": "ready",
        }

    @classmethod
    def build_slips(
        cls,
        db: Session,
        bookmaker: str = "sportybet",
        match_day: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Builds target-odds SportyBet or Bet9ja slips from active upcoming fixtures."""
        bm = cls.normalize_bookmaker(bookmaker)
        now = datetime.now(timezone.utc)
        now_cutoff = (now - timedelta(hours=2)).replace(tzinfo=None)

        fixtures = (
            db.query(Fixture)
            .options(
                joinedload(Fixture.league),
                joinedload(Fixture.home_team),
                joinedload(Fixture.away_team),
                joinedload(Fixture.predictions),
            )
            .filter(
                Fixture.status.notin_(["FINISHED", "FT", "AET", "PEN", "POSTPONED", "CANCELLED"]),
                Fixture.match_date >= now_cutoff,
            )
            .order_by(Fixture.match_date.asc())
            .all()
        )

        eligible_fixtures: List[Fixture] = []
        for fix in fixtures:
            if CanonicalCompetitionService.is_school_or_youth_competition(
                league_name=fix.league.name if fix.league else "",
                home_team_name=fix.home_team.name if fix.home_team else "",
                away_team_name=fix.away_team.name if fix.away_team else "",
            ):
                continue
            if match_day and match_day != "ALL_DAYS":
                _, day_key, _ = cls._format_gmt1_parts(fix.match_date)
                if day_key != match_day:
                    continue
            eligible_fixtures.append(fix)

        fix_ids = [f.id for f in eligible_fixtures]
        corners_map: Dict[int, CornerPredictionSnapshot] = {}
        cards_map: Dict[int, CardPredictionSnapshot] = {}
        for i in range(0, len(fix_ids), 500):
            chunk = fix_ids[i:i + 500]
            for cs in db.query(CornerPredictionSnapshot).filter(CornerPredictionSnapshot.fixture_id.in_(chunk)).all():
                corners_map[cs.fixture_id] = cs
            for cds in db.query(CardPredictionSnapshot).filter(CardPredictionSnapshot.fixture_id.in_(chunk)).all():
                cards_map[cds.fixture_id] = cds

        pool: List[Dict[str, Any]] = []
        for fix in eligible_fixtures:
            pool.extend(
                cls._extract_fixture_markets(
                    fix,
                    corners_map.get(fix.id),
                    cards_map.get(fix.id),
                )
            )

        slips = {
            "safe_2_odds": cls._assemble_preset_slip(
                bookmaker=bm,
                preset_key="safe_2_odds",
                label="2-Odds Safe Banker",
                description="High-probability Double Chance, Over 1.5, and strong 1H Goal picks near ~2.00x fair odds.",
                pool=pool,
                allowed_markets={"over_1_5_goals", "dc_1x", "dc_x2", "ht_over_0_5_goals", "1x2_home", "1x2_away"},
                target_odds=2.0,
                min_prob=0.74,
                max_legs=4,
            ),
            "balanced_3_odds": cls._assemble_preset_slip(
                bookmaker=bm,
                preset_key="balanced_3_odds",
                label="3-Odds Daily Multi",
                description="Diversified cross-league Over 1.5, BTTS/GG & Double Chance multi targeting ~3.00x.",
                pool=pool,
                allowed_markets={"over_1_5_goals", "over_2_5_goals", "btts_yes", "dc_1x", "dc_x2"},
                target_odds=3.0,
                min_prob=0.68,
                max_legs=5,
            ),
            "goals_corners_5_odds": cls._assemble_preset_slip(
                bookmaker=bm,
                preset_key="goals_corners_5_odds",
                label="5-Odds Goals & Corners",
                description="Multi-market combo blending Over 2.5, GG/NG, 1st Half Goals, and verified Corners/Cards.",
                pool=pool,
                allowed_markets={
                    "over_2_5_goals",
                    "btts_yes",
                    "ht_over_0_5_goals",
                    "over_8_5_corners",
                    "over_3_5_cards",
                    "over_1_5_goals",
                },
                target_odds=5.0,
                min_prob=0.60,
                max_legs=6,
            ),
            "high_yield_10_odds": cls._assemble_preset_slip(
                bookmaker=bm,
                preset_key="high_yield_10_odds",
                label="10-Odds High Yield",
                description="Higher-return 6-8 leg ticket mixing Over 2.5, BTTS Yes, and strong 1X2 home/away edges.",
                pool=pool,
                allowed_markets={"over_2_5_goals", "btts_yes", "1x2_home", "1x2_away", "over_1_5_goals"},
                target_odds=10.0,
                min_prob=0.54,
                max_legs=8,
            ),
        }

        return {
            "status": "ok",
            "bookmaker": bm,
            "profile": BOOKMAKER_PROFILES[bm],
            "total_fixtures_scanned": len(eligible_fixtures),
            "slips": slips,
        }

