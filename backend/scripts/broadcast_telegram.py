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

def send_outcome_recap(client: httpx.Client, is_night_mode: bool, now_utc: datetime):
    if is_night_mode:
        prev_title = "Daily Picks"
        prev_date = now_utc.strftime("%A, %b %d, %Y")
        start_utc = datetime(now_utc.year, now_utc.month, now_utc.day, 6, 50, 0, tzinfo=timezone.utc)
        end_utc = datetime(now_utc.year, now_utc.month, now_utc.day, 21, 59, 59, tzinfo=timezone.utc)
    else:
        prev_title = "Early Morning Picks"
        prev_date = now_utc.strftime("%A, %b %d, %Y")
        start_utc = datetime(now_utc.year, now_utc.month, now_utc.day, 0, 50, 0, tzinfo=timezone.utc)
        end_utc = datetime(now_utc.year, now_utc.month, now_utc.day, 6, 50, 0, tzinfo=timezone.utc)

    # Fetch recent finished fixtures from API
    try:
        url = f"{API_BASE_URL}/api/fixtures/recent"
        resp = client.get(url)
        if resp.status_code != 200:
            return
        raw_data = resp.json()
        fixtures = raw_data.get("fixtures", []) if isinstance(raw_data, dict) else (raw_data if isinstance(raw_data, list) else [])
    except Exception:
        return

    recap_items = []
    for fix in fixtures:
        if not isinstance(fix, dict):
            continue
        status = fix.get("status", "")
        if status not in ["FINISHED", "FT", "AET", "PEN"]:
            continue
        match_date_raw = fix.get("match_date")
        if match_date_raw:
            try:
                dt = datetime.fromisoformat(str(match_date_raw).replace("Z", "+00:00"))
                if dt < start_utc or dt > end_utc:
                    continue
            except Exception:
                pass
        h_score = fix.get("home_score") if fix.get("home_score") is not None else 0
        a_score = fix.get("away_score") if fix.get("away_score") is not None else 0
        tot_goals = h_score + a_score
        pred = fix.get("prediction", {})
        prob = round((pred.get("over_1_5_probability") or 0.75) * 100) if isinstance(pred, dict) else 75
        recap_items.append({
            "home": fix.get("home_team", {}).get("name", "Home"),
            "away": fix.get("away_team", {}).get("name", "Away"),
            "home_score": h_score,
            "away_score": a_score,
            "total_goals": tot_goals,
            "prob": prob,
            "is_won": tot_goals >= 2
        })

    if not recap_items:
        return

    won_count = sum(1 for item in recap_items if item["is_won"])
    total_count = len(recap_items)
    win_rate = round((won_count / max(1, total_count)) * 100, 1)

    lines = [
        "📊 <b>SOCCER GOAL PREDICTOR — RESULTS RECAP</b> 📊",
        f"📅 <i>{prev_date} ({prev_title} Results)</i>\n"
    ]

    for idx, item in enumerate(recap_items, 1):
        status_icon = "✅ WON" if item["is_won"] else "❌ LOST"
        lines.append(
            f"{idx}. ⚽ <b>{item['home']} {item['home_score']} - {item['away_score']} {item['away']}</b>\n"
            f"   🔥 Over 1.5 Prob: <b>{item['prob']}%</b> | Total Goals: <b>{item['total_goals']}</b> -> <b>{status_icon}</b>\n"
        )

    lines.append(f"📈 <b>Summary: {won_count}/{total_count} Won ({win_rate}% Accuracy)</b>")
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
        client.post(tg_url, json=payload)
        logger.info("✅ Outcome recap broadcast delivered successfully!")
    except Exception as e:
        logger.error(f"Error dispatching outcome recap to Telegram API: {e}")


def run_broadcast():
    logger.info("Fetching upcoming fixtures from API...")
    url = f"{API_BASE_URL}/api/fixtures/upcoming"
    
    fixtures = []
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                raw_data = resp.json()
                if isinstance(raw_data, dict):
                    fixtures = raw_data.get("fixtures", [])
                elif isinstance(raw_data, list):
                    fixtures = raw_data
            else:
                logger.error(f"Failed to fetch fixtures: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Error connecting to backend API: {e}")

    # Fallback retry if fixtures is empty (e.g., Render server cold start or pending ingestion)
    if not fixtures:
        logger.info("No fixtures returned initially. Triggering API sync fallback...")
        try:
            with httpx.Client(timeout=60.0) as client:
                sync_resp = client.post(f"{API_BASE_URL}/api/ingest/sync")
                if sync_resp.status_code == 200:
                    logger.info("API sync completed. Re-fetching upcoming fixtures...")
                    resp2 = client.get(url)
                    if resp2.status_code == 200:
                        raw_data = resp2.json()
                        if isinstance(raw_data, dict):
                            fixtures = raw_data.get("fixtures", [])
                        elif isinstance(raw_data, list):
                            fixtures = raw_data
        except Exception as e:
            logger.error(f"Error during fallback sync: {e}")

    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour

    # Night Broadcast (10 PM GMT / 22:00 UTC): Broadcasts 1:00 AM – 6:50 AM GMT fixtures for the next day
    is_night_mode = (current_hour >= 20 or current_hour < 3)

    # STEP 1: Dispatch Outcome Recap Report for previous broadcast window
    try:
        with httpx.Client(timeout=30.0) as client:
            send_outcome_recap(client, is_night_mode, now_utc)
    except Exception as recap_err:
        logger.error(f"Error checking/sending outcome recap: {recap_err}")

    if is_night_mode:
        category_title = "EARLY MORNING PICKS"
        window_title = "1:00 AM – 6:50 AM GMT"
        target_date = (now_utc + timedelta(days=1)).date() if current_hour >= 20 else now_utc.date()
        target_start_utc = datetime(target_date.year, target_date.month, target_date.day, 0, 50, 0, tzinfo=timezone.utc)
        target_end_utc = datetime(target_date.year, target_date.month, target_date.day, 6, 50, 0, tzinfo=timezone.utc)
        date_display_str = target_date.strftime("%A, %b %d, %Y")
    else:
        category_title = "DAILY TOP PICKS"
        target_date = now_utc.date()
        start_hour_gmt = max(7, (now_utc + timedelta(hours=1)).hour)
        display_start = f"{start_hour_gmt}:00 AM" if start_hour_gmt < 12 else f"{start_hour_gmt - 12 if start_hour_gmt > 12 else 12}:00 PM"
        window_title = f"{display_start} – 11:59 PM GMT"
        now_cutoff_utc = now_utc - timedelta(minutes=15)
        target_start_utc = max(datetime(target_date.year, target_date.month, target_date.day, 6, 50, 0, tzinfo=timezone.utc), now_cutoff_utc)
        target_end_utc = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=timezone.utc)
        date_display_str = target_date.strftime("%A, %b %d, %Y")

    picks = []
    for fix in fixtures:
        if not isinstance(fix, dict):
            continue
        pred = fix.get("prediction")
        if not pred or not isinstance(pred, dict):
            continue

        match_time_raw = fix.get("match_date")
        if match_time_raw:
            try:
                dt = datetime.fromisoformat(str(match_time_raw).replace("Z", "+00:00"))
                if dt < target_start_utc or dt > target_end_utc:
                    continue
            except Exception:
                pass

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

    # GUARD: If no picks are available, DO NOT dispatch message
    if not top7:
        logger.warning(f"No upcoming fixtures/picks available for Telegram broadcast ({category_title}). Suppressing dispatch.")
        return

    lines = [
        f"🎯 <b>SOCCER GOAL PREDICTOR — {category_title}</b> 🎯",
        f"📅 <i>{date_display_str} ({window_title})</i>\n",
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
