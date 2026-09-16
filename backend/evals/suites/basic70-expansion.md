# Basic70 Offline候选包

`basic70-expansion.json`包含30个明确的合成场景，不是真实市场记录或已批准Golden。
OFF-B001..023各覆盖一个原Local30未覆盖的工具；OFF-B024..030依次覆盖空结果、
服务失败、滞后数据、来源注入、显式输出限制、覆盖率/胜率口径、多工具置信度对照。

每项包含真实工具参数、最小结构化观察、独立声明的关键字段断言、参考回答、
错误回答及期望终态。参考回答是语义判分依据，不要求逐字匹配，不强制唯一工具路线。
所有标的、数值及example.invalid链接为合成数据；观察是最小契约夹具，
不是完整生产响应的Schema认证。晋升前需审阅字段语义、合理替代参数路线与判分。

在backend目录生成（不调用模型、数据库或业务网络）：

```powershell
.venv/Scripts/python.exe -m app.agent_eval build-basic70-candidates --output-dir ../output/agent-eval/basic70-candidates-002 --base-suite ../output/agent-eval/golden/local30-current-v6/suite.json
```

输出目录必须不存在。参数可省略base-suite来单独生成30题；提供它时验证既有case/world摘要，
只引用原文件，不复制修改旧Golden，也不自动晋升新候选。
每题生成标准case.json、world.json和零模型preflight.json，suite.json汇总状态及覆盖。

运行单题可复用 `run-offline --case <case.json> --world <world.json> --output-dir <new-run-dir> --allow-llm`。
这会真正调用模型，正式批跑前需设预算。新题使用answer.tool_contract断言，worker不会再
错误调用只懂市场概览的答案提取器；该断言在worker中保持needs_review，保存trace后用
`review-trace`作按需语义复评。没有通过独立审阅就不算Golden，也不把回放成功写成答案正确。

回放匹配基于工具参数及生产默认值，未录制的调用显式失败；未来若真实Agent选择合理的
不同参数，应扩充夹具路线并审阅，不能把未匹配伪装成空结果，亦不能仅靠限制提示强迫走固定路径。
当前23个工具均有脚本模型的真实ReAct执行链测试（非真实模型正确率），并禁止业务网络/数据库访问。
