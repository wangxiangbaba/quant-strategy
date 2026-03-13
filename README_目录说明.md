# 项目目录说明

## 目录结构

```
program/
├── push/                    # 推送模块
│   ├── feishu_notify.py
│   ├── telegram_notify.py
│   ├── wechat_notify.py
│   ├── wechat_ferry_notify.py
│   └── push_notify.py
├── strategy/                # 策略模块
│   └── portfolio_intraday_matrix.py
├── tests/                   # 测试模块
├── scripts/                 # 批处理
│   └── run_portfolio_intraday.bat
└── config_account.py
```

## 运行方式

在 program 目录下执行：

```bash
# 实盘 10m
python -m strategy.portfolio_intraday_matrix live 10m

# 回测 5m
python -m strategy.portfolio_intraday_matrix backtest 5m

# 或使用批处理
scripts\run_portfolio_intraday.bat live 10m
```
