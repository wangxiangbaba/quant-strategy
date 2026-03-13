@echo off
chcp 65001 >nul
echo 正在打开 GitHub 创建仓库页面...
echo 仓库名已预填为: quant-strategy
echo.
echo 请按以下步骤操作:
echo   1. 在打开的页面中点击 "Create repository"
echo   2. 创建完成后，回到此窗口按任意键
echo   3. 将自动执行 git push 推送代码
echo.
start "" "https://github.com/new?name=quant-strategy&description=豆粕量化策略与Django看板"
pause
echo.
echo 正在推送代码到 GitHub...
cd /d "%~dp0"
"C:\Program Files\Git\bin\git.exe" push -u origin main
if errorlevel 1 (
    echo.
    echo 推送失败。请确认:
    echo   1. 已在 GitHub 创建 quant-strategy 仓库
    echo   2. 已登录 GitHub 或配置好凭据
    echo.
    echo 手动推送命令: git push -u origin main
)
pause
