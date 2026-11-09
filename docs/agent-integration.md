# 受控 Agent 联调

本期将真实百炼 `qwen-plus` 接入 LangGraph 单 Agent，但日志来源仍是工单 18 的模拟 HTTP 来源。该运行方式不代表真实日志来源验收完成。

## 安全配置

默认关闭真实调用。服务端通过环境变量注入 `DASHSCOPE_API_KEY`，不要写入前端或日志。北京地域标准 `qwen-plus` 使用项目默认的官方 OpenAI 兼容地址；子业务空间 Key 需要该空间具备模型调用权限，不要求额外配置专属域名。启用前确认地域、模型权限、价格版本和单价。

```ini
AGENT_ENABLED=false
AGENT_MODEL=qwen-plus
AGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENT_REGION_CONFIRMED=false
AGENT_BUDGET_MICROYUAN=10000000
```

百炼公开模型页显示，北京区域 `qwen-plus` 支持 Function Calling 和结构化输出；非思考模式输入价格为 0.8 元、输出价格为 2 元（每百万 tokens，输入不超过 128K）。部署前仍须以账号控制台和实际地域价格为准。调用参数显式关闭 thinking，并禁用客户端隐式重试。

官方配置依据：[子业务空间的模型调用](https://docs.bailian.console.aliyun.com/zh/model-studio/model-calling-in-sub-workspace)。北京标准模型采用公共兼容地址；不要把 DashScope 原生地址当作 OpenAI 兼容地址。

Windows 修改用户／系统环境变量后，已启动的终端、IDE 和 API 服务仍可能继承旧值。若出现 401，先重启承载服务的程序，再核对地域与模型权限；不要将密钥打印到终端或报告。本次联调实际发现进程 Key 与系统 Key 不一致，刷新子进程环境后工具调用成功。

在承载API的新终端中，确认已通过服务端环境注入Key后，配置价格与开启开关。以下价格仅对应本次已核对的北京非思考价格，未来运行前需重新核对：

```powershell
$env:AGENT_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
$env:AGENT_REGION_CONFIRMED='true'
$env:AGENT_INPUT_MICROYUAN_PER_MILLION_TOKENS='800000'
$env:AGENT_OUTPUT_MICROYUAN_PER_MILLION_TOKENS='2000000'
$env:AGENT_PRICE_VERSION='beijing-public-nonthinking-2026-11-07'
$env:AGENT_BUDGET_MICROYUAN='10000000'
$env:AGENT_ENABLED='true'
```

安装与本机API/来源/前端启动沿用项目README及[模拟场景说明](simulated-incident-scenarios.md)。调查期间先用页面“取消调查”，等待执行退出确认；退出API时使用Ctrl+C并等待进程结束，模拟来源在其终端单独Ctrl+C停止。重启不会自动恢复旧图，只读查看历史不产生新调用；需要继续时由人工启动新轮。关闭真实模型应设置AGENT_ENABLED=false后重启API，不通过删除预算表或切换schema来“重置余额”。本批无迁移，回退代码前先停止写入并保留现有账本及脱敏审计，不运行破坏性迁移来消除已发生费用。

## 运行边界

- Agent 只能请求无参数的本轮只读查询；后端绑定调查、轮次、执行身份和时间范围。
- 不提供 Shell、任意 URL、SQL、修复、重启、配置写入或删除日志工具。
- 每次调用前检查执行是否有效、是否停止、是否超时及预算；取消、失联和重启不会自动恢复旧图。
- 日志先通过现有证据适配器脱敏；模型输出需通过版本化 Pydantic Schema 和证据引用校验。
- 自由模型产物仅保存为假设或未确定根因；自报 confirmed 或引用传播轨迹都不能确认模型的任意解释。旧受控分析协议仍可产生固定的轨迹支撑结论。建议不自动执行，严重程度建议需要人工确认。
- 问题描述只提取固定症状标签，结合数据库固化的服务/时间提供有限上下文，不发送原始标题或描述。标签来自未验证报告，可能包含否定语境，不能当作观测事实；无法匹配时为空。
- 结构化轨迹报告关键矛盾时，服务端强制保留“未解决矛盾”及核验缺口，不能由模型文字排除；保存最后的业务SQL后再次检查截止时间，跨界产物全部回滚。
- V2观测方向与模型候选分类分别展示：权限拒绝、配置缺失、连接池占用和依赖超时各自关联被引用证据；仅网关504/应用超时或多个不同方向时不强行确认唯一方向。model_category保留模型分类，observed_directions保留各方向依据。观测分类不是根因。
- 新保存的Agent建议由服务端统一限制为当前范围只读核查项，包含旧V1材料路径；模型自由建议不会因冠以“只读”而直接展示，已有历史记录不改写。
- Agent假设引用不可核验时显示待重新核验，保持原结果、假设和历史；只能人工关联新轮恢复支撑。恢复后新依据再次失效会再次降级，读取或来源恢复不自动续跑。

## 预算与数据

本机数据库按微元记录共享累计预算，默认上限 10 元。请求前预留最大输入／输出费用；成功且 usage 合法时结算，超时或 usage 不明保留预留，不盲目退款。checkpoint 仅保存步骤摘要，不保存密钥、未脱敏日志或完整思维链。

## 验证

确定性回归：

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m pytest backend/tests/test_agent_config.py backend/tests/test_agent_model.py backend/tests/test_agent_runtime.py -q
node node_modules/vue-tsc/bin/vue-tsc.js -b
node node_modules/vite/bin/vite.js build
```

真实模型调用须由操作者显式配置、确认账号权限和预算后执行；本仓库测试默认只使用模型替身和模拟来源。不得以替身测试勾选 A33 或完整 A34。

真实链路测试为 `backend/tests/test_agent_live.py`，仅在 `RUN_BAILIAN_LIVE=1` 时执行，使用隔离 PostgreSQL、模拟 HTTP 日志和真实百炼模型。默认测试运行会跳过付费调用。`AGENT_LIVE_REPORT` 可指定脱敏验收报告路径；只应在测试目录配置该路径。

真实测试覆盖普通超时、空证据、受控因果轨迹及未解决矛盾四种场景，报告保存脱敏证据和落库结果。语义评审仍不可省略：模型曾忽略结构化矛盾，因此由服务端裁决保护产物；这些案例不证明普遍推理质量。工单20的逐组覆盖、迁移与真正活动进程退出证据见 `.scratch/logscope-v1/release-validation.md`。

固定质量矩阵位于 `backend/tests/test_agent_quality.py`，默认跳过真实付费调用。已配置安全凭据、价格和原累计预算后，可用下列命令复核；不要同时开启其他付费测试开关：

```powershell
$env:PYTHONPATH='backend'
$env:PGOPTIONS='-c max_parallel_workers_per_gather=0'
$env:AGENT_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
$env:AGENT_ENABLED='false'
$env:RUN_BAILIAN_QUALITY='1'
.venv/Scripts/python.exe -m pytest backend/tests/test_agent_quality.py -k real_model -q
```

矩阵报告分别保存安全模型原始字段和服务端产物。机器断言只验证引用、分类归属和受控输出契约；人工评审还须检查因果、矛盾、替代解释、范围及业务影响是否有依据。本次8例模型分类吻合6例，仍有4例自报confirmed及其他无依据断言，不代表自由语义通过。严重程度建议仍须人工核实实际影响再确认。累计费用估算0.123742元，详见[本批验收](../.scratch/agent-quality-followup/validation.md)。

前端已取消远程字体CSS导入，使用原字体名和本机回退，离线字体服务不会阻塞页面加载。启动与使用十种场景见[模拟场景说明](simulated-incident-scenarios.md)。

## 模型语义后续实验

2026-11-09 V2模型输入进一步使用 `bounded-observations-v1`：保留关联代号、移除个人/凭据占位字段，并添加带证据ID的观测方向摘要及逐事件解释边界。原始页面证据和V1输入不改。固定四例有限字段检查4/4通过，但原始文字仍会编造或扩展轨迹；不能视为自由根因推理通过。分类改善包含服务端摘要辅助，详见[上下文评估](../.scratch/simulated-incident-edge-cases/context-validation.md)。

2026-11-08 增补提示词以保留矛盾、多个方向及因果不确定性，不改变服务端证据约束或确认能力。固定四例真实评估中原始kind均为hypothesis、impact均为空，但多方向分类及自由文字仍有失败，不能视为完整语义通过；[验收状态](../.scratch/simulated-incident-edge-cases/semantic-validation.md)保留旧新报告、失败与费用。付费质量矩阵可额外设置 `CHECK_RAW_AGENT_SEMANTICS=1` 检查原始kind/impact等有限字段，仍须显式启用 `RUN_BAILIAN_QUALITY=1`；字段断言不是完整文字语义验收。
