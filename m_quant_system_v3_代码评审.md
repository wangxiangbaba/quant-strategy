# m_quant_system_v3_live.py 代码评审报告

## 一、整体评价

### 优点
- **架构清晰**：风控、数据、指标、回测、实盘分层明确，模块化较好
- **风控增强**：动态水位线、日内熔断、时间过滤等设计合理
- **复权数据**：使用 `KQ.m@DCE.m` 消除换月跳空，思路正确
- **Numpy 回测**：用数组替代 DataFrame 循环，性能有提升空间

### 不足
- 存在多处逻辑 Bug，实盘可能导致盈亏计算错误或止损异常
- 部分设计与注释不符（如「Tick 级熔断」实际未实现）
- 回测与实盘在止损、权益计算上不一致

---

## 二、已发现的 Bug

### 🔴 Bug 1：回测中 entry_price 引用错误（严重）

**位置**：第 320、330 行

```python
trade_pnl = (open_i1 - entry_price[i - 1 if i > 0 else i]) * cur_lots * cfg.multiplier
```

**问题**：平仓时用 `entry_price[i-1]` 或 `entry_price[i]` 作为入场价，但实际入场发生在更早的某根 K 线 `j`，正确应为 `entry_price[j]`。当前写法会用到错误或 NaN 的入场价，导致 PnL 计算错误。

**修复建议**：用变量记录当前持仓的入场价，例如：
```python
# 在循环外定义
current_entry_price = np.nan

# 开仓时
current_entry_price = open_i1

# 平仓时
trade_pnl = (open_i1 - current_entry_price) * cur_lots * cfg.multiplier  # 多头
```

---

### 🔴 Bug 2：回测权益曲线被错误覆盖（严重）

**位置**：第 353-364 行

**问题**：主循环已正确计算 `equity_curve`（含手续费、滑点），但后面用 `eq_final` 覆盖了 `df["equity"]`。`eq_final` 仅做逐日 MTM，未扣除手续费和滑点，导致回测绩效失真。

**修复建议**：直接使用主循环中的 `equity_curve`：
```python
df["equity"] = equity_curve  # 不要用 eq_final 覆盖
```

---

### 🔴 Bug 3：开空时未设置 low_entry（严重）

**位置**：第 377-384 行

**问题**：开多时有 `high_entry = close`，开空时缺少 `low_entry = close`。空头止损依赖 `low_entry`，未初始化会导致首次进入止损逻辑时用 `low_entry == 0` 的兜底，行为不可靠。

**修复建议**：在 `short_cond` 分支中补充：
```python
elif short_cond:
    ...
    low_entry = close  # 缺失此行
```

---

### 🟡 Bug 4：实盘止损与仓位计算使用不同 stop_mul（中等）

**位置**：第 358-359 行 vs 第 491、499 行

**问题**：开仓时用自适应 `stop_mul`（1.5~3.5）计算 `lots`，但止损监控固定用 `cfg["stop_atr_high"]`（3.5）。两者不一致，可能造成：
- 实际止损距离大于预期
- 仓位与风险暴露不匹配

**修复建议**：止损监控也应使用与开仓相同的 `stop_mul`，或在每次 K 线更新时保存 `stop_mul` 供止损逻辑使用。

---

### 🟡 Bug 5：KQ 连续合约的 TargetPosTask 可能不适用（中等）

**位置**：第 385 行

**问题**：`KQ.m@DCE.m` 为指数/连续合约，通常不能直接下单。实盘一般交易具体合约（如 `DCE.m2609`）。若对 KQ 使用 `TargetPosTask`，可能报错或无法成交。

**修复建议**：确认 TqSdk 对 KQ 的支持方式；若需实盘交易，应改为具体合约，或使用换月逻辑。

---

### 🟡 Bug 6：DAILY_RISK 初始化时机（轻微）

**位置**：第 57-62 行

**问题**：`DAILY_RISK["date"] = datetime.now().date()` 在模块加载时执行。若程序在 23:59 启动，跨日后 `today != DAILY_RISK["date"]` 会立即触发重置，逻辑上可接受，但 `start_equity` 在首次调用前为 0，需依赖后续赋值，可读性一般。

**修复建议**：在 `run_live()` 启动时显式初始化 `DAILY_RISK["start_equity"]`。

---

### 🟡 Bug 7：loss_per_lot 计算缺少 tick_size（轻微）

**位置**：第 359 行

```python
loss_per_lot = (atr_val * stop_mul) * 10 + (cfg["commission_per_lot"] * 2 + cfg["slippage_ticks"])
```

**问题**：滑点应为 `slippage_ticks * tick_size`，与回测和 v2 一致。当前未乘 `tick_size`，会导致仓位偏大。

**修复建议**：
```python
loss_per_lot = (atr_val * stop_mul) * 10 + (cfg["commission_per_lot"] * 2 + cfg["slippage_ticks"] * 1.0)
```
（若 `tick_size=1` 可省略，但建议显式写出以保持一致性）

---

## 三、其他建议

1. **print_report 年化计算**：`252 / len(ret)` 在日线回测中可接受，但 `ret` 若含大量 0 会导致年化失真，可考虑用实际交易天数。
2. **异常后重启**：`run_live()` 异常后外层 `while True` 会重启，但未做退避或次数限制，可能造成频繁重启。
3. **硬编码账号**：账号密码直接写在代码中，建议改为环境变量或配置文件。

---

## 四、总结

| 类型     | 数量 |
|----------|------|
| 严重 Bug | 3    |
| 中等 Bug | 3    |
| 轻微 Bug | 1    |

建议优先修复 Bug 1、2、3，再考虑其余问题。修复完成后再用于实盘或回测评估。
