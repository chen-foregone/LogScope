# 真实 Agent 接入工作地图

## Notes

- 父事项：[19](../logscope-v1/issues/19-connect-real-agent-and-analysis-loop.md)，保持原文及状态不变。
- 配套规格：[spec.md](spec.md)。本期仅真实百炼模型加模拟日志来源，不等于真实日志来源验收。
- 用户确认归档 17 完成并授权恢复实施；01 已认领，其他工单尚未开始。

## Decisions-so-far

- 2026-11-06：北京官方地址真实工具调用成功；真实百炼＋模拟 HTTP 来源＋隔离 PostgreSQL 两轮调查成功，见 [实际联调与语义复核](live-validation.md)。语义复核仍有无依据推断，不关闭最终质量验收。

- 用户批准单 Agent、LangChain 接入、LangGraph 编排、qwen-plus、Windows 本机及更多故障类型。
- checkpoint 不自动恢复旧执行；本次联调累计预算按 10 元执行，真实费用以供应商账单为准。
- 用户批准 HTTP API + PostgreSQL + UI 测试边界以及以下依赖图：
  - [01 首轮真实分析](issues/01-first-real-analysis.md)：已完成确定性纵向实现；真实百炼调用和真实日志来源验收仍未证明。
  - [02 多轮只读查询](issues/02-bounded-readonly-tools.md)：依赖 01。
  - [03 多类型分析与核验](issues/03-analysis-and-reverification.md)：依赖 02。
  - [04 人工等级确认](issues/04-human-severity-decisions.md)：依赖 01。
  - [05 隔离与预算验证](issues/05-isolation-and-budget.md)：依赖 02、03、04。
  - [06 真实案例验收](issues/06-real-validation.md)：依赖 05。

## Fog

- 首批安全配置已通过独立测试；尚未装配到 worker，没有发起模型请求，整张 01 尚未完成。
- 归档迁移已占用 0017，新增迁移编号须重新检查。
- 真实调用前核实百炼地域、凭据注入、模型可用性、价格和账单侧预算能力；秘密不得记录在此。
- 实施时明确工具、模型输出、停止确认和费用预留的具体上限，默认关闭不可安全估价的真实调用。
