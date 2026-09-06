# 07: 取消调查并隔离迟到产物
Status: resolved
Type: task
Blocked by: 05

## What to build
纵向交付取消请求 schema、停止阶段与确认事实持久化、幂等取消 API、执行写入隔离及UI过渡。点击取消不等于已取消，已保存材料保留。

## Blocked by
- 05 形成可追溯结论并结束调查

## Spec
[首版实施规格](../spec.md)：D2、D3、D8。
用户故事主要交付：US-11、US-12、US-13；US-47 取消隔离部分。
验收组主要交付：A11、A12；A15 取消侧部分（08与10最终协调）、A26 状态变化、A35 取消过渡部分。

## 开工门槛
须有可验证的停止安排、在途请求尽力中止、执行终止确认及停止后旧写入隔离机制契约；尚未选定，不能仅以页面断连或本地标志证明停止。needs-info 待该设计契约及验证方法齐备解除；不是重问已批准取消规则，不能擅称执行方案已获批准。

## 验收标准
- [x] 取消目标绑定调查及当前轮，立即停止安排新操作并尽力中止在途请求，重复请求不另起停止流程、不误取消其他轮。
- [x] 终止未确认时 UI/API 仍调查中／正在停止，持续占名额并拒绝手动新轮，不抢先显示已取消。
- [x] 只有可靠确认执行终止后原子进入已取消，保留结束原因和确认事实，名额只释放一次。
- [x] 停止前已保存的证据仍可读取；停止后迟到证据、结论、等级产物全部拒绝，旧请求重放也不能污染当前或后续轮。
- [x] 已结束后收到取消返回真实状态；与完成交错不产生双终态，不自行定义取消总优先规则，08补竞争裁决。
- [x] 取消阶段、终态和已有材料持久化且刷新可见，业务状态变化更新最后更新时间，读取不更新。
- [x] UI 清楚展示停止等待与确认后的区别，不让停止中的调查通过按钮或直接API绕过名额/重开限制。

## 测试与完成证据
HTTP API + 真实 PostgreSQL 配可控执行/查询适配点，观察取消后的调度与中止、延迟确认、三类迟到产物及重放；附执行确认/隔离证据和真实API UI E2E。替身只证明适配边界，实际执行安全由10、19验证；08与10覆盖A15完整交错。完成须包含 schema/API/UI 与隔离测试。

## Comments

用户已批准停止与隔离契约并明确要求实施 07：取消请求持久化后立即拒绝新查询及新的证据、结论写入；通知在途查询取消，确认适配器退出且旧执行不能写入后才进入已取消；确认前保持调查中／正在停止，不启动新轮、不提前释放名额；重复取消幂等、保留已保存材料并拒绝迟到产物；进程失联不假定停止，完整恢复留给 10。沿用已确认的 API＋真实 PostgreSQL、可控执行/查询调度及少量真实浏览器测试边界。开工门槛据此解除。

## Answer

### 实现与停止确认契约（初次实施记录；最终验收见末尾）

- 基点核实 master `036b394`，开工仅本工单批准记录有修改。已读 AGENTS、docs/agents 三份规范、GLOSSARY、spec、06/07 及执行/查询/证据/结论/测试/UI；加载 tdd 并读 tests/mocking 约定。无可调用 Agent 工具，直接实施和自查，不冒称子代理独立审查。
- 新增 `POST /api/investigations/{investigation_id}/attempts/{attempt_id}/cancel`，路径绑定目标调查及轮次，不接收自由终态、确认事实或请求体。调查行→轮次锁下首次持久 `cancel_requested_at` 并将执行 `valid=false`，事务提交即拒绝新查询/证据/结论，结论旧幂等键在重放判断之前拒绝。重复取消不重置时间、不另起工作；已完成/失败/取消返回真实当前投影，不改历史。与正常完成竞争由同调查行锁串行，先提交事实保持唯一终态，不定义取消总优先。
- 新迁移 `0009_cancel_confirmation.py`（head0008→0009）保存取消时间、确认时间、确认事实及执行身份 `stop_protocol`；仅新建默认受控执行器标识 `controlled_queries_v1`，旧执行/自定义执行器不倒填安全事实。升级不假设旧进程已停止；重复取消或重启后也不将未知协议当默认协议。
- 默认受控执行器本身只持久接纳、不启动线程/Agent，唯一实际异步活动为已持久接纳的 `controlled_queries`。取消先隔离写入；查询所属进程在每次实际查询/重试等待前检查持久请求，运行中通过 `asyncio.wait(timeout=0.1)` 驱动持久取消检查，跨独立API实例有效；收到请求 cancel 在途调度并 await 适配器清理真正退出。查询异常/返回后还受短事务身份检查，吞取消返回不能成为成功或新产物。
- `QuerySchedule.deadline` 对清理 await 使用 shield，已进入超时取消清理的子协程不重复 cancel，避免用户取消打断实际清理后伪称退出。未退出时持久query active不变、来源闸门保留、调查中/stopping、保留期为空，拒绝新轮。进程失联、数据库不可用不视作退出，无自动恢复，10处理。
- 确认事务仅在持久执行协议已知、无active查询时将状态改已取消、`end_reason=user_canceled`、`result=null`，保存 `stop_confirmed_at` 和 `controlled_queries_exited_and_writes_fenced`，结束+30天保留期同事务。只有实际await退出的查询处理才能将active清除；退出事务失败整笔回滚，仍在途/正在停止。取消请求端不凭valid=false认定物理退出。
- 来源闸门按来源哈希排序且只清理query_id属于本查询的占用，不释放其他调查闸门；取消重试等待仍保存来源已声明的Retry-After，不能借取消绕限流。已有证据和结论历史不删除；查询停止后的安全状态为canceled，不计成功覆盖。严重程度执行写入口尚未存在，仍未开放（未知入口404）；不得声称实现12的等级建议。
- UI新增取消按钮、正在停止/尚未确认/仍占名额/不能新轮提示和显式刷新状态；确认后显示已取消与主动停止原因，不冒充正常完成或根因结果。重复点击停止按钮禁用；刷新读持久状态，沿用导航序列保护，不持有执行凭据。

### TDD red→green 实际记录

统一命令前缀 `PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest`；全部HTTP业务API断言＋真实PG17隔离schema，外部查询/调度可控。未通过SQL查表断言业务，SQL仅安排旧版数据或注入PG事务错误。

1. 空闲取消及证据保留/结论重放隔离：`backend/tests/test_cancellation.py -q --tb=short`，QlnenU **1 failed in2.94s**（取消404）→ql4YWH仍红（响应日期+00:00与公开模型Z不一致）→加入response_model后y7EIHC **1 passed in2.47s**。
2. 跨独立API实例取消在途且等待吞取消适配器实际清理：uxJfcN **1 failed/1 passed in34.08s**（未通知协程，仅30秒deadline触发退出）→持久取消监视与退出事务5sxNBh **2 passed in3.89s**。
3. 已在超时清理中再用户取消：dGub5c **1 failed/2 passed in5.36s**（清理被二次取消打断）→shield及不重复cancel，9ELAFA **3 passed in5.57s**。
4. 回归补重试等待不再调用、来源释放、终态取消返回真实状态、三并发取消；未知执行器重启测试HU3Ws9 **1 failed/5 passed in10.73s**（按重启后默认适配器误确认）→持久stop_protocol，BthAaM **6 passed in10.54s**。
5. 真实UI测试 `tests/test_ui.py::test_cancel_ui_distinguishes_stopping_from_confirmed_and_refreshes`：GltA2z **1 failed in23.58s**（无取消按钮）→UI实现jE4vK5 **1 passed in21.04s**；真实Uvicorn/Vite/Chromium与PG，受控源取消后仍清理3秒，确认前刷新仍正在停止、按钮禁用、无新轮，清理后显式刷新已取消并持久。
6. 取消限流等待不能绕Retry-After：首版测试错误复用永久等待调度导致挂起，任务5f0YoG在1m17s主动停止；改为第二API实例健康有限源进行观察，不改业务规则。VaSiWk **1 failed/10 deselected in3.11s**（取消清闸后新查询201绕限流）→保留retry_at/blocked，6IKb2D **11 passed in19.78s**。
7. 回归补0008旧记录升级、不伪确认；取消请求三阶段PG除零原子回滚；完成/取消并发只一个真实终态；退出确认事务PG故障仍保守active并且重启不自动恢复。扩展测试不是每例虚构红灯，hdCVPv **13 passed in21.06s**。新增13项后端测试＋1项真实浏览器测试。

### 完整验收与范围限制

- 首轮完整命令 `PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost TEST_DATABASE_URL='postgresql+psycopg://logscope_test:local_test_only@127.0.0.1:55433/logscope_test' .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，99oHqt **164 passed in316.41s，退出0**。追加退出确认故障回归后最终完整复跑见后续记录，不能把164当最终计数。
- 构建EJzVop `npm run build && git diff --check` **退出0**：vue-tsc -b成功，Vite3294模块/2.41s，JS1001.78kB/gzip323.01kB；仅既有大chunk和LF/CRLF提示。
- 真实容器logscope-01-test-pg17实测 **17.11**，127.0.0.1:55433。各例新schema真实迁移至0009，旧0004/0005/0006升级回归及新增0008升级；不碰开发库、不drop测试schema，容器保留。UI fixture finally terminate/wait关闭其API/Vite，无detach。
- 没有实现08十分钟总预算/完整终态裁决、09重开、10失联恢复、11全局容量；当前没有全局名额表，不虚构释放名额计数验证。停止期间维持调查中及现有同调查运行唯一约束，保留期不启动；未来容量必须按运行状态计入stopping。没有真实连接器/Agent或任意异步代码沙箱；适配器须非阻塞、合作式取消、不遗留子任务。100ms持久检查不是硬实时通知保证，数据库故障时保守停止等待。
- 旧版/未知stop_protocol安全保守：即使无active查询也保持正在停止，由10确认；部署须停止旧版本写入后迁移，旧实例不认识新取消契约不可混跑。进程失联/确认提交失败不会自动认定终止或释放运行资格。
- 本工程现无map.md及既有工作日志；依“现有文件优先、仅必要实现/测试/迁移可新建”未额外建文档，将交接记录集中本Answer。保持claimed，不git commit/push，交主代理独立审查。

### 审查前最终复验（已被下方审查修复后的171项复验取代）

- 最终完整任务 **p9FO1w**，上述完整命令实测 **165 passed in310.49s，退出0，无失败/跳过**，相对基点151新增13项后端API/PG＋1项真实Chromium。包含退出确认事务失败与终态phase最终实现，不混用中间164结果。
- 最终构建 **tOhPFh** 退出0，`vue-tsc -b && vite build`成功：3294模块/2.32s，JS1001.78kB/gzip323.01kB；同命令git diff --check通过，仅既有chunk警告及LF/CRLF提示。
- 完整测试后独立PowerShell实测 **8000/5173 ListenerCount=0，ApiViteProcessCount=0**。没有动开发数据库、删除schema、commit/push或标resolved。文档追加后再次diffcheck。

### 07审查修复：逐项TDD证据与最终计数

2026-11-02，按用户指定先加载tdd，沿用已批准公共HTTP API＋真实PG／外部调度与UI seam；额外直接验证公开 `QuerySchedule.deadline` 调度契约，不查询内部表断言业务。未撤销原有未提交实现、未扩展来源闸门释放重构。

1. **P1 重复外部取消不能冒充真实退出**：新增 `test_deadline_repeated_external_cancel_waits_for_adapter_exit`，原实现任务 **G1bLVR：1 failed in1.12s**，断言明确复现 `pending.done()==True` 而适配器仍在清理。引入共用 `wait_for_exit`：仅首次通知cancel，循环shield直到真实task.done，外层重复取消仅打断本次等待而不再取消子任务；deadline及controlled所有退出清理路径使用。**toKNez：14 passed in21.67s**。再补HTTP边界用户取消＋请求task连续四次cancel，通过ASGI边界控制请求生命周期，断言真实adapter未退出时API仍调查中、stop_confirmed_at为空、query仍running，退出后才已取消；**zAthyP对应此例通过**。现有确认事务PG故障回滚、重启保守测试继续通过，外部请求取消不凭wrapper提前完成清active。
2. **P1 持久cancel与来源限流交错**：新增公共API参数化用例 `test_cancel_preserves_late_rate_limit_through_public_api`，可控调度覆盖“故障已产生、持久cancel在检查之前提交”及“适配器吞cancel后最终抛限流”两种次序。**zAthyP：2 failed,1 passed,14 deselected in5.80s**，两例均实测reason=None。修复deadline在取消退出后传递真实限流；controlled丢弃业务结果但保存rate_limited、Retry-After、retry_not_before及故障记录，再在原取消事务里持久释放所属query_id而保留blocked。第二独立API新调查查询必须409 rate_limited且带恢复时间，不使用SQL窥探闸门。**dvHUYB：17 passed in26.58s**。
3. 上述限流修复同类边界补公开deadline超时清理测试 `test_deadline_keeps_rate_limit_reported_during_timeout_cleanup`：**vb6fP0：1 failed in1.20s**（来源最终报告限流被转换为TimeoutError）；仅补超时清理后传播rate_limited，不接纳超时成功产物。**Jr1IoQ：18 passed in26.90s**。
4. **P2 UI取消归属**：新增真实Chromium `test_cancel_navigation_isolates_pending_errors_and_old_finally`，真实API创建A/B，仅网络边界暂扣cancel响应。**35dYJC：1 failed in25.35s**，A慢取消导航B后B按钮disabled；失败关闭浏览器产生Playwright待处理route的CancelledError日志，不是业务绿灯。修复canceling按attemptId区分，读序列＋hash路由＋调查id＋轮次id＋当前取消归属共同保护success/catch/finally；导航／刷新清空旧取消状态。**y0hyL6：1 passed in19.55s**，覆盖A→B按钮可用、B取消中A旧失败/finally不能解除B按钮或显示旧错误、B错误只显示B、返回A不携带B错误。

**本次最终完整复验（取代审查前165计数）**：

- 主代理已有 **iqu43e：165 passed in315.27s，退出0**；确认其完成后才启动本次UI，未并发抢占8000/5173。
- 最终 **9xX1IZ** 命令：`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost TEST_DATABASE_URL='postgresql+psycopg://logscope_test:local_test_only@127.0.0.1:55433/logscope_test' .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，原始输出 **171 passed in323.60s (0:05:23)，退出0，无失败/跳过**。相对本次修复前165增加6项（5项后端含参数化，1项UI）；相对151基点共新增20项，工单07现为18项后端取消测试＋2项取消UI测试。
- **A38a6f**：`npm run build && git diff --check` 退出0；`vue-tsc -b && vite build`成功，3294模块，**built in2.53s**，JS **1,001.91kB／gzip323.03kB**。仅既有大chunk警告与Git LF/CRLF提示，无类型或diff空白错误。
- 完整回归后PowerShell实测 **ListenerCount=0，ApiViteProcessCount=0**。测试仅隔离PG17端口55433及每例新schema，不碰开发库、不drop schema、不提交／推送、不标resolved，仍claimed。
- 限制仍保留：不合作、永不退出的适配器将持续占用并保持正在停止，不能以请求取消或等待超时伪造停止确认；进程失联、DB故障仍保守，恢复由10负责。外部适配器与调度测试证明受控边界，不代表真实连接器／Agent生产验收；UI交错测试暂扣/失败响应，不冒称真实网络故障已端到端复现。未实现08／09／10／11的额外能力。

### 主代理最终验收

- Standards 与 Spec 两轴独立审查：无明确编码规范硬违例；发现重复取消提前退出、限流交错丢失限制、UI跨调查状态3项行为问题，均先红后绿修复。独立复审核实3项闭环，无剩余阻断；重复闸门释放逻辑属于非阻断异味，未扩大重构。
- 主代理独立专项：18 passed in23.87s。随后误将 npm 配置定位到 frontend 导致 ENOENT；纠正到工程根目录后 `npm run build && git diff --check` 退出0，vue-tsc/Vite通过（1.81s，JS1001.91kB），仅既有大chunk及LF/CRLF提示。
- 主代理最终全套沿上述隔离测试库命令，任务 JaJIq5 退出0：**171 passed in293.13s，无失败/跳过**，包括真实PG迁移、API业务回归、真实Chromium界面和重启场景。相对151基点新增20项。
- 验收限制按前文保留，严重程度执行写入口未开放；没有全局名额表，不声称已经验证全局释放计数。07在受控契约范围内设resolved，创建事项地图记录结论，不修改父规格或擅自实施08。开发库未迁移，不推送远程。
