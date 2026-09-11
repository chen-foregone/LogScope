# 08: 限制单轮总时限并协调终态竞争
Status: resolved
Type: task
Blocked by: 06, 07

## What to build
纵向交付持久总预算与退出协调 schema、受控期限/结束 API、原子终态裁决及 UI。总时限包含查询、重试和等待，执行安全确认未完成时不提前释放名额。

## Blocked by
- 06 处理查询故障、有限重试及部分来源缺失
- 07 取消调查并隔离迟到产物

## Spec
[首版实施规格](../spec.md)：D2、D3、D8。
用户故事主要交付：US-09；US-47 期限与终态竞争部分，关联 US-11、US-18。
验收组主要交付：A09；A15 取消/超时/完成部分，失联交错10补全；A26 终态变化。

## 开工门槛
退出协调协议必须明确：停止安排、隔离确认、原子裁决事件与合法结果、名额释放及可观察验证方式。原needs-info门槛已由本单Comments明示设计契约及implement08授权解除。未批准取消总优先或超时总优先，不硬编优先级；若设计改变既定终态业务含义才需另行确认。例行协调算法可在授权内设计。

## 验收标准
- [x] 从单轮启动起总时限10分钟，包含原始查询、最多额外2次重试及2秒/5秒等待和其他等待，不因重试刷新预算。
- [x] 达到时限停止安排新操作并走失败处理；不再为已耗尽预算的新查询启动完整30秒等待。
- [x] 旧执行停止或隔离尚未确认时仍保持未安全结束的可见阶段、占名额且不可重开；不能用到时限猜测旧执行已停止。
- [x] 对按明示裁决协议归属超时退出的轮次，安全确认后原子落定失败与超时原因；已合法结束的轮次不被超时覆盖，持久保存既有证据，名额只释放一次。
- [x] 控制完成、取消与超时在提交前后交错，最终只有一个符合明示协议的合法终态，无双释放、双执行或迟到证据/结论/等级写入。
- [x] UI/API 可理解地显示退出等待和最终原因，业务状态变化更新最后更新时间；刷新不改变裁决结果。
- [x] 不把正常证据不足变成失败；失联事件交错由10纳入相同裁决协议，不以任意优先级替代业务约束。

## 测试与完成证据
HTTP API + 真实 PostgreSQL，配可控时钟、调度、查询与确认适配点，保存10分钟边界、等待预算、隔离未确认和交错事件外部轨迹、状态和UI证据。不得仅测内部计时函数；10补A15失联矩阵，19验证实际执行身份。完成贯通 schema/API/UI 与竞争测试。

## Comments

2026-11-03 implement08 明确授权，基点 master `9907365`，工作区干净。已读 AGENTS、docs/agents 三规范、README/GLOSSARY/spec、06/07/08 和现有执行/写入/查询/取消/UI及完整相关测试；无 ADR。已加载 tdd，沿已批准 HTTP业务API＋真实PG17、可控时钟/调度/来源/退出适配和少量真实Chromium seam，逐项red→green，不批量先写测试。无可调用Agent工具，不冒称独立子代理；两轴独立审查留主代理。

明示实施算法与可验证契约（例行设计授权内，不新增业务优先级）：

1. 新轮使用同一服务端clock固定 `created_at`（正式启动时刻）与 `deadline_at=created_at+10分钟`，日志范围与运行预算独立。重试、重复启动/旧键、来源等待均不续期。旧轮从已有created_at推导deadline，不倒填stop_protocol或退出证明，历史终态不改。
2. 所有裁决沿调查→轮次/执行→来源闸门的短事务锁顺序；锁内取当前clock。调查中且尚无停止归属时，`now>=deadline_at` 才能取得timeout归属并持久timeout_requested_at、valid=false与updated_at。截止前合法完成提交胜出则终态不可覆盖；截止前cancel已取得归属则保持用户取消，即使退出超过deadline；截止后尚无归属的cancel/完成不得抢成取消/成功。非法身份不允许触发他轮裁决。没有“超时总优先”或“取消总优先”。
3. 证据、结论（含重放）、查询接纳和进度/结束边界均锁内重验deadline；发现到期先提交持久写隔离，再拒绝业务请求，不能因HTTPException回滚丢失超时请求。GET仅读（执行上下文过期可拒绝但不写），不靠访问触发到期。
4. lifespan启动可控定时协调器，每轮扫描到期/待退出轮次，逐调查短事务try-lock/skip-locked，跨实例重复轮询幂等；无I/O持锁、不启动新查询，不自动续跑。空闲已知controlled_queries_v1且无active查询可确认；未知协议、失联保守active或适配器仍清理时保持调查中/timeout_stopping、不清闸、不启保留期。DB不可用不推测退出；恢复仍限10。
5. 查询实际deadline=min(30秒,剩余总预算)，重试/Retry-After等待也被总预算监视截断；每次实际调度前及返回后复验。保留07真实task退出、shield/重复cancel防护，停止后吞取消结果不纳入；晚到来源限流仍保存，不借超时解除Retry-After。超时请求提交后立即拒绝旧写；只有实际await确认查询退出才清所属active/闸门，确认事务失败仍保守在途。
6. 确认已知协议且无active后，按已持久归属唯一落定：timeout→失败/end_reason=attempt_deadline_exceeded/result=null，cancel→已取消/user_canceled；保存stop确认事实、ended_at、updated_at与结束+30天，材料不删除。运行态唯一约束持续保留；11尚无全局容量表，不虚称验证释放计数。
7. UI显示固定deadline、超时等待退出及最终失败原因；超时不显示用户主动停止或正常结束。保留导航读序列与取消归属保护，不加入自由终态入口。不扩大09/10/11/18/19，不碰开发库；完成只追加本单Answer，保持claimed、不commit/push、不写工作日志。

## Answer

### 实现及可部署契约

- 基点`9907365`，保持claimed，未commit/push，未写工作日志或其他工单。新增`0010_attempt_deadline.py`、`app/deadlines.py`和`test_deadlines.py`均为必要迁移/实现/验收文件，无额外报告/ADR。主代理仍须两轴独立审查，不以本次自查代替独立审查。
- 启动事务使用注入的服务端clock保存created_at与deadline_at=启动+10min；查询重试、等待、幂等重放不续期。0010从0009升级，旧轮deadline按原created_at+10min推导，终态/材料不改；不倒填stop_protocol、timeout归属或退出确认。已过期且协议未知的旧运行轮被隔离但仍调查中/timeout_stopping，不能假认旧执行已退出。
- `arbitrate_deadline`只在调用方持调查/轮次锁的短事务内裁决；已合法终态不变，截止前已持久cancel归属不被timeout夺走，截止后无归属的完成/取消只能取得timeout停止归属。所有写入身份仍绑定调查/轮次/执行；未增加公共自由结束/确认入口。结论完成旧键在合法已完成后仍可只读重放；取消/超时旧产物重放拒绝。
- 证据/结论/查询接纳与查询进度及结束重验期限；到期保存timeout_requested_at、valid=false、updated_at，HTTP403不使隔离回滚。确认使用savepoint：确认事务失败保留超时归属/隔离，503提示未确认；取消入口同样处理到期确认失败，不将其退回running。隔离自身数据库失败仍503，不伪造持久成功。
- 默认lifespan协调器每100ms等待一轮（非硬实时保证），通过线程执行短数据库事务；按调查FOR UPDATE SKIP LOCKED跨实例幂等，锁竞争留下一轮，无I/O跨锁。无需用户请求也能处理空闲到期；GET调查/列表/轮次仍只读，执行context/accepted过期仅403不写。关闭lifespan取消等待器，await正在运行的数据库协调轮再dispose，不detach。
- 同一查询固定monotonic剩余上界，同时每次调度从持久deadline与clock重算剩余；实际原始查询min(30,剩余)，重试/Retry-After等待min(来源或2/5等待,剩余)，监视也按clock/monotonic总界截断。包括来源吞取消后的真实清理：沿07 wait_for_exit/shield首次cancel并等task真正退出，重复cancel不能冒充完成。晚到rate_limited与Retry-After仍持久到所属来源闸门，不借超时绕限流。
- active在途或未知stop_protocol时仍调查中/timeout_stopping、ended_at/stop_confirmed_at/retention为空；不清所属query闸门、不允许新轮，现有同调查唯一运行约束仍成立。只有查询处理实际await退出并提交active=false，且已知controlled_queries_v1无其他活动，才确认失败/result=null/end_reason=attempt_deadline_exceeded，stop确认、ended_at、updated_at与结束+30d原子写入，材料/历史保留。退出提交失败保守active留10处理，协调器不凭时间清active。11尚无全局容量表，不声称验证了全局名额释放计数。
- UI显示固定单轮截止时间与共享预算、timeout_stopping等待退出/占位/不能新轮、最终10min失败原因，不误显示用户主动停止或正常排查结束。保留已有readSequence和取消归属导航保护，无新增轮询写API；显式刷新读持久事实。
- 部署须停止旧版写入、备份调查产物后，用正确DATABASE_URL与PYTHONPATH=backend运行`.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head`到0010，再启动配套API/UI。新旧实例不混跑；本次只操作PG17隔离schema，开发库未迁移。

### 逐项TDD与补充验收记录

统一前缀`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest`，HTTP业务API＋真实PG、外部clock/schedule/source/退出控制；SQL仅安排旧schema或PG故障，不窥表断言业务事实。

1. 固定启动期限：sqwnWA **1 failed in2.57s**（启动未用clock）→6ZdA1g **1 passed in2.77s**。
2. 到期证据隔离持久与安全终态：nVf2M6 **1 failed/1 passed in3.93s**（迟到201）→cqwtJm **2 passed in3.62s**。
3. 完成在deadline不可抢成功、已提交完成保持终态/重放：L31olS **1 failed/2 passed in5.18s**（过期完成201）→u9cZ65 **3 passed in4.99s**。
4. 到期cancel不得抢取消、未知执行协议保持等待：gW4EIP **1 failed/3 passed in6.67s**（误stopping）→Gby8zp **4 passed in6.39s**。
5. 空闲无HTTP请求到期及GET只读：Lgbjtf **1 failed/4 passed in8.40s**（缺deadline_schedule）→FxSGoH **5 passed in7.85s**。
6. 在途到期通知真实清理，清理前不终态/不保留期：3Xxx43 **1 failed/5 passed in39.94s**（仍等30s）→OoaPoH **6 passed in9.46s**；后参数化截止前cancel已归属、清理跨deadline保持已取消。
7. 临近deadline不能启动完整30s：tNzbRa **1 failed/6 passed in11.39s**（实际调度30而非剩余3）→uDW7Sz **7 passed in11.43s**。剩余期望改为0<实际<=3并留正常调度开销容差，不将精确浮点3当业务规则。
8. 重试等待截断：bVw5a0 **1 failed/7 passed in13.22s**（剩余1仍安排2）→EALkJM **8 passed in12.68s**，无第二次来源调用。
9. 真Chromium/UI：WXBS9d **1 failed in28.10s**（缺截止文案）→ntJtJu **1 passed in24.60s**。真实API/Vite/PG，来源触发外部clock跨10min、真实清理3秒，页面刷新观察等待、禁用取消、确认后失败与导航离开；不是伪造业务响应。
10. 执行context过期只读拒绝：G9DrhB **1 failed in2.62s**（200）→3rW9tt **14 passed in21.86s**。
11. 确认失败持久隔离：j7p0dg **1 failed/16 passed in28.44s**（取消入口确认失败回滚成running）→savepoint保留隔离，tOEn4y **35 passed in54.65s**（当时17项08＋18项07全部）。原07取消事务失败整体回滚仍通过，不改变其语义。
12. 补强重试后clock已消耗预算：0De1KQ **1 failed in2.73s**（第二次仍30，虽监视已截断）→每次从同一deadline重算min，KpuHDp **21 passed in34.78s**。

补充回归非虚称逐例先红：证据/结论/查询旧键过期拒绝且历史不变、PG确认除零持久隔离、未知执行器取消归属、0009真实升级不伪确认、两个API协调器与过期完成/取消竞争、总期限退出晚到限流阻断另一调查、真实剩余0.2秒deadline不推进clock也退出且无重试。实际源调用/返回与公开API事实作断言，适配点只控制外部时间/调度。

### 中间完整结果及兼容说明

- 后端回归YP91kn **162 passed/2 failed in300.87s**：原0004/0005升级测试旧轮已过期，还要求继续写201。明确将两例原证据/结论兼容测试clock固定在原轮启动时刻，保留其身份/历史/版本断言；新增08旧轮真实到期迁移测试覆盖保守隔离，不通过豁免旧轮deadline“修绿”。7MtYSi定向 **15 passed in27.75s**。
- JqBB5z **20 passed in32.62s**；首次完整iCrqVB **192 passed in384.21s，退出0**，之后发现并先红修复第12项预算重算，再做最终完整复跑，192不是最终计数。
- 构建wvex37：根目录`npm run build && git diff --check`退出0，vue-tsc/Vite成功，3294模块、2.69s、JS1002.56kB/gzip323.21kB，仅既有>500kB chunk与LF/CRLF提示；未做无关拆包。

### 最终完整验收与服务收尾

- 最后预算重算修复后，L7MMyI完整运行`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost TEST_DATABASE_URL=<PG17测试库连接> .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`：**193 passed, 1 warning in 387.69s (0:06:27)，退出0**。测试连接为127.0.0.1:55433/logscope_test，仅隔离schema；开发库未操作。后台结果已完整收集。
- 唯一warning来自`test_concurrent_finish_idempotency_and_version_guard`：Pydantic `UnsupportedFieldAttributeWarning`，`Field(alias='investigation_id')`在该上下文无效；如实保留，未屏蔽，也无失败或跳过。
- 最终根目录构建与差异检查4aYf65：`npm run build && git diff --check`完成，退出0；vue-tsc与Vite成功，3294模块，Vite 2.56s，JS1002.56kB/gzip323.21kB。仅>500kB chunk提醒与Git LF/CRLF提示，未扩大范围拆包或改换行策略。
- 完整测试后独立PowerShell核验：8000/5173监听数**0**；命令行同时包含本项目log-scope和uvicorn/vite的python/node服务进程数**0**。测试API/Vite已随fixture退出，无detach；PG17测试容器保留供后续独立审查使用。
- HEAD仍为`9907365`，改动仅本工单及必要schema/API/UI/测试文件。保持claimed、未resolved、未commit/push，未写工作日志；主代理后续进行Standards/Spec两轴独立审查。

### 独立审查2修复（2026-11-03）

保留收到任务时全部08未提交实现，继续保持claimed；只修改本单Answer及deadline相关实现/测试，不resolved、不commit/push、不操作开发库。加载tdd；沿用户本次明示的HTTP＋真实PG事务内时钟、可控调度／API生命周期seam执行。无可调用Agent工具，不冒称额外独立审查。

#### P1：最终裁决与精确契约

- 修复入口验时后多SQL／分析跨deadline仍接收产物的问题。调查／轮次锁在外层事务中持有；业务暂存写入位于savepoint内；业务SQL全部完成后重新调用当前clock。最终采样到期则回滚该savepoint（证据、结论、完成状态、取消归属、不可用提示、来源闸门及查询接纳／进度／结果写入均不残留），再在仍持锁的外层事务保存timeout归属和执行写隔离，独立savepoint尝试确认退出；403或确认失败503不回滚隔离。
- 取消最终采样晚到时撤销本请求尚未持久化的cancel与已取消终态，改归timeout；之前事务已合法持久化的cancel归属继续保留。证据／结论／查询重放在查询历史之后再次复验，拒绝入口尚未到期但查找期间到期的旧键；已经合法完成的只读结论重放仍不被重新分类。
- 对Comments40的“截止前合法完成提交胜出”作必要精确定义：不是首次入口验时胜出。可实现的线性化点是**持调查／轮次锁、业务SQL已完成、释放业务savepoint及外层COMMIT之前的最终当前clock采样**；只有该点严格早于deadline且该事务随后成功提交，才是合法完成／取消落定。采样到期（含相等）不能胜出；数据库提交失败不算成功。最终采样后不再分析或写业务SQL，仅释放savepoint和COMMIT；取消响应的只读投影也不改变已裁决归属。
- 应用时钟采样与PostgreSQL实际COMMIT不能原子化；**不保证任意网络、调度、COMMIT落盘延迟下实际墙钟提交仍早于deadline**。即使最终采样599，随后COMMIT延迟到601，也不能声称实现了物理截止前提交。当前契约以短事务最终裁决点而非入口点判合法，成功提交后保持唯一归属；非硬实时保证、不暗改为首次验时胜出，也不复用旧now绕过期限。

#### 逐项红绿与补强证据

命令前缀同上；所有数据库URL由现有fixture指向127.0.0.1:55433/logscope_test独立schema。SQLAlchemy数据库事件只驱动真实SQL执行时外部clock跨界或PG除零故障；业务断言均经HTTP GET/POST，不窥表验证。

1. 完成INSERT前599→601：RxUEB0 **1 failed in3.70s**，实际201而非403；加最终采样／savepoint回滚后WXKeSa **1 passed in3.21s**。后参数化补充非finish结论和最后UPDATE才跨界，验证已暂存完成／产物全部撤销。
2. 证据INSERT、非法证据布尔提示UPDATE、cancel归属UPDATE跨界：MoJ0Te **3 failed in6.08s**，分别迟到201、422、已取消；应用最终复验后先暴露测试误用未公开字段的KeyError，读取实际API投影改用evidence_notices，未通过窥表或放宽业务断言修绿。GgGA05 **28 passed in46.19s**包含跨界回滚与重放全部覆盖。
3. 历史查找期间跨界：dwS4hQ暴露查询重放仍200（证据／结论同类已通过）；查询接纳和重放增加相同最终裁决。补充确认阶段PG除零：证据／结论／取消均503、timeout_stopping及隔离持久、暂存产物为空；补充查询INSERT跨界不启动来源、无新查询残留。这些为修复后补强，不虚称每个参数逐例独立先红。
4. 查询最后active=false写入才599→601：mxwSQe **1 failed in3.18s**（迟到201）→进度及最终结果事务同样加savepoint和最后复验，uRbiwl **1 passed in3.04s**，HTTP403、最终失败且查询timed_out，不把已回滚的finished结果当成功。

#### P2：真实线程退出与dispose顺序

- 复用DeadlineSchedule适配点获取外部可取消的协调task，真实PG扫描轮通过数据库事件阻塞；从API `app.router.lifespan_context`启动和关闭，外部连续cancel三次。918XEw **1 failed in3.21s**，数据库轮未释放时coordinator已done，准确复现审查问题。
- 修改coordinator：首次CancelledError后循环shield等待running真实返回，重复cancel仅继续等待；不cancel to_thread包装task、不detach。取回线程异常后再传播原始cancel，lifespan既有await随后才能dispose。CMqOPw **1 passed in2.35s**：阻塞时coordinator和lifespan关闭均未done且无dispose；释放真实数据库轮后才cancelled，engine_disposed观察到轮已退出。9jAija期限专题 **29 passed in46.44s**。

#### 最终验证记录

- 主代理原ZzaSsx输出已收集：**193 passed in374.68s，退出0**；待其结束并确认8000/5173无监听后才启动本次含UI完整回归，未抢占端口。
- C39b95后端专测：**192 passed in370.35s，退出0**；7CIlYy完整后端＋UI：**208 passed in434.48s，退出0**。两次均为中间结果，因运行期间补强了查询最终落定测试／实现，另冻结最终实现复跑，不将中间结果冒作最终冻结验收。
- dPFMWH根目录`npm run build && git diff --check`退出0，vue-tsc／Vite通过、3294模块、Vite3.08s；JS1002.56kB/gzip323.21kB。仅既有大chunk、构建回调耗时与LF/CRLF提示，不做无关拆包。
- **最终冻结完整验收sfeX2H**：`.venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`（同上PYTHONPATH／NO_PROXY前缀）输出 **209 passed in407.99s (0:06:47)，退出0**，无失败、跳过或warning汇总。所有后台输出已完整收集，不以启动通知代替完成证据。
- **最终构建5ThI3N**：根目录`npm run build && git diff --check && git status --short && git rev-parse --short HEAD`退出0；vue-tsc／Vite成功、3294模块、Vite3.44s、JS1002.56kB/gzip323.21kB；仅上述既有提示。HEAD仍9907365，未新增其他工单修改，全部原有未提交文件保留。
- 完整测试结束后PowerShell核验8000/5173监听数 **0**，命令行含本项目log-scope及uvicorn/vite的python/node服务进程数 **0**；未detach或停用他人服务，PG测试容器保留。

### 主代理最终验收（取代前文待审查状态）

- 两轴独立审查：Standards无明确硬违规；预算组合存在非阻断重复异味，未扩大重构。Spec发现事务内跨截止仍落定及协调器重复取消提前关闭两项问题，均实际先红后绿修复；独立复审两个旧阻断均闭环，无同范围剩余阻断，另实跑25项定向HTTP/真实PG及生命周期回归通过。
- 主代理冻结后独立全量任务 e7ZCtu：**209 passed in400.09s，退出0，无失败/跳过或warning汇总**。相对07基点171新增38项，包含37项期限/竞争后端测试及1项真实Chromium UI。
- 主代理最终根目录构建95DY2y：`npm run build && git diff --check`退出0，vue-tsc/Vite成功，3294模块，2.77s，JS1002.56kB/gzip323.21kB；仅既有chunk与LF/CRLF提示。
- 裁决精确语义遵循上文最终采样线性化契约，不承诺物理COMMIT硬实时截止；真实退出未知时不提前失败或释放运行资格。开发库未迁移，不实现09/10/11，工单08据此resolved，更新事项地图和当日项目日志后在当前分支本地提交、不推送。

### 范围限制

- 受信适配器必须异步非阻塞、合作式取消且不遗留子任务；永不退出则持续等待，不把总时限当物理退出证明。100ms监视不是硬实时保证，DB故障/执行失联/退出提交失败保守保留active，由10的恢复协议处理；没有自动续跑或重开。
- 未实施09重开、10失联恢复、11容量、18真实来源、19真实Agent；严重程度执行写入口仍未开放，不声称新增等级写入能力。HTTP主层为TestClient/ASGI＋真实PG，不混称全部TCP；UI才由真实Uvicorn/Vite/Chromium走网络。仅实际接口范围内保证迟到隔离，真实执行安全仍需18/19验收。
