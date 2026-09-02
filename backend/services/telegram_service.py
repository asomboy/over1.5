import os
import sys
import logging
import itertools
from functools import reduce
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
import httpx

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logger = logging.getLogger(__name__)

# Ensure .env is loaded reliably regardless of working directory
try:
    from dotenv import load_dotenv
    env_file = os.path.join(BACKEND_DIR, ".env")
    if os.path.exists(env_file):
        load_dotenv(env_file)
except Exception:
    pass

def get_telegram_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "8917826403:AAHNxYEicw76o_lPljmb6yy0LRGI8sC0S7Q")

def get_telegram_chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "568393640")


class TelegramNotificationService:
    """
    Telegram Notification Bot Service for automated 3-Odds Accumulator tickets
    and goal prediction broadcasts across 3 daily match windows:
    1. Early Morning to Morning (00:00 - 11:59 GMT+1)
    2. Afternoon (12:00 - 16:59 GMT+1)
    3. Evening to Midnight (17:00 - 23:59 GMT+1)
    """

    @classmethod
    async def send_message(cls, text: str, bot_token: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
        """Sends a plain text or HTML formatted message via Telegram Bot API."""
        token = bot_token or get_telegram_token()
        cid = chat_id or get_telegram_chat_id()

        if not token or not cid:
            logger.info(f"Telegram Bot Token ({'set' if token else 'missing'}) or Chat ID ({'set' if cid else 'missing'}) not configured. Skipping message dispatch.")
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    logger.info("Telegram notification sent successfully.")
                    return True
                else:
                    logger.warning(f"Telegram API responded with status {resp.status_code}: {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"Error sending Telegram notification: {e}")
            return False

    @staticmethod
    def calculate_market_odds(prob: float) -> float:
        """Derives estimated fair bookmaker decimal odds with 5% margin."""
        if not prob or prob <= 0:
            return 1.50
        return round(max(1.10, min(3.50, 1.0 / (prob * 0.95))), 2)

    @classmethod
    def find_best_3_odds_ticket(cls, candidate_picks: List[Dict[str, Any]], target_odds: float = 3.00) -> Optional[Dict[str, Any]]:
        """
        Mathematical combinatorial optimizer that selects the highest expected-value
        combination of 2 to 4 legs totaling approximately ~2.70 to 3.50 odds (~3.00 odds target).
        """
        if not candidate_picks or len(candidate_picks) < 2:
            if candidate_picks:
                p = candidate_picks[0]
                odds = p.get("odds") or cls.calculate_market_odds(p.get("prob", 0.75))
                return {
                    "legs": candidate_picks,
                    "total_odds": odds,
                    "joint_probability": p.get("prob", 0.75),
                    "expected_value_pct": round(((p.get("prob", 0.75) * odds) - 1.0) * 100, 1)
                }
            return None

        # Filter out candidates with insufficient confidence
        clean_picks = [p for p in candidate_picks if (p.get("prob") or 0) >= 0.65]
        if len(clean_picks) < 2:
            clean_picks = candidate_picks[:4]

        best_combo = None
        best_score = -999.0

        for k in range(2, min(5, len(clean_picks) + 1)):
            for combo in itertools.combinations(clean_picks, k):
                tot_odds = round(reduce(lambda x, y: x * (y.get("odds") or cls.calculate_market_odds(y.get("prob", 0.75))), combo, 1.0), 2)
                joint_prob = reduce(lambda x, y: x * (y.get("prob") or 0.75), combo, 1.0)
                ev = (joint_prob * tot_odds) - 1.0
                odds_diff = abs(tot_odds - target_odds)

                # Objective function: rewards high EV, closeness to 3.00 odds, and high average leg confidence
                avg_prob = sum(p.get("prob", 0.75) for p in combo) / len(combo)
                score = (ev * 2.5) - (odds_diff * 0.9) + (avg_prob * 0.8)

                if 2.50 <= tot_odds <= 3.80:
                    if score > best_score:
                        best_score = score
                        best_combo = {
                            "legs": list(combo),
                            "total_odds": tot_odds,
                            "joint_probability": joint_prob,
                            "expected_value_pct": round(ev * 100, 1)
                        }

        # Fallback to closest top 3 picks if exact range combination not found
        if not best_combo and len(clean_picks) >= 2:
            selected = clean_picks[:3]
            tot_odds = round(reduce(lambda x, y: x * (y.get("odds") or cls.calculate_market_odds(y.get("prob", 0.75))), selected, 1.0), 2)
            joint_prob = reduce(lambda x, y: x * (y.get("prob") or 0.75), selected, 1.0)
            best_combo = {
                "legs": selected,
                "total_odds": tot_odds,
                "joint_probability": joint_prob,
                "expected_value_pct": round(((joint_prob * tot_odds) - 1.0) * 100, 1)
            }

        return best_combo

    @classmethod
    async def broadcast_3_odds_window(
        cls,
        window_title: str,
        time_range_str: str,
        ticket: Optional[Dict[str, Any]],
        top_bankers: List[Dict[str, Any]],
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None
    ) -> bool:
        """
        Broadcasts the 3-Odds Accumulator Ticket and Top Goal Banker Picks
        for a specific daily time window (Morning, Afternoon, or Evening).
        """
        now_gmt1 = datetime.now(timezone.utc) + timedelta(hours=1)
        date_header = now_gmt1.strftime("%A, %b %d, %Y")

        lines = [
            f"🎯 <b>SOCCER GOAL PREDICTOR — {window_title.upper()}</b> 🎯",
            f"📅 <i>{date_header} • {time_range_str} (GMT+1)</i>\n"
        ]

        if ticket and ticket.get("legs"):
            legs = ticket["legs"]
            tot_odds = ticket.get("total_odds", 3.00)
            joint_prob_pct = round((ticket.get("joint_probability") or 0.40) * 100, 1)
            ev_pct = ticket.get("expected_value_pct", 25.0)

            lines.append(f"🔥 <b>PREMIUM ~3.00 ODDS ACCUMULATOR TICKET</b> 🔥\n")

            for idx, leg in enumerate(legs, 1):
                home = leg.get("home", "Home")
                away = leg.get("away", "Away")
                league = leg.get("league", "League")
                prob_pct = round((leg.get("prob") or 0.75) * 100)
                odds = leg.get("odds") or cls.calculate_market_odds(leg.get("prob", 0.75))
                xg = round(float(leg.get("xg") or 2.60), 2)
                score = leg.get("score") or "2-1"

                match_time = "TBD"
                if leg.get("match_date"):
                    try:
                        if isinstance(leg["match_date"], datetime):
                            dt = leg["match_date"]
                        else:
                            dt = datetime.fromisoformat(str(leg["match_date"]).replace("Z", "+00:00"))
                        dt_gmt1 = (dt.astimezone(timezone.utc) if dt.tzinfo else dt) + timedelta(hours=1)
                        match_time = dt_gmt1.strftime("%I:%M %p")
                    except Exception:
                        match_time = str(leg["match_date"])[11:16]

                lines.append(
                    f"{idx}️⃣ ⚽ <b>{home} vs {away}</b>\n"
                    f"   🏆 {league} | ⏰ {match_time} GMT+1\n"
                    f"   🎯 Market: <b>Over 1.5 Goals</b>\n"
                    f"   📊 Prob: <b>{prob_pct}%</b> | Odds: <b>{odds:.2f}</b> | xG: <b>{xg}</b>\n"
                )

            lines.append(
                f"💰 <b>TOTAL TICKET ODDS: {tot_odds:.2f}</b>\n"
                f"📈 <b>Model Win Prob: {joint_prob_pct}%</b> | Value: <b>+{ev_pct}% EV</b>\n"
                f"💡 <i>Recommended Staking: 1.0 Unit Flat</i>\n"
            )

        # Include additional Top Over 1.5 Goal Banker Matches if available
        if top_bankers:
            lines.append("⚡ <b>TOP OVER 1.5 GOAL BANKERS (THIS WINDOW):</b>")
            for b_idx, b_item in enumerate(top_bankers[:4], 1):
                b_home = b_item.get("home", "Home")
                b_away = b_item.get("away", "Away")
                b_prob = round((b_item.get("prob") or 0.80) * 100)
                b_xg = round(float(b_item.get("xg") or 2.70), 2)
                lines.append(f"  • <b>{b_home} vs {b_away}</b> — <b>{b_prob}% Over 1.5</b> (xG: {b_xg})")
            lines.append("")

        lines.append("🤖 <i>Powered by Poisson & Dixon-Coles Match Intelligence</i>")
        msg = "\n".join(lines)
        return await cls.send_message(msg, bot_token=bot_token, chat_id=chat_id)

    @classmethod
    async def broadcast_daily_top_picks(
        cls,
        candidate_picks: List[Dict[str, Any]],
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None
    ) -> bool:
        """Broadcasts daily top picks ticket if candidate picks exist, else suppresses message."""
        if not candidate_picks:
            logger.info("No candidate picks available for daily top picks broadcast.")
            return False
        ticket = cls.find_best_3_odds_ticket(candidate_picks)
        return await cls.broadcast_3_odds_window(
            "Daily Top Picks",
            "All Day",
            ticket,
            candidate_picks,
            bot_token=bot_token,
            chat_id=chat_id
        )

    @classmethod
    async def broadcast_outcome_recap(
        cls,
        recap_items: List[Dict[str, Any]],
        window_title: str,
        date_str: str,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None
    ) -> bool:
        """Formats and broadcasts Outcome Recap Report for previous window predictions."""
        if not recap_items:
            logger.info("No finished fixtures available for outcome recap broadcast.")
            return False

        won_count = sum(1 for item in recap_items if item.get("is_won"))
        total_count = len(recap_items)
        win_rate = round((won_count / max(1, total_count)) * 100, 1)

        lines = [
            "📊 <b>SOCCER GOAL PREDICTOR — RESULTS RECAP</b> 📊",
            f"📅 <i>{date_str} ({window_title} Results)</i>\n"
        ]

        for idx, item in enumerate(recap_items, 1):
            home = item.get("home", "Home")
            away = item.get("away", "Away")
            h_score = item.get("home_score", 0)
            a_score = item.get("away_score", 0)
            total_goals = h_score + a_score
            prob = round((item.get("prob") or 0.75) * 100)
            is_won = item.get("is_won", False)
            status_icon = "✅ WON" if is_won else "❌ LOST"

            lines.append(
                f"{idx}. ⚽ <b>{home} {h_score} - {a_score} {away}</b>\n"
                f"   🔥 Over 1.5 Prob: <b>{prob}%</b> | Goals: <b>{total_goals}</b> -> <b>{status_icon}</b>\n"
            )

        ticket_status = "🎯 TICKET WON! 💰" if (won_count == total_count and total_count > 0) else f"{won_count}/{total_count} Hits"
        lines.append(f"📈 <b>Summary: {won_count}/{total_count} Won ({win_rate}%) &bull; {ticket_status}</b>")
        lines.append("⚡ <i>Powered by Dixon-Coles Goal Expectation Engine</i>")

        msg = "\n".join(lines)
        return await cls.send_message(msg, bot_token=bot_token, chat_id=chat_id)

