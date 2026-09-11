# Agent Live Judge Calibration

## Status

当前状态：`awaiting_human_labels`。独立 Judge 已配置并完成 50 条模型标注，但尚未完成 Human A / Human B 双人盲标，因此不能声称 Judge 已校准，也不能启用 Judge 发布门禁。

| 项目 | 值 |
|---|---|
| Agent model | `deepseek-v4-flash` |
| Judge model | `deepseek-v4-pro` |
| Judge prompt | `agent-live-eval-judge-v2` |
| Provider endpoint | `https://api.deepseek.com` |
| Credentials | 本地复用 `DEEPSEEK_API_KEY`；未写入 artifact 或仓库 |
| Source baseline | `live-20260911T080804Z-f4288138` |
| Calibration items | 50 |
| Human labels | 0/230 applicable dimensions per annotator |

Judge 与 Agent 使用不同模型和独立环境变量，但当前共用同一 provider 凭据。如果以后要求 provider-level independence，应单独配置 `LIMITUPLAB_EVAL_JUDGE_API_KEY` 和对应 base URL。

## Sample Coverage

| Category | Items |
|---|---:|
| boundary | 10 |
| recovery | 10 |
| replan | 9 |
| multi_tool | 8 |
| multi_turn | 6 |
| simple | 5 |
| stress | 2 |

所有 50 条均评价 clarity、relevance、task resolution；40 条评价 completeness。动态维度覆盖如下：

| Dimension | Applicable items |
|---|---:|
| boundary compliance | 10 |
| uncertainty disclosure | 10 |
| failure transparency | 10 |
| risk explanation | 10 |

## Preliminary Judge Output

以下数字只是 Judge 自身输出分布，不代表校准结果：

| Metric | Value |
|---|---:|
| Judge case pass rate | 36.00% |
| Clarity | 1.100 / 2 |
| Relevance | 0.900 / 2 |
| Task resolution | 0.740 / 2 |
| Completeness | 0.425 / 2 |
| Boundary compliance | 2.000 / 2 |
| Uncertainty disclosure | 0.600 / 2 |
| Failure transparency | 0.600 / 2 |
| Risk explanation | 0.600 / 2 |

该分布与 baseline 中“确定性 PASS 但回答无效”的抽查一致，但在人类标注完成前，不能据此判断 Judge 的准确性。

## Artifacts

校准文件保存在：

`output/agent-live-eval/live-20260911T080804Z-f4288138/judge-calibration-deepseek-v4-pro/`

- `judge_packet.json`：包含 Judge 分数；Human A/B 不应查看。
- `human_a_labels.json`：Human A 独立填写。
- `human_b_labels.json`：Human B 独立填写。

每位标注者只修改自己文件中各 item 的 `scores`，取值只能是 0、1、2；`notes` 可选。两人不得查看 Judge packet 或对方文件。

## Calibration Gate

完成双标后运行：

```powershell
cd backend
python scripts/calibrate_agent_live_judge.py evaluate `
  ..\output\agent-live-eval\live-20260911T080804Z-f4288138\judge-calibration-deepseek-v4-pro\judge_packet.json `
  ..\output\agent-live-eval\live-20260911T080804Z-f4288138\judge-calibration-deepseek-v4-pro\human_a_labels.json `
  ..\output\agent-live-eval\live-20260911T080804Z-f4288138\judge-calibration-deepseek-v4-pro\human_b_labels.json
```

每个维度必须同时满足：

- Cohen's κ ≥ 0.70。
- Human A / B exact agreement ≥ 80%。
- Judge 对人工共识 exact agreement ≥ 80%。

任何维度未达标时，Judge 只能作为诊断指标，不得进入发布门禁。该原则与 OpenAI Graders 中将确定性 grader 和 model grader 分开配置、验证的做法一致：[OpenAI Graders](https://developers.openai.com/api/reference/resources/graders)。
