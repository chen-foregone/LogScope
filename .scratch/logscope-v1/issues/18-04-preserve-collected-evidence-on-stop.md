# 18-04：取消、超时及提交故障后保持材料一致
Status: resolved
Type: task
Blocked by: 18-02, 18-03

## Parent

[18：只读外部日志来源接入（本期模拟）](18-connect-real-readonly-log-source.md)

## What to build

采集取消、超时及数据库故障后的持久事实正确展示，完成完整回归与学习交付。

## Spec

[配套规格](../../simulated-readonly-log-source/spec.md)。2026-11-04 用户已确认四张纵向子工单及实施，既有未提交变更保留。

## Acceptance Criteria

- [x] 取消和时限后拒绝旧产物，实际HTTP退出及连接清理得到验证
- [x] 待提交证据回滚，此前已提交证据保留，重试可靠判同
- [x] 运行专项、全量、类型及浏览器验证，分清实际协议与受控故障证据
- [x] 提供实际代码学习总结及6至8道不附答案的问题

## Comments

2026-11-04：发布并接纳实施；按阻塞顺序执行，claimed 不表示已完成。

## Answer

2026-11-05 完成。取消/时限事实仍由既有受控停止协议判定；查询循环在每个记录处理前及 `controlled()` 轮询中检查持久停止/截止事实，一旦请求停止即退出 HTTP 客户端上下文并拒绝后续材料，只有实际退出后才由既有流程确认终态。实际 HTTP 退出测试使用会持续等待并观察断连的 `timeout` 模拟场景：取消返回 403、轮次已取消、证据 0 条、查询记录 `canceled`、旧执行写入 403，模拟服务端观察到断连请求数大于 0。

证据按每条材料独立短事务保存：人工 SQL 故障夹具在第二条 `attempt_evidence` 插入时报错后，接口返回 503；第一条已提交证据保留，第二条证据与纳入关系随当前事务回滚，查询材料标记 `failed` 并释放来源闸门，轮次仍调查中；随后用新请求键重试成功且可靠身份判同，证据从 1 条恢复为 2 条。HTTP 已退出后的独立清理事务若也失败，则保守保留 active 事实，不伪称退出。

验证与边界：`backend/tests/test_log_sources.py` **16 passed**、`tests/test_log_source_ui.py` **3 passed**；`npm run build`（vue-tsc + Vite）通过。首次完整 `pytest backend/tests tests` 为 **488 passed / 5 failed / 493 total**，5 项失败均是本次执行 PATH 临时缺少 `docker` 导致既有 `docker exec pg_dump` 审计用例的环境性失败（`FileNotFoundError`），非业务断言；改用含 Docker 的 PATH 复跑该 5 项 **5 passed**，合并 **493 passed**。实际 HTTP 证据来自模拟服务进程；30 秒边界为真实等待，其余长时场景复用可控调度或人工 SQL 异常夹具，报告中不冒充真实来源验收。

模块学习材料（入口、调用链、表、事务、锁、幂等、时间规则及 8 道不附答案问题）见 `docs/simulated-log-source.md`，问题首次不附答案，待用户逐题讨论。真实供应商协议、稳定身份与只读账号仍未验证，A33 不因本期模拟而完成。
