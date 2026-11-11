import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram_broadcast")

API_BASE_URL = os.getenv("API_BASE_URL", "https://soccer-goal-predictor-api.onrender.com").rstrip("/")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()


def _extract_fixtures_list(raw_data: Any) -> List[Dict[str, Any]]:
    if isinstance(raw_data, list):
        return [item for item in raw_data if isinstance(item, dict)]
    if isinstance(raw_data, dict):
        items = raw_data.get("data")
        if not isinstance(items, list):
            items = raw_data.get("fixtures", [])
        return [item for item in items if isinstance(item, dict)]
    return []


def _parse_iso_utc(raw_dt: Any) -> datetime | None:
    if not raw_dt:
        return None
    try:
        dt = datetime.fromisoformat(str(raw_dt).strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as err:
        logger.debug("Skipping unparseable match_date '%s': %s", raw_dt, err)
        return None


def send_outcome_recap(client: httpx.Client, is_night_mode: bool, now_utc: datetime) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        return

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

    try:
        resp = client.get(f"{API_BASE_URL}/api/fixtures/finished")
        if resp.status_code != 200:
            logger.warning("Finished fixtures endpoint returned HTTP %s", resp.status_code)
            return
        fixtures = _extract_fixtures_list(resp.json())
    except Exception as exc:
        logger.warning("Failed to fetch finished fixtures for recap: %s", exc)
        return

    recap_items: List[Dict[str, Any]] = []
    for fix in fixtures:
        status = str(fix.get("status") or "").upper()
        if status not in ["FINISHED", "FT", "AET", "PEN"]:
            continue

        dt = _parse_iso_utc(fix.get("match_date"))
        if dt is None or dt < start_utc or dt > end_utc:
            continue

        h_score = fix.get("home_score")
        a_score = fix.get("away_score")
        if h_score is None or a_score is None:
            continue

        pred = fix.get("prediction")
        if not isinstance(pred, dict) or pred.get("over_1_5_probability") is None:
            continue

        tot_goals = int(h_score) + int(a_score)
        prob = round(float(pred["over_1_5_probability"]) * 100)
        recap_items.append({
            "home": (fix.get("home_team") or {}).get("name", "Home"),
            "away": (fix.get("away_team") or {}).get("name", "Away"),
            "home_score": int(h_score),
            "away_score": int(a_score),
            "total_goals": tot_goals,
            "prob": prob,
            "is_won": tot_goals >= 2,
        })

    if not recap_items:
        return

    won_count = sum(1 for item in recap_items if item["is_won"])
    total_count = len(recap_items)
    win_rate = round((won_count / max(1, total_count)) * 100, 1)

    lines = [
        "📊 <b>SOCCER GOAL PREDICTOR — RESULTS RECAP</b> 📊",
        f"📅 <i>{prev_date} ({prev_title} Results)</i>\n",
    ]
    for idx, item in enumerate(recap_items, 1):
        status_icon = "✅ WON" if item["is_won"] else "❌ LOST"
        lines.append(
            f"{idx}. ⚽ <b>{item['home']} {item['home_score']} - {item['away_score']} {item['away']}</b>\n"
            f"   🔥 Over 1.5 Prob: <b>{item['prob']}%</b> | Total Goals: <b>{item['total_goals']}</b> -> <b>{status_icon}</b>\n"
        )
    lines.append(f"📈 <b>Summary: {won_count}/{total_count} Won ({win_rate}% Accuracy)</b>")
    lines.append("⚡ <i>Powered by Dixon-Coles Goal Expectation Engine</i>")

    tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": "\n".join(lines),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        tg_resp = client.post(tg_url, json=payload)
        if tg_resp.status_code == 200:
            logger.info("Outcome recap broadcast delivered successfully.")
        else:
            logger.warning("Telegram recap returned HTTP %s: %s", tg_resp.status_code, tg_resp.text)
    except Exception as e:
        logger.error("Error dispatching outcome recap to Telegram API: %s", e)


def run_broadcast() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables must be set.")
        sys.exit(1)

    logger.info("Fetching upcoming fixtures from API...")
    url = f"{API_BASE_URL}/api/fixtures/upcoming"
    fixtures: List[Dict[str, Any]] = []

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                fixtures = _extract_fixtures_list(resp.json())
            else:
                logger.error("Failed to fetch fixtures: %s %s", resp.status_code, resp.text)
    except Exception as e:
        logger.error("Error connecting to backend API: %s", e)

    if not fixtures:
        logger.info("No fixtures returned initially. Triggering API sync fallback...")
        headers = {"X-Admin-Key": ADMIN_API_KEY} if ADMIN_API_KEY else {}
        try:
            with httpx.Client(timeout=60.0) as client:
                sync_resp = client.post(f"{API_BASE_URL}/api/ingest/sync", headers=headers)
                if sync_resp.status_code == 200:
                    logger.info("API sync completed. Re-fetching upcoming fixtures...")
                    resp2 = client.get(url)
                    if resp2.status_code == 200:
                        fixtures = _extract_fixtures_list(resp2.json())
        except Exception as e:
            logger.error("Error during fallback sync: %s", e)

    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour
    is_night_mode = current_hour >= 20 or current_hour < 3

    try:
        with httpx.Client(timeout=30.0) as client:
            send_outcome_recap(client, is_night_mode, now_utc)
    except Exception as recap_err:
        logger.error("Error checking/sending outcome recap: %s", recap_err)

    if is_night_mode:
        category_title = "EARLY MORNING PICKS"
        window_title = "1:00 AM – 6:50 AM GMT+1"
        target_date = (now_utc + timedelta(days=1)).date() if current_hour >= 20 else now_utc.date()
        target_start_utc = datetime(target_date.year, target_date.month, target_date.day, 0, 50, 0, tzinfo=timezone.utc)
        target_end_utc = datetime(target_date.year, target_date.month, target_date.day, 6, 50, 0, tzinfo=timezone.utc)
        date_display_str = target_date.strftime("%A, %b %d, %Y")
    else:
        category_title = "DAILY TOP PICKS"
        target_date = now_utc.date()
        start_hour_gmt = max(7, (now_utc + timedelta(hours=1)).hour)
        display_start = f"{start_hour_gmt}:00 AM" if start_hour_gmt < 12 else f"{start_hour_gmt - 12 if start_hour_gmt > 12 else 12}:00 PM"
        window_title = f"{display_start} – 11:59 PM GMT+1"
        now_cutoff_utc = now_utc - timedelta(minutes=15)
        target_start_utc = max(datetime(target_date.year, target_date.month, target_date.day, 6, 50, 0, tzinfo=timezone.utc), now_cutoff_utc)
        target_end_utc = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=timezone.utc)
        date_display_str = target_date.strftime("%A, %b %d, %Y")

    picks: List[Dict[str, Any]] = []
    for fix in fixtures:
        pred = fix.get("prediction")
        if not isinstance(pred, dict) or pred.get("over_1_5_probability") is None:
            continue

        dt = _parse_iso_utc(fix.get("match_date"))
        if dt is None or dt < target_start_utc or dt > target_end_utc:
            continue

        picks.append({
            "home": (fix.get("home_team") or {}).get("name", "Home"),
            "away": (fix.get("away_team") or {}).get("name", "Away"),
            "league": (fix.get("league") or {}).get("name", "League"),
            "dt": dt,
            "prob": round(float(pred["over_1_5_probability"]) * 100),
            "score": pred.get("most_likely_score") or "N/A",
        })

    picks.sort(key=lambda x: x["prob"], reverse=True)
    top7 = picks[:7]

    if not top7:
        logger.warning("No upcoming fixtures/picks available for Telegram broadcast (%s). Suppressing dispatch.", category_title)
        return

    lines = [
        f"🎯 <b>SOCCER GOAL PREDICTOR — {category_title}</b> 🎯",
        f"📅 <i>{date_display_str} ({window_title})</i>\n",
        f"🔥 <b>Best {len(top7)} Matches Most Likely To Have 2+ Goals (Over 1.5):</b>\n",
    ]
    for idx, p in enumerate(top7, 1):
        match_time = (p["dt"].astimezone(timezone.utc) + timedelta(hours=1)).strftime("%I:%M %p")
        lines.append(
            f"{idx}. ⚽ <b>{p['home']} vs {p['away']}</b>\n"
            f"   🏆 {p['league']} | ⏰ {match_time} GMT+1\n"
            f"   🔥 <b>Over 1.5 Probability: {p['prob']}%</b> | Expected Score: <b>{p['score']}</b>\n"
        )
    lines.append("⚡ <i>Powered by Dixon-Coles Goal Expectation Engine</i>")

    tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": "\n".join(lines),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            res = client.post(tg_url, json=payload)
            if res.status_code == 200:
                logger.info("Telegram Top 7 Daily Picks broadcast delivered successfully.")
            else:
                logger.error("Telegram API error %s: %s", res.status_code, res.text)
                sys.exit(1)
    except Exception as e:
        logger.error("Error dispatching to Telegram API: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    run_broadcast()
