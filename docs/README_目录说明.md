# 项目目录说明

## 目录结构

```
program/
├── push/                    # 推送模块（飞书、Telegram、企业微信、WeChatFerry）
│   ├── feishu_notify.py
│   ├── telegram_notify.py
│   ├── wechat_notify.py
│   ├── wechat_ferry_notify.py
│   └── push_notify.py
├── strategy/                # 策略模块
│   ├── portfolio_intraday_matrix.py   # 10品种矩阵（5m/10m/30m/60m/1d）
│   ├── portfolio_matrix_full.py       # 日线矩阵
│   ├── m_quant_system_v2.py
│   ├── m_quant_system_v3_live.py
│   └── rb_quant_system.py
├── tests/                   # 测试模块
│   ├── test_push.py
│   ├── test_wechat_ferry.py
│   └── test_internet.py
├── scripts/                 # 批处理
│   ├── run_portfolio_intraday.bat
│   ├── run_portfolio_full.bat
│   ├── start_dashboard.bat
│   ├── init_git.bat
│   └── create_github_repo.bat
├── docs/                    # 文档
│   ├── README_目录说明.md
│   └── Telegram推送配置说明.md
├── quant_web/               # Django 看板
└── conf/                    # 配置模块
    ├── config_account.example.py
    └── config_account.py    # 本地配置（不提交）
```

## 运行方式

在 program 目录下执行：

```bash
# 矩阵策略
python -m strategy.portfolio_intraday_matrix live 10m
python -m strategy.portfolio_intraday_matrix backtest 5m

# 日线矩阵
python -m strategy.portfolio_matrix_full live
python -m strategy.portfolio_matrix_full backtest

# 或使用批处理
scripts\run_portfolio_intraday.bat live 10m
scripts\run_portfolio_full.bat backtest
scripts\start_dashboard.bat
```
