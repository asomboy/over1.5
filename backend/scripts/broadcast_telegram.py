import os
import sys
import logging
from datetime import datetime, timezone, timedelta
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram_broadcast")

API_BASE_URL = os.getenv("API_BASE_URL", "https://soccer-goal-predictor-api.onrender.com")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8917826403:AAHNxYEicw76o_lPljmb6yy0LRGI8sC0S7Q")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "568393640")

def run_broadcast():
    logger.info("Fetching upcoming fixtures from API...")
    url = f"{API_BASE_URL}/api/fixtures/upcoming"
    
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch fixtures: {resp.status_code} {resp.text}")
                sys.exit(1)
            raw_data = resp.json()
            if isinstance(raw_data, dict):
                fixtures = raw_data.get("fixtures", [])
            elif isinstance(raw_data, list):
                fixtures = raw_data
            else:
                fixtures = []
    except Exception as e:
        logger.error(f"Error connecting to backend API: {e}")
        sys.exit(1)

    picks = []
    for fix in fixtures:
        if not isinstance(fix, dict):
            continue
        pred = fix.get("prediction")
        if not pred or not isinstance(pred, dict):
            continue
        picks.append({
            "home": fix.get("home_team", {}).get("name", "Home"),
            "away": fix.get("away_team", {}).get("name", "Away"),
            "league": fix.get("league", {}).get("name", "League"),
            "time": fix.get("match_date", ""),
            "prob": round((pred.get("over_1_5_probability") or 0.75) * 100),
            "score": pred.get("most_likely_score", "2-1")
        })

    picks.sort(key=lambda x: x["prob"], reverse=True)
    top7 = picks[:7]

    now_gmt1 = datetime.now(timezone.utc) + timedelta(hours=1)
    now_str = now_gmt1.strftime("%A, %b %d, %Y")

    lines = [
        "🎯 <b>SOCCER GOAL PREDICTOR — TOP 7 DAILY PICKS</b> 🎯",
        f"📅 <i>{now_str} (GMT+1)</i>\n",
        f"🔥 <b>Best {len(top7)} Matches Most Likely To Have 2+ Goals (Over 1.5):</b>\n"
    ]

    for idx, p in enumerate(top7, 1):
        match_time = "TBD"
        if p["time"]:
            try:
                dt = datetime.fromisoformat(str(p["time"]).replace("Z", "+00:00"))
                match_time = (dt.astimezone(timezone.utc) + timedelta(hours=1)).strftime("%I:%M %p")
            except Exception:
                match_time = str(p["time"])[11:16]

        lines.append(
            f"{idx}. ⚽ <b>{p['home']} vs {p['away']}</b>\n"
            f"   🏆 {p['league']} | ⏰ {match_time} GMT+1\n"
            f"   🔥 <b>Over 1.5 Probability: {p['prob']}%</b> | Expected Score: <b>{p['score']}</b>\n"
        )

    lines.append("⚡ <i>Powered by Dixon-Coles Goal Expectation Engine</i>")
    msg = "\n".join(lines)

    tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            res = client.post(tg_url, json=payload)
            if res.status_code == 200:
                logger.info("✅ Telegram Top 7 Daily Picks broadcast delivered successfully!")
            else:
                logger.error(f"Telegram API error {res.status_code}: {res.text}")
                sys.exit(1)
    except Exception as e:
        logger.error(f"Error dispatching to Telegram API: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_broadcast()
