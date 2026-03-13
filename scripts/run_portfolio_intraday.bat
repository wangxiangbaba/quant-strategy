@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -m strategy.portfolio_intraday_matrix %*
) else (
    python -m strategy.portfolio_intraday_matrix %*
)
pause
