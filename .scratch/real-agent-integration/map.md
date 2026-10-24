# 真实 Agent 接入工作地图

## Notes

- 父事项：[19](../logscope-v1/issues/19-connect-real-agent-and-analysis-loop.md)，保持原文及状态不变。
- 配套规格：[spec.md](spec.md)。本期仅真实百炼模型加模拟日志来源，不等于真实日志来源验收。
- 用户确认归档17完成并授权恢复实施；2026-11-07继续收尾后，01–06均已按实际实现与验收证据核对为resolved，父19原始范围保留。

## Decisions-so-far

- 2026-11-06：北京官方地址真实工具调用成功；真实百炼＋模拟 HTTP 来源＋隔离 PostgreSQL 两轮调查成功，见 [实际联调与语义复核](live-validation.md)。语义复核仍有无依据推断，不关闭最终质量验收。

- 用户批准单 Agent、LangChain 接入、LangGraph 编排、qwen-plus、Windows 本机及更多故障类型。
- checkpoint 不自动恢复旧执行；本次联调累计预算按 10 元执行，真实费用以供应商账单为准。
- 用户批准 HTTP API + PostgreSQL + UI 测试边界以及以下依赖图：
  - [01 首轮真实分析](issues/01-first-real-analysis.md)：resolved，真实百炼+模拟HTTP+真实API/PG与UI已验证，真实生产来源不在子规格范围。
  - [02 多轮只读查询](issues/02-bounded-readonly-tools.md)：resolved，两轮补查正常结束、持续补查达上限、去重和错误边界已验证。
  - [03 多类型分析与核验](issues/03-analysis-and-reverification.md)：resolved，观测方向与模型分类分别追溯；引用失效和人工恢复后再次失效均待重新核验；新V1/V2建议统一只读。
  - [04 人工等级确认](issues/04-human-severity-decisions.md)：resolved，Agent完成后仍须人工确认，真实页面确认与刷新持久化验证通过。
  - [05 隔离与预算验证](issues/05-isolation-and-budget.md)：resolved，三调查共享预算/未知usage保留，预算不足UI零调用、停止及实际进程重启已验证。
  - [06 真实案例验收](issues/06-real-validation.md)：resolved，固定8例原始/受控结果及语义失败保留；全量582通过/20付费跳过，审查补丁最终兼容138通过/16跳过、浏览器与类型构建通过。
- 2026-11-07：本批后续8例真实调用、用量账本与原始语义/受控结果分别保留，分类吻合6/8，原始语义未全通过。累计估算0.123742元；[后续验收](../agent-quality-followup/validation.md)。

## Fog

- 先前“未装配worker/未真实调用”的Fog是旧阶段状态，已由实际联调和本批证据解除；历史Answer保留时间含义。
- 运行/账本迁移为0018；本批仅增量JSON字段，无新增数据库迁移。
- 模型仍会自报confirmed、忽略矛盾或编造业务影响，只读边界与人工等级决策不可去掉，受控展示不能冒称模型自由语义可靠。
- 父19原始真实来源要求、A33和完整A34继续排除；账号硬限额和供应商账单不由测试schema预算替代。
