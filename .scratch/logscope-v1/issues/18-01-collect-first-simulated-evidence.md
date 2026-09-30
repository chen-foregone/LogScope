# 18-01：接通模拟外部来源并展示首条脱敏证据
Status: resolved
Type: task
Blocked by: 04, 06

## Parent

[18：只读外部日志来源接入（本期模拟）](18-connect-real-readonly-log-source.md)

## What to build

独立本地只读HTTP模拟服务，经固定范围及有效执行身份采集并复用现有保存事务，展示持久模拟标识、查询状态及脱敏证据。

## Spec

[配套规格](../../simulated-readonly-log-source/spec.md)。2026-11-04 用户已确认四张纵向子工单及实施，既有未提交变更保留。

## Acceptance Criteria

- [x] 默认关闭，回环来源配置由服务端控制，不接受浏览器端点或凭据
- [x] 实际HTTP来源 + 业务API + PostgreSQL验证正常及空结果
- [x] 不跨来源I/O持数据库锁；刷新和重启后材料及模拟标识保留

## Comments

2026-11-04：发布并接纳实施；按阻塞顺序执行，claimed 不表示已完成。

## Answer

2026-11-05 完成。新增独立模拟服务 `backend/app/mock_logs.py` 与适配器 `backend/app/log_sources.py`：`SIMULATED_LOGS_URL` 未设置时 `create_app` 不构造任何来源；显式设置时只为服务目录中的来源构造 `SimulatedLogSource`。适配器仅接受带显式端口的 `http://127.0.0.1:PORT`（拒绝凭据、路径、查询、片段），`trust_env=False` 且不跟随重定向；浏览器仍只能经既有执行身份接口发起查询，不能提交端点、凭据、范围或错误分类。

`query_router` 复用原受控编排：查询接纳、进度与最终状态各自短事务，HTTP 读取发生在事务外；每条材料经 `evidence.save_material` 独立短事务保存，不跨来源 I/O 持业务锁。新查询状态材料持久化 `mode=simulated`、`protocol=simulated-logs-v1`，证据材料持久化 `source_mode=simulated`；标签来自数据库材料，刷新、重启及来源配置移除后仍保留（历史证据专项已覆盖）。

验证：`backend/tests/test_log_sources.py` 16 项通过（含正常、空结果、重启后详情相等、分页场景等），`tests/test_log_source_ui.py` 3 项通过。空结果来源状态为 `succeeded`、证据 0 条、轮次仍为调查中，覆盖提示不完整，不输出“无异常”结论。所有测试使用 55433 隔离 schema 与随机端口模拟服务。
