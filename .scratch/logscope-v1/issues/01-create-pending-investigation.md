# 01: 创建并持久查看待处理调查

Status: resolved
Type: task
Blocked by: None

## What to build

纵向交付创建、列表基础读取和详情查看：设计输入输出 schema、真实 PostgreSQL 迁移与持久化、HTTP 业务 API 和 UI，创建调查而不创建运行轮。产物必须经刷新及服务重启仍可查看，不以页面内存样例交付。

## Blocked by

None，无直接依赖。

## Spec

[首版实施规格](../spec.md)：D1、D2、D6、D7、D8、T1–T3。
用户故事主要交付：US-01、US-36（基础持久化）；关联 US-37、US-43、US-48。
验收组主要交付：A01；A32 的空库迁移和创建持久化部分。A24 初始未评估、A26 创建与读取、A35 创建查看部分由本单交付；A32 升级与完整历史在20汇总。

## 开工门槛

无额外未知业务输入；具体字段校验、重放身份、迁移与测试工具可在授权内设计并记录契约，不把框架或表结构称为用户已批准。服务配置选择留02，完整筛选留15；不因创建缺服务而隐式启动。

## 验收标准

- [x] 用户通过 UI 创建，真实 API 返回标识、待处理、未评估、创建时间、最后更新时间和保留信息，无运行轮且证据条数为0。
- [x] 缺服务或日志范围仍只能形成待处理记录，不能进入调查中；无效创建输入不留下半记录，重复请求按明确契约处理。
- [x] 列表与详情读取数据库事实，刷新及重启后记录、状态和时间保持，读取不触发执行。
- [x] 创建更新最后更新时间；查看和基础检索不改变该时间，也不延期；保存统一时间基准并按 UTC+8 展示。
- [x] 无轮记录默认从创建起保留30天，详情可见基础期限；运行与延期、清理的完整行为分别由16、17补全。
- [x] 空 PostgreSQL 数据库真实迁移后可完成创建与读取，数据不依赖进程内存，失败事务不留下可见残缺记录。

## 测试与完成证据

通过 HTTP 业务 API + 真实 PostgreSQL 验证创建、输入失败、重放、刷新重启和时间不变；保存迁移执行结果及脱敏请求响应。增量 UI E2E 走真实 API/数据库完成创建到详情，不仅点击样例。仅允许外部适配点替身，不把替身视为真实来源或 Agent；20汇总升级和全链路证据。完成须有 schema/API/UI 与测试证据，不以只建表结束。

## Comments

2026-11-02：用户明确授权实施01及HTTP业务API＋真实PostgreSQL、少量真实UI与迁移测试。已按规则从ready-for-agent改为claimed；工作区非Git仓库，未init、未commit。改前源码/config保存在`../baseline-01/`。测试不查询业务表作断言，仅创建隔离schema安排环境，业务事实通过HTTP读取；测试schema保留，不删除开发数据。

## Answer

### API契约与实现范围

- `POST /api/investigations`：必需`Idempotency-Key` UUID；JSON仅`title`（trim后1–200字符）、`description`（可省略，trim后0–4000字符）。拒绝额外字段、非字符串、NUL/代理码点、缺失或非法请求身份，422且不保存记录/占用身份。服务与范围不是本单创建字段，不实现服务选择、启动或运行轮。
- 首次创建201；同一UUID与规范化后相同内容重放200并返回原记录，重启后仍有效；相同UUID不同内容409，无新增记录。PG唯一约束＋`INSERT ON CONFLICT`确保并发重放恰好一条；输入校验前无写入，写入事务提交后才返回。无身份请求不隐式生成新身份。
- `GET /api/investigations?q=`：`{items,total}`，可选标题/UUID trim、不区分大小写字面包含检索（百分号不作通配符），最后更新时间倒序、UUID稳定次序；本单不分页、不实现完整服务/时间筛选或概况。`GET /api/investigations/{uuid}`：详情；未知UUID404、格式错误422。
- 输出显式Pydantic/OpenAPI schema：UUID、title、description、待处理、未评估、service=null、attempts=[]、evidence_count=0、created_at、updated_at、retention_expires_at；不暴露请求键。01仅无轮记录，状态/严重程度/空轮投影是该已持久记录的不变量，不包含任何运行实现。
- PG `timestamptz`保存统一时间，HTTP归一UTC/Z，UI通过Asia/Shanghai展示UTC+8；创建/更新时间同一时刻，期限创建+30天。读取/检索/重放均不更新或延期。
- Alembic真实迁移0001调查表、0002请求身份唯一约束；应用不通过create_all偷偷建表。应用工厂关闭连接池后可重新建应用；已另测真正Uvicorn进程退出重启。
- Vue真实创建表单、校验、API列表检索、详情及hash刷新直达、网络失败重试沿用请求身份；删除内存样例和假指标/日志源健康展示。未实施02、Agent、日志来源、延期或清理。

### 实际修改路径

`backend/app/main.py`、新增`backend/app/investigations.py`；新增`backend/alembic.ini`、`backend/migrations/env.py`、`backend/migrations/versions/0001_pending_investigations.py`、`0002_request_identity.py`；新增`backend/requirements-test.txt`、`backend/tests/test_investigations.py`、`tests/test_ui.py`；修改`frontend/src/App.vue`、`frontend/src/style.css`。本工单及当日日志记录交付证据，dist由构建生成。未修改README/GLOSSARY/父规格/其他工单。

### 红绿与真实验证证据

- 首轮创建/持久化：pytest先在真实PG报缺失Alembic script_location（1 error），实现迁移与API后1 passed。
- 并发重放：红灯4次均201，期望200/200/200/201；PG唯一身份与事务实现后2 passed。
- 非法输入：红灯NUL触发真实PG DataError；HTTP字符校验实现后3 passed，全部非法请求后列表为空且同一身份可随后合法创建。
- 基础检索：红灯ALPHA仍返回两条；实现字面包含检索后PG17上4 passed。
- UI纵向：真实无头Chromium红灯找不到调查标题输入；实现创建/详情后1 passed。第二项UI回归把真实API/PG已提交的响应在浏览器传输处丢弃，再次点击得到200原记录、检索恰好一条，不是静态mock。
- 最终`PYTHONPATH=backend .venv/Scripts/python.exe -m pytest backend/tests tests -q --tb=short`：7 passed in 16.88s（5 API、2 UI）；包含真实Uvicorn进程终止后重启、详情与列表完整记录保持不变。`npm run build`（vue-tsc -b＋Vite）：成功；`python -m compileall -q backend/app backend/migrations`：成功。构建仍有>500kB chunk警告，不影响通过，未借机扩大重构。
- `DATABASE_URL=... .venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head`退出0；`current`实际输出`0002 (head)`。每项API用独立空schema运行真实upgrade head。`docker exec logscope-01-test-pg17 postgres --version`：PostgreSQL 17.11。UI→Vite代理→真实Uvicorn→PG17，刷新详情/搜索再打开、UTC+8显示及时间不变通过，页面无JS错误。

### 环境与执行命令结果

- `git status --short`开工/结束均128，not a git repository；不初始化、不提交，提交留主代理处理。`command -v docker; command -v psql; python --version; .venv/Scripts/python.exe --version; node --version; npm --version`确认Docker、Python3.13.14、Node22.22.2、npm10.9.7；无宿主psql，使用容器与psycopg。`docker version; docker compose ps; pip list`成功，Docker Desktop引擎可用、项目Compose未运行。
- `ls -a; docker ps -a; docker image ls postgres; ls backend; ls .scratch`检查现状成功；`mkdir -p .../baseline-01 && cp ...`基线成功，必要测试/迁移目录mkdir成功。`diff -ru baseline-01/backend backend; diff -u baseline-01/frontend/src/App.vue frontend/src/App.vue`退出1表示存在差异，不是测试失败。
- 后台`pip install pytest httpx playwright`原清华镜像失败（找不到pytest）；改为`pip install --index-url https://pypi.org/simple pytest httpx playwright`成功；`python -m playwright install chromium`后台成功。测试依赖已另存requirements-test.txt。未安装connector/专家。
- `docker run --name logscope-01-test-pg ... -p 127.0.0.1:55432:5432 -d postgres:16`成功，仅初期本机已有版本；`docker exec ... pg_isready`成功。后台`docker pull postgres:17-alpine`成功，再`docker run --name logscope-01-test-pg17 ... -p 127.0.0.1:55433:5432 -d postgres:17-alpine`成功；最终验收全部使用17。两个容器为本次独立测试，不挂载现有开发卷，不drop数据库或删除业务数据。
- Uvicorn与`npm run dev -- --strictPort`均后台受任务管理、仅绑定127.0.0.1；期间通过TaskStop停止旧API后启动最终代码，最终验证后停止本次验证服务。测试内Uvicorn子进程有finally terminate/wait，不detach。

### 2026-11-02 独立复核与最小修复

- 重新读取01、父规格相关条款、API/应用工厂、两版迁移、全部测试及Vue/构建配置。原有7项真实PG17测试重新运行：7 passed in 15.68s；首次复核启动脚本因假设npm绝对路径不存在而失败，未执行测试，改用实际Node运行Vite后成功；不属于产品失败。
- 补查发现孤立代理码点输入虽被Pydantic拒绝，默认422响应回显原始输入时却触发UnicodeEncodeError，违反非法输入422契约。先在既有HTTP非法输入测试加入title/description代理码点回归，真实红灯1 failed in 2.18s；随后仅main.py增加RequestValidationError处理，422保留type/loc/msg，不回显原始input/context，JSON使用ASCII转义以保证编码安全。未改变创建/查询/迁移逻辑。
- 修复后单项1 passed in 2.07s；完整5 API＋2真实Chromium UI：7 passed in 14.91s，退出0；npm run build（vue-tsc＋Vite）退出0，仍有990.94kB JS chunk警告。持久创建列表详情、四并发幂等/冲突、非法输入无残留、进程重启、UTC/UTC+8、查看与重放不改时间、创建+30天均通过现有测试及代码核对。
- 隔离PG为logscope-01-test-pg17，PostgreSQL17.11，127.0.0.1:55433；UI使用新建review01_5fe388f3b00549c787617df1c89d6df9 schema，真实迁移exit0并核实版本0002。API每例另建隔离schema。仅在进程内读取容器测试连接配置，未打印凭据；未连接或迁移开发库。测试schema保留，PG17及原PG16容器仍运行，复核启动的API/Vite已停止。
- 剩余限制：现有测试无跨进程同时创建冲突压力验证；UI请求身份仅在当前页面内保留，丢响应后刷新/重新打开表单不保证继续原身份；这些未扩张为本单新功能。完整筛选/保留延期清理仍属后续工单。仅修改main.py、既有测试、本01及当日日志，构建刷新dist；未实施下一单，未git init/commit。

### 审查与限制

已调用code-review技能，以用户要求的改前baseline替代不存在的Git固定点。无可调用Agent工具，因此未伪称并行独立子代理复核；由本代理分开按工程标准与工单规格审查。标准轴无剩余阻断问题；规格轴发现缺少显式输出schema，已补齐并全量回归。测试依赖声明亦已补充。主代理可再次独立复核。工单01验收均实际通过，resolved仅限01，不宣称首版其他19单完成。开发数据库未迁移；实际迁移/持久化证据来自隔离PG17。当前不存在map.md，按用户限制不额外创建报告/地图文件。
