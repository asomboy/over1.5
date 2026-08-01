@echo off
echo ==========================================
echo  Starting Soccer Goal Predictor Application
echo ==========================================
echo.

set "SCRIPT_DIR=%~dp0"

:: Start Backend in a new CMD window
start "Soccer Goal Predictor - Backend API" cmd /k "cd /d "%SCRIPT_DIR%backend" && venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

:: Start Frontend in a new CMD window
start "Soccer Goal Predictor - Frontend Web" cmd /k "cd /d "%SCRIPT_DIR%frontend" && npm run dev"

echo.
echo Backend API:      http://localhost:8000
echo Backend Health:   http://localhost:8000/health
echo API Docs:         http://localhost:8000/docs
echo Frontend Web App: http://localhost:5173
echo.
