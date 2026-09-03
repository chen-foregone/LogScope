# 04: 可靠判同并展示本轮证据条数
Status: resolved
Type: task
Blocked by: 03

## What to build
纵向交付证据身份 schema 与迁移、事务判同及纳入 API、详情与列表的本轮证据条数和可能重复提示。不将证据数量解释为日志扫描量、故障数或根因数。

## Blocked by
- 03 保存、展示脱敏证据及调查内一致代号

## Spec
[首版实施规格](../spec.md)：D4、D6、D8。
用户故事主要交付：US-23、US-24；US-26 的同轮计数部分。关联 US-42。
验收组主要交付：A18、A19；A26 新证据业务变化部分；A20 历史纳入在12，A29 全集统计在15。

## 开工门槛
无额外未知输入，遵守03依赖。依据稳定来源身份设计判同事务，能力不足走保留与可能重复提示，不编造稳定身份。真实来源身份能力由18验证。

## 验收标准
- [x] 同轮同一来源稳定记录ID重复获取仅保存、纳入并计一次；文件来源可用稳定文件身份与偏移。
- [x] 并发重复提交仍只计一次，失败事务不留下半套证据或错误计数；重放结果可理解。
- [x] 相同文本但来源或时间不同的独立记录不被误合并；不能只凭时间、摘要或脱敏后相同内容判同。
- [x] 无可靠身份时保留各条证据并提示可能重复，不能为了凑唯一键静默丢弃。
- [x] UI 与 API 一致显示“证据条数”，统计当前轮已保存、去重且纳入的证据；无轮为0，历史轮保留各自条数。
- [x] 实际新增证据更新最后更新时间，读取或未产生业务变化的重复提交不伪造更新；脱敏核验材料仍符合03。

## 测试与完成证据
HTTP API + 真实 PostgreSQL 覆盖并发重复、稳定文件偏移、同文本不同来源/时间及无ID材料；检查持久证据、计数和UI提示，附增量UI验证。只通过外部适配点安排输入，不断言私有算法调用次数。历史引用12、概况15与真实连接器18分别补全，不以模拟来源冒充真实集成。

## Comments

2026-11-02 开工：用户已授权implement并确认HTTP业务API＋真实PG17隔离schema＋受控适配＋少量真实UI seam；固定基点master 40989ea631b3ce7dc3e0221a39fa489551f19894，开工git status干净。先claimed；完整实际通过后resolved，不commit/push。仅04纵向范围，不实现05/12/18/Agent；不触开发库、不删除schema，不新增额外报告或地图。

## Answer

### 输入与冲突契约

- `controlled-event-v1`新增可选独立`identity`：`{"kind":"record","record_id":"稳定原始日志ID"}`或`{"kind":"file","file_id":"稳定文件身份","offset":0}`；省略/null表示未声明可靠身份。`record.fields.record_id`与`correlation_id`仍只是脱敏关联材料，绝不自动升级为可靠日志身份。仅可信生产者经受控执行认证入口声明；这是稳定身份接口，不能验证任意ID真实性，不是万能日志识别。真实生产者的稳定性、文件轮转身份和offset语义由18验证。
- record_id/file_id严格字符串1–4096字符，拒绝控制字符和孤立代理码点；offset严格整数0–2^63−1，拒绝bool/字符串/浮点/负数/超限/null。kind及各层未知字段失败关闭，继续03白名单event/error_code、64KiB、认证与轮次/执行有效性、来源与查询/发生范围校验，不扩大自由文本输入。浏览器没有生产者凭证或identity写入界面，普通用户不能伪造来源身份。
- 身份键为调查独立密钥的HMAC-SHA256，对带版本域的JSON结构`[source_id, identity对象]`编码；record/file类型、字段边界与offset类型不混淆，不使用分隔符拼接。判同范围由`attempt_id + identity_key`限定，发生时间不进入身份键；不同来源、不同身份、不同调查互不合并。没有可靠身份时键为NULL，全部保留并提示可能重复，不按文本/时间/摘要/脱敏内容猜测。
- 内容冲突指纹是另一版本域的HMAC，输入规范化UTC发生时间＋原始受控record（包含屏蔽前凭据字段，因此脱敏结果相同也能发现变化）；只存不可逆指纹、不存原值。相同key且内容相同返回200及原证据UUID/材料；查询条件/定位是获取上下文，变化时仍返回最初保存的材料。相同key但发生时间、事件/错误码或任何record字段变化，固定安全409，保留旧证据，不覆盖、不新增、不更新时间、不回显内容。并发不同内容仅一个201、另一个409。
- 认证、严格schema、执行归属/有效性、来源和范围全部在判同之前校验，重复不能绕过。保留03固定不可用布尔提示规则；非法材料422不保存原文，不新增证据、不更新调查时间。来源/范围失败无业务变化，身份认证失败401/403。

### 事务、计数、迁移与界面

- 延续调查→轮次/执行FOR UPDATE锁序，串行保护调查HMAC密钥和同轮判同；0005增加`identity_key`、`content_key`、`identity_status`及数据库`UNIQUE(attempt_id, identity_key)`。新增证据与updated_at同事务，失败无半条证据或已占用身份。重复200返回旧证据，无更新调查时间；有效新增201才更新时间。未声明身份的重传仍201新增，这是明确保留契约。
- 轮次投影新增evidence_count，与持久证据列表长度一致；调查详情/列表沿既有最新轮投影，无轮0、不累加历史、不跨调查去重。列表与详情显示“证据条数”“可能重复”，详情说明可靠身份重复只计一次、无身份逐条保留，不代表扫描日志量/故障数/根因数/覆盖完整率。
- identity_status为reliable/unavailable/legacy_unknown；公开只返回安全状态，不返回来源身份原值、身份键或内容指纹。来源与locator继续03整体代号化。内存pg_dump审计验证合成identity/凭据/字段原值未持久化；错误诊断、HTTP及浏览器DOM无原值。03既有服务目录快照保留受控source_id配置标识，未扩大为自由文本或敏感来源标识。
- 0004旧证据全部以legacy_unknown回填，NULL身份键不触发合并，保留每条UUID、材料和原调查更新时间；API/UI提示“升级前证据未声明可靠身份”，不倒推record.fields.record_id/locator/文本为可靠身份。真实0004→0005升级用两条相同旧材料验证历史可读且计数2，再接收可靠身份并重放后计数3。
- 部署迁移步骤：先停止旧写入进程并备份调查产物库；在目标环境设置既有安全DATABASE_URL、PYTHONPATH=backend，执行`.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head`至0005，再启动匹配版本API/UI。此次只迁移隔离测试schema，未迁移开发库；不运行降级或删除历史。

### 实际红绿与验证

- 可靠record身份首项真实HTTP/PG红灯422→实现schema/0005/事务重放/轮次计数→1 passed；文件身份6并发首项全422红灯→严格file schema→record+file两项passed。同key并发仅一个201、其余200且UUID一致；不同ID/offset并发各自201。
- 身份冲突、认证/范围重复旁路、未知身份/格式/字段、offset类型及边界、recordID非法值、来源隔离/类型域/分隔符案例、无identity但有普通record_id逐条保留、跨调查独立及原值内存审计作为已实现契约的补充回归，未虚称这些补充断言逐项先红。证据定向16 passed in23.16s。
- 真实UI“本轮证据条数”缺失红灯→更正文案；列表“可能重复”缺失红灯→增加最新轮提示。真实Chromium经API注入、可靠身份重放与无身份重复，刷新后3卡/条数3，列表同为3及可能重复，身份原值未进DOM。旧数据升级可读说明缺失红灯→新增legacy中文提示。迁移测试只用SQL安排真实旧schema夹具，业务断言全部HTTP；pg_dump仅安全审计且不落文件。
- 首轮全套45 passed in91.12s；随后审查补充同key内容竞争及插入后updated_at故障回滚两项，再完整复跑，最终结果见下方。既有独立Uvicorn进程重启用例改为可靠身份，重启后200返回原UUID且updated_at/计数1不变。既有无身份重复保留和03错误回滚测试继续保留。
- 测试中发现独立轮次接口时间输出为+00:00而调查投影为Z；该差异不影响本单计数/证据行为，调整断言仅比较证据、计数和提示，未改无关序列化。首次文档查找glob超时，后通过既有备份元数据定位工程当日日志，未写额外报告。

### 最终完整结果

- 最终命令`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`，后台任务UOIDoI已收集完成，退出0，**47 passed in108.87s**，0失败/跳过（既有38＋新增9项HTTP/PG；既有真实UI与进程重启用例增强）。
- `npm run build`任务8MWKAY退出0，包含`vue-tsc -b`与Vite build，3294模块、JS996.52kB/gzip320.90kB；仅既有>500kB chunk非阻断警告。`git diff --check`退出0，仅LF/CRLF提示。核心实现及全改动范围已自审，未扩展其他工单。
- 开工核实真实容器logscope-01-test-pg17、postgres:17-alpine、PG17.11、127.0.0.1:55433。各API例独立新schema真实迁移0005，升级例从0004到0005；UI沿既有fixture创建隔离schema后起127.0.0.1 API/Vite，随机凭证仅进程内。全部schema保留，未触开发库、未drop，PG容器保持运行；没有detach，finally terminate/wait停止测试服务。
- 最终独立PowerShell检查8000/5173监听0、限python/pythonw/node的Uvicorn/Vite残留0，退出0。HEAD保持master 40989ea631b3ce7dc3e0221a39fa489551f19894，无commit/push。六项验收在上述支持范围内实际通过后resolved，交主代理审查后提交。

### 文件与限制

- 新增`backend/migrations/versions/0005_evidence_identity.py`；修改`backend/app/evidence.py`、`backend/app/attempts.py`、`frontend/src/App.vue`；测试修改`backend/tests/test_evidence.py`、`backend/tests/test_attempts.py`、`backend/tests/test_investigations.py`（夹具支持真实0004升级起点）、`tests/test_ui.py`；文档仅本04及工程当日日志。
- 不新增05结论、12历史引用/重开契约、18真实来源、Agent、完整取消失联/终态机制、统计筛选概况。当前依旧仅受控结构化事件，不宣称任意文本脱敏或生产者任意ID真实性保证。数据库后端密钥及备份保护沿03边界，部署追踪仍不得记录请求体/认证/原始身份；真实来源端稳定性须18集成验证。无跨轮引用实现或跨轮累计。

### 2026-11-02 Standards独立复核与最终真实复跑

- 重新Read AGENTS及指向的issue-tracker/triage-labels/domain、GLOSSARY、04、README和相关spec；未发现额外ADR、CODING_STANDARDS或CONTRIBUTING。核对`git diff 40989ea`的8项tracked差异及唯一untracked迁移0005。当前无可调用Agent工具，由主代理直接独立复核，未伪称并行子代理。
- Standards：0阻断、0需修复规范违反。Mysterious Name、Duplicated Code、Feature Envy、Data Clumps、Primitive Obsession、Repeated Switches、Shotgun Surgery、Divergent Change、Speculative Generality、Message Chains、Middle Man、Refused Bequest均仅作判断基线，项目规范优先、跳过工具强制项；现有局部重放返回及测试场景重复不足以支持抽取/搬移/拆分，无无故重构。
- Spec：在04范围内未发现缺失、错误实现或越界。复核strict身份schema及file offset整数边界、来源/类型域结构化HMAC、身份内容冲突409、认证及范围先于判同、调查→轮次/执行锁序、唯一约束及插入/更新时间同事务、NULL旧身份不合并与0004→0005历史保留、最新轮计数及可能重复提示。列表/详情沿既有REPEATABLE READ快照，轮次计数与所返回证据同一读取结果；错误安全响应、隐藏SQL参数、pg_dump内存审计及DOM断言延续03边界。真实生产者身份稳定性仍由18验证，不扩称完整首版。
- 运行前实际确认容器`logscope-01-test-pg17`为`postgres:17-alpine`、PG17.11、仅映射127.0.0.1:55433。完整命令：`NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost TEST_DATABASE_URL='postgresql+psycopg://logscope_test:local_test_only@127.0.0.1:55433/logscope_test' PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`。任务aj6v7y已收集完成：退出0，**47 passed in129.71s**，0失败/跳过。每个后端例新隔离schema真实迁移，升级例0004→0005；UI由既有conftest自动新schema迁移并启动真实API/Vite，真实Chromium与进程重启用例实际执行。
- `npm run build`任务UEdsz9已收集完成、退出0：vue-tsc -b及Vite成功，3294模块、2.18s、JS996.52kB/gzip320.90kB；仅既有>500kB chunk非阻断警告。`git diff --check`退出0，仅LF/CRLF提示。
- 全测结束后独立PowerShell确认8000/5173监听0、限python/pythonw/node的Uvicorn/Vite残留0，退出0；fixture finally terminate/wait，无detach。全部schema保留、不drop、不执行开发库迁移，PG容器保持原运行状态。本次只追加04和已有当日记忆，不改业务/测试/配置、不新增报告，04保持resolved；HEAD仍40989ea，无commit/push。
