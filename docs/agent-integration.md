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

## 运行边界

- Agent 只能请求无参数的本轮只读查询；后端绑定调查、轮次、执行身份和时间范围。
- 不提供 Shell、任意 URL、SQL、修复、重启、配置写入或删除日志工具。
- 每次调用前检查执行是否有效、是否停止、是否超时及预算；取消、失联和重启不会自动恢复旧图。
- 日志先通过现有证据适配器脱敏；模型输出需通过版本化 Pydantic Schema 和证据引用校验。
- 模型产物只形成根因假设或受控证据支持的结论；建议不自动执行，严重程度建议需要人工确认。

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
