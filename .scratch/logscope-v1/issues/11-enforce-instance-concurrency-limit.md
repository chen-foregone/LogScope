# 11: 执行实例级三调查并发限制
Status: resolved
Type: task
Blocked by: 10

## What to build
纵向交付实例名额一致性 schema、事务化启动/释放业务 API 和满额 UI 提示。实例最多同时运行3个调查，同调查最多一轮，不建后台排队功能。

## Blocked by
- 10 识别失联及服务中断并安全结束旧执行

## Spec
[首版实施规格](../spec.md)：D2、D3、D8。
用户故事主要交付：US-17；关联 US-16、US-47。
验收组主要交付：A06；A05、A11、A13 的名额联动回归。

## 开工门槛
无额外未知输入，遵守10依赖并沿用已明确的执行终止与隔离契约；名额持久化、事务隔离算法属于授权内实现选择。实例限制不能退化成每进程各3个。

## 验收标准
- [x] 多调查并发竞争同一实例容量，最多3个调查中，多个业务进程也共享该上限。
- [x] 满额请求明确返回未启动及稍后重试，UI显示原因；不排队、不创建貌似运行的新轮。
- [x] 同调查并发启动最多一轮及一次名额，事务失败不泄漏名额或留下假运行记录。
- [x] 正在停止、正在确认执行状态及超时隔离未确认均占名额，不能提前释放让第4个执行进入。
- [x] 合法安全终态只释放一次；结束、取消、失联及重放竞争不重复释放，释放后须用户重新请求启动，不自动消费原满额请求。
- [x] UI/API与持久状态一致，重启不重置容量事实或自动续跑，满额拒绝不伪造业务更新时间变化。

## 测试与完成证据
通过 HTTP API + 真实 PostgreSQL 发起多客户端/多进程竞争，控制执行停留和确认，观察实际同时执行数、拒绝结果及释放后手动重试。提供事务与UI证据，不以内部信号量单测替代实例限制；20汇总端到端。完成贯通 schema/API/UI 与竞争验证。

## Comments

2026-11-03 实施11，基点624c7a0工作区干净，关联主任务132/实施133。已读AGENTS、docs/agents、GLOSSARY、spec及attempts/cancellation/deadlines/interruption协议；无ADR。已加载tdd及tests/mocking；用户已批准HTTP业务API+真实PG、真实Chromium、多进程/迁移/重启seam，不再重复确认。无可调用Agent工具，不冒称独立代理审查。

设计：新增0013数据库持久单行接纳互斥锁，不新增可漂移的名额计数。启动沿调查锁→实例接纳锁顺序；其他终态路径仍调查→轮次→来源闸门，不反向获取实例锁。仅新启动持实例锁，获得锁后的独立SQL在READ COMMITTED新快照统计attempts.status=调查中（包括stopping/confirming/timeout），不足3才同事务插轮/接纳/更新。终态只改变现有真实status，不维护第二份释放事实；与启动竞争时未提交终态被保守计入，提交后下次手动请求才可用，不会超发。所有新增启动被实例锁串行，回滚释放锁且不留下轮次；同调查重放先检查，不因满额破坏幂等。明确固定启动事务READ COMMITTED，防止等待锁前快照漏计。满额409文字明确未启动、不排队、稍后手动重试，拒绝无业务更新时间写入。

迁移不改旧running/执行身份/退出事实，旧运行即使已超过3也原样保留且阻止新接纳，未知退出继续占位。升级必须停旧版本业务与写入、备份后迁移并整体换新版本，禁止旧版本混跑；不以迁移虚构终止或自动释放。仅测试55433隔离schema，全部保留；不迁移开发库。逐片red→green后全量与根构建，追加准确Answer并保持claimed等待主代理独立审查，不commit/push/地图日志，不实施12+。

## Answer

### 实现与约束

- 基点624c7a0，工单claimed，关联132/133；不commit/push/resolved，不改地图/日志，不实施12+，待主代理独立审查。无可调用Agent工具，不冒称独立审查通过。
- `backend/migrations/versions/0013_instance_admission.py` 新增只有id=1的持久锁行，无名额人工计数；不改任何旧运行/身份/退出事实。部署前必须停止旧版本业务写入、备份并迁移，再整体运行新版本；旧版本绕过接纳锁，禁止混跑。此是升级前置约束，不能声称数据库能识别并拦截任意旧版本程序。
- `backend/app/attempts.py` 启动显式READ COMMITTED，调查行锁→实例锁，锁等待后独立SQL统计所有调查中attempt（包含停止/失联/超时未确认）。同调查幂等重放和非法输入先处理；不足3才插轮、接纳、更新，同事务提交/回滚。满额409明确尚未启动/不排队/稍后手动重试；无轮、无updated/保留期变化。锁行不存在会失败封闭，不偷偷降级为无限容量。
- 正常完成、取消确认、超时确认、可靠失联确认沿既有协议唯一改变status，即释放唯一事实，不增加可漂移的release计数/事件；重复终态不能凭空多放名额。终态不取实例锁，不形成实例→调查反向锁序；未提交结束暂被保守计入，稍后手动请求才使用。实例锁仅串行短接纳事务，不是后台任务队列。
- `frontend/src/App.vue` 启动表单显示最多3、过渡阶段占位、满额不排队及手动重试；既有错误显示原样呈现满额原因，409清除不确定启动状态，不自动再发请求。
- `tests/conftest.py` UI隔离改为逐例schema及API/Vite生命周期，避免原会话共享数据库的UI用例将未结束轮次不断累积后互相满额。仍是真实迁移、PG、API与Chromium，finally回收进程，schema保留不删除；不通过清空业务表/自动结束假释放来绕过容量规则。

### 红绿及补强证据

统一测试环境使用55433测试PG、PYTHONPATH=backend、PGOPTIONS=-c max_parallel_workers_per_gather=0及大小写NO_PROXY；业务断言经HTTP（TestClient或真实Popen TCP）。

1. 容量API切片：UA4HId红灯 **1 failed/14 deselected in3.74s**，第4调查错误201而非409；新增0013及接纳锁/事务快照后0QVCD0绿灯 **1 passed/14 deselected in3.00s**。覆盖满额无轮/时间变化、同调查重放200、新key重复409、取消后不自动启动、手动重试201、旧终态重放不重复释放及重建应用仍满额。
2. UI切片：GgaTLC真实Chromium红灯 **1 failed/64 deselected in37.16s**，缺实例容量提示；新增文案后aWzlvc **3 passed/78 deselected in32.52s**，包含UI及API容量、真实跨业务进程竞争。UI满额有原因且表单可操作，释放后刷新也不启动，必须点击；真实双进程8个不同调查Barrier竞争恰3个201/5个409，读取持久事实3调查中，拒绝记录原样，第三服务重启仍拒绝。所有Popen仍活时验证，finally再回收，不用进程退出诱发释放来制造通过。
3. LSTevw启动专项 **21 passed in50.20s**，新增三阶段占位（stopping/timeout_stopping真实在途适配器await，confirming_execution真实活owner及其随后退出）、接纳真实PG除零回滚不泄漏最后名额、0012四个旧未知运行升级0013保持四轮占位、重建应用无虚构停止。补强项未逐个出现新红灯，不冒称独立TDD红灯。
4. HXOtip真实PG锁快照补强 **2 passed/21 deselected in4.42s**：两个独立API应用，先事务接纳后暂停，后请求已到实例锁且pg_blocking_pids实际证实等待；先事务提交则后者409，真实PG除零回滚则后者201，均最终恰3轮调查中，无假轮。不是只用线程起跑概率推断数据库互斥。
5. xwdTPC根目录 `npm run build && git diff --check` **退出0**，vue-tsc/Vite通过，3294模块、3.17s、JS1007.94kB/gzip325.11kB；仅既有>500kB chunk与LF/CRLF提醒。

### 冻结全量与风险

首轮冻结全量TsengG：**341 passed/27 setup errors in1488.06s (0:24:48)**。全部错误为逐例UI fixture检查8000端口时仍短暂connect成功，未有业务断言失败；全量结束后PowerShell只读核验8000/5173无监听，无本次uvicorn/vite/pytest残留，未停止未知进程。新增有上限10秒的端口关闭检查，每次socket超时0.1秒，不复用未知服务、不sleep循环。完整UI VduhuR **62 passed/3 failed in1254.37s**，端口setup错误已消失；剩余3项为原两处httpx.Client默认5秒ReadTimeout（创建/启动HTTP尚未返回）。将这两处统一为其他UI已用trust_env=False、timeout=20，不改业务期限/断言/重试；5kYTSw定向 **3 passed/62 deselected in50.82s**。第二次冻结ltlt9d **367 passed/1 failed in1716.36s (0:28:36)**，唯一剩余是cancel_navigation另一处httpx默认5秒创建ReadTimeout。统一剩余同型客户端为20秒后aJy0YT **1 passed/64 deselected in15.36s**。**最终冻结kTXMDS：368 passed in1674.38s (0:27:54)，退出0，无失败/跳过/warning汇总。** 相对358基点增加10项：后端容量/多进程/三过渡阶段/接纳回滚/旧版升级/锁等待提交回滚共9项，真实Chromium容量1项。实现及测试在该次启动后未变，完整输出已收集。最终PowerShell核验8000/5173监听数0、pytest/uvicorn/Vite测试进程数0；没有detach或停止未知服务，全部受控Popen finally回收。开发库未操作，schema及个人目录未删除。最终构建fXdIrZ `npm run build && git diff --check`退出0，vue-tsc/Vite通过，3294模块、3.65s，产物大小同前，仅既有chunk/构建回调耗时/LF-CRLF提醒。工单保持claimed，待主代理独立审查及其最终本地提交。

容量范围是同一业务schema/数据库实例；不同独立测试schema有意隔离。部署不能将同一业务实例拆到不同schema以绕开共享锁。旧超额运行保持超额事实并停止新接纳，不主动杀执行；未知/外机/旧执行退出仍沿10保守等待。执行安全只继承既有本机Windows controlled_queries_v1，不宣称交付19真实Agent或任意远程执行恢复。容量锁可能造成短事务接纳等待，这是并发事务互斥，不保留满额请求也不建立后台业务排队。

### 最终独立审查与交付

2026-11-03 主代理核对实际差异、0013迁移及最终交接，独立Standards与Spec双轴审查均0阻断问题。Spec审查另用项目虚拟环境真实复跑容量专项9 passed/14 deselected in24.35s、Chromium容量专项1 passed/64 deselected in17.60s；首次系统Python缺pytest未启动测试，不计为通过证据。独立审查未冒称复跑全量，全量依据实施代理已收集冻结368项输出。主代理git diff --check退出0，8000/5173监听0。验收完成设resolved，更新地图并进行当前分支本地提交；不push，不迁移开发库。前文claimed/待审均为历史过程，以本段最终状态为准。
