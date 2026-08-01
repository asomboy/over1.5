# Soccer Goal Predictor - Full-Stack Local Launcher Script

Write-Host "==========================================" -ForegroundColor Green
Write-Host " Starting Soccer Goal Predictor Application" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Start Backend in background process
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$ScriptDir\backend'; & '$ScriptDir\backend\venv\Scripts\python.exe' -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

# Start Frontend in background process
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$ScriptDir\frontend'; npm.cmd run dev"

Write-Host ""
Write-Host "Backend API:      http://localhost:8000" -ForegroundColor Cyan
Write-Host "Backend Health:   http://localhost:8000/health" -ForegroundColor Cyan
Write-Host "API Docs:         http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "Frontend Web App: http://localhost:5173" -ForegroundColor Green
Write-Host ""
