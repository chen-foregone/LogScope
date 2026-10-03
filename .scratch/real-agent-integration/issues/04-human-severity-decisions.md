# 04: 贯通业务影响建议与人工等级确认
Status: ready-for-agent
Type: task
Blocked by: 01

## Parent

[19](../../logscope-v1/issues/19-connect-real-agent-and-analysis-loop.md)；[规格](../spec.md)。

## What to build

用户查看真实模型给出的业务影响依据和严重程度建议，通过现有人工流程确认或自行评估，人工等级不被自动覆盖。

## Acceptance criteria

- [ ] 模型依据通过现有业务影响规则与严格模型校验，不将可信度、日志数量或优先级当作严重程度。
- [ ] 人工决定优先；新建议需显式确认，版本过时或执行失效时拒绝写入／确认。
- [ ] 幂等重试不重复创建事件；并发人工决定不被模型产物覆盖。
- [ ] 跨轮保留人工时间及非当前验证提示，UI 展示建议依据、提交状态、错误和冲突。
- [ ] API、真实数据库和 UI 测试覆盖人工优先、过时建议、重复确认、并发和失效执行。

## Comments

业务实现暂停，待恢复授权。
