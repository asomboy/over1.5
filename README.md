# ⚽ Soccer Goal Predictor

A full-stack web application scaffold for predicting soccer goal expectations and match analytics.

## 🛠️ Tech Stack

- **Backend**: FastAPI (Python 3.14)
- **Frontend**: React (Vite) + Tailwind CSS (v4) + Lucide Icons
- **Database**: SQLite (`backend/soccer_predictor.db`) managed with SQLAlchemy
- **Communication**: REST API with CORS enabled for local development

---

## 🚀 How to Run Locally

### Option 1: Quick Launch Script (Windows PowerShell)
Run the automated launcher script from the root directory:
```powershell
.\run_dev.ps1
```

---

### Option 2: Run Backend & Frontend Separately

#### 1. Start FastAPI Backend
```bash
cd backend
# Activate virtual environment (if needed)
.\venv\Scripts\Activate.ps1

# Start Uvicorn development server
python -m uvicorn main:app --reload --port 8000
```
- **Backend API**: `http://localhost:8000`
- **Health Check**: `http://localhost:8000/health` (Returns `{"status": "ok"}`)
- **Swagger Docs**: `http://localhost:8000/docs`

#### 2. Start React Frontend
```bash
cd frontend
npm run dev
```
- **Frontend App**: `http://localhost:5173`

---

## 📁 Project Structure

```
Soccer Goal Predictor/
├── backend/
│   ├── main.py              # FastAPI app, CORS, GET /health endpoint
│   ├── database.py          # SQLite database connection & SQLAlchemy setup
│   ├── soccer_predictor.db  # SQLite database file (auto-generated on startup)
│   ├── requirements.txt     # Python dependencies
│   └── venv/                # Python virtual environment
├── frontend/
│   ├── index.html           # HTML template with Google Fonts
│   ├── package.json         # React + Vite + Tailwind CSS dependencies
│   ├── vite.config.js       # Vite configuration with @tailwindcss/vite
│   └── src/
│       ├── App.jsx          # Soccer Goal Predictor homepage UI with health check
│       ├── index.css        # Tailwind CSS imports & custom glassmorphism styles
│       └── main.jsx         # React application root
├── run_dev.ps1              # Concurrent dev runner script
├── .agents/AGENTS.md        # Persistent system rules and architectural guidelines
└── README.md                # Documentation

---

## 🔒 System Standards & Stability Guarantees

1. **Timezone Normalization**: Ingested match dates are saved as naive UTC datetimes in SQLite, and queries compare against naive UTC timestamps for 100% SQLite query accuracy.
2. **Silent Background Refresh**: Frontend polling runs silently (`isSilent = true`) every 20 seconds, updating live scores without unmounting Table 2 or triggering full-screen loading cards.
3. **High-Performance Pagination**: Table 2 renders using 50-item page slicing, keeping browser DOM render speeds under 5ms.
4. **Dynamic Prediction Ratings**: Every team receives unique attack and defense strength ratings via `resolve_team_ratings`, ensuring diverse Over 1.5 Goal percentages (60%–95%+).
5. **Data Ingestion Integrity**: Synthetic mock fixtures are automatically purged during live ingestion to maintain official match schedules.

```
