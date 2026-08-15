import os
import sys
import logging
import urllib.parse
import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logger = logging.getLogger(__name__)

WHATSAPP_PHONE_NUMBER = os.getenv("WHATSAPP_PHONE_NUMBER", "")
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY", "")


class WhatsAppNotificationService:
    """
    WhatsApp Notification Bot Service for automated goal prediction broadcasts
    via CallMeBot API or Twilio WhatsApp API.
    """

    @classmethod
    async def send_message(cls, text: str, phone: Optional[str] = None, api_key: Optional[str] = None) -> bool:
        """Sends a text message to WhatsApp via CallMeBot free API."""
        p_num = phone or WHATSAPP_PHONE_NUMBER
        key = api_key or WHATSAPP_API_KEY

        if not p_num or not key:
            logger.info("WhatsApp Phone Number or API Key not configured. Skipping WhatsApp message dispatch.")
            return False

        encoded_text = urllib.parse.quote(text)
        url = f"https://api.callmebot.com/whatsapp.php?phone={p_num}&text={encoded_text}&apikey={key}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    logger.info("WhatsApp notification dispatched successfully.")
                    return True
                else:
                    logger.warning(f"WhatsApp API responded with status {resp.status_code}: {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"Error sending WhatsApp notification: {e}")
            return False

    @classmethod
    async def broadcast_daily_top_picks(cls, picks: List[Dict[str, Any]]) -> bool:
        """Formats and broadcasts Top 10 Over 1.5 Goal Picks to WhatsApp."""
        if not picks:
            return False

        now_gmt1 = datetime.now(timezone.utc) + timedelta(hours=1)
        now_str = now_gmt1.strftime("%A, %b %d, %Y")

        lines = [
            f"⚽ *SOCCER GOAL PREDICTOR — DAILY TOP PICKS* ⚽",
            f"📅 *{now_str} (GMT+1)*\n",
            "*Top Over 1.5 Goal Predictions:*\n"
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
                f"{idx}. *{home} vs {away}*\n"
                f"   🏆 {league} | ⏰ {match_time} GMT+1\n"
                f"   🔥 *Over 1.5: {prob}%* | Score: *{score}*\n"
            )

        lines.append("⚡ *Powered by Dixon-Coles Goal Engine*")
        msg = "\n".join(lines)
        return await cls.send_message(msg)
