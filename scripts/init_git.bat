@echo off
chcp 65001 >nul
cd /d "%~dp0.."

where git >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Git，请先安装
    pause
    exit /b 1
)

if exist ".git" (
    echo 已是 Git 仓库，跳过初始化。
    git status
) else (
    echo 正在初始化 Git 仓库...
    git init
    git add .
    git status
    echo.
    echo 请执行: git commit -m "初始提交"
    echo 然后: git remote add origin https://github.com/你的用户名/仓库名.git
    echo       git push -u origin main
)

pause
