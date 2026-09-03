# 03: 保存、展示脱敏证据及调查内一致代号
Status: resolved
Type: task
Blocked by: 02

## What to build
交付受控证据接收、脱敏持久化与详情展示的纵向切片：schema、迁移、业务 API、UI 和测试。脱敏先于保存、展示、交 Agent，并覆盖请求处理、诊断及错误输出；不保存敏感原值。建立调查内一致代号，跨轮延续由12补全。

## Blocked by
- 02 按固定范围启动唯一调查尝试

## Spec
[首版实施规格](../spec.md)：D4、D8、T2。
用户故事主要交付：US-20、US-21；US-22 的同轮与跨调查部分。关联 US-37、US-47。
验收组主要交付：A16；A17 的同轮代号、跨调查隔离和错误输出部分（跨轮12补全）；A26 证据写入部分。

## 开工门槛
02解决执行身份契约后开展受控接收。敏感检测、不可逆代号与拒绝路径算法属于授权内实现选择，无须泛化再问许可；真实来源能力在18验证，不宣称已选协议。去重由04、历史引用由12补全。

## 验收标准
- [x] 证据详情持久显示来源、日志发生时间、查询条件、脱敏片段或稳定定位及脱敏说明；缺最低追溯字段不得作为充分确认依据。
- [x] 密码、令牌、密钥及会话凭据直接屏蔽，姓名、电话、邮箱、证件替换一致代号；敏感关联标识也处理。
- [x] 同调查同身份跨日志代号一致，不同调查独立，算法不依赖额外保存原敏感值；尽量保留错误码、时间及安全关联线索。
- [x] 无法可靠脱敏的内容不保存为可分析证据、不交 Agent，UI 提示不可用且提示本身不泄漏原值。
- [x] 持久产物、API响应、UI、错误与诊断输出都无测试注入的原敏感值，异常路径没有脱敏旁路。
- [x] 只有有效调查、目标运行轮和执行身份可提交；普通用户不能绕过身份自由写证据，跨调查错误归属被拒绝。
- [x] 成功新增证据更新最后更新时间，读取不更新；刷新重启后脱敏核验材料可读，旧轮不被覆盖。

## 测试与完成证据
以 HTTP API + 真实 PostgreSQL 注入含敏感字段的受控外部材料，检查实际存储、响应、UI及错误输出，提供脱敏断言与拒绝证据；增量 UI E2E 查看核验字段。代号跨轮由12验收，真实脱敏入口由18、19补。替身不证明真实日志协议或 Agent 集成，完成须贯通 schema/API/UI。

## Comments

2026-11-02 开工：用户授权implement；master bff5bc2563434429558cbbaaf772173c09aa860c，git status干净。主验收沿用已确认HTTP API＋真实PG17隔离schema＋真实UI，逐项红绿；保留schema，不接开发库，不commit/push。03仅已保存数量、不做04去重；12/18/19/05及完整停止失联机制不提前实现。

## Answer

### 安全支持边界及技术契约

- 仅接收 `controlled-event-v1` JSON结构化事件，所有层级extra=forbid、严格字段类型；不接任意日志文本、模型输出、文件上传或通用JSON。不用几个regex宣称任意文本可靠识别。可信生产者须把原始事件转换为本契约，未经支持的自由文本/未知字段必须拒绝，真实来源适配由18验收，Agent由19验收。
- 必需format、source_id、带时区occurred_at、query（带时区starts_at/ends_at、fields）、record（event/error_code/fields）；可选locator。event仅request_failed/request_completed/dependency_failed；错误码仅ETIMEDOUT、ECONNREFUSED、EACCES、HTTP_400/401/403/404/429/500/502/503、NONE；新增事件/错误码需显式扩展受审契约，不能任意串透传。支持片段为脱敏结构化原始事件，非模型摘要；保留时间、已知错误语义与安全关联。任何材料达到该最低追溯要求仍不证明因果/根因。
- fields仅name/phone/email/document/correlation_id/record_id及password/token/key/session/authorization/cookie/secret；后七类直接`[已屏蔽]`，其余全部不可逆代号化。没有任何任意文本或URL透传字段。非空字段上限4096，拒绝控制字符及孤立代理码点；总请求流超过64KiB拒绝。时间范围必须本轮start≤query.start≤occurred<query.end≤本轮end，右边界排除。
- 来源必需匹配启动时固化的source_ids；配置本身收紧为ASCII字母数字下划线连字符1–120受控标识，拒绝URL/userinfo/query凭据配置，避免写进轮次快照。输出来源整体HMAC来源代号，不直接展示输入标识。locator无论URL路径、userinfo、query、fragment或其他值都整体HMAC定位代号，不保存任何原组成部分，不生成可点击原链接。定位代号是受控来源重取后比较的不可逆线索，不能反解或单独保证实时来源可访问；核验片段与查询范围同时保存。
- 每调查首条证据在调查行FOR UPDATE下生成独立随机256位HMAC密钥，存后端专用表；HMAC-SHA256输入为带类型域的JSON数组，避免分隔符歧义，全部摘要保留。相同类型同值在query/record及同调查多记录中一致，不同调查独立；不同字段类型不猜测为同一个自然人，不自行手机号/邮箱归一化。密钥不在浏览器/API/日志/配置样例中，数据库及备份访问权限是秘密保护边界；没有额外保存原值映射。并发串行化密钥创建，真实进程重启保持一致。跨轮业务验收/引用留12。
- `POST /api/executor/investigations/{investigation}/attempts/{attempt}/executions/{execution}/evidence`：独立Authorization Bearer EXECUTOR_CREDENTIAL，缺/错/空配置401；关联调查/轮次/执行ID/valid/调查中不符403。先锁调查再锁匹配轮次与执行行，和启动锁序一致。不存在用户自由上传/状态编辑入口，路由不进入公开OpenAPI。执行context现有capabilities保持原契约，不提前宣称真实Agent已接通。
- 成功201新增UUID证据，脱敏结果才进入SQL JSON；同事务更新updated_at及密钥，失败全部回滚。读取详情、列表、轮次均展示同一已保存材料，不更新updated_at；当前轮evidence_count仅已保存数量。重复提交201新增且计数增加，本单没有幂等/去重，不冒充04。提交连接异常503说明结果未确认、先读本轮，不鼓励盲目重传造成重复。
- 未知结构/自由文本/非法值返回固定安全422；对有效执行仅保存轮次布尔不可用提示，不存原文/动态字段名/来源/异常文本，不增加证据数、不更新调查时间。来源/范围不匹配422固定理由且不产生任何记录；无权执行连提示也不能写。UI展示固定“部分材料不可用”并注明未纳入分析。解析流中断安全503；写入任何意外异常安全503，原异常不传播、不打印，SQLAlchemy hide_parameters进一步保护参数。部署仍须关闭含请求体/Authorization/完整URL的访问追踪，不在此宣称能控制第三方代理日志。

### 红绿与实际验证

- 第一项HTTP＋真实PG：端点404红灯（1 failed）→0004/接收/投影实现后1 passed，包含保存/重建应用/同值代号/重复保存真实计数；pg_dump仅以内存检查该隔离schema无注入原值及执行凭证，不写导出文件，不用查表作为业务断言。
- 不可靠输入：缺evidence_notices红灯（1 failed）→只持久布尔安全提示后1 passed。来源透传红灯（1 failed）→整体来源代号化后通过。配置凭据URL未拒绝红灯（1 failed）→来源标识契约收紧后全套通过。
- 未预期异常未经安全处理红灯（1 failed）→边界捕获并安全503，定向含PG故障/并发/失效身份/范围/未知格式/真实进程重启合计12 passed in17.29s。PG SELECT 1/0和意外异常由测试外部DB连接事件安排，业务事实只经HTTP验证回滚，无生产测试hook。
- 真实UI先新schema迁移并启动API/Vite，缺“尚无已保存证据”红灯（1 failed）→证据卡、空状态、固定拒绝提示实现后1 passed in15.36s。覆盖API注入证据、刷新卡片来源/发生时间/查询条件/脱敏核验材料/定位/说明、拒绝提示、代号刷新一致、可见内容及隐藏DOM均无合成原值，浏览器无执行凭证。
- 测试构造修正如实记录：非法路径起初构造为query导致404，随后32hex本身是合法UUID导致403，改为明确非法UUID后安全422通过，并非两项业务漏洞。试用适配器在启动内直接设失败状态触发既有仅待处理/调查中响应约束，因此移除该越界场景，只验证真实valid=false身份；本单不扩展05结束/状态机制。
- 最终完整一次命令：`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，**38 passed in88.53s**，0失败/跳过；原26＋新增11证据HTTP/PG＋1真实Chromium UI。增强既有独立Uvicorn进程重启用例，重启前存证据、重启后读取并重新提交，同一代号/定位保持且真实计数2。
- `npm run build`（vue-tsc -b && vite build）退出0，3294模块、JS996.27kB；仅既有>500kB chunk非阻断警告，未做无关拆包。`git diff --check`退出0，只有LF/CRLF提示。自审覆盖输入字段到SQL/API/UI、来源/定位凭据旁路、认证/归属/锁序、拒绝记录和异常输出。
- 真实PG17.11容器logscope-01-test-pg17/127.0.0.1:55433；每HTTP用例独立新schema执行真实Alembic upgrade head0004；UI夹具每轮新issue03_ui_UUID schema真实迁移，随后启动127.0.0.1 API8000/Vite5173，services.example、随机仅内存EXECUTOR_CREDENTIAL。所有schema保留，无drop、未碰开发数据库。测试夹具finally terminate/wait，未detach；首次收尾进程匹配把检查命令自身纳入导致退出1，改为只查python/node后独立复核退出0，8000/5173无监听且无服务进程。PG容器保持原状态。

### 路径与限制

- 新增backend/app/evidence.py、backend/migrations/versions/0004_redacted_evidence.py、backend/tests/test_evidence.py、tests/conftest.py；修改backend/app/attempts.py、investigations.py、main.py、services.py、backend/tests/test_attempts.py、frontend/src/App.vue、style.css、tests/test_ui.py、本03和当日记忆。
- 受控输入支持范围刻意有限，不能接任意文本/未知模型输出；定位代号不反解、不证明真实来源在线；当前无Agent调用，不宣称验证了真实Agent输入。密钥作为后端DB秘密必须由部署保护，不提供公开导出接口。同一字段值精确一致，不通过姓名/电话推断同一自然人，不跨调查关联。
- 未实现04去重/可靠原始记录身份判同、05结论、12跨轮引用与验收、18真实来源、19真实Agent；07/08/10完整停止失联/终态竞争仍后续实施，本单仅按现有valid和轮次状态拒写并锁定校验，不声称已完成停止协议。11容量、15完整筛选统计、20整体发布验收不提前完成。
- HEAD保持master bff5bc2563434429558cbbaaf772173c09aa860c，无commit/push。03七项验收按上述受控结构化支持边界全部通过后resolved，待主代理审查后提交。

### 2026-11-02 最后 Standards 审查及独立真实复跑

- 审查范围为 `git diff bff5bc2 --` 的全部9个已跟踪文件及 `git ls-files --others --exclude-standard` 列出的4个新文件；基准实际解析为bff5bc2563434429558cbbaaf772173c09aa860c，HEAD无新增提交。先读AGENTS所指工单/分诊/领域规范、GLOSSARY及03，再读README/spec、完整业务差异、新文件及测试fixture；未发现ADR或额外CODING_STANDARDS/CONTRIBUTING。当前没有可调用子代理，本次由主代理直接完成Standards审查，未宣称独立并行子代理审查。
- Standards findings：0项阻断、0项需修复的规范违反；所列12类代码异味按启发式逐项判断，跳过工具可检查规则。输入白名单到脱敏SQL/API/UI、固定安全拒绝提示、事务异常回滚、调查→轮次执行锁序、来源与时间边界、密钥调查隔离及重启持久化均已核对。接收、脱敏、原子保存属于同一证据用例，少量时间格式转换与局部导入不构成当前必须提取或拆职责的依据。03明确的受控支持范围优先，不将04去重、12跨轮或18/19真实来源/Agent误列为本单阻断；无无故重构。
- 先理解 `tests/conftest.py` 自动建新UI schema、迁移、启动及finally停止API/Vite；未手动启动服务。`docker ps --filter name=logscope-01-test-pg17 --format '{{.Names}} {{.Image}} {{.Ports}}'` 与 `docker exec logscope-01-test-pg17 psql -U logscope_test -d logscope_test -tAc 'SHOW server_version'` 均退出0，实际为postgres:17-alpine、127.0.0.1:55433→5432、17.11。
- 独立完整命令：`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost TEST_DATABASE_URL='postgresql+psycopg://logscope_test:local_test_only@127.0.0.1:55433/logscope_test' PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`。后台任务90dwQ8已收集完成，退出0，**38 passed in72.75s**，0失败/跳过；后端每例独立schema真实upgrade head0004，UI真实API/Vite/Chromium，包含真实进程重启和PG故障回滚，非沿用此前88.53s结果。
- `npm run build` 后台任务NDWxgp已收集完成，退出0；vue-tsc -b通过，3294模块，Vite构建2.16s，CSS373.54kB、JS996.27kB（gzip320.79kB）；仅既有>500kB chunk非阻断警告。`git diff --check`退出0，仅LF/CRLF提示。
- 测试结束后独立PowerShell核查Get-NetTCPConnection监听8000/5173数量0，Get-CimInstance仅匹配python/pythonw/node的Uvicorn/Vite进程数量0，检查退出0。没有detach、没有删除schema、没有连接迁移开发库，PG容器保持原运行状态。本次仅追加03与工程当日日志；业务/测试/配置不改、不创建临时脚本或报告、不commit/push，03保持resolved。
