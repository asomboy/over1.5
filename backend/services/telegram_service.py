import os
import sys
import logging
import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logger = logging.getLogger(__name__)

try:
    from config import BASE_DIR
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except Exception:
    pass

def get_telegram_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "")

def get_telegram_chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "")


class TelegramNotificationService:
    """
    Telegram Notification Bot Service for automated goal prediction broadcasts.
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
            async with httpx.AsyncClient(timeout=10.0) as client:
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

    @classmethod
    async def broadcast_daily_top_picks(cls, picks: List[Dict[str, Any]], bot_token: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
        """Formats and broadcasts Top 10 Over 1.5 Goal Picks for the day."""
        if not picks:
            return False

        now_gmt1 = datetime.now(timezone.utc) + timedelta(hours=1)
        now_str = now_gmt1.strftime("%A, %b %d, %Y")
        lines = [
            f"🔥 <b>SOCCER GOAL PREDICTOR — DAILY TOP PICKS</b> 🔥",
            f"📅 <i>{now_str} (GMT+1)</i>\n",
            "Top High-Probability Over 1.5 Goal Predictions:\n"
        ]

        for idx, item in enumerate(picks[:10], 1):
            home = item.get("home_team", {}).get("name", "Home")
            away = item.get("away_team", {}).get("name", "Away")
            league = item.get("league", {}).get("name", "League")
            prob = round((item.get("prediction", {}).get("over_1_5_probability") or 0.75) * 100)
            score = item.get("prediction", {}).get("most_likely_score", "2-1")

            match_time = "TBD"
            if item.get("match_date"):
                try:
                    dt = datetime.fromisoformat(item["match_date"].replace("Z", "+00:00"))
                    dt_gmt1 = dt.astimezone(timezone.utc) + timedelta(hours=1)
                    match_time = dt_gmt1.strftime("%I:%M %p")
                except Exception:
                    match_time = item["match_date"][11:16]

            lines.append(
                f"{idx}. ⚽ <b>{home} vs {away}</b>\n"
                f"   🏆 {league} | ⏰ {match_time} GMT+1\n"
                f"   🔥 <b>Over 1.5: {prob}%</b> | Scoreline: <b>{score}</b>\n"
            )

        lines.append("⚡ <i>Powered by Dixon-Coles Goal Expectation Engine</i>")
        msg = "\n".join(lines)
        return await cls.send_message(msg, bot_token=bot_token, chat_id=chat_id)
