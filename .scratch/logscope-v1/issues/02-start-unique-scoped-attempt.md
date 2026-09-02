# 02: 按固定范围启动唯一调查尝试
Status: resolved
Type: task
Blocked by: 01

## What to build
交付从待处理到唯一调查尝试的纵向启动路径，涵盖服务选择与范围 schema、迁移、事务化业务 API、受控执行身份和 UI。执行仅可读取被调查环境，允许写调查产物，修复建议不得自动执行。

## Blocked by
- 01 创建并持久查看待处理调查

## Spec
[首版实施规格](../spec.md)：D1–D3、D8、未决实施选择。
用户故事主要交付：US-02、US-03、US-16；US-47 的执行身份与启动部分。关联 US-04、US-37。
验收组主要交付：A02、A05；A26 启动更新时间、A35 创建启动详情部分。实例三名额由11完成。

## 开工门槛
先明确服务与日志来源的配置映射、配置管理入口，以及执行身份如何持久识别、下发和校验的设计契约与验证方法；目前均未选定。needs-info 在这些具体契约齐备后解除，不重新批准业务规则，不虚构供应商或执行框架已获批准。普通 schema、原子启动与请求重放算法可在授权内设计；遵守01依赖。

## 验收标准
- [x] 启动前必须确定服务与合法起止时间，默认最近1小时；跨度恰24小时可启动，超过24小时或非法起止拒绝并显示原因。
- [x] 启动成功持久保存一轮调查尝试、固定服务与绝对起止时间，时钟推进不滑动；运行中修改服务或范围被拒绝。
- [x] 同调查重复点击、请求重放及并发启动最多产生一轮调查中、一次名额占用；回滚不留假运行轮。
- [x] 用户 API 不可自由编辑执行身份或绕过受控执行端写入；执行身份能关联调查与轮次，过期身份无权写入。
- [x] UI 显示真实运行范围及拒绝原因；只有启动成功更新状态和最后更新时间，读取不更新时间。
- [x] 被调查环境工具只读；不重启、不改配置、不删日志，不因用户要求修复而放开权限。

## 测试与完成证据
HTTP API + 真实 PostgreSQL 验证1h默认、24h边界、并发启动及事务失败；用可控执行适配点观察唯一启动和身份边界。提供持久化 schema/API 契约、UI E2E 启动详情、只读能力配置证据。替身仅证明受控流程；真实来源与 Agent 由18、19验收，11补实例容量，20汇总。

## Comments

2026-11-02 用户明确批准解除 needs-info 的契约：后端配置文件维护服务目录与日志来源标识映射，前端 API 拉取可选服务，只启动有效已配置服务；无管理页面、不接真实日志源、不显示连接正常。数据库同事务创建轮次和唯一持久执行身份，固化服务与绝对起止、防重复并发；用户不可指定身份或状态。执行端独立凭证校验调查/轮次/身份有效性，凭证不交浏览器、不写日志，无凭证配置默认拒绝。以可控执行适配器和受控只读上下文端点验证唯一启动及身份边界，无真实日志/Agent/伪进度。停止、失联、完整旧写入隔离留07/10，实例三并发留11，证据/结论留03及后续。HTTP API＋真实PG＋可控执行适配＋少量真实UI E2E的测试边界已确认，不再询问。

开工基点：09d10b2f8bc5752ea7dd3f8b6465ca3b49e5711d（master），git status干净；01已resolved。测试仅使用隔离PG17端口55433及新schema，保留测试数据；不commit/push。启动原子性具体技术设计将在逐条红绿验证中记录，不将未来外部执行当作本单已有能力。

## Answer

### 实施契约（独立技术设计，不冒称用户指定算法）

- 配置：SERVICES_FILE 指向后端 JSON，services 含 id/name/source_ids；加载时验证结构、唯一服务及合法字符，无文件配置为空目录，非法文件安全报错且不回显内容。backend/services.example.json 仅可控标识，无真实凭据。GET /api/services 仅返回 id/name，不返回来源映射、凭证或连接健康。修改文件后重启加载，无管理页/真实连接器。EXECUTOR_CREDENTIAL 只由环境安全注入；未配置或空值默认拒绝所有执行端访问，无真实密钥样例。
- POST /api/investigations/{id}/attempts：必需 Idempotency-Key UUID；JSON仅 service_id 与可选 starts_at/ends_at。时间均省略时锁定记录后使用服务器UTC now和now-1h；显式时间必须同时提供带时区ISO绝对字符串（含T），拒绝null、数字及数值字符串、无时区、倒序/相等、超24h；恰24h允许。字段额外输入（含executionID/execution_id/status）422。未配置服务422、未知调查404。范围保存timestamptz，来源映射在轮次中快照固化，浏览器不可编辑。
- 首次201、相同键相同规范化请求200当前调查投影；同键不同内容409。调查行FOR UPDATE串行化同调查启动，部分唯一索引防双运行轮，调查/请求唯一约束防重放，执行接纳表attempt_id唯一。独立键在已有运行轮时409，不另建轮。没有实现实例三并发限制（11）。
- 最小原子边界：ControlledExecutionAdapter.accept仅通过传入的同一PG连接创建唯一执行身份/持久接纳，和轮次、更新时间、运行保留保护同事务提交。它不进行网络dispatch，不启动线程/进程，不扫描日志、不调用Agent；一次接纳就是本单可观察的执行下发边界，不将持久接纳伪称真实工作进度。接纳异常（包括已插入执行身份后故障）回滚，安全503并允许同键恢复；PG事务故障不会留下假轮。数据库提交时连接中断可能结果不明，503明确要求同键重试读取事实，而不声称一定未启动。此设计不是跨数据库/外部执行器分布式原子保证，不允许以后随意把外部dispatch塞进该接口后沿用保证；真实执行接入需重新设计接纳/确认协议，未引入队列。
- 受控只读端点：GET /api/executor/investigations/{id}/attempts/{attempt}/executions返回该轮有效持久身份；GET .../executions/{execution}/context严格关联调查、轮次、身份、valid和调查中状态。独立Authorization Bearer凭证（常量时间字节比较），缺/错凭证401；不存在/错调查/错轮/失效身份403。只读上下文capabilities=[read_execution_context]、environment_tools=[]、execution_mode=controlled；无任何环境写工具、证据/结论写入口或任意状态编辑接口。执行路由不出现在公开OpenAPI。浏览器业务API不含执行身份或凭证；应用不记录凭证/异常细节，本次Uvicorn关闭access log。未来部署不得另行记录Authorization或把凭证放URL。
- 创建仍待处理/未评估/无轮；启动成功调查中、未评估、证据0、更新updated_at；详情/列表/创建幂等重放都读真实当前轮次投影，不伪返回待处理。运行时retention_expires_at=null、UI显示运行中不清理；未实现到期清理、延期或结束后的重新计期。服务显示采用当前已启动轮service_id；未做后续多轮筛选/搜索口径决定（15）。服务重启只读原记录/身份，不自动重新接纳、不假报旧执行已终止。
- UI：从API取服务、默认服务器最近1h，可切换带时区绝对范围；显示服务器明确拒绝原因、成功后固定服务和范围（UTC+8）且不再提供启动/改范围表单。请求未确认时保留同内容请求键，重复按钮有in-flight保护；丢响应后显式重试返回同一轮。提示可控执行、未接真实来源/Agent，不显示连接正常、伪进度或分析结果。

### 红绿与实际运行结果

- 服务目录：TypeError缺services_file红灯→实现后1 passed；启动404红灯→迁移/API实现后发现UTC序列化Z/+00差异，统一响应schema后2 passed。
- 范围：显式null被接受201红灯→严格字段校验；只读轮次资源PATCH返回404与预期405差异→补只读轮次GET后3 passed。数值时间字符串被Pydantic当作时间戳接受201红灯→要求ISO字符串含T后单项1 passed。覆盖默认1h、恰24h、24h+1微秒、空/非法/无时区/半范围/非法身份字段，无效请求不留轮且同键可合法重试。
- 并发：缺独立执行凭证参数红灯→6同键并发仅201一次其余200且响应一致；4独立键重复全部409；通过独立认证HTTP执行端观察恰1持久接纳，重启仍同身份。无内部调用次数断言。
- 故障：缺execution_adapter注入红灯→真实PG SELECT 1/0事务故障，轮次与身份一起回滚，原调查事实不变，同键恢复201；普通适配异常未处理红灯→安全503回滚，3项故障/失效身份测试全部通过。异常测试仅使用随机非真实敏感标记，不提交真实秘密。
- 身份：缺context端点404红灯→实现后7项增量通过；非ASCII Authorization触发TypeError红灯→改字节常量时间比较后单项通过。真实无效身份通过受控适配器安排valid=false，再由HTTP验证准确身份403；无公开失效/状态变更旁路。
- 配置：来源含NUL未拒绝红灯→合法字符校验后单项通过；空目录不能启动、无凭证默认拒绝，服务API无来源/秘密。
- UI：真实API+Vite已启动后，找不到被调查服务输入红灯→真实Chromium路径1 passed，npx vue-tsc -b退出0。新增默认1h启动丢响应重放回归真实请求先提交PG后浏览器abort；和既有01创建/丢响应测试一并最终通过。
- 真正Uvicorn进程启动/退出/重启后固定范围、同键投影、身份接纳保持一致：单项1 passed in4.26s；测试子进程finally terminate/communicate，无脱管进程。
- 最终一次全套：PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short：19 passed in39.48s（15 API/集成，4真实Chromium UI）。npm run build含vue-tsc及Vite成功；git diff --check退出0。保留既有>500kB JS chunk非阻断警告（995.04kB），未借机重构。
- 每例新schema经真实Alembic upgrade head（0003），PG17容器logscope-01-test-pg17，端口127.0.0.1:55433；UI独立schema issue02_ui_d0ca80d7dee045abab7591c558436ac8，保留诊断数据。未连接/迁移开发库，未删除任何schema。API/Vite仅127.0.0.1后台任务管理，结束停止本次验证服务，容器保留。

### 修改文件与主代理审查点

- 新增：backend/app/services.py、attempts.py、execution.py；backend/migrations/versions/0003_scoped_attempts.py；backend/services.example.json；backend/tests/test_attempts.py。
- 修改：backend/app/core/config.py、main.py、investigations.py、frontend/src/App.vue、tests/test_ui.py、.env.example、本02；当日记忆仅追加。未改其他工单/父规格/GLOSSARY，未实现03+。dist为本机构建产物，Git忽略。
- 基点始终为master 09d10b2f8bc5752ea7dd3f8b6465ca3b49e5711d；开工干净，未commit/push，留主代理审查后提交。
- 重点复核：事务内可控接纳与未来外部执行器保证不可混淆；提交结果不明同键重试语义；独立执行凭证部署/日志边界；迁移运行期保留null；01幂等重放当前投影。尚无独立多进程并发压力测试，当前是HTTP线程并发＋独立进程重启，PG约束而非内存锁；无生产真实来源/Agent集成声明。
- 07/08/10完整停止、失联、超时裁决及旧执行写入隔离未实现；11全局容量、03+证据/结论和16/17完整保留清理不在本单。可控只读端点证明本单身份边界，不冒充这些后续验收。02六项验收按用户批准的可控边界全部真实通过。

### 2026-11-02 create幂等重放P2最终审查

- 已先核实：create在默认READ COMMITTED下INSERT ON CONFLICT DO NOTHING后单独SELECT调查，再current_view读取attempts；旧调查SELECT完成后并发start提交，重放可混合旧updated_at/retention_expires_at与新attempt/service/status。既有GET的REPEATABLE READ不能覆盖此写事务。
- 新增真实PG＋HTTP并发回归2例（replay-first/start-first），不是新增24例或24项UI：SQLAlchemy连接事件仅暂停/观察测试DB边界，独立连接用pg_blocking_pids等待真实锁竞争，不替换SQL结果、不增加生产hook、不用sleep猜时序；业务结果只断言HTTP。replay-first在重放读调查后推进start，未修复真实出现混合投影；start-first在启动已持锁并接纳但未提交时发重放，要求锁协议等待后返回完整新投影。后者未修复返回完整旧投影，是新增锁协议用例失败，不冒称第二个混合投影漏洞。
- RED：`PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests/test_attempts.py -k create_replay_keeps_consistent -q --tb=short`：2 failed,12 deselected in2.97s。只改生产investigations.py重放SELECT追加with_for_update及说明；GREEN同命令2 passed,12 deselected in2.64s。
- 锁序/死锁审查：重放与start均先取得同一调查行FOR UPDATE，再读取轮次；不用FOR SHARE后升级（避免多个重放/启动的共享锁升级环），不新增attempt/执行身份先于调查锁的反向路径；冲突插入DO NOTHING不更新已存在调查。两种顺序真实PG都完成，无死锁/503，原4创建并发、6同键启动及4独立键启动回归仍通过。只锁单调查、事务内完成投影后释放；不提升引擎或所有写事务隔离，不改变GET快照、start算法或接纳边界。这是现有路径锁序复核＋受控并发回归，不冒充无限并发压力证明。
- 投影双向依赖判断为非阻断：attempts顶层导入investigations定义，investigation_router内部延迟导入current_view，create_app先完成模块加载再构造路由；现有导入顺序/独立进程重启均通过，无实际循环初始化异常。本次不移动模块、不重构投影接口。
- 读取Settings环境加载约定、.env.example（本地无.env）、Vite代理8000和服务目录配置；测试库沿用TEST_DATABASE_URL或fixture默认的既有隔离PG17.11容器logscope-01-test-pg17/127.0.0.1:55433，启动前校验host/port拒绝开发库。每API新schema真实Alembic upgrade head0003；最终UI schema `issue02_final_ui_84c7f74bd2184b469673af8bb5ee4595`，数据保留，无drop/开发库迁移。API用进程环境DATABASE_URL指向新schema、SERVICES_FILE=backend/services.example.json、随机进程内EXECUTOR_CREDENTIAL（不打印），Vite及API仅绑定127.0.0.1。
- 正确执行顺序：先新建隔离schema并用其DATABASE_URL执行 `PYTHONPATH=backend .venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head`；同环境启动 `.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log` 与 `node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort`；确认health及Vite代理services真实JSON后执行 `NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PYTHONPATH=backend UI_BASE_URL=http://127.0.0.1:5173 .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`。UI必须先有真实API/Vite，不能只裸跑pytest冒称可执行完整E2E；本机已有HTTP_PROXY/HTTPS_PROXY，需本地NO_PROXY。
- 最终完整一次：26 passed in44.42s，0失败/跳过（原24＋本次2；19 API/真实PG集成＋7真实Chromium UI）；`npm run build`（vue-tsc -b && vite build）退出0，3294模块、JS995.16kB，仅原有>500kB chunk非阻断警告；`git diff --check`退出0，仅既有LF/CRLF提示。
- 设施失败如实记录：前两次启动检查分别收到非JSON、重复services探测1s读超时，均未运行全套；补本地NO_PROXY、10s启动请求超时并复用已校验JSON后完成上述26项。父进程finally terminate/wait；即时TCP探测8000曾显示仍可连接，导致验证包装退出1（pytest/build/diff均0），随后独立复核8000/5173无监听且无Uvicorn/Vite进程。首次额外进程检查正则转义错误，改用通配符重新成功核验；未把失败检查算通过。既有PG容器保持原状态，所有本次API/Vite/测试子进程已停，未新增常驻PG服务。
- 本次增量仅backend/app/investigations.py、backend/tests/test_attempts.py、本02和`.workbuddy/memory/2026-11-02.md`；保留接手前全部未提交02修改，不改其他工单/规格/配置/前端，不留临时验证脚本。HEAD仍09d10b2f8bc5752ea7dd3f8b6465ca3b49e5711d，未commit/push。

### 2026-11-02 审查候选核实与两项P2实际修复

- 本次接手时已有02未提交实施改动，完整保留；未将候选直接视为事实。仅增量修改 frontend/src/App.vue、backend/app/investigations.py、tests/test_ui.py、backend/tests/test_attempts.py、本02及当日项目记忆，未改其他工单、规格、迁移或写事务。HEAD仍为09d10b2f8bc5752ea7dd3f8b6465ca3b49e5711d，未commit/push。
- P2-1确认存在：真实Chromium以route.fetch执行真实API/PG请求，再暂存响应，A启动后经列表导航B；原实现首先表现为B表单仍被A全局starting禁用。只补导航reset后再次red：201晚到把B标题/详情替换为A，422晚到把A错误展示在B；A→列表→A也收到旧错误。3项均真实失败，不用静态源码断言冒充回归。
- P2-1修复：loadPage新序列重置starting；start捕获调查id、location.hash与readSequence，成功结果/pendingStart清理、catch错误、finally状态均只在三者仍属当前页面时写回。避免仅比较id/route导致原路返回接纳旧响应，也不以增加读取序列使同页正常重试失效。保留同页丢响应同键重试。
- UI回归新增3项（201/422参数化＋原路返回失败后重试）：A未返回时B可启动；A晚到后B标题/URL不变、无旧错误、B在途仍禁用；B真实提交后再丢响应，同键重试200且投影完全一致，证明旧A成功未清空B请求身份；原路返回不接受旧错误并可重新201启动。已有同页默认1h丢响应200重放仍通过。
- P2-2确认存在：测试独立连接设施用SQLAlchemy after_cursor_execute事件，在HTTP GET读完调查SELECT后暂停该读取线程，另一个HTTP POST真实提交启动，再恢复GET读取attempt。详情与列表各一例均red（2 failed in2.97s）：旧updated_at/retention_expires_at仍与待处理完全相同，却混入新attempt、orders及调查中。业务断言只检查HTTP响应，不直接查表断言。没有给生产增加测试hook。
- P2-2修复：仅listing/detail连接使用execution_options(isolation_level=REPEATABLE READ)，在首次读后整次响应共用一致快照；引擎默认、创建/启动写事务、FOR UPDATE、接纳与幂等算法均不改。连接退出回滚读事务并由连接池恢复默认隔离，不机械把写事务提升隔离级别。受控回归断言在途GET完全等于启动前投影，后续详情/列表完全等于启动成功投影，且POST能在暂停GET期间成功。
- 定向绿灯：5 passed,14 deselected in10.61s（真实API/Vite/Chromium＋真实PG）。随后增强B丢响应身份断言时，首轮全量22 passed/2 failed，原因是测试先unroute自动处理在途请求、再abort触发Route is already handled；改为先abort再unroute，仅修测试设施顺序，不掩盖业务错误。
- 最终完整命令等价于在本次API/Vite环境运行 PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short：24 passed in41.03s（17 API/真实PG集成＋7 Chromium UI，含并发启动、真实进程重启、回滚、身份边界）；npm run build（vue-tsc -b && vite build）成功；git diff --check退出0。仅既有大chunk非阻断警告，JS995.16kB，未借机重构。临时验证入口仅用于管理服务生命周期，完成后移除。
- 严格沿用测试契约：PG17容器logscope-01-test-pg17，127.0.0.1:55433，每API测试独立新schema真实Alembic upgrade head0003；最终UI独立schema issue02_review_ui_25311cfa768d4b90ba23ba0f8e2dd16d。未使用/迁移开发库，所有测试schema及数据保留。真实Uvicorn8000及Vite5173仅127.0.0.1，父验证进程finally终止并wait本次子进程；最终再次检查端口均关闭，PG容器保留。无真实凭据写入文档/日志，无额外生产测试hook。
