@echo off
chcp 65001 >nul
cd /d "%~dp0"

where git >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Git，请先安装：
    echo   1. 访问 https://git-scm.com/download/win
    echo   2. 下载并安装 Git for Windows
    echo   3. 安装完成后重启终端，再运行本脚本
    pause
    exit /b 1
)

if exist ".git" (
    echo 已是 Git 仓库，跳过初始化。
    git status
) else (
    echo 正在初始化 Git 仓库...
    git init
    echo.
    echo 已创建 .gitignore，正在添加文件...
    git add .
    git status
    echo.
    echo 请执行以下命令完成首次提交：
    echo   git commit -m "初始提交：豆粕量化策略与 Django 看板"
    echo.
    echo 如需关联远程仓库（如 GitHub）：
    echo   git remote add origin https://github.com/你的用户名/仓库名.git
    echo   git branch -M main
    echo   git push -u origin main
)

pause
