# 09: 手动重开调查并查看逐轮历史
Status: resolved
Type: task
Blocked by: 08

## What to build
纵向交付多轮持久化 schema、手动新轮 API 与逐轮详情 UI。调查整体跟最新轮，旧轮状态、服务范围、失败原因、证据与结论不可被覆盖。新轮仍受唯一执行与只读约束。

## Blocked by
- 08 限制单轮总时限并协调终态竞争

## Spec
[首版实施规格](../spec.md)：D1、D2、D4、D5、D8。
用户故事主要交付：US-05、US-14、US-15；US-36 多轮持久化部分。
验收组主要交付：A04、A03 的最新轮投影及旧历史部分；A26 新轮更新时间、A32 多轮持久化部分、A35 重开历史部分。A25 人工等级跨轮由14补全。

## 开工门槛
无额外未知输入，遵守08依赖及其执行安全契约。多轮服务展示/筛选投影的新增契约留15，不暗改成搜索全历史；详情明确每轮服务即可。历史引用12、等级14、完整保留16分别补全。

## 验收标准
- [x] 已完成、失败、已取消均能由用户在原调查手动发起新轮；分别验证各终态，不自动重开且无累计次数上限。
- [x] 每轮启动默认最近1小时、最多24小时，用户可调整服务范围，启动后固定；旧轮未安全结束仍拒绝启动。
- [x] 并发重开最多一轮运行，失败事务不留假轮；重试查询不增加轮数。
- [x] 整体状态跟最新轮，逐轮可查看独立服务、范围、状态、结束原因、脱敏证据、结论与条数；旧终态和材料保持不变。
- [x] 新轮不自动继承历史证据或覆盖，新范围不冒充已覆盖；历史纳入需12的显式检查。
- [x] 刷新与服务重启后完整多轮历史可读，启动更新最后更新时间，浏览不更新、不延期。
- [x] schema 与UI为人工等级跨轮保留及评估时间留出正确语义；14负责人工优先、历史影响提示和实际跨轮验收，不将旧等级视为当前已验证。

## 测试与完成证据
HTTP API + 真实 PostgreSQL 从三种终态逐一重开，反复多轮并验证旧记录不变、并发与刷新重启。提供逐轮响应、持久化证据及真实API UI E2E 取消后新轮看历史。替身只用于安排外部终态条件，不直接拼造业务成功状态；20汇总迁移升级。完成包含 schema/API/UI 与历史测试。

## Comments

2026-11-03 implement09 明确授权，基点master `707f4a3`，初始工作区干净。已读AGENTS、docs/agents、GLOSSARY、README、spec、08/09及现有启动、执行、结论、取消、查询和UI测试；无ADR。加载tdd，采用已批准HTTP业务API＋真实PG17、可控外部clock/调度/来源及少量真实Chromium边界，一项red→green再下一项。当前无可调用Agent工具，不冒称子代理审查。

明示实施契约：
1. 沿现有POST attempts：新请求键才可手动新轮；同调查锁串行化，先查所有历史请求键，同键同内容200返回当前调查投影且绝不创建/复活轮次，同键异内容409。旧键即使原轮已终态、新轮也已终态仍不得重开。不同新键并发最多一个201，其余409；接纳失败整笔回滚，不消耗轮次序号。
2. 调查锁内检查全部旧轮：任何调查中轮次或active旧查询均拒绝；不以到时限、页面断连或进程重启推测安全退出。保持08期限及执行写入隔离，所有产物仍绑定调查/轮次/执行，旧完成结论仅允许原内容只读重放。
3. 新增0011，不修改旧迁移。每调查持久attempt_number正整数唯一，锁内max+1；按此顺序投影latest，不按同值clock或随机UUID。升级按既有created_at/id顺序确定旧轮序号，保持旧材料和状态；历史无法推导真实同时序列，不虚称恢复不存在的事实。
4. 新轮默认服务器最近1h、显式带时区范围<=24h、固定范围及启动+10min期限。证据/结论/查询覆盖从空开始，不自动引用历史；历史完整只读。整体状态与证据条数只跟最新轮。详情逐轮明确服务；保留既有列表服务投影，不扩大全历史搜索，15再统一服务筛选。
5. 新轮启动更新updated并清空运行中保留截止；终态沿既有最新轮结束+30d。浏览仅读不更新或延期。schema预留严重程度来源/评估时间，未评估保持未评估；不开放14人工调整或将旧等级当当前验证。
6. UI展示逐轮导航、独立材料及手动新轮表单；证据锚点限定调查/轮次，异步导航不串调查。完成保留claimed，仅追加本单Answer，不resolved、不commit/push、不改地图/日志；不操作开发库、不drop测试schema，不实现后续10/11/12/14/15/16/18/19。

## Answer

### 实现及协议

- 基点master `707f4a3`。扩展现有 `POST /api/investigations/{id}/attempts`，不增加自由终态入口：已完成、查询失败、确认取消后均可由用户使用新键发起新轮，无自动新轮或累计次数检查。沿调查行锁、唯一运行索引与事务内执行接纳；任何调查中旧轮（含stopping/timeout_stopping）或active历史query均409。启动失败回滚整个新轮、执行及updated/retention，不消耗编号。
- 幂等在运行检查之前查询全部历史键：同键同内容200返回当前调查投影，不重复启动；同键异内容409。旧键在其后多轮结束后仍只能重放，不会偷偷重开。02丢响应身份与UI pendingStart沿用；旧完成结论只允许原轮原内容只读重放，不恢复执行身份，不更新当前轮。证据、查询、结论继续绑定调查/轮次/执行，旧身份替换成新轮路径仍403。
- 新增必要迁移 `0011_attempt_history.py`，旧迁移不改。持久 `attempt_number` 为每调查正整数唯一，锁内取最大值+1；读取按其升序，最新轮及整体状态/条数不再由同值clock、随机UUID决定。旧数据升级按原created_at/id确定性回填序号；只能延续旧版既有排序，不能恢复旧数据从未保存的真实同刻启动先后。部署须先停止旧写入、备份产物并以正确DATABASE_URL/PYTHONPATH运行 `.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head` 到0011，再启动配套API/UI；旧版INSERT不含编号，不能新旧混跑。本次只迁移隔离测试schema，未操作开发库。
- 每轮固定服务/source_ids与日志范围，默认用该次服务端clock取最近1h，显式带时区范围最大24h，deadline仍同次启动+10min。新轮证据/结论/查询全空、覆盖不完整，不能用历史证据ID直接支撑新结论；未来12显式引用才能纳入。调查最新条数不累计旧轮；详情保留旧轮服务、范围、终态/原因、脱敏证据、结论版本与查询故障，GET逐轮及整体时间均统一UTC Z。
- schema增加调查级severity、severity_source、severity_assessed_at，约束未评估时来源/时间为空，实际高中低必须有human/agent来源及评估时间；启动不清空字段。API读持久元数据，UI未评估明确不代表低风险，有历史评估则显示来源、评估时间及不代表当前影响已验证。没有人工调整、Agent建议/确认或等级历史入口，不冒称交付14跨轮人工优先验收。
- UI所有轮次独立section，持久编号、当前/历史只读标签、逐轮导航与每轮材料；终态出现手动新轮表单，服务与范围可重新选择。证据锚点绑定attempt_id/evidence_id，点击只滚动、不覆盖调查hash，不跳到新轮；沿既有readSequence/route guard防异步导航串调查。停止未确认不出现新轮表单，API仍最终校验active旧query。
- 启动更新updated、运行中retention为空；结束沿现有最新轮结束+30d。GET浏览/刷新不更新或延期。列表service保持原有最新轮字段投影，搜索仍仅标题/编号，不暗改为搜索全历史；15统一筛选、16完整保留、10失联、11全局容量、12引用、14等级、18/19真实连接均未扩入。

### 逐项红绿记录与补强

统一pytest前缀：`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest`。数据库为现有PG17容器logscope-01-test-pg17、127.0.0.1:55433/logscope_test；fixture每例独立schema且不drop。HTTP主层TestClient/ASGI＋真实PG；浏览器及进程重启验收才走真实TCP，不混称全部TCP。

1. 完成后新轮与历史材料：I3Eyrn **1 failed in3.56s**（原409拒绝重开）；去除单轮封锁后QAQVML仍 **1 failed in2.98s**，暴露逐轮GET `+00:00` 与整体 `Z` 格式不同；统一时间后8VRmjC **1 passed in3.00s**，旧证据/结论不变、新轮空材料、固定范围/期限和更新时间成立。
2. 同clock八轮与历史键：7jByLX **1 failed/1 passed in4.16s**（缺attempt_number）；0011及持久排序后ggyRmE **2 passed in5.47s**。八轮通过真实cancel API终态，轮数不限；每次重放全部历史键均不创建轮，同键异服务409，应用重建读取一致。
3. 升级后active旧query：bb0sds **1 failed/2 passed in7.49s**（active旧query仍201）；加入调查锁内历史query闸门，uKbksc **3 passed in7.53s**。旧schema SQL仅安排兼容性/保守遗留条件，不用SQL伪造本次正常完成。后补真实0010双历史轮升级，active=true拒绝/false可启动第3轮、旧历史不变，DtQQG5 **6 passed in15.73s**。
4. 真实必要查询临时网络故障三次后失败、新服务24h边界及旧身份隔离、并发相同/不同新键、PG接纳除零回滚属于补强非虚称逐例先红；E6Rw6o **5 passed in11.05s**。来源与调度只安排外部失败，不直接更新业务终态。失败旧轮证据/查询原因不变，查询内重试仍仅一轮，新轮不能引用历史ID，最新结束+30d与浏览只读成立。
5. 评估元数据：HKoY4o **1 failed in2.70s**（缺severity_source）；增加持久/API/UI语义后tMeK4q **6 passed/16 deselected in37.40s**（含下项UI）。当前未评估来源/时间保持null，未伪造历史评估。
6. 真Chromium取消→新轮→历史：TN3P8D **1 failed in26.62s**（缺启动新一轮按钮）；UI贯通后同tMeK4q通过。真实Uvicorn/Vite/PG，先经受控API保存脱敏证据与未结束结论，浏览器取消，再选择新服务重开；历史结论链接落本轮证据、新轮无旧错误材料，刷新历史保持、GET不改updated/retention，返回列表无串页。只测试可控输入，不称真实日志/Agent验收。
7. 更新原05拒绝新键重开的断言为09新契约，同时增加旧完成请求重放不能复活/写新轮约束；原真实服务进程重启测试扩展成完成→新轮取消→再次重启，逐轮证据/结论与旧请求不变。Zk6JOT定向 **83 passed in156.20s**，覆盖05/02及进程重启。

### 完整验收与环境诊断

- 首次完整cS6kad：`pytest backend/tests tests -q --tb=short` **214 passed/2 failed in431.52s**。两失败均原证据pg_dump审计，不是业务断言；5qVZPQ复现 **2 failed in6.19s**。真实导出诊断为PG共享内存 `No space left on device`：保留大量测试schema后pg_dump全catalog并行计划申请共享内存失败。没有drop任何schema、没有改容器全局参数；只给三个审计导出进程设置 `PGOPTIONS=-c max_parallel_workers_per_gather=0`，继续真实pg_dump与原敏感值排除断言，不跳过/屏蔽审计。uYvYhG **2 passed in9.41s**。
- 根目录YTImsi：`npm run build && git diff --check`退出0，vue-tsc/Vite成功，3294模块、Vite3.50s、JS1003.85kB/gzip323.62kB；仅既有>500kB chunk及LF/CRLF提醒，不做无关拆包。

### 最终冻结验收

- n6iUc8完整命令：`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，最终 **216 passed in481.95s (0:08:01)，退出0**，无失败、跳过或warning汇总。相对既有209新增6项后端历史测试及1项真实Chromium UI；扩展原真实进程重启、05兼容断言不另增计数。输出已全部收集。
- GMOXfX最终根目录 `npm run build && git diff --check && git status --short && git rev-parse --short HEAD`退出0；vue-tsc/Vite成功，3294模块、Vite3.50s、JS1003.85kB/gzip323.62kB。仅既有chunk、构建回调耗时及LF/CRLF提示，未扩大范围优化。
- 完整suite后PowerShell核验：8000/5173监听数 **0**，命令行同时含本项目log-scope及uvicorn/vite的python/node进程数 **0**。服务fixture finally退出，无detach；PG17测试容器及诊断schema保留。HEAD仍707f4a3，未commit/push，开发库未触碰。

### 主代理最终验收（取代前文待审查状态）

- Standards独立审查未发现明确硬违规或需报告异味；Spec审查发现新轮绝对范围未重置、丢响应刷新身份丢失，以及恢复流程的目录依赖、损坏存储、UUID大小写、合法时间格式六类P2，逐项红绿修复。最终复审无剩余阻断，另以实际前后端解析器230组边界对照0差异；不将这些内存验证冒充浏览器回归。
- 主代理最终冻结全套任务4KOkUP：**261 passed in538.53s，退出0，无失败/跳过或warning汇总**。相对209基点新增52项（6后端历史、46真实浏览器场景），既有重启测试扩展完整多轮历史。
- 主代理最终根目录WWZocJ：`npm run build && git diff --check`退出0，vue-tsc/Vite通过，3294模块，2.65s，JS1007.32kB/gzip324.92kB；仅既有大chunk及LF/CRLF提示。
- 会话存储只保留当前标签页的调查级请求UUID与表单body，关闭标签页/清站点数据后不保证未知请求恢复；不承诺跨标签页、跨设备持久幂等。已有记录无效时仍允许读详情但阻断新启动，不能不安全放弃后新开。
- 09在明示范围内resolved，更新地图及当日记忆，当前分支本地提交、不推送；开发库未迁移。人工等级仅预留元数据与展示语义，14的人工优先/等级历史不在本单验收中。

### 边界与交接

- 08期限裁决、已知协议确认退出与active保守防护沿用；本单不增加进程失联判定、不把刷新或进程重启当安全停止证明。当前受控适配器须合作式异步退出；未知执行/确认失败仍可能阻断新轮，10再恢复。
- 无可调用Agent工具，未冒称独立两轴审查。所有变更留主代理审查；本单保持claimed，不resolved、不commit/push，不改地图、日志、其他工单。新增文件仅必要0011迁移和09验收测试。

### 审查两个P2修复（2026-11-03）

- 已加载tdd。先只读代码/后端/进程，发现主代理完整pytest运行；用Wait-Process等其退出后才编辑UI和运行Chromium，没有抢占8000/5173或干预主代理测试。保留全部原未提交实现；本次仅改App.vue、tests/test_ui.py与本Answer。
- P2-1逐项红绿：新增真实Chromium同页绝对范围启动→取消→下一轮用例。首次0qWp7J为创建API 5秒连接读取超时，不计业务红灯；测试客户端改20秒后mcbmMe **1 failed/17 deselected in28.34s**，明确实际rangeMode=absolute、预期recent。仅在接纳响应确认成功后初始化下一轮范围并清空起止时间，5EiLKP **1 passed/17 deselected in23.83s**。最终实现复用restoreStart：无待确认身份才恢复默认；未确认的原内容不被重置。
- P2-2逐项红绿：新增实际API已接纳→测试进程经真实受控queries API令该轮快速失败→只在浏览器网络边界丢弃响应→SPA刷新用例；fA9xFI **1 failed/20 deselected in29.74s**，刷新后直接出现新轮表单而没有待确认流程。实现后JJoVE2 **4 passed/17 deselected in30.97s**，含P2-1及SPA刷新/F5/跨调查导航三种确认路径。
- 现在按调查ID在sessionStorage保存且仅保存`{key: UUID, body: 原始表单JSON字符串}`，先保存再发送。刷新/导航恢复同一调查的原请求，未确认时禁改服务/范围/时间，显示“启动结果待确认”及“重试原请求并确认结果”；即使GET已看到快速终态或运行中，也不默认为本次请求确认、不产生新UUID。点击确认重发完全相同key/body，后端200读取当前投影且不加轮；成功确认后清除该身份、初始化下一轮最近1小时，终态时才出现显式新轮按钮。明确404/409/422拒绝解除锁定以修正输入；网络错误、5xx或成功响应JSON无法读取继续保留同键。旧异步回调只清除匹配调查/UUID的记录，不覆写其他调查或后来的轮次。
- 补强真实网络暂扣：route.fetch先实际提交并暂扣201响应→SPA刷新→实际取消快速终态→同键200确认→显式启动第2轮→释放旧201，断言页面仍第2轮、数据库仅2轮。跨调查用例在A未确认时启动B，返回A仍原服务/绝对时间与原UUID；存储不含执行凭证。修改原丢响应测试按钮为明确确认入口，保留原200/相同响应断言；原迟到201/422及取消导航回归继续通过。ZS2CzN全部真实Chromium **22 passed in84.11s**。
- 安全与生命周期限制：sessionStorage仅同源、当前标签页会话，支持SPA导航及F5，确认或明确拒绝后删除；不做TTL自动遗忘，以免未知结果被误当新轮。关闭标签页、清除站点数据或会话被浏览器丢弃后不保证恢复；页面已明确提醒，不能宣称跨设备/跨标签页全局幂等。存储只有服务ID、用户输入时间及UUID，不含执行身份、执行凭据、证据或结论；但表单输入仍可被同源脚本读取，不是密钥保险箱。禁用/损坏sessionStorage会阻止恢复/发送并显示错误，不默默降级为新键发送；用户应保留会话完成确认。执行凭证仅存在Python测试进程，未注入浏览器。未知5xx可能持续待确认，需要服务恢复后原键重试，没有不安全的“放弃并新开”入口。
- 最终完整命令：`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，tZMyRv **221 passed in482.55s (0:08:02)，退出0**；比原216新增5个浏览器场景（默认范围1、快速终态参数化3、暂扣1）。全部输出已收集，不仅后台通知。
- ysWvFM根目录`npm run build && git diff --check && git diff --stat`退出0，vue-tsc/Vite成功，3294模块、Vite3.32s、JS1005.27kB/gzip324.10kB；仅既有大chunk、回调耗时及LF/CRLF提示。测试仍只迁移隔离PG17端口55433的新schema，不drop，不碰开发库。保持claimed，不resolved、不commit/push，不写地图日志。

### 复审3 三项P2修复（2026-11-03）

- 加载tdd，沿已授权真实Chromium UI＋真实HTTP业务API＋隔离PG17边界逐项红绿。先读代码/写测试，等待主代理ve9riy完整结束（**221 passed in416.52s，退出0**）才运行UI，未抢8000/5173端口。仅本次修改App.vue、tests/test_ui.py及本Answer，保留原两P2、后端/迁移及其他已有未提交实现。
- P2-1目录独立确认：真实API接纳201→真实取消终态→只在浏览器网络边界丢响应→刷新时目录503/空/移除orders。xwlDzg **2 failed/1 passed/22 deselected in36.65s**，503/空目录确认按钮禁用；移除单服务原已通过，不虚称红灯。确认按钮不再依赖当前目录，新启动仍校验当前目录服务；nAlFG3 **3 passed/22 deselected in26.55s**。每例原key/body重放200、仅1轮，确认后不会偷偷新轮。
- P2-2运行时校验与fail closed：外层必须恰为key/body、key为标准UUID字符串、body为字符串JSON；内部必须为对象、非空且<=80字符服务ID、无未知字段，范围要么均省略，要么合法带时区绝对时间、起止有序且<=24h。不依赖当前目录判断历史服务是否合法。明确区分getItem返回null（不存在）与已有空值/JSON null/损坏对象（无效），不做JSON.parse后as强转。restore独立捕获异常，GET详情继续，显示“原启动身份无法恢复”及阻断解释，不提供放弃并新开。
- P2-2逐项红绿组：损坏JSON `{`、JSON null、body `{}`、坏UUID、非字符串body、外层多余字段、空/过长服务、单边范围、无时区、倒序、>24h、非法日期、getItem禁用、setItem禁用，共15种。qQ5pYg **15 failed/25 deselected in124.68s**（损坏JSON/getItem禁用导致详情缺失，其他缺少安全阻断解释）；OuD2cF **15 passed/25 deselected in50.46s**。禁止写入时持久化失败不发送POST；进程内按调查记住故障，SPA刷新不静默恢复为可新开，按钮和处理函数双重阻断。F5在存储仍损坏/禁止时同样重新阻断；不声称能恢复已丢失的身份。
- 清理异常补强（非逐例先红）：原调查201暂扣→切换另一调查→getItem/removeItem禁用或原存储损坏→释放旧响应，当前调查标题/告警不变，返回原调查可读且明确阻断，两调查轮数分别1/0。clear只删除同调查、相同key且相同body的记录，异常记在原调查，不向当前页面抛错。明确拒绝404/409/422清理使用已发送的原身份匹配，不把后端已明确拒绝的非法范围再当不可恢复损坏；同页保留原输入供修改，迟到拒绝不污染导航。
- P2-3大小写统一：扩展原快速终态测试，以大写调查UUID路由启动→后端201接纳→受控queries API令轮次快速失败→丢响应→F5。6Q5A8q **1 failed/40 deselected in26.28s**，实际直接出现新轮而丢失pending；路由读及storage键统一小写后Qg8wjC **1 passed/40 deselected in20.00s**，恢复锁定原服务/范围，完全相同key/body重放200且不加轮，之后才可显式新键201。
- 全UI回归过程如实记录：UrSCrY **42 passed/2 failed in143.29s**，暴露明确422后清理误触恢复校验与原路返回迟到422的锁定；匹配原身份清理修正后DGjEgS **43 passed/1 failed in172.40s**，余下是422后错误重置绝对表单；保留同页拒绝输入、仅成功确认后恢复新轮默认，DFi04x最终 **44 passed in130.74s**。保留原绝对范围校验、默认最近1小时、丢响应、迟到201/422、取消导航及跨调查归属断言，没有删除或放宽旧断言。
- 最终完整4ayoNP：`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost TEST_DATABASE_URL='postgresql+psycopg://logscope_test:local_test_only@127.0.0.1:55433/logscope_test' .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，**243 passed in526.73s (0:08:46)，退出0**，输出已完整收集。相对221新增22个真实浏览器场景（目录3、损坏/禁用15、清理异常3、大写UUID1）。只迁移隔离测试新schema，不drop、不碰开发库。
- 最终4ckTbW：`npm run build && git diff --check && git diff --stat && git status --short`退出0；vue-tsc/Vite成功，3294模块、Vite3.24s、JS1007.10kB/gzip324.79kB；仅既有大chunk、回调耗时与LF/CRLF提示。维持claimed，未resolved、未commit/push，未修改地图/日志。

### 最终复审P2：绝对时间格式恢复（2026-11-03）

- 仅修改App.vue、tests/test_ui.py与本Answer，保留其他全部未提交实现。先核对上文ve9riy全套完成记录（221 passed in416.52s，退出0），再次检查无pytest/Uvicorn/Vite进程及8000/5173监听后才使用UI端口；旧任务ID当前不可查询，不冒称再次取得其任务输出。加载tdd，测试边界为已授权真实Chromium＋真实HTTP API＋隔离PG17；未触碰开发库或drop测试schema。
- 读取StartAttempt及实测当前Pydantic 2.14.1：必须大写T且带时区，支持HH:mm、HH:mm:ss；时区支持Z/z、±HH:MM、±HHMM。仅小时、小时小数、分钟小数、±HH、偏移秒均不接受；秒小数接受点或逗号及任意位数，截断到6位微秒。前端恢复按此解析，年份1..9999、真实日历日期、小时0..23、分钟/秒0..59、偏移小时0..23/分钟0..59；以微秒BigInt比较严格正跨度和<=24h，避免Date.parse毫秒截断误拒绝合法微秒范围或放过超24h一微秒。恢复校验的计算值不写回请求；原body字符串与原key保持不变，重发不规范化、不新建身份。
- 先补8种真实接纳201→仅浏览器边界丢响应→F5→原输入锁定→原key/body确认200→仍1轮测试：分钟Z、分钟带冒号偏移、紧凑偏移、小写z、秒精度、逗号长小数、1微秒正范围、截断后恰24h。CJTz0I红灯 **6 failed/2 passed/44 deselected in68.46s**：分钟/偏移/z/逗号及微秒范围被误标损坏；原秒与普通点小数已通过，不虚称全部先红。
- 修复后sIDnqj定向：`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest tests/test_ui.py -k 'supported_absolute_pending or unrecoverable_pending' -q --tb=short`，**33 passed/29 deselected in96.07s，退出0**。新增10种非法恢复补强（非逐例先红）：仅小时、分钟小数、相等范围、小数截断后相等、超24h一微秒、非法偏移、24点、非闰年2月29日、零年、body未知字段；每种及原非法body先由真实API确认422，再验证存储恢复fail closed且不发POST。原无时区、倒序、超24h、单边时间、外层任意字段等断言全部保留。
- wigM9J全套：`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，**261 passed in548.55s (0:09:08)，退出0**，输出完整收集，无失败/跳过。相对243新增18个真实浏览器参数场景。
- U1X35d根目录`npm run build`退出0，vue-tsc/Vite成功，3294模块，Vite3.17s，JS1007.32kB/gzip324.92kB；仅既有大chunk/回调耗时提示。`git diff --check`退出0，仅LF/CRLF提醒。测试结束后核验pytest/Uvicorn/Vite进程数0、8000/5173监听数0，HEAD仍707f4a3。工单保持claimed；未resolved、未commit/push、未改地图/日志。
