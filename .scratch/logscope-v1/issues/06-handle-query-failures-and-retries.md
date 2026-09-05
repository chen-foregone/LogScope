# 06: 处理查询故障、有限重试及部分来源缺失
Status: resolved
Type: task
Blocked by: 05

## What to build
纵向交付查询故障分类与来源状态 schema、有限重试和受控失败 API、UI 故障与覆盖提示；保存脱敏故障原因，不用查询不到证明没有异常。

## Blocked by
- 05 形成可追溯结论并结束调查

## Spec
[首版实施规格](../spec.md)：D3、D4、D8。
用户故事主要交付：US-07、US-08、US-10。
验收组主要交付：A07、A08、A10；A03 技术失败部分、A26 状态与来源变化相关部分。总时限08、真实来源18补全。

## 开工门槛
无额外未知输入，遵守05依赖。可用可控查询适配点先验证业务规则，错误与限流映射随18真实协议核对；重试工具选择可自行设计，不虚构供应商限制。

## 验收标准
- [x] 每次原始日志查询30秒超时，临时网络或超时最多额外重试2次，间隔依次2秒、5秒；重试仍属同轮。
- [x] 权限、凭据和参数错误不盲目重试；限流遵循来源限制，不绕过限制或无限重试。
- [x] UI/API 显示可理解且脱敏的故障分类与原因，错误产物及诊断无凭据原值。
- [x] 必要查询持续失败且无法继续时轮次为失败并保留已保存证据；正常结束但证据不足仍为已完成／未确定根因。
- [x] 部分来源不可用但仍能排查时继续，展示缺失来源及覆盖不完整，不因缺失排除原因或断言无异常。
- [x] 来源故障、轮次状态与已有证据重启后可读，整体跟最新轮；业务变化按契约更新最后更新时间，读取不更新。
- [x] UI 可区分正在查询、受限等待、部分缺失和最终失败，不能显示样例成功覆盖率；十分钟总预算由08贯通。

## 测试与完成证据
HTTP API + 真实 PostgreSQL，配可控时钟/调度和外部查询端观察实际请求及时间间隔，覆盖三次上限、错误分类、限流、必要来源失败与部分来源继续。提供响应、时间轨迹、持久故障和UI证据，不断言私有重试函数次数；真实协议由18证明。完成包含 schema/API/UI 和测试。

## Comments

2026-11-02 用户implement授权。开工核实master 1089a9b5165b29968b3ecdf6a19696bcf8a51990、工作区干净，先claimed；读取AGENTS及指向规范、spec/GLOSSARY、现有执行/证据/结论/迁移/测试/UI。首次记忆搜索命中其他工作区日志，后续已直接读取本工程.workbuddy/memory/2026-11-02.md完整历史并以本工程日志为准。无可调用Agent工具，直接实施与自审，不宣称独立子代理审查。仅06及必要源码/迁移/测试/UI、本单Answer和工程当日日志；不commit/push、不改其他工单。

## Answer

### 受控API与来源能力契约

- 新增隐藏于公开OpenAPI的`POST /api/executor/investigations/{investigation_id}/attempts/{attempt_id}/executions/{execution_id}/queries`。独立`Authorization: Bearer EXECUTOR_CREDENTIAL`及UUID `Idempotency-Key`必需；无/错/未配置凭证401，执行归属不匹配、valid=false或非调查中403。普通用户无查询错误/终态编辑入口，UI不持有执行凭证。来源与起止时间严格来自启动snapshot；拒任何请求体、URL、来源覆盖、客户端故障分类和query参数422，不提供任意网络请求或SSRF入口。
- `create_app(query_sources={source_id: adapter}, query_schedule=..., clock=...)`只允许服务端代码注入受信能力，默认空映射、无真实连接。适配器仅异步`query(source_id, starts_at, ends_at)`，同步函数在调用前拒绝。输出仅`None`表示受控查询操作成功，不能返回日志、自由message或任意结果；非None记adapter_error。本单只持久化真实已运行的查询状态，不虚构证据或扫描数量。证据收集仍调用03现有受控evidence入口，严格脱敏/范围/身份/去重，不旁路03。
- 本单保守必要性策略：本轮snapshot全部来源作为可替代受控查询能力；至少一个来源查询成功（含零日志）即可继续；全部配置来源均不可用才技术失败。不是empty=失败，不是一个坏=全失败，不从证据不足判失败。此策略不声称某供应商来源必备性或任意复杂分析仍可继续；18/19确定来源能力/必要性后须扩契约及验收，不能客户端声明“必要/可忽略”绕过判断。
- `queries`返回持久查询历史，来源为03相同调查HMAC `来源代号-*`，不回显真实来源标识。`query_coverage.complete`只表示最新受控查询已成功查询所有snapshot来源，不是证据完整率；未发起/在途/部分失败均false。notice明确查询成功/零日志不表示没有异常，不能从缺失排除原因；missing_sources给安全来源代号。旧轮/旧证据/结论历史不改写。

### 超时、重试与限流

- 每次原始查询实际受30秒deadline调度：创建异步task，`asyncio.wait(timeout=30)`到期cancel，await来源协程真正退出；即使吞取消返回也丢弃越界结果并记timeout。没有把传timeout参数冒充超时执行，没有不可中断线程后抢先释放名额。适配器必须非阻塞、合作式取消且不遗留子任务；拒同步函数不能证明任意恶意async代码不阻塞事件循环，真实连接器仍须18协议验收。取消清理不退出就保持查询在途/调查中，绝不提前宣告退出。
- 网络network或timeout最多额外2次，等待严格从前次调用退出后开始2秒、5秒，同轮最多3次。受控外部调度观察网络请求起点0/2/7；30秒超时请求起点0/32/67、最后97秒，证明5秒不是从调用开始算。生产等待`asyncio.sleep`单次调度，无sleep重试轮询。
- 安全原因枚举network、timeout、permission、credentials、parameters、rate_limited、not_configured、adapter_error；异常原message、URL及凭据不入SQL/响应/诊断。权限/凭据/参数/能力错误不重试。未配置或同步拒绝实际调用次数0；其余次数来自服务端实际await，不接收客户端统计。每次故障记录attempt、固定30秒查询边界、实际安排wait_seconds、合法retry_after_seconds，不保存原异常。
- 限流只接受受信来源明确给出的有限正数秒Retry-After；小于等于30秒可按来源值等待，仍最多3次。超过30秒不提前重试，保留恢复UTC时间；无/非法/0/NaN/字符串Retry-After不猜等待、不自动重试，保守封闭等待来源协议恢复。本单未提供用户解除闸门接口；未知恢复须后续受审来源能力处理。30秒是单次查询边界，不把合法4秒Retry-After伪写成2/5秒，也不把长Retry-After算作30秒调用超时。
- 额外持久来源闸门按来源SHA256配置标识隔离，同来源跨调查/进程串行，避免新幂等键/其他调查绕限流；原来源配置契约禁止凭据URL，不存原秘密。持久retry_at用UTC clock，运行deadline和wait用事件循环monotonic职责；clock只控制业务时间，等待以实际失败退出后起算。来源未知恢复不通过重启/新键清除。闸门不跨真实I/O持数据库锁；崩溃/事务失败保守在途，由10后续恢复协议解决，不假装已结束。

### 事务、结束隔离、历史与UI

- Alembic新增`0007_controlled_queries.py`，从0006创建controlled_queries和query_source_gates，不改旧迁移。查询记录关联attempt/execution/request_key，唯一(attempt_id,request_key)，部分唯一active attempt；来源闸门持久query_id/blocked/retry_at。短事务按调查→轮次执行→排序来源闸门接纳；提交后才I/O，每次进度短事务重新验证执行有效性；结束事务原子清闸门、保存查询终态、必要来源全不可用时失败/ended_at/end_reason、valid=false、调查更新时间及ended+30天保留期。写失败整笔回滚，无假终态。
- 同键在途202读取已有状态、结束但执行仍有效200读取原结果；新键在途409。已结束/失效执行连旧查询键也403，不再写入；查询导致失败后通过公共GET查历史，不重新授权执行。多独立API实例并发同查询只1次真实来源调用，另一同键202/新键409，退出后有效轮同键200，不新增查询或更新时间。
- 05正常结束事务增加在途查询检查409，查询已接纳不能在外部await尚未退出时正常结束；若结束先提交，query身份检查403。继续沿调查行锁顺序与valid隔离，不实现07取消、08总10分钟或10完整失联协议。05结束请求自身幂等只读重放不改，正常结束证据不足仍已完成/未确定根因。
- 技术失败保留已有证据与全部结论版本，result=null不冒充正常结果，end_reason=necessary_sources_unavailable；ended_at与保留期+30天原子落定，迟到证据/结论/query拒绝。事务异常503固定脱敏提示；接纳后进度/终态事务失败会保守留下running查询并阻止并发/结束，不虚报完成或自动重新dispatch，恢复留10。
- UI显示正在查询/受限等待/来源缺失/成功、实际次数、安全原因、每次等待及来源Retry-After、缺失代号、覆盖不完整和不代表无异常；失败结束不再误显“正常排查结束”或“尚未结束”。不展示样例覆盖率、自由状态编辑、真实源/Agent成功声明。全部读取继承现有REPEATABLE READ投影，GET不刷新时间或延期。
- 部署先备份调查产物、停止旧版本写入；安全设置DATABASE_URL/PYTHONPATH=backend，运行`.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head`至0007，再启动匹配API/UI。仅隔离PG17/55433各新schema执行真实迁移，另0006→0007保留记录升级验收及既有0004/0005升级回归；开发库未碰，未drop或删除schema。

### TDD与实际命令记录

- 统一测试解释器`.venv/Scripts/python.exe`；首次误用PATH的python缺pytest，未运行业务测试，随即使用既有venv，不安装/改变依赖。每轮命令使用`PYTHONPATH=backend`，涉及网络/UI均设置`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost`。
- `-m pytest backend/tests/test_queries.py -q`：首条TypeError缺query_sources红→1 passed；缺SourceFailure红→实际网络重试2 passed；在途结束错误201红→结束保护3 passed；适配自由输出被当成功红→受限None契约4 passed；未配置原因误adapter_error红→not_configured/0调用5 passed；客户端请求体被忽略红→拒输入6 passed。
- `tests/test_ui.py::test_query_failure_is_persistent_safe_and_not_completed -q`：缺覆盖/故障提示且失败误显正常结束红→修UI后1 passed in13.85s，真实API/Vite/Chromium刷新持久。
- 定向`test_rate_limit_cannot_be_bypassed_with_new_idempotency_key`：新键绕限流201红→持久恢复期限1 passed；`test_cancellation_suppressing_adapter_cannot_claim_success_after_deadline`吞取消被当成功红→明确deadline丢弃越界1 passed；`test_sync_adapter_is_rejected_before_it_can_block_deadline`同步调用已执行红→调用前拒绝1 passed；`test_rate_limit_is_shared_across_investigations`其他调查绕限流201红→来源持久闸门及新键两例2 passed。
- 回归扩展不是虚称每例先红：权限/凭据/参数9种限流组合，真实30秒await/清理阻塞观察，受控30秒×3及2/5等待，多来源成功+失败与证据代号一致/仍可正常未确定完成，错误无原文，真实PG四阶段除零回滚，失败保留证据/结论/结束+30d，独立API实例并发幂等，0006升级与重启持久。业务事实全部通过HTTP API断言；SQL只安排旧schema和事务故障，不查表断言业务事实。来源/clock/wait是外部注入能力，不patch私有重试函数。
- 中间完整命令`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest backend/tests tests -q`：任务DtiIda退出0，139 passed in244.20s；补升级/限流等后任务EQh4Yu退出0，142 passed in262.23s。不是最终计数，最后完整结果待下段收集填写。

### 最终完整验收及自审

- 最终命令`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest backend/tests tests -q`，后台任务aZHtbG实际收集退出0：**143 passed in267.15s**，无失败/跳过。原113项完整回归，加29项查询API/PG和1项真实UI；最后独立实例并发补强包含在本轮，不混用142项结果。
- 最终`npm run build && git diff --check`任务vLKvab退出0：vue-tsc -b类型检查及Vite成功，3294模块、2.34s、JS1000.47kB/gzip322.55kB，仅既有>500kB chunk警告和LF/CRLF提示，无无关拆包修改。文档追加后再次diffcheck。
- UI fixture自动真实迁移隔离schema后启动127.0.0.1 API8000/Vite5173/Chromium，finally terminate/wait收尾、不detach；所有测试schema保留、既有PG容器保持，未碰开发库。最终独立PowerShell Get-NetTCPConnection监听8000/5173=0，Get-CimInstance限python/pythonw/node的Uvicorn/Vite残留=0，退出0。
- 自审范围为`git diff 1089a9b`及全部新增queries.py/0007/test_queries.py，不用空的1089a9b...HEAD冒称审查已提交变化。Standards轴未发现需阻断规范违反；Spec轴逐项核对实际查询、30秒取消退出、有限重试/限流、部分缺失、脱敏、证据历史、事务/并发及05结束兼容。修复过的新键/跨调查绕限流与吞取消等真实红灯如上；无可调用子代理，未冒称独立并行审查。终态写失败保守running、未知Retry-After保守阻断、受信async非阻塞要求均显式保留限制，不伪装完整10/18。
- 七项验收在上述受控支持契约内实际通过后才resolved。HEAD仍master 1089a9b，无commit/push；只必要源码/迁移/测试/UI、06与工程当日日志，不改其他工单、不新增报告/ADR/地图。

### 范围限制与交接

- 未连接真实日志协议/Agent，受控查询只交付真实执行的状态，收集证据沿03入口；不声称真实供应商错误映射、任意异步代码沙箱或模型质量已验证。来源必要性为明确可替代保守策略，零日志不意味着无异常，查询覆盖不意味着证据完整。
- 未实现07用户取消、08单轮10min及完整终态优先裁决、10失联恢复、18真实来源、19 Agent、09重开、11容量或其他工单。下一单07须在06在途持久状态/合作式取消上设计停止确认与迟到隔离，08/10不得把本单保守running状态当可自动重开。18须验证只读协议、限流作用域与恢复/Retry-After语义、非阻塞协程取消及03严格脱敏；19仍受证据与结论契约。
- 未提交/推送，交主代理独立审查后提交；无新增报告、ADR或地图，其他工单未修改。

### 2026-11-02 两项 Spec P2 实际修复与最终复验

- 先Read当前06、queries.py、attempts.py和test_queries.py确认候选。移除同轮历史限流整批409；持久来源闸门仍跨调查/独立API实例生效，但逐来源判定：在途/仍受限来源记录`skipped`、安全`inflight`/`rate_limited`原因、attempts=0、failures=[]，不调用适配器、不占用或释放他人的闸门；健康来源继续实际执行。未知Retry-After仍不猜恢复、不解除该来源限制，但不永久阻断同批健康源。保存的partial查询只代表真实接纳过至少一个可执行来源，不宣称全源成功。
- 所有来源都被闸门阻止时返回409结构化`detail.code=sources_blocked`，只含安全来源代号、原因、可用时的`retry_not_before`和固定说明；未知时间明确等待退出或来源确认恢复。不新建查询历史、不更新调查时间、不自动失败、不后台排队。仍保留同轮正在执行查询的新键409/同键202保护。混合实际失败与skip不会以缺失自行判定必要查询全失败，完整退出/恢复策略仍留后续工单。
- 覆盖投影以本轮snapshot全部来源为基准，空历史也逐个列出缺失安全代号，后续pending/querying/unavailable/skipped均算缺失；只有本次全部来源成功才complete。新启动同事务创建调查随机脱敏密钥，继续使用03来源HMAC，读取不写密钥/更新时间；新增迁移0008为缺密钥旧调查补随机密钥，旧密钥/证据不改，不依赖旧历史轮必须存在执行身份。正常已完成但未查询仍complete=false并列出全部缺失，不把证据不足/缺失当无异常或技术失败。
- UI补`skipped`和`inflight`安全中文状态；真实Chromium覆盖查询前缺失、未扫描正常完成、混合查询成功+限流、再次查询跳过与刷新持久。测试夹具在临时目录配置独立混合服务，真实Uvicorn注入受控来源，不改生产服务配置、不伪造响应。
- 红绿记录：限流60秒/无Retry-After原整批409测试改为partial契约，任务2TfZnh **2 failed/28 deselected in4.02s** → KSVtdg **2 passed/28 deselected in3.78s**；查询前缺失列表任务C0kf0p **1 failed/29 deselected in2.72s**（missing=[]）→查询回归绿。混合UI任务beWCh4 **1 failed/1 passed/11 deselected in25.19s**（skip文案为空）→补UI后iDcKcH **2 passed/11 deselected in21.30s**。
- 如实保留中间失败：3nRxOS为32passed/1failed，UI使用不存在文案，修正为既有“尚未查询所有来源”；h8X1Ie为7passed/1failed，混合UI启动请求5秒客户端ReadTimeout，新增该例显式15秒客户端期限，不改生产deadline/重试。完整首跑mniSkE为**150 passed/1 failed in299.82s**：旧0005升级历史轮缺执行身份触发覆盖回退异常；新增0008补随机密钥、移除执行身份回退，原升级夹具不改，定向BVvcHZ **1 passed/63 deselected in3.97s**。
- 最终完整命令`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，任务pmCina实际收集退出0：**151 passed in286.50s，0失败/跳过**。相对原143项新增8项（查询API参数化/跨调查/全部阻塞/未扫描共6项，真实UI2项）；既有真实30秒退出、2/5重试、幂等、PG事务故障、升级/重启完整回归均执行。
- 真实隔离PostgreSQL **17.11/127.0.0.1:55433**，每例新schema真实迁移head0008，旧0004/0005/0006升级保留记录。未触开发库、未删除schema，PG服务保留；UI/API由fixture finally terminate/wait收尾。未扩大取消、10min总预算或失联恢复，不commit/push、不改其他工单。
- 最终独立PowerShell实测8000/5173监听=0、python/pythonw/node的Uvicorn/Vite残留=0，退出0；文档追加后`git diff --check`退出0，HEAD仍1089a9b，无commit/push。
- 构建任务tbvtF5退出0：`npm run build && git diff --check`，vue-tsc/Vite通过，3294模块、1.96s、JS1000.57kB/gzip322.61kB，仅既有大chunk与LF/CRLF提示。文档追加后再次diffcheck及独立服务收尾核验。部署迁移目标由旧Answer的0007更新为**head0008**；0008只补旧缺失密钥，降级不删除可能已被引用的脱敏密钥。

### 2026-11-02 最终 Standards 独立复核与真实全套复跑

- 重新读取AGENTS及指向的三份工程规范、GLOSSARY、06/spec、README相关规则，核对`git diff 1089a9b`全部8个tracked差异及4个untracked（queries.py、0007、0008、test_queries.py）。HEAD仍为1089a9b，`1089a9b...HEAD`为空，未以空提交差异替代工作区审查。未发现额外CODING_STANDARDS/CONTRIBUTING/ADR。当前无可调用Agent工具，由本轮直接独立复核，不冒称并行子代理。
- Standards结论：0项新增核实阻断、0项需修复项目规范违反。命名/重复/FeatureEnvy/DataClumps/PrimitiveObsession/RepeatedSwitch/ShotgunSurgery/DivergentChange/SpeculativeGenerality/MessageChains/MiddleMan/RefusedBequest均仅作启发，工程规则优先、跳过工具强制项。非阻断观察：查询模块集中接纳、重试、进度和结束职责，后续协议演进可能产生DivergentChange；查询/证据来源HMAC形状与短事务身份检查存在有限重复，但当前同域规则明确且测试验证一致，不为此扩大共享抽象或跨模块重构。UUID、受限状态字符串、SQLAlchemy DSL链及QuerySchedule外部调度适配点不构成必须包装/多态/删委托的依据；未发现其他有实际影响的异味。
- 重点核对QuerySchedule真实deadline cancel后await退出、吞取消不算成功与清理未退不提前结束；限流/在途逐来源skip、健康继续且不释放他人闸门、全阻409不写历史；snapshot全来源未扫描missing与03一致安全代号、0008补旧密钥及GET不写；调查行锁顺序、幂等/独立实例并发、短事务不跨I/O、PG故障原子回滚；技术失败result=null并保留证据/结论历史、valid=false及正常未确定完成分离；输入/异常/SQL/公共投影/DOM隐私。本轮未核实新增P1/P2，不修改业务或测试、不虚构新增red/green。07/08/10/18/19及受信异步合作式取消限制保持原交接边界。
- 实测既有隔离容器logscope-01-test-pg17为PG **17.11**、127.0.0.1:55433。完整命令`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost TEST_DATABASE_URL='postgresql+psycopg://logscope_test:local_test_only@127.0.0.1:55433/logscope_test' PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，任务UJQMzs收集完成：**151 passed in285.78s，0失败/跳过**。真实PG隔离schema迁移head0008、旧升级、事务/并发/重启、真实30秒清理退出和Chromium UI均实际执行；主层HTTP TestClient，不混称全部网络TCP。UI由tests/conftest自动迁移新schema并管理真实API/Vite，未手动复用未知服务。
- 构建与差异任务q2mpmg：`npm run build && git diff --check`完成成功，vue-tsc -b及Vite通过，3294模块、2.54s、JS1000.57kB/gzip322.61kB；仅既有>500kB chunk及LF/CRLF提示，无无关拆包。全测后独立PowerShell实测8000/5173监听=0、python/pythonw/node的Uvicorn/Vite残留=0，退出0；fixture finally terminate/wait，保留测试schema及原PG容器，不触开发DB、不drop。仅追加本Answer和工程既有当日记忆，无额外报告/地图/ADR，无commit/push；文档追加后再次diffcheck。
