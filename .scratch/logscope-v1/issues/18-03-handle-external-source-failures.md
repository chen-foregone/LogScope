# 18-03：来源故障时展示重试与覆盖缺失
Status: resolved
Type: task
Blocked by: 18-01

## Parent

[18：只读外部日志来源接入（本期模拟）](18-connect-real-readonly-log-source.md)

## What to build

经实际HTTP模拟故障验证分类、重试、限流和部分覆盖，成功材料保留。

## Spec

[配套规格](../../simulated-readonly-log-source/spec.md)。2026-11-04 用户已确认四张纵向子工单及实施，既有未提交变更保留。

## Acceptance Criteria

- [x] 30秒单次、10分钟总预算，临时故障最多额外2次，退出后2/5秒等待
- [x] 权限凭据参数错误不重试，限流及跨调查闸门不可由新key绕过
- [x] UI显示原因和缺失，不将查询成功解释成无异常

## Comments

2026-11-04：发布并接纳实施；按阻塞顺序执行，claimed 不表示已完成。

## Answer

2026-11-05 完成。适配器把真实 HTTP 状态映射为受控分类：401→`credentials`、403→`permission`、400/422→`parameters`、429→`rate_limited`（仅接受受控整数 `Retry-After`）、502/503→`network`、504/超时→`timeout`，其他→`adapter_error`；错误体不读取、不回显。查询编排沿用既有规则：本轮固定 10 分钟，单次最多 30 秒且不超过剩余预算；临时网络/超时最多共 3 次并在失败退出后等待 2、5 秒；权限/凭据/参数不重试；429 只按明确整数值设置共享闸门恢复时间，新请求键与跨调查都不能绕过既有持久来源闸门。部分来源失败保留成功材料并返回不完整覆盖；全部来源不可用沿用既有轮次失败规则。

协议契约错误（畸形 JSON、非 `simulated-logs-v1`、多余顶层字段、超过 1MiB 或 100 条、单条材料非法/冲突）归为 `adapter_error`，不误标为查询参数错误；400/422 HTTP 状态仍归 `parameters`。逐条拒绝后继续处理响应中的后续合法记录，来源整体不显示成功。

验证（实际 HTTP 模拟）：`temporary` 前两次 503、第三次成功，实际等待 `[2,5]`，证据 2 条；`permission`、`credentials`、`parameters` 各仅 1 次且无等待；`limited` 429 映射 `rate_limited` 且 1 次；`empty` 0 条、`unidentified` 3 条；`conflict`/`invalid` 均保留坏记录之前的证据并继续纳入后续合法记录（各 2 条），来源原因 `adapter_error`。`test_actual_http_timeout_has_thirty_second_budget` 用真实 HTTP 等待 29–35 秒验证 30 秒单次边界（后续重试由可控调度提前终止，报告中不冒充完整等待）。新增适配器边界测试：拒绝非 127.0.0.1/非 HTTP/含凭据/含路径或查询的端点、畸形与超限响应、非受控协议；307 不跟随重定向；500 错误体不读取且合成敏感值不出现在异常文本。两来源一成功一 403 时证据 2 条、轮次仍调查中、`query_coverage.complete=false` 且缺失 1 个来源。浏览器用例断言非法材料场景显示“原因：适配器能力或返回不符合受控契约”和缺失来源；覆盖提示明确“查询成功不表示没有异常”。`test_queries.py` 既有重放/跨调查闸门用例继续通过，新来源沿用同一闸门。文件 19 项、UI 3 项通过。
