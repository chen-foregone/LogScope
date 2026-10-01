# 18-02：重复采集保持可靠证据条数
Status: resolved
Type: task
Blocked by: 18-01

## Parent

[18：只读外部日志来源接入（本期模拟）](18-connect-real-readonly-log-source.md)

## What to build

重复、无身份和冲突材料经共用证据规则保存，API和UI可靠计数。

## Spec

[配套规格](../../simulated-readonly-log-source/spec.md)。2026-11-04 用户已确认四张纵向子工单及实施，既有未提交变更保留。

## Acceptance Criteria

- [x] 同身份同内容只计一次，不同ID同文本分别保存
- [x] 身份冲突保留旧证据，无身份逐条保留并提示
- [x] 未知字段、自由文本和越界材料拒绝，不保存敏感原文

## Comments

2026-11-04：发布并接纳实施；按阻塞顺序执行，claimed 不表示已完成。

## Answer

2026-11-05 完成。采集路径调用与既有证据入口完全相同的 `evidence.save_material`，没有复制第二套校验、脱敏、判同或纳入算法。`identity_key` 对受控来源与被调查记录的原始身份做调查内 HMAC，`content_key` 对未脱敏原始事件内容做 HMAC；同身份同内容只保存并纳入一次，不同 ID 即使文本相同也各自保存。无身份材料 `identity_status=unavailable`，逐条保留并追加“可能重复”提示；不按文本哈希或时间近似伪造判同。

同身份内容变化返回冲突并保留旧证据；未知字段、自由文本、越界材料固定拒绝并提交 `evidence_unavailable` 布尔提示，原始值不进入 SQL、异常文本或响应。单条拒绝不会撤销此前已提交材料，也不会丢弃响应中后续合法记录；来源整体标记不可用、不宣布成功。模拟与既有非模拟材料若碰撞同一可靠身份返回 409，避免判重路径吞掉模拟来源标签。

验证：`backend/tests/test_log_sources.py` 覆盖正常响应 3 条（同 ID 两条折叠为 1，另 1 条不同 ID 同文本独立保存）最终 2 条、重复新键重采仍 2 条、无身份 3 条并提示、冲突场景保留 1 条旧证据并纳入后续 1 条合法记录（共 2 条）、非法场景拒绝坏记录后仍保存后续 1 条合法记录（共 2 条），均不含 `synthetic-secret`；UI 用例断言 API 与页面证据条数一致。该文件 19 项全部通过。
