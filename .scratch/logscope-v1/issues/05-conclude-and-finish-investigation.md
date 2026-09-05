# 05: 形成可追溯结论并结束调查
Status: resolved
Type: task
Blocked by: 03

## What to build
纵向交付结论及版本持久化 schema、受控结论/结束 API 和详情 UI，明确调查状态与根因结果分离。展示可核验因果链或待验证假设，结束不覆盖历史，修复建议仅供参考。

## Blocked by
- 03 保存、展示脱敏证据及调查内一致代号

## Spec
[首版实施规格](../spec.md)：D2、D3、D5、D8。
用户故事主要交付：US-06、US-28、US-29；US-05 初始状态投影部分。关联 US-37、US-47。
验收组主要交付：A22、A03 的正常完成部分；A26 结论及状态写入。技术失败06、逐轮历史09、真实推理19补全。

## 开工门槛
无额外未知输入，遵守03依赖；可设计结论契约和受控案例的业务校验。不得仅靠字段齐全或结构校验宣称已证明因果或真实模型质量；真实 Agent 语义和只读闭环在19验收。退出竞争在08、10补全，不自设终态优先级。

## 验收标准
- [x] 结论清楚展示故障现象、原因如何导致现象、关联可核验证据、关键矛盾及替代解释处理，可从结论定位证据。
- [x] 只有数量或时间相关、仅摘要不可追溯、证据链存在未处理关键矛盾的案例不能被呈现为已确认根因；不强制至少两条证据。
- [x] 证据不足时保留待验证假设、现有依据与缺失证据，正常结束为已完成／未确定根因，不称无故障或技术失败。
- [x] 已确认根因与未确定根因均可正常结束；整体状态跟最新轮，状态、结果和结论核验状态分别表达。
- [x] 结论及支撑关系、版本和结束原因持久化，重启可读；拒绝过期执行身份和已结束轮的后续产物，历史不被覆盖。
- [x] 结论或状态业务变化更新最后更新时间，读取不更新；UI 显示真实结束时间与结果，修复建议不触发环境写操作。

## 测试与完成证据
通过 HTTP API + 真实 PostgreSQL 验证充分因果案例、相关性反例、证据不足和重放/结束后写入，提供逐案语义评审依据及UI详情证据，不以结构校验替代因果判断。可控 Agent 仅限适配验收；19必须用真实 Agent 补足可追溯输出验收。schema/API/UI 与结论持久化均完成才交付。

## Comments

2026-11-02 开工：用户授权implement05，核实master fd3b9c6且git status干净，先claimed。读取AGENTS及工程规范、spec/GLOSSARY/01-04 Answer、执行/证据/投影/API/迁移/UI/tests与工程当日日志；已确认seam不再询问。只用127.0.0.1:55433现有PG17.11及每例新schema，真实迁移；不触开发库、不drop、不commit/push、不新增报告/ADR/地图。无可调用Agent工具，直接实施与自审，不冒称子代理审查。

## Answer

### API及安全支持边界

- 新受控`POST /api/executor/investigations/{investigation_id}/attempts/{attempt_id}/executions/{execution_id}/conclusions`，隐藏于公开OpenAPI。必需独立`Authorization: Bearer EXECUTOR_CREDENTIAL`及UUID `Idempotency-Key`；普通浏览器没有凭证或任意确认/结束写入口。缺/错/空配置401，调查/轮次/执行不匹配或新请求使用失效身份403。没有增加环境工具、自动修复或外部dispatch。
- 请求仅`format="controlled-analysis-v1"`、`kind=undetermined|hypothesis|confirmed`、`mechanism=null|dependency_error_propagation`、`evidence_ids`（最多32个不同UUID）、`missing`（至多3项，仅causal_trace/dependency_observation/alternative_exclusion）、`expected_version`（严格非负整数）、`finish`（严格布尔）。所有层级未知字段/自由文本/动态字段名/非法代理码点/格式均失败关闭，流上限64KiB。结论文字完全由服务端受限模板产生，敏感片段只通过03已脱敏证据引用；不使用regex宣称任意文本可靠脱敏，不接任意模型结论。
- 未确定根因允许零证据，但须明确缺证据；不能称无故障。假设须有受支持候选机制、本轮依据和缺口；完成后仍是假设而结果为未确定根因。引用须是本轮已保存、已纳入的实际证据，不存在/重复/跨调查/同调查跨轮均422；沿03继承服务、来源和查询/发生范围，不能从结论重写这些边界。12历史纳入及13失效重核没有假实现。
- 为提供有意义且安全的最小因果输入，在03 `controlled-event-v1.record`增加可选严格`causal_trace`：`format=observed-propagation-v1`，dependency_error/propagated_error为ETIMEDOUT、ECONNREFUSED、EACCES、HTTP_500/502/503之一；request_outcome=failed_by_dependency|independent_failure|unknown；local_rejection和independent_failure=excluded_by_trace|unresolved；contradiction=none_observed|unresolved。不是任意文本/布尔强制确认，而是可信受控生产者对同一次调用的依赖错误传播及两个竞争解释的观测声明；认证接口不能独立证明生产者说真话，真实追踪适配和Agent语义由18/19验收。
- 确认只支持“依赖调用错误经同调用传播路径导致请求失败”：引用至少一条可核验受控轨迹，event须request_failed，记录error_code=dependency_error=propagated_error，outcome须failed_by_dependency，本地拒绝与独立失败须均被轨迹排除，关键矛盾未报告，missing为空。数量、相同时间、普通关联ID或字段齐全均不够；单条完整轨迹可支持，不强制两条。扫描同轮全部已纳入轨迹（不仅选中的引用），存在未处理/不匹配轨迹或03不可用材料提示时保守拒绝确认，不能靠省略矛盾证据确认。
- 确认输出`verification=supported_controlled_trace`、现象、因果机制、替代解释和关键矛盾模板、引用及明确边界；不宣称科学证明、普遍因果判断或全局无矛盾。不确认输出insufficient。支持机制刻意有限而非禁止所有有意义因果链；未来真实Agent只能在这个受审结构内提交，其他机制须扩展观测契约和语义验收，不能简单开放自由文本或直接接受模型confirmed标签。
- 解析/校验返回固定安全422，意外读写/PG异常返回固定安全503且from None，不传播SQL/输入/异常内容，SQLAlchemy继续hide_parameters。另以真实红灯确认全局Pydantic错误loc会泄漏未知动态字段名，改成固定type/loc/msg，不回显input/context/动态路径/异常文本；保留UI需要的24小时范围提示。部署仍须禁止代理/访问追踪记录请求体、认证及完整敏感URL；不宣称控制第三方日志。pg_dump仅内存审计，无秘密落报告。

### 版本、事务、投影与迁移

- 结论表仅追加：UUID、轮次、执行身份、单调version、请求UUID/规范化安全结构、created_at和模板material；UNIQUE(attempt_id,version)与UNIQUE(attempt_id,request_key)。请求结构没有自由文本，执行身份仅后端表保存，不出公共投影。轮次`conclusions`给完整版本，`conclusion`为最新版本，更新不会覆盖历史。
- 新请求需有效执行且轮次调查中，并以expected_version对当前历史数量做CAS；过时409。相同请求键同规范化内容200返回原结论，不新增/更新时间；同键异内容409。包括正常结束请求本身在身份已失效后可200重放，但只是读取同执行同轮已提交原结果，绝不恢复身份或接收新产物；新键/迟到结论403，迟到证据包括旧可靠身份重复均403。6独立HTTP连接同键仅一个201，其他200；不同键同版本仅一个201、另一个409。
- 锁序与03一致：调查FOR UPDATE→轮次/执行FOR UPDATE→证据与版本校验→追加。`finish=true`同一事务写结论、轮次已完成/result/ended_at/end_reason=normal_completion、执行valid=false、调查updated_at及retention_expires_at=ended_at+30天。任一写故障全回滚；finish=false仅追加和更新调查时间，不结束、不释放执行。当前执行适配器没有线程/在途外部工作，valid=false是所有已实现写入口的持久隔离；绝不冒称能停止未来外部Agent进程。
- 独立结果已确认根因/未确定根因，与轮次状态和核验状态分离；InvestigationView允许已完成，不再被01旧模型限制。GET列表/详情沿既有REPEATABLE READ，轮次详情也补同快照；创建/启动重放仍持调查锁返回真实已完成投影，不伪返回调查中或待处理，读取与重放不刷新时间/期限。05不重开09：新启动键在已存在结束轮时409，同原启动键仍200真实投影；UI不加重开按钮。
- 新真实迁移0006从0005追加conclusions及attempts.ended_at/result/end_reason，不改旧迁移、不改证据或HMAC密钥。0005→head旧数据夹具保留同调查历史轮/当前轮两条证据UUID、材料、状态与更新时间；升级后拒跨轮引用、接收当前轮结论，旧轮整体不变。旧数据不凭空补ended_at或结论。无轨迹证据的04内容指纹仍排除新可选null字段，保持既有可靠身份重放；带轨迹的内容纳入指纹，变化继续409。
- 部署：停止旧版本写入并备份调查产物库，经安全配置注入DATABASE_URL和PYTHONPATH=backend，执行`.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head`至0006，再启动匹配API/UI。本次仅隔离测试schema执行迁移，未对开发库操作；未降级、未删除schema。
- UI展示正常结束时间（UTC+8）、独立结果、当前版本及所有历史版本、假设依据/缺口、确认现象/机制/矛盾/替代解释和适用边界；证据链接在当前详情滚动到对应实际证据卡，不破坏hash路由。刷新保持、无执行身份/凭证DOM，无强制确认按钮。修复建议仅供参考的提示不提供执行操作。

### 红绿与实际验证过程

- 首项命令前缀统一`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PYTHONPATH=backend .venv/Scripts/python.exe -m pytest`。`backend/tests/test_conclusions.py -q --tb=short`：初始404红灯1 failed in2.39s→0006/接口/投影/原子结束后1 passed in2.02s。
- `backend/tests/test_conclusions.py::test_hypothesis_versions_reference_only_this_attempt -q --tb=short`：422红灯1 failed in2.22s→假设引用与版本支持后文件2 passed in3.47s；不存在/跨调查依据拒绝、同键不更新时间、CAS409、两版本追加、最后版本投影均由HTTP验证。
- `backend/tests/test_conclusions.py::test_confirm_requires_observed_propagation_not_correlation -q --tb=short`：轨迹输入422红灯1 failed in2.59s→受控轨迹契约和最小语义评估后文件3 passed in4.80s，确认只有1条轨迹也可完成；普通两条同时间超时不能确认。机制不一致、独立失败/替代未排除/矛盾未处理五参数反例为后续契约回归，不虚称各自先红。
- 结束后误开新轮与未选中矛盾隐藏：文件2 failed/8 passed in14.51s→状态保护与全轮轨迹校验后10 passed in14.30s。全局动态JSON字段loc泄露单例1 failed in3.66s→固定安全校验响应后定向14 passed in20.46s（含真实0005升级、时钟及快照）。
- `tests/test_ui.py::test_conclusions_history_completed_result_and_evidence_navigation -q --tb=short`：真实UI缺未确定根因/历史红灯1 failed in21.43s→UI后1 passed in18.12s。定位采用click.prevent滚动而非改路由，测试改为目标在视口；随后参数化已确认/未确定两种真实结束路径，确认页校验因果/替代/矛盾/边界，旧假设版本及缺口保留。
- 首次完整67 passed/2 failed in163.83s：两个新调度例把同步DB暂停放在同TestClient ASGI portal，第二请求无法到达数据库；修测试为独立TestClient事件循环/独立HTTP连接，不改生产，定向`-k race`7 passed in10.14s（包含名称含trace的5例）。DB设施仅暂停SQL连接和注入PG故障，不伪造SQL结果/不查业务表断言；无业务私有hook。
- 增量覆盖四个事务写阶段真实PG除零回滚及同键恢复、6并发幂等/不同键CAS、证据与结束两方向受控交错、详情/列表结束中一致快照、可控时钟精确结束+30d、同调查跨轮拒绝、跨调查拒绝、迟到写入、内存pg_dump安全审计、真正Uvicorn结束后再重启读取/重放。所有业务事实由HTTP断言；迁移SQL仅安排旧schema环境。升级与并发补强后的最终结果见下一节。

### 最终完整验收结果

- 最终实际命令：`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，后台任务umiqWc已收集，退出0，**72 passed in140.52s**，0失败/跳过；既有47项全部回归，加23项结论HTTP/真实PG和2项真实UI（原UI用例保留）。上一轮HrVL37为71 passed in137.32s，最终增加独立连接不同键CAS及升级跨轮夹具后重跑，不混用旧结果。
- 最终`npm run build && git diff --check`任务62zbCC已收集退出0；vue-tsc -b和Vite成功，3294模块、3.32s、JS998.72kB/gzip321.81kB。仅既有>500kB chunk警告与LF/CRLF提示，未做无关拆包；最终文档追加后再次diff检查。
- 实际PG容器logscope-01-test-pg17，postgres:17-alpine，SHOW server_version=17.11，仅映射127.0.0.1:55433。后端每例独立新schema真实upgrade head0006，旧升级例0004/0005→head；UI conftest自动新schema迁移后启动真实127.0.0.1 API8000/Vite5173和Chromium，随机认证仅在进程内。所有测试schema保留，无drop、未碰开发库。
- 测试服务由fixture/重启夹具finally terminate/wait或communicate管理，无detach。最终独立PowerShell核实8000/5173监听0、限python/pythonw/node的Uvicorn/Vite残留0，退出0；PG原状态保留。
- 直接自审全部tracked diff与新源码/迁移/测试：认证及敏感输入边界、旧指纹兼容、调查→轮次执行锁序、提交前原子结束与回滚、CAS及同键重放、同轮证据限制、因果支持边界、全轮矛盾与不可用提示、读投影快照和公共身份不泄露均复核；不冒称独立子代理或真实Agent质量评审。六项验收仅在上述受控能力边界实际通过后resolved。
- HEAD保持master fd3b9c6，未commit/push。最终交主代理审查后提交；不把05 resolved扩大为首版或后续工单完成。

### 未实现后续与交接

- 无18真实日志、19真实Agent/模型质量、09手动重开、12历史纳入、13证据失效/待重新核验假实现；无07/08/10取消/超时/失联终态完整裁决，不自设终态优先级。05仅确保当前正常结束的持久身份失效与既有写入口状态保护，不能退化基本安全。11全局容量、15完整筛选概况、16/17延期清理、20发布收口继续后续。
- 修改仅05所需：新增conclusions.py、0006、test_conclusions.py；修改attempts/evidence/investigations/main、App.vue、既有重启测试和UI测试、本05及当日日志。没有新增报告/ADR/地图，没有commit/push，留主代理复核。

### 2026-11-02 Spec审查P2：假设的最低事件支持

- 重新Read规格US-29/D5/A22及GLOSSARY“待验证假设”：已有依据不等于任意非空引用。确认原conclusions.py仅检查mechanism及saved非空，而语义循环仅约束confirmed，因此request_completed/NONE无轨迹也能被201保存为依赖错误传播假设，属真实缺口。
- 只在现有controlled-analysis-v1唯一候选dependency_error_propagation增加最低支持：选中引用中至少一条dependency_failed且error_code为现有明确非NONE错误，或request_failed且错误属于既有轨迹可表达的ETIMEDOUT/ECONNREFUSED/EACCES/HTTP_500/502/503。显式依赖失败的4xx可提供候选依据；仅请求侧4xx无法区分本地拒绝等，不单独支持依赖传播。request_completed任意错误及任何事件/NONE不支持。使用显式白名单，不把未知值当成正常或非NONE错误。
- 这是受控格式的最低候选支持而非证明传播：不要求完整causal_trace、同调用链或替代解释已排除；已有不完整/矛盾轨迹的请求错误仍可保留假设和缺口，不能confirmed。假设依旧insufficient、结束依旧已完成/未确定根因。confirmed原有全轮矛盾及不可用材料规则未变。无新增机制、字段、自由文本或UNKNOWN枚举。
- 混合证据采用选中集合至少一条适当支持：正常日志与支持日志任意顺序可并列引用，不要求每条证据都支持；同轮存在但未引用的支持项不能为纯正常引用背书。拒绝422不改版本、状态、updated_at或执行身份；同键可降为undetermined正常结束。UNKNOWN输入仍在证据入口422、不保存、不误称正常；零依据可undetermined，不等于无故障。
- 已核查全部现有候选：Analysis仅dependency_error_propagation一个机制，UI只对应该模板；undetermined不声明机制，confirmed沿用严格因果验证。未任意开放其他候选。
- 新增36项三事件×12错误枚举HTTP/真实PG回归、4项混合引用顺序/选择回归、1项UNKNOWN拒绝回归（共41项）；另增强原5项不一致/未处理轨迹回归，确认拒confirmed但允许有依据hypothesis。业务断言只走HTTP，未用数据库查询代替业务判断。
- 红灯实际命令：`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests/test_conclusions.py -k 'minimum_event_support or mixed_evidence_requires_selected_support or unknown_event_is_not_normal' -q --tb=short`；任务bAT5gm已收集，退出1，**20 failed, 21 passed, 23 deselected in59.89s**，失败均为实际201而应422。
- 最小生产修复后结论文件首跑39VfPf：63 passed/1 failed in104.30s，新回归全通过；失败是既有6并发客户端夹具同时write_text同一services.json，截断读取触发服务配置错误。仅把configured_client临时配置改为各客户端独立UUID文件，不改生产配置/锁/校验。重跑vopd04已收集退出0，**64 passed in85.00s**。不把设施失败隐去或说成产品红灯。
- `npm run build && git diff --check`任务PFTDhO已收集退出0；vue-tsc -b成功、Vite3294模块/2.36s、JS998.72kB/gzip321.81kB，仅既有大chunk和LF/CRLF提示。
- 最终完整命令：`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`；任务oQmXjK已实际等待并收集，退出0，**113 passed in181.46s**，0失败/跳过（原72＋新增41）。原5项轨迹反例增加hypothesis断言不增加测试计数，完整真实Chromium/重启/迁移/事务并发回归均执行。
- 实测SHOW server_version为17.11，仍用logscope-01-test-pg17/127.0.0.1:55433；每例新schema真实迁移head0006，旧迁移回归照常。测试业务HTTP由FastAPI TestClient运行，UI/重启验收另用真实Uvicorn网络HTTP；不混称所有用例均通过TCP。仅安排受控事件输入，无真实日志或Agent质量声明。
- 结束后独立PowerShell Get-NetTCPConnection及Get-CimInstance核实8000/5173监听0、限python/pythonw/node的Uvicorn/Vite残留0，退出0；fixture finally terminate/wait，任务均已收集，无detach、无强停未知服务。全部schema及原PG保留，未碰开发库。
- 本次增量仅conclusions.py、test_conclusions.py、test_attempts.py一行夹具隔离、05 Answer及工程既有当日记忆；保留开始时所有05未提交内容，无其他业务/规格/工单修改，无新报告/地图。HEAD仍fd3b9c6，未commit/push。

### 2026-11-02 Standards轴及最终独立验证

- 重新读取AGENTS指向的issue-tracker/triage-labels/domain规范、GLOSSARY、README、spec及05 Answer；未发现额外CODING_STANDARDS/CONTRIBUTING或ADR。基点解析为fd3b9c6570e29b2ac5171b8378cb133a87b8d57d，HEAD无新增提交；审查`git diff fd3b9c6`全部8个tracked文件及3个untracked（conclusions.py、0006迁移、test_conclusions.py），不是仅审HEAD三点差异。无可调用Agent工具，直接进行独立复核，不宣称并行子代理审查。
- Standards finding：**0项新增已核实P1/P2，0项需修复的文档规范违反**。领域词汇区分已完成、未确定根因、待验证假设与核验状态；假设最低支持修复符合GLOSSARY已有依据要求。对照12类异味逐项判断，规范优先，不把启发式当硬规则或借机无关重构。
- 非阻断判断：conclusions.py86–121的hypothesis最低依据与confirmed因果充分性是不同规则，不应因分支相似强行合并为统一确认门槛。evidence.py68–69与conclusions.py93–97有有限错误码枚举重复（可能Duplicated Code），前者为严格外部观测契约、后者为包含显式依赖4xx的候选规则，现有矩阵覆盖其有意差别，本轮不抽象。conclusions.py62–66与证据写入的锁定形状重复是安全边界一致，不足以认定Feature Envy/Shotgun Surgery；UUID及受限Literal已经表达本轮概念，未发现需新增包装类型的Primitive Obsession/Data Clumps。SQLAlchemy查询链是DSL而非跨领域Message Chains；未发现有实际影响的Mysterious Name、Repeated Switches、Divergent Change、Speculative Generality、Middle Man或Refused Bequest。
- 安全复核：独立Bearer先于材料处理，固定安全校验/异常输出；严格causal_trace枚举无自由文本输出；假设只接受选中本轮适当事件支持，正常事件/NONE/UNKNOWN及未选支持不能背书，且不要求完整轨迹；确认必须有选中同调用传播支持并扫描全轮未处理轨迹/不可用提示。调查→轮次/执行锁序、版本CAS、追加历史、四阶段回滚、正常结束valid=false及结束请求只读幂等重放一致；不以重放恢复执行，不泄漏公共执行身份/凭证。受控生产者观测声明仍不证明真实来源或Agent质量，18/19和完整退出协调不提前认领。
- 本轮最终完整命令：`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，任务8LcUWH已等待并收集，退出0，**113 passed in190.78s**，0失败/跳过。包含41项最低假设依据回归、真实迁移/并发/原子回滚/重启及真实Chromium UI；后端主测试为HTTP TestClient，UI/重启另外走真实Uvicorn网络HTTP，不混称全部为TCP。
- 本轮`npm run build && git diff --check`任务8mM4yV已收集退出0，vue-tsc -b及Vite成功，3294模块、2.44s、JS998.72kB/gzip321.81kB；仅既有>500kB chunk与LF/CRLF非阻断提示。记录追加后再执行diff检查。
- 实测测试PG `SHOW server_version`=17.11，仅使用既有logscope-01-test-pg17/127.0.0.1:55433；fixture每例创建全新schema并真实迁移，UI自动新schema/API/Vite/Chromium。没有执行开发库迁移或drop，全部schema与原PG保留。全测后独立PowerShell核实8000/5173监听0、限定python/pythonw/node的Uvicorn/Vite残留0，退出0；fixture finally管理停止，无detach、无强停未知服务。
- 本轮仅追加05 Answer及既有当日日志，未修改业务/测试/配置，未新增文件或无关重构，保留全部既有未提交实现。没有新核实P1/P2，故没有虚构本轮TDD红绿或再次修改已修复规则。05保持resolved，HEAD仍fd3b9c6，未commit/push。
