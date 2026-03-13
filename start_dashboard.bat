@echo off
chcp 65001 >nul
cd /d "%~dp0quant_web"
if exist "..\.venv\Scripts\python.exe" (
    echo 使用虚拟环境启动量化看板...
    ..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
) else (
    echo 正在启动量化看板 (Django)...
    python manage.py runserver 127.0.0.1:8000
)
pause
