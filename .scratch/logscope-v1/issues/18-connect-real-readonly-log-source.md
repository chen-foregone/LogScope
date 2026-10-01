# 18: 只读外部日志来源接入（本期模拟）
Status: resolved
Type: task
Blocked by: 04, 06

## What to build
纵向交付首个真实日志来源的安全配置与来源能力 schema、协议适配/业务查询API、真实来源状态及证据UI；将固定scope和执行identity贯穿查询、错误分类、脱敏、核验材料与可靠计数。

## Blocked by
- 04 可靠判同并展示本轮证据条数
- 06 处理查询故障、有限重试及部分来源缺失

## Spec
[首版实施规格](../spec.md)：D3、D4、D8、T1–T3、未决实施选择。
用户故事主要交付：US-04 的真实来源只读部分、US-48 的真实来源集成部分；关联 US-20、US-21、US-23。
验收组主要交付：A33；A07、A08、A16、A18、A19 的真实协议补验。

## 原真实接入门槛（后续补验）
须选定实际来源与协议，明确服务映射、只读权限、受控访问环境及凭据安全注入；具备稳定记录身份能力说明、定位/核验方式、错误分类与限流契约。当前无该访问与协议契约，needs-info 待其齐备解除。不把示例服务名、供应商或凭据方案当已批准；凭据不得写入文档、记忆或报告。

## 验收标准
- [ ] 在受控真实来源以最小只读身份完成实际协议查询，验证拒绝重启、改配置、删除日志及其他环境写操作。
- [ ] 查询仅覆盖本轮固化服务和范围，绑定有效调查/轮次/执行identity，不能跨scope查询或用失效身份写入产物。
- [ ] 实际返回材料先脱敏再保存、展示、交Agent；不可靠脱敏拒绝分析并提示，真实错误输出不泄漏凭据或敏感原值。
- [ ] 保存来源、发生时间、查询条件及可核验脱敏片段/稳定定位；如来源无稳定身份则保留并提示可能重复，不虚构可靠判同。
- [ ] 用真实协议验证稳定记录ID或文件身份/偏移重复只计一次，独立同文本记录不误合并，API和UI条数一致。
- [ ] 实际查询支持30秒超时、临时故障最多额外2次及2秒/5秒等待；权限/凭据/参数错误不盲重试，限流遵循真实来源要求。
- [ ] 来源故障与覆盖缺失在UI真实可见且脱敏；必要来源持续失败与部分来源可继续正确区分，不将查询不到推断为无异常。

## 测试与完成证据
提供受控真实来源协议、只读权限、scope/identity、来源身份与脱敏错误契约测试记录，业务侧仍走 HTTP API + 真实 PostgreSQL，并展示真实证据UI。危险/故障情形可在适配层受控模拟，但必须区分哪些是实际协议验证；假日志仅限可控适配验收，不能替代A33。完成含配置/schema/API/UI及真实集成证据。

## Comments

### 2026-11-04 范围调整与实施授权

用户确认本期使用独立运行的模拟外部HTTP服务，替代当前交付对真实供应商的依赖，并批准实施。以上真实来源验收条目保留为后续补验，不作为本期阻塞，也不能以模拟结果勾选A33。当前交付以[模拟接入配套规格](../../simulated-readonly-log-source/spec.md)及以下子工单为准。

- [18-01：接通模拟外部来源并展示首条脱敏证据](18-01-collect-first-simulated-evidence.md)，Blocked by: 04, 06。
- [18-02：重复采集保持可靠证据条数](18-02-deduplicate-collected-evidence.md)，Blocked by: 18-01。
- [18-03：来源故障时展示重试与覆盖缺失](18-03-handle-external-source-failures.md)，Blocked by: 18-01。
- [18-04：取消、超时及提交故障后保持材料一致](18-04-preserve-collected-evidence-on-stop.md)，Blocked by: 18-02, 18-03。

本期完成条件：四张子工单完成，模拟HTTP完整链路与安全回归通过、UI明确标识、提供运行说明和学习材料；真实供应商协议和账号权限仍未证明。

## Answer

2026-11-05 完成本期模拟接入范围，四张子工单均已解决并附证据。实现包括：独立回环 HTTP 模拟服务 `backend/app/mock_logs.py`（合成数据、只读 GET、启动参数选择 normal/empty/unidentified/conflict/invalid/permission/credentials/parameters/temporary/limited/timeout，不提供业务故障注入接口）；后端适配器 `backend/app/log_sources.py`（显式回环 origin、无重定向/代理、1MiB/100 条边界、受控错误分类）；`SIMULATED_LOGS_URL` 显式配置才为服务目录来源构造适配器，默认关闭；`query_router` 复用既有查询编排、来源闸门、重试/时限/停止协议，并把响应逐条送入既有 `evidence.save_material`；查询状态持久化 `mode/protocol`，证据持久化 `source_mode=simulated`，UI 明确标识模拟来源，历史记录不因当前配置变化误标。

验证：`backend/tests/test_log_sources.py` **19 passed**、`tests/test_log_source_ui.py` **3 passed**、`npm run build`（vue-tsc + Vite）通过；完整 `pytest backend/tests tests` 为 **496 passed in 1479.44s，退出 0**（PATH 含 `docker`、连接级禁用并行查询、独立端口）。双轴复查后补强：端点仅接受显式回环 HTTP、不跟随重定向/不读错误体、1MiB/100 条与协议形状边界测试；协议/材料不合契约归 `adapter_error` 而非参数错误；单条拒绝不丢弃后续合法记录；503 清理只标记当前来源，不虚构未查询来源状态。运行说明、模块学习总结及 8 道不附答案问题见 `docs/simulated-log-source.md`。

**未完成/未证明**：以上真实来源验收条目仍未勾选，A33 未完成。真实供应商协议、稳定记录身份、只读账号权限、真实错误输出脱敏及真实限流契约均未验证；模拟服务的 POST/PUT/DELETE 405 只证明模拟协议，不证明真实账号只读授权。后续取得真实访问契约后须在本工单或新工单补验，不能以本期结果勾选 A33。
