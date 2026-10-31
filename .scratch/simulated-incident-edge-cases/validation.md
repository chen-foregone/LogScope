# 四类模拟现场边界案例验收

日期：2026-11-08。基线1de5e9b。真实日志延期到低优先级工单21；本批没有接入生产来源，不勾选A33或完整A34。

## 实现及可复现入口

沿用 docs/simulated-incident-scenarios.md 的启动方式，将 --scenario 选择为 order-noise、order-multiple、order-conflicting 或 order-missing。固定UTC 2026-11-01 00:00–01:00范围及四来源目录不变。没有数据库迁移、协议扩展、模型切换、自动修复或续跑。

order-conflicting刻意复用redis-contradiction的unresolved标记，并不提供新增反证协议或独立反证文本。噪声案例仅表示不同请求的观测，不证明这些请求不可能存在共同原因。多方向分类是观测归类，不是故障归因。

## 测试证据

- 首次四例均因Unknown mock scenario失败，添加场景后四例通过。测试经过业务HTTP、真实隔离PostgreSQL与独立HTTP来源，付费模型以替身替换。
- targeted.xml/txt：35通过、20付费默认跳过，94.07秒，覆盖新场景、原场景和Agent质量边界。
- 类型检查与Vite生产构建通过；原有大于500kB包体积提示仍在。
- live.xml/txt：四例真实北京qwen-plus均完成，4通过、15未选中，67.52秒。测试通过仅表示调用/证据/受控结果契约通过。
- full.xml/txt：593通过、24付费默认跳过、0失败，1843.72秒；业务代码与测试在完整套件启动后未改动。包含后端、迁移/进程重启与Chromium回归。不将不同测试运行数量累加。

## 真实模型语义评审

下面引用的JSON保存安全校验通过的模型原始字段与服务端产物。记录器不会保存未通过校验或可能含秘密的自由文本；不是任意模型原始响应的无条件归档。

| 案例与报告 | 原始回答评审 | 服务端结果 |
| --- | --- | --- |
| [noise](../agent-quality-followup/live/order-noise-8b2e12f7.json) | 部分符合：区分不同请求，但将不同关联代号当作故障必然独立的证明；自报confirmed及业务影响无充分依据。 | hypothesis；仅保留被引用数据库权限观测，不把它解释为网关超时原因。 |
| [multiple](../agent-quality-followup/live/order-multiple-a2ebc2bd.json) | 失败：把连接池满直接解释为权限拒绝及504原因，选择单一resource方向，以同时发生支持因果。 | hypothesis；权限、资源分别关联证据，汇总unknown。 |
| [conflicting](../agent-quality-followup/live/order-conflicting-8bc0e810.json) | 失败：忽略unresolved矛盾，声称没有反证，以邮箱/关联代号/时间确认Redis传播。 | hypothesis；保留未解决矛盾，匿名轨迹不绑定具体依赖。 |
| [missing](../agent-quality-followup/live/order-missing-0546b1ad.json) | 失败：列出缺失材料但仍自报confirmed，将应用轨迹扩展为网关504因果；影响人数及持续时间无观测支持。 | hypothesis；unknown，Redis缺失来源明确可见，保留具体依赖身份缺口。 |

四例原始kind全部为confirmed，不能将结构合法、候选方向命中或服务端模板算作模型推理通过。本批交付是可重复的对抗案例与质量评估，不证明模型自由语义有所改善。没有重跑筛选好答案。

## 费用与边界

共8次真实调用：4280+4053+4255+4023=16611微元，即0.016611元；四例预留均归零。此前累计估算0.123742元，本批后累计0.140353元，仍在原10元总预算内。报告保留在既有live目录以继续被预算汇总纳入；本报告不复制账本文件重复计费。估价不能替代供应商账单及账号硬限额。

## 代码审查

按code-review技能，以1de5e9b固定基线对本批工作区及新文件进行独立双轴静态审查。Standards：0项；Spec：0项。审查未运行测试，也未将静态无问题视为真实模型语义通过。
