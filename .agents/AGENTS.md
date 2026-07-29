# Repository Architectural Rules & System Guarantees

This document establishes mandatory guidelines and technical constraints for the **Soccer Goal Predictor** project. All developers and AI agents MUST adhere strictly to these principles to prevent regressions.

---

## 1. Timezone & Date Normalization Constraints
- **SQLite Naive UTC Storage**: All fixture dates ingested from external APIs (ESPN / Football API) MUST be converted to naive UTC `datetime` objects (`match_date.astimezone(timezone.utc).replace(tzinfo=None)`) before saving to SQLite.
- **SQLite Cutoff Queries**: All date filtering queries in FastAPI endpoints (e.g., `get_upcoming_fixtures`) MUST compare against naive UTC datetimes (`(datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None)`). Passing timezone-aware objects to SQLite string comparisons causes incorrect date filtering.
- **ISO Output Formatting**: API JSON responses MUST serialize `match_date` using explicit ISO UTC formatting ending with timezone offsets (`+00:00` or `Z`).
- **GMT+1 Frontend Formatting**: Frontend date utilities (`formatDateGMT1` and `getGMT1DayKey` in `App.jsx`) MUST parse dates safely using `parseMatchDate` and format times with `Intl.DateTimeFormat('en-US', { timeZone: 'Europe/London' })`.

---

## 2. Dynamic Goal Predictions & xG Calculation Rules
- **No Hardcoded 1.5/1.2 Fallbacks**: FastAPI endpoints (`backend/main.py`) MUST NOT overwrite stored prediction values with static fallback constants (e.g., `h_xg = 1.5`, `a_xg = 1.2`). Predictions MUST reflect actual database `Prediction` records or dynamic `PoissonPredictionEngine.predict_fixture` outputs.
- **Team Strength Rating Variance**: `resolve_team_ratings` in `prediction_service.py` MUST apply hash-based deterministic variance across team attack and defense ratings (`att_var`, `def_var`) so every fixture receives distinct expected goal totals (xG) and unique Over 1.5 Goal probabilities (ranging from 60% to 95%+).
- **Statistics Analyzed Check**: `calculate_xg` MUST check whether teams have 3+ analyzed matches before relying strictly on historical match averages, falling back to dynamic `resolve_team_ratings` otherwise.

---

## 3. UI State Stability & Silent Refresh Controls
- **Silent Background Polling**: `fetchUpcomingFixtures` in `App.jsx` MUST support an `isSilent` parameter (`fetchUpcomingFixtures(isSilent = true)`). Background interval refreshes (every 20s) MUST run silently without setting `loading = true`. Setting `loading = true` during background polling causes Table 2 to flicker and unmount.
- **Stable Match Day Selection**: Table 1 (`selectedPickDay`) MUST default to the current match date (`Today`) when matches exist, falling back gracefully to the earliest upcoming match day without resetting or jumping unexpectedly during background refreshes.

---

## 4. DOM Performance & Table Pagination
- **Table 2 Pagination**: Main fixtures table in `App.jsx` MUST map over `paginatedFixtures` sliced by `pageSize` (default: 50 items per page). Rendering > 1,000 unpaginated raw DOM rows simultaneously stalls browser render threads.
- **Safe Array Sorting**: Array `.sort()` callbacks MUST convert date strings via `parseMatchDate(x.match_date)?.getTime() || 0` to prevent `NaN` evaluation errors in JavaScript.

---

## 5. Ingestion Integrity & Placeholder Prevention
- **Mock Placeholder Purges**: `DataIngestionService.fetch_and_ingest_from_api` MUST purge synthetic mock placeholders (`FIX-%`, `HIST-%`, `SEED-%`) so only official live/upcoming match calendars exist in SQLite.
- **League Slug Disambiguation**: `season_slug` parsing MUST check specific regional keys (e.g., `"scottish"`) BEFORE broad keys (e.g., `"premier-league"`) to avoid mislabeling Scottish Premiership fixtures as English Premier League.
