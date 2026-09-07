# 横盘缩量策略说明

横盘缩量已纳入统一策略注册表，策略 ID 为 `volume_consolidation`，当前成熟度为 `exploratory`，输出类型为 `observation_pool`。它只研究已经出现可验证收盘涨停、随后进入 2–4 个交易日整理窗口的沪深主板股票。

## 规则与证据

当前规则版本沿用涨停后研究引擎的版本声明。核心证据包括：

- 不晚于信号日的真实涨停锚点。
- 连续且来源一致的 OHLCV 窗口。
- 整理区间幅度、相对涨停收盘变化和成交量收缩。
- 首次符合日期、持续观察状态、风险和 `data_missing`。

持续符合可以继续出现在观察池，但不会重复计为新的首次信号。再次涨停会建立新的事件锚点。历史读取不得使用 `data_as_of` 之后的 K 线。

## 产品展示

通过 `/recommendations?strategy=volume_consolidation` 进入统一策略工作台；公共数据由以下接口提供：

```text
GET /api/strategies/volume_consolidation/latest
GET /api/strategies/volume_consolidation/history
GET /api/strategies/volume_consolidation/stocks/{symbol}
GET /api/strategies/volume_consolidation/statistics
```

页面显示观察池、涨停锚点、规则阈值、证据、缺失项、逐日路径和历史样本完整度，不显示概率、胜率承诺或名次。空池与数据不足分别解释，不放宽规则凑候选。

## Outcome 与边界

每日首次可观察信号以不可变快照保存。D+1、D+3、D+5 只按实际市场交易日对齐；对应 K 线缺失时保持未就绪，不用下一根可用数据替代。

该策略仍处于探索阶段，成交量来源、复权和历史覆盖会限制样本完整度。完成足够前向样本积累前不能申请排名资格，也不构成交易建议。
