@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 正在打开 GitHub 创建仓库页面...
start "" "https://github.com/new?name=quant-strategy&description=豆粕量化策略与Django看板"
pause
echo 正在推送...
git push -u origin main
pause
