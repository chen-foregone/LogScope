# 10: 识别失联及服务中断并安全结束旧执行
Status: resolved
Type: task
Blocked by: 09

## What to build
纵向交付失联确认与中断事实 schema、受控协调API、服务重启行为及UI阶段；确认旧执行不能运行或写入后才安全结束旧轮，不自动续跑。

## Blocked by
- 09 手动重开调查并查看逐轮历史

## Spec
[首版实施规格](../spec.md)：D3、D8。
用户故事主要交付：US-18、US-19；US-47 失联隔离部分，关联 US-36。
验收组主要交付：A13、A14；A15 加入失联的最终交错验收（承接08）；A26 状态变化、A32 重启历史部分。

## 开工门槛
须明确真实执行存活判定、终止确认、旧执行不能继续运行或写入的保障协议，以及业务服务与执行实例不同重启情形的验证方式。当前尚未选定，needs-info 待具体设计与受控验证条件齐备解除；不能用页面连接、心跳超时或仅重启业务服务替代确认，不将执行方案称已批准。

## 验收标准
- [x] 识别执行失联后显示调查中／正在确认执行状态，继续占名额并拒绝新轮；失联信号本身不直接宣布旧执行停止。
- [x] 仅确认旧执行不能继续运行或写入后，将服务中断影响轮次记失败，保留已保存证据及明确中断原因，释放一次名额并允许手动新轮。
- [x] 旧执行在确认前后迟到的证据、结论、等级写入均按有效身份隔离；重放或旧进程恢复也不能污染新轮。
- [x] 页面刷新或断连不改变业务运行状态，不作为执行失败或停止证明。
- [x] 业务服务重启后调查及多轮材料可见，不自动续跑、不自动新轮；不能仅凭重启认定外部旧执行终止。
- [x] 失联、取消、10分钟超时、正常完成交错只产生一个合法终态，无重叠执行或双释放；按08明示协议验证，不另设未经批准的终态优先级。
- [x] 中断阶段与结束事实持久化，UI/API一致且可追溯；状态业务变化更新最后更新时间，读取不更新。

## 测试与完成证据
HTTP API + 真实 PostgreSQL，受控终止/网络/调度适配点覆盖页面断连、业务服务重启但执行仍活、旧执行终止及恢复重放；提交确认与隔离证据、A15完整交错矩阵及UI状态证据。实际执行机制须有可观察证明，不能仅内存假标志通过；19补真实 Agent 执行，20汇总重启验收。

## Comments

2026-11-03 implement10 授权内详细设计，基点master b59e02f干净，已加载tdd并读规范、词汇、规格及07-09/现有执行边界。无可调用Agent工具，不冒称子代理。以下是本次实现契约，不称用户批准新的部署方案：

1. 范围限定既有 controlled_queries_v1：接纳自身不启动后台执行，唯一活动是受信、非阻塞、合作式且不遗留子任务/子进程的查询适配器。新增0012保存接纳进程及各查询实际承载进程的身份（本机Windows安装指纹、PID、内核创建时间）；不同API进程分别记录。Windows内核进程句柄的退出信号/原PID已不存在/同PID创建时间已更换才是本机原进程退出证明。跨机器、权限不足、不支持平台及无身份的旧数据一律unknown，不能凭新服务启动或租约失效确认。此范围无需新增外部执行部署或终止权限；不终止任何用户服务。真实Agent/任意子进程不在本协议保证内，19须另行验证。
2. 受控失联信号及后台观察到已退出的所属进程均先短事务保存lost_requested_at并valid=false；提交隔离后才在后续确认事务采集进程退出事实。阶段confirming_execution仍调查中、保留运行资格/active/source gate，拒绝新轮及旧证据/结论/查询/重放。页面GET/刷新/断连只读；无自动续跑/新轮。
3. 失联的确认要求已知协议、已记录接纳进程可靠退出，且全部仍active查询的各自承载进程均可靠退出。活进程或unknown不清active/闸门。对已退出查询仅释放所属query_id的闸门，保留来源限流事实；原query材料追加中断状态而不删除历史。写隔离只证明旧身份不能写，不冒称物理退出。旧/未知stop_protocol或缺身份保持等待，绝不凭valid=false确认。
4. 沿08调查→轮次→来源闸门锁顺序及最后clock采样语义。终态先合法提交保持不变；已有cancel/timeout停止归属不被失联夺走。无既有归属时失联若最终采样已到deadline则归timeout，否则归service_interrupted；失联先取得归属后后续cancel/timeout不改其业务含义。失联存在时统一显示确认等待，但最终按原归属落定。只有可靠退出且持久写隔离后同事务落定唯一终态/确认事实/updated/结束+30d；确认失败保留隔离。11未有全局容量表，不虚称释放计数。
5. 主验收沿已确认HTTP业务API+真实PG17、可控时钟/调度/网络及少量真实Chromium。真实Popen托管API进程经poll/wait观察存活和退出，finally回收，不detach、不杀未知服务；覆盖多API活进程、API重启、进程退出、旧身份/重放、新轮及四事件完整交错。新增迁移不倒填旧退出事实，不碰开发库、不drop测试schema。逐项red→green；完成只写本单Answer，保持claimed，不resolved/commit/push/地图日志。

## Answer

### 实现与安全证明的精确范围

- 基点master `b59e02f`，起始干净，保持claimed，未commit/push/resolved，未写地图/工作日志。已读AGENTS、docs/agents三规范、GLOSSARY、README/spec、07-10及执行/查询/取消/期限/证据/结论/API/UI测试，加载tdd及tests/mocking。无可调用Agent工具；主代理两轴审查尚未执行，不冒称独立审查。
- 新增0012，仅增失联时间、确认材料和执行/查询进程身份，不改旧迁移、不倒填旧身份或退出事实。部署仍须先停止旧版本写入并备份调查产物，再用正确DATABASE_URL运行真实Alembic upgrade head；不支持不知道新隔离契约的旧版混跑。本次仅操作127.0.0.1:55433测试库隔离schema，开发数据库未迁移。
- 进程身份为本机Windows MachineGuid、PID和GetProcessTimes内核创建时间。OpenProcess查询/同步权限取得句柄后，WaitForSingleObject(0)的signaled证明已退出；无该PID（ERROR_INVALID_PARAMETER）或创建时间变化证明原身份已不在该PID。权限不足、外机、不支持平台、缺身份、未知stop_protocol一律unknown。确认材料持久保存PID、创建时间和观察类型/state，不对普通API暴露MachineGuid。没有心跳/租约到期冒充物理死亡，没有自动kill服务。
- 这个证明仅覆盖既有controlled_queries_v1的受信范围：接纳不启动执行线程，查询适配器异步非阻塞、合作式、不遗留子任务/子进程。观察接纳进程退出不能代替其他API查询承载进程退出；每个active查询另存实际承载进程身份，必须全部可靠退出才清active及所属query_id闸门。活动查询完成正常await清理也仍是既有可靠退出路径。不同机器/克隆MachineGuid、PID命名空间/Windows安装身份迁移、任意远程Agent或子进程不属于此证明；未知时保守等待，不部署推定机制。跨主机生产执行、容器/Linux和真实Agent须后续单独选择并验证，不能宣称已获得这些部署授权。
- 新受控POST `.../executions/{execution_id}/lost` 与后台实际进程退出观察先持调查/轮次锁提交lost_requested_at及valid=false。失联只接受路径身份和受控Bearer凭证，拒绝客户端证明/自由状态；不能用valid=false反推物理退出。后台由原lifespan协调器托管，try-lock逐调查运行，不续跑、不发新查询/新轮。确认在后续事务进行，确认失败回滚确认/闸门/active/终态，隔离仍保留。GET/页面刷新只读，不触发隔离或延长保存。
- 失联后仍调查中/confirming_execution，保留运行资格、active与来源闸门，拒绝新轮、旧证据、结论（包括旧幂等重放）、查询及context。持久失联通知当前实际查询取消并await真实清理；清理确认才更新query interrupted，但只退出协程不冒称API承载进程已死。失联在活接纳进程上可能持续等待到该进程真实退出，这是保守范围限制，不是无条件自动恢复保证。
- 可靠确认后，持锁唯一落定失败/service_interrupted/result=null，保存stop_confirmed_at、process_exit_and_writes_fenced、观察事实、ended_at、updated与结束+30d；既有证据/结论/查询材料保留。来源闸门只清所属query_id，不清blocked/retry_at；没有11全局名额表，因此只验证现有调查中运行唯一资格，不虚称全局名额释放计数。严重程度执行写入口仍未开放，未宣称实现等级写隔离API。
- UI显示“正在确认执行状态”、尚未可靠退出、仍占位/不能新轮，取消按钮禁用；失败历史显示“服务中断，已确认旧执行退出并隔离写入”，新轮及刷新仍保留旧失败原因。

### 裁决矩阵与范围

沿08短事务最终当前clock采样语义，不保证任意COMMIT网络/落盘延迟的物理硬截止。已合法终态不可覆盖；已持久cancel/timeout归属不被失联夺走；无归属时失联最终采样若已到deadline则归timeout，否则service_interrupted。失联先持久归属后，cancel/timeout不夺走，确认仍需真实退出。

四事件全部24排列通过HTTP+PG+真实Popen验证（每行固定第一事件，余3事件6种排列）：

| 第一合法事件 | 其余排列数 | 最终状态/原因 | 退出前约束 |
| --- | ---: | --- | --- |
| complete | 6 | 已完成/normal_completion | 完成时无active，后续不能改终态 |
| cancel | 6 | 已取消/user_canceled | 既有07已知空闲协议确认，不改成中断 |
| timeout | 6 | 失败/attempt_deadline_exceeded | 沿08归属，不改成取消或完成 |
| lost | 6 | 失败/service_interrupted | 活进程期间调查中/confirming_execution，不允许新轮 |

此24排列是提交前后受控顺序矩阵，不冒称穷举所有物理线程/网络调度；跨API真实在途/退出与事务内时钟跨界另测。旧恢复身份及原路径重放403，新轮材料不被旧身份污染；已合法完成的历史结论仍沿05/09只读重放契约，不重新授权。

### 实际逐项red→green

统一前缀`PYTHONPATH=backend NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest`，主层TestClient/HTTP+真实PG，Popen及Chromium层真实TCP，不混称全部TCP。

1. 失联信号与写隔离：QLrtwR **1 failed in3.09s**（lost404）→BFP6tn **1 passed in3.16s**。保留已存证据/结论，旧产物/重放/新轮拒绝，GET不改业务状态。
2. 真实接纳进程退出：vCrSay **1 failed in15.50s**（进程已wait退出但状态不结束）→L6py6E **2 passed in9.73s**。真实poll存活时不终态，finally terminate/communicate回收后才能确认失败，重启读材料、新轮、旧写拒绝。
3. 无页面/lost调用的后台退出识别：NC0iMA **1 failed in14.59s**→g1vCrM **3 passed in10.63s**。
4. 独立API真实查询承载进程退出：1HmwHM **1 failed in16.18s**（保守active无法恢复）→aUrwus **4 passed in16.27s**。后补强承载适配器吞取消保持真实活动，确保不能以取消请求冒充退出；等待承载Popen退出后才恢复。
5. 24排列矩阵：8TknJW **1 failed/18 passed in62.38s**（lost先归属却被cancel夺成已取消）→gDGtuT **24 passed/4 deselected in79.77s**。修复cancel/finalize_deadline不夺持久失联归属。
6. 真实Chromium等待/刷新：LJ6DTt **1 failed in27.72s**（无失联文案）→uU2unf **1 passed in20.24s**。新增最终原因/旧轮历史真实进程退出UI补强，未虚称该补强逐项红灯。
7. 失联通知实际在途适配器退出：EX29zT **1 failed in33.04s**（仍等30秒而非持久lost通知）→dHrNuE **1 passed in2.67s**。仍活API进程保持confirming，不把协程退出冒充进程退出。
8. 失联事务内599→601：JFoU67 **1 failed in3.43s**（无timeout归属）→qpywaU **1 passed in3.02s**。最终当前采样过期归timeout，丢弃无总优先假设。
9. 补强：未知自定义stop_protocol重建应用后仍等待、确认事务PG除零回滚但隔离保留、结束+30d及0011真实旧轮升级缺身份不伪确认。UPviZJ **31 passed in104.71s**（当时尚无最后两项）；旧升级fixture最初缺redaction key导致503/KeyError，补齐真实旧启动需要的输入后，lnoXZW **3 passed/94 deselected in27.12s**，包含旧升级及两项真实Chromium路径。

### 环境诊断与完整验收

- 4tg1G6专项 **22 passed/11 setup errors in84.37s**：PG17迁移catalog并行计划申请32MiB共享内存报No space left on device，随后数据库自动恢复；不是11项业务红灯。仅只读docker logs/pg_isready诊断，未drop schema、重启容器或改全局参数；数据库自主恢复ready后，只在测试命令环境增加`PGOPTIONS='-c max_parallel_workers_per_gather=0'`，原pg_dump测试的同类保护保持不变。
- KlNint受保护专项 **32 passed/1 failed in120.10s**，唯一失败是上述旧升级输入缺redaction key；修正后lnoXZW绿灯。未屏蔽测试或降级数据库。
- 首次全量pfzCRD **295 passed/1 failed in675.36s**。原02测试要求真实API进程退出后继续旧执行写入，与10不自动续跑冲突；将其历史持久性验证调整为进程退出前正常完成，再重启读完整材料/多轮；新增10真实进程测试覆盖中断终态和旧身份拒绝，不豁免活进程或未知退出。fgkAkX定向 **1 passed in7.66s**。
- 根目录构建m1waZx `npm run build && git diff --check` **退出0**，vue-tsc/Vite成功，3294模块，3.25s，JS1007.75kB/gzip325.03kB；仅既有chunk/构建回调耗时及LF/CRLF提醒。
- **最终冻结全量2NUCWF**：`PYTHONPATH=backend PGOPTIONS='-c max_parallel_workers_per_gather=0' NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，原始输出 **296 passed in661.75s (0:11:01)，退出0**，无失败/跳过/warning汇总。相对261基点增加33项后端失联验收（含24排列）和2项真实Chromium，输出已完整收集。
- 全套后独立PowerShell实测 **8000/5173 ListenerCount=0，所有python/node uvicorn/vite进程数=0**，Popen全部finally回收；没有detach或停止未知服务，PG测试容器保留。
- 最后C8EQfN构建成功（2.86s，同3294模块/产物大小），diffcheck发现本Answer追加的EOF空行，已删除后重验；不隐瞒该非业务差异错误。

### 审查3逐项修复（2026-11-03）

保留收到任务时全部10未提交实现，加载tdd，沿用户明确指定的HTTP API＋真实PG、真实Popen来源/进程及数据库事件控制时间/故障seam逐项先红后绿。仅新增修改`backend/app/interruption.py`、`backend/tests/test_interruption.py`及本Answer；保持claimed，不resolved/commit/push，不改地图、工作日志或开发库。无可调用Agent工具，不冒称额外独立审查。

#### 问题1：恢复丢失已持久来源限流

- 真实Popen API中的受信来源实际抛出`SourceFailure(rate_limited, retry_after=30)`，API进度已保存waiting及retry_not_before，实际等待期间终止并wait该Popen；通过另一API等待后台可靠退出恢复，再发另一调查查询。VUaxLm红灯：**1 failed in5.48s**，错误201/not_configured而非409。
- 恢复从本轮source_ids与已持久query material来源顺序映射来源键，持来源闸门锁，仅更新仍属于该query_id的闸门。合并真实材料中的rate_limited：原gate无限期限制优先；两个有限期限取较晚值；任一无限期限制不被有限值放宽。无新限流材料时不改原blocked/retry_at，不清他人query所有权。active/中断材料/闸门/终态仍同一确认事务。
- KQcfNh绿灯：**1 passed in5.03s**。补强4种实进程情形：仅真实持久材料、更严格有限gate、无限gate、他人query gate。后3种仅用SQL安排既存闸门输入，来源材料和退出仍为真实Popen产生；业务断言全部经API，分别409/rate_limited/原或更严格期限、无限期、409/inflight，非内存假门。

#### 问题2：后台lost漏最终期限裁决

- Jdc0up红灯：**1 failed in4.66s**，后台在真实PG UPDATE lost_requested_at事件内599→601仍无timeout_requested_at。
- HTTP与后台共享`isolate_lost`领域操作：持调查/轮次锁，入口裁决后写lost、执行隔离及更新时间，再以当前clock最终裁决；用持锁取得的旧行判断既有归属，避免本事务新lost抢过已经到期的期限。已持久cancel/timeout/lost及合法终态不被重分类；隔离与timeout原子提交。
- KeM5BC绿灯：**2 passed in6.02s**，前后台真实PG跨界均通过；后台下一轮可靠确认后失败原因必须attempt_deadline_exceeded。线性化仍沿08：持锁业务SQL完成后的当前最终采样，不承诺任意COMMIT传输/落盘延迟下物理墙钟硬截止。

#### 问题3：一个坏确认饿死整轮其他候选与期限扫描

- 每次坏候选确认终态UPDATE执行真实PG `SELECT 1 / 0`，持续故障不只失败一次；另一个调查分别为到期、真实Popen退出。hFRTqR红灯：**2 failed in29.61s**，两个好调查都长期仍调查中。
- 最初079f24为1个饥饿失败及1个测试设施失败：第二Popen自身协调器绕过了观察端PG故障注入，导致坏候选在其进程被确认。为第二Popen显式禁用定时协调，但仍经真实API接纳/真实内核退出；随后hFRTqR准确双红。未替换进程存活或SQL结果。
- 每候选确认的材料读取、退出核验、闸门合并、active及终态写入放入独立savepoint；SQL确认失败回滚该savepoint，已提交的lost写隔离保留，然后继续下一候选及原deadline扫描。连接失效向上传播，外层提交失败也不吞成成功；隔离SQL失败仍HTTP503，不产生伪持久隔离或退出事实。数据库整体不可用时不保证推进，本方案不以假成功掩盖不可用。
- VCtokI绿灯：**3 passed in12.84s**，两个饥饿情形及原确认事务回滚测试通过。另补API隔离真实PG除零：503、GET仍为原持久状态，恢复后旧合法写仍可201，验证失败隔离未冒称成功。

#### 排列响应与真实并发补强

- 24个排列现在逐次断言完成首事件201、被停止/终态拒绝403；cancel/lost必须200且返回投影等于持久GET；timeout写必须403；每步核验cancel/timeout归属，不仅比较最后终态。
- 新增失联分别与完成、取消、timeout的双锁序6项：两个独立TestClient/API应用事件循环连接同一真实PG，首事务实际持调查FOR UPDATE锁暂停，第二请求已到SQL且尚未返回后才释放；两请求响应及最终唯一原因均断言。lost先归属时，timeout竞争在lost最终采样后的COMMIT事件推进到11分钟，真实到期不能夺走失联归属；反向顺序则保留完成/取消/超时原归属。
- 初版aaKZDc **37 passed/6 failed in180.11s**：一个TestClient的同步SQL暂停阻塞同一API事件循环，第二请求未能到PG，不是业务race红灯。改为两个独立API事件循环后un81h7 **6 passed in18.75s**。Qv6ThQ完整失联专项 **47 passed in149.97s**；最后补强真实到期COMMIT交错后sDRwmy **6 passed in24.31s**。新增补强不冒称每个参数均有独立先红记录。
- 24排列是受控顺序矩阵，6并发项是特定真实锁竞争轨迹，不声称穷尽线程、网络或所有物理调度。真实进程证明仍仅限本机Windows及受信controlled_queries_v1；未知身份/外机/任意子任务仍保守等待，数据库故障无无条件自动恢复承诺。

#### 完整验收与环境边界

- 用户提示正在运行的主任务f2PuHD已完整收集：**296 passed in669.73s，退出0**。它不是本次修复后的冻结全量；在它完成前仅运行后端专测，未抢UI端口。随后PowerShell确认8000/5173监听数0才启动本次完整回归。
- zYBTLU完整后端＋UI：**310 passed in768.71s，退出0**，无失败/跳过/warning汇总。运行期间最后增强了timeout竞争测试的COMMIT时钟事件，因此另启动4DGsom冻结复跑，不将该中间结果冒作最终冻结证据。
- NPqEQb根目录`npm run build && git diff --check`退出0，vue-tsc/Vite成功，3294模块、3.31s、JS1007.75kB/gzip325.03kB，仅既有>500kB chunk及LF/CRLF提示。
- **最终冻结4DGsom**：`PYTHONPATH=backend PGOPTIONS='-c max_parallel_workers_per_gather=0' NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，**310 passed in699.93s (0:11:39)，退出0**，无失败/跳过/warning汇总。相对收到任务时296项增加14项；全部后台完整输出已收集，不以启动通知代替完成报告。
- **最终构建3s8ZRN**：根`npm run build && git diff --check && git rev-parse --short HEAD`退出0，vue-tsc/Vite通过，3294模块、3.63s、同产物大小；仅既有大chunk/构建回调耗时/LF-CRLF提示。HEAD仍`b59e02f`，未提交或推送。
- 完整冻结测试后PowerShell核验：8000/5173监听数**0**，全部python/node uvicorn/vite服务进程数**0**；Popen由fixture finally回收，没有detach或停止未知服务。所有测试均用现有隔离schema fixture，不drop schema、不改PG全局配置、不迁移开发库；原未提交文件全部保留，仅本次必要实现/测试及本Answer追加变化。报告最终追加后再执行diffcheck。

### 主代理最终验收（取代前文待审查状态）

- 两轴独立审查无明确规范硬违规；发现死查询恢复限流丢失、后台期限最终采样缺失、坏候选饿死协调扫描，以及活查询退出覆盖更严格限流四类行为问题，均实际先红后绿修复。有限重复异味通过共享isolate_lost与release_source_gates一并收敛，无无关重构。最终复审核实全部闭环，无剩余阻断，另独立44项定向测试通过。
- 主代理最终冻结全套z0DTzE：**358 passed in859.62s，退出0，无失败/跳过/warning汇总**。相对261基点新增97项（95后端含进程、排列、真实锁竞争及限流矩阵，2真实Chromium）。本层完整回归包括真实PG/真实Popen/API重启/升级和浏览器。
- 主代理最终根目录rNSuQG：`npm run build && git diff --check`退出0，vue-tsc/Vite成功，3294模块，3.28s，JS1007.75kB/gzip325.03kB；仅既有chunk/回调耗时/LF-CRLF提示。
- 10在明确本机Windows受控协议内resolved，未知/外机/缺旧身份仍保守等待，不承诺任意执行部署恢复。无自动续跑或新轮，未交付11全局容量及19真实Agent。开发库未迁移，当前分支本地提交、不推送，更新地图和当日日志。

### 最后P2：统一所有查询退出的来源闸门合并（2026-11-03）

- 加载tdd后沿用户指定公共HTTP API＋隔离真实PG seam，保留所有既有未提交实现。本次仅改queries.py、interruption.py、test_interruption.py及本Answer；不resolved/commit/push，不改地图/日志，不操作开发库或drop schema。
- 首个真实TCP/Popen红灯xc87q2：来源实际rate_limited30进入waiting，SQL仅将仍属本人query的gate安排为无限限制；lost200、在途query403、活承载进程仍存活，另一调查公共查询409却暴露30秒retry，**1 failed/47 deselected in5.90s**。共享释放实现后lrJEjW **1 passed/47 deselected in4.82s**。后补真实TCP三种gate（无限、更晚有限、他人归属）全部在冻结专项执行。
- 提取queries.release_source_gates供正常完成、cancel/timeout/lost实际await退出及dead进程恢复共同调用。短事务内仅选query_id仍属本人的gate，按source_key排序FOR UPDATE；更新再次带query_id条件。无新rate_limited仅清本人query_id，完整保留旧blocked/retry_at；新rate_limited与旧限制合并，任一无限则无限，两个有限取更晚。无跨来源I/O持锁，不释放他人query，不增加新协议或扩大重构。
- 36项公共API矩阵：normal/cancel/timeout/lost × 无限/更晚有限/他人query × 无新限流/新有限限流/新未知retry。适配器实际进入及await退出，SQL仅安排已有gate输入；另一调查真实API必须409、rate_limited或inflight以及原更严格期限。dead恢复另扩无新限流三项；原四项保留。不会只查SQL自证gate。
- 第二个独立红灯NpZ7ty：来源先明确30、后返回None/0/-1/unknown/inf/nan，旧代码沿用前次有限retry；另一调查API仍暴露30秒，**6 failed/89 deselected in14.08s**。每次新rate_limited先清retry_not_before，只有有效正有限来源值才能再设有限期限，未知/缺失保守无限；绿灯已包含下述185项定向及最终全套。未将新增补强矩阵冒称逐参数独立红灯。
- 初步释放矩阵jhF3YB **41 passed/43 deselected in93.24s**；随后新增真实TCP有限/他人、dead无新限流及未知retry反例，最终定向NZQLkH执行test_interruption.py、test_queries.py、test_cancellation.py、test_deadlines.py：**185 passed in504.12s，退出0**，无失败/跳过。
- 主全套FYpuAL已实际等待并收集：**310 passed in797.59s，退出0**；这不是本次冻结结果。它运行时只跑隔离后端专测，不抢UI；完成后PowerShell确认无pytest/uvicorn/vite进程及8000/5173监听，才启动本次冻结全套iY7Tt2。
- **本次最终冻结iY7Tt2**：`PYTHONPATH=backend PGOPTIONS='-c max_parallel_workers_per_gather=0' NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost TEST_DATABASE_URL='postgresql+psycopg://logscope_test:local_test_only@127.0.0.1:55433/logscope_test' .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，完整输出 **358 passed in906.55s (0:15:06)，退出0**，无失败/跳过/warning汇总。相对310增加48项：真实TCP waiting/lost3、各活释放路径矩阵36、dead无新限流3、未知retry6。测试启动后未再改任何实现或测试。
- **最终构建ef9Axl**：`npm run build && git diff --check && git rev-parse --short HEAD`，**退出0**；vue-tsc/Vite通过，3294模块、2.95s，JS1007.75kB/gzip325.03kB，仅既有>500kB chunk及LF/CRLF提示。HEAD仍b59e02f。Answer最终补齐后再次diffcheck。
- 冻结全套后PowerShell实测pytest/uvicorn/vite进程数**0**、8000/5173监听数**0**。主全套未干预、UI未争抢，所有受控Popen由fixture回收。测试仅独立测试schema且保留，不drop、不改PG全局参数，不碰开发库。工单保持claimed，无resolved/commit/push、地图或日志修改。
