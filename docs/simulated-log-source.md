# 模拟外部日志来源运行与学习指南

本期工单18接入独立HTTP模拟服务，不是生产日志平台，不证明真实账号的只读权限，也不满足真实来源验收A33。默认不启用；不要使用真实凭据或个人数据。

## 启动

使用项目虚拟环境并安装后端依赖。先按现有项目说明启动PostgreSQL并迁移业务schema。在项目根目录的两个PowerShell终端分别运行：

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m app.mock_logs --port 8091 --scenario normal
```

```powershell
$env:PYTHONPATH='backend'
$env:SIMULATED_LOGS_URL='http://127.0.0.1:8091'
$env:SERVICES_FILE='backend/services.example.json'
# DATABASE_URL及EXECUTOR_CREDENTIAL使用现有安全运行配置，不写入报告。
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

按项目说明启动前端。创建调查，选择服务，将日志范围设置为2026-11-01T00:00:00Z至2026-11-01T01:00:00Z。时间范围是历史日志范围，不是轮次10分钟执行预算。通过现有受控执行接口取得执行ID并携带运行时凭证和新的Idempotency-Key调用该执行的queries接口。浏览器不持有此凭证，也不新增自动查询按钮；详情刷新后可见模拟标签和两条脱敏证据。

清除SIMULATED_LOGS_URL后默认不接来源。配置只允许显式127.0.0.1 HTTP origin及端口，不跟随重定向，不使用环境代理。配置存在时，服务目录中的来源映射到该模拟服务；不要把此开发配置用于生产。

## 协议与演示场景

只读GET /v1/logs，参数为受控source_id及带时区starts_at、ends_at，采用半开时间范围。成功返回simulated-logs-v1协议及items，每条为现有受控Material格式。客户端最多接收1MiB解码响应及100条记录，超限拒绝，不截断也不宣称完整。没有分页、生产查询表达式或任意文本清洗能力。

模拟数据固定发生在2026-11-01T00:15:00Z，三个返回项中两个为同一ID重复，另一项ID不同但内容相同，因此正常纳入两条。所有敏感字段都是合成值。

启动时--scenario可选normal、empty、unidentified、conflict、invalid、permission、credentials、parameters、temporary、limited、timeout。故障控制只在启动参数中，不暴露业务修改接口。temporary在该模拟进程前两次请求返回503；limited返回429和60秒Retry-After；timeout等待35秒。切换场景重启模拟服务，必要时新建轮次；已有失败轮次不能继续写入。模拟端拒绝POST/PUT/DELETE不等于真实环境账号经过只读授权验证。

## 模块学习

- 入口：create_app按显式配置构造SimulatedLogSource；query_router按既有执行身份接纳查询。新查询状态材料持久化mode=simulated、protocol=simulated-logs-v1，证据材料保存source_mode=simulated，历史引用也保留标签，重启后标签不依赖当前配置。
- 调用链：queries的受控查询与30秒调度 → SimulatedLogSource实际HTTP读取 → CollectedLogs → 逐条Material校验 → save_material共用原有证据事务 → 调查详情 → App.vue。旧适配器返回None仍兼容，任意数组不被默认为成功。
- 关键表：investigations → attempts → controlled_executions；controlled_queries记录幂等及活动事实，query_source_gates跨调查互斥/限流；evidence保存脱敏材料，attempt_evidence负责本轮纳入，investigation_redaction_keys提供调查内稳定代号。
- 锁：先调查再轮次/执行；来源闸门按键排序，网络I/O不持业务事务锁。模拟请求被取消会退出HTTP客户端上下文；客户端退出不保证远端已经停止处理，故只接受没有写副作用的协议。
- 事务：接纳、进度、每条证据及最终状态分别提交。第一条提交后第二条失败，第一条不回滚；第二条证据及其纳入关系若遇数据库错误一起回滚。查询最终事务失败时，之前active查询记录可能仍存在，阻断新key，不能伪称已退出。采集证据写入503则在HTTP已退出后尝试独立清理事务：标记查询failed并释放来源闸门，保留已提交证据；清理也失败时仍保守保留活动事实。
- HTTP不等于数据库效果：非法材料返回422前可提交evidence_unavailable提示；截止拒绝403前可提交隔离和停止确认。503表示结果未确认，应读取已有材料，不盲目假定全部回滚。
- 状态：来源成功不等于根因确认；全部unavailable按既有规则使轮次失败，部分失败继续显示覆盖不足。非法/冲突材料归为parameters，已提交的合法材料保留，来源不显示成功。
- 时间：本轮启动固定10分钟；来源单次最多30秒且不超过剩余预算，临时错误最多额外2次，退出后等待2/5秒；权限/凭据/参数不重试；429遵循受控Retry-After整数秒值，其他形式保守不设恢复时间。不保证物理COMMIT落盘硬实时截止。
- 幂等：请求key重放不重新采集；新key重新采集后可靠身份判同。无身份记录重采会增加条数并提示可能重复；不按文本合并，也不能称其实现了可靠幂等。

## 学习问题（不附答案）

1. 正常空响应的来源状态、证据条数和根因结果有何区别？
2. evidence与attempt_evidence分别表示什么，历史引用为什么不改来源轮次？
3. 正常场景返回三个材料，两个ID相同且内容相同，第三个ID不同但内容相同，首次和新key重采分别计多少？无身份场景有何不同？
4. 有效未到期轮次提交未知自由文本字段，返回422时提示、证据和原文保存各如何变化？
5. 人工SQL故障夹具在第二条独立证据插入成功后、纳入关系插入前报错，第一条已提交，独立清理事务成功，响应503后哪些材料仍保留，新key能否重查？
6. 两个调查并发读同一来源，一个已持有在途闸门，另一个换key会不会再发HTTP？只有这个来源时会不会留下新查询历史？
7. 受控时钟在证据业务写入后跨过固定截止时间，接口拒绝时哪些待提交写入被撤销、哪些超时事实可能提交？与未知执行停止协议有何差别？
8. 实际HTTP模拟、可控时钟和人工SQL故障分别证明什么？为什么它们仍不能证明真实来源ID稳定和真实账号不能修改配置？

用户回来后一次讨论一道，先推理、再结合实际代码和测试纠偏、最后复述；问答不阻塞实施完成。

模拟模式与原有非模拟材料如碰撞同一可靠身份，返回安全冲突且不改历史，避免判重路径丢失模拟标签；应使用独立模拟来源标识。
