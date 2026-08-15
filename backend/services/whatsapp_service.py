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

# CallMeBot configuration
WHATSAPP_PHONE_NUMBER = os.getenv("WHATSAPP_PHONE_NUMBER", "")
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY", "")

# Twilio WhatsApp configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

# Green-API configuration
GREENAPI_INSTANCE_ID = os.getenv("GREENAPI_INSTANCE_ID", "")
GREENAPI_TOKEN = os.getenv("GREENAPI_TOKEN", "")


class WhatsAppNotificationService:
    """
    Multi-provider WhatsApp Notification Bot Service for automated goal prediction broadcasts
    supporting CallMeBot, Twilio WhatsApp API, and Green-API.
    """

    @classmethod
    async def send_message(cls, text: str, phone: Optional[str] = None, api_key: Optional[str] = None) -> bool:
        """
        Sends a WhatsApp message using configured provider (CallMeBot, Twilio, or Green-API).
        """
        p_num = phone or WHATSAPP_PHONE_NUMBER
        key = api_key or WHATSAPP_API_KEY

        # 1. Try CallMeBot API if phone and api_key are set
        if p_num and key:
            encoded_text = urllib.parse.quote(text)
            url = f"https://api.callmebot.com/whatsapp.php?phone={p_num}&text={encoded_text}&apikey={key}"
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        logger.info("CallMeBot WhatsApp notification dispatched successfully.")
                        return True
                    else:
                        logger.warning(f"CallMeBot responded with status {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"Error sending CallMeBot WhatsApp notification: {e}")

        # 2. Try Twilio WhatsApp API if Account SID and Auth Token are set
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and p_num:
            twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
            to_number = f"whatsapp:{p_num}" if not p_num.startswith("whatsapp:") else p_num
            payload = {
                "From": TWILIO_WHATSAPP_NUMBER,
                "To": to_number,
                "Body": text
            }
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        twilio_url,
                        data=payload,
                        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                    )
                    if resp.status_code in [200, 201]:
                        logger.info("Twilio WhatsApp notification dispatched successfully.")
                        return True
                    else:
                        logger.warning(f"Twilio WhatsApp responded with status {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"Error sending Twilio WhatsApp notification: {e}")

        # 3. Try Green-API if Instance ID and Token are set
        if GREENAPI_INSTANCE_ID and GREENAPI_TOKEN and p_num:
            clean_phone = p_num.replace("+", "").replace("-", "").replace(" ", "")
            green_url = f"https://api.green-api.com/waInstance{GREENAPI_INSTANCE_ID}/sendMessage/{GREENAPI_TOKEN}"
            payload = {
                "chatId": f"{clean_phone}@c.us",
                "message": text
            }
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(green_url, json=payload)
                    if resp.status_code == 200:
                        logger.info("Green-API WhatsApp notification dispatched successfully.")
                        return True
                    else:
                        logger.warning(f"Green-API responded with status {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"Error sending Green-API WhatsApp notification: {e}")

        logger.info("No WhatsApp provider credentials configured or dispatch failed.")
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
