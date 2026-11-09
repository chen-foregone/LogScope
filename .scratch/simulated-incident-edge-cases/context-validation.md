# 模型证据上下文改进与有限评估

2026-11-09，基线7ecee51，承接工单02的有限提示/分析协议评估。保持模拟日志、真实北京qwen-plus、累计10元预算、只读工具及人工等级决定。不修改模型schema、确认权限、数据库结构或历史记录；不勾选A33或完整A34。

## 改动

模型可见V2事件仅保留fields.correlation_id；个人信息代号和凭据屏蔽占位符不参与模型分析。结构化观测、来源、时刻、错误码、原始传播轨迹保持。每条V2观测旁增加解释边界：没有业务影响观测、跨组件因果尚未建立；有轨迹时明确其未命名依赖及只适用于本事件。边界不删除轨迹对本事件的传播声明，也不把none_observed改成unresolved。

上下文协议bounded-observations-v1按受控signal归纳观测方向及证据ID；多个方向汇总unknown。这是服务端事实摘要，不是模型独立发现的根因。初始输入和工具响应均应用同一投影；V1保持原样。保留数据库和UI证据完整脱敏字段，不把模型投影写回存储。场景名称、操作者答案和评分规则不交给模型。

## 测试

- context-red.txt：新增HTTP业务测试在模型上下文仍有email/token时先失败（1例，9.71秒）。
- context-targeted.xml/txt：43通过、20付费默认跳过，227.37秒；覆盖V1/V2、工具流程、新场景、存储与重启，断言模型投影精简而页面证据仍完整。
- 类型检查、Vite生产构建通过；原有大包提示保留。
- Standards、Spec独立静态审查均0项；不等于付费模型语义通过。
- context-full.xml/txt：296通过、24付费跳过、297环境准备错误，1385.33秒。约一半时专用PG17容器64MiB共享内存不足（日志03:15:27 UTC），数据库进程随后SIGSEGV退出并自动恢复；从首个迁移失败开始，剩余用例因recovery mode无法建测试schema。容器数据卷/宿主磁盘均有余量，没有删除诊断数据或修改全局配置。数据库03:18:18 UTC恢复ready。此轮不是全绿，不能把未执行用例算通过。
- 修整测试环境：后端及UI测试连接URL同时传search_path和max_parallel_workers_per_gather=0，避免URL options覆盖进程PGOPTIONS。仅测试连接生效；审计schema解析取第一个空白前的schema token，原有敏感值检查不变。生产代码不因环境故障变更。
- 从完整XML提取297个环境错误nodeid保存context-recovery-cases.txt，仅补跑这些未执行用例；context-recovery.xml/txt：297全部通过，1369.91秒，包含剩余后端和Chromium用例。与首次296通过、24跳过共同覆盖整套617项，分次完成，非单次全绿。
- context-audit.xml/txt：5项schema导出安全审计全部通过，560.97秒，确认连接URL及schema解析辅助调整没有缩减审计范围。它们与前述套件重叠，不另加到总数。测试连接调整再次Standards审查无发现；真实评估文档Spec复审无发现。

## 真实模型首次四例

context-live.xml/txt：4通过、15未选中，78.63秒；断言仅代表原始kind/impact及多方向category等有限字段和受控保存契约。每例只执行一次；不重复挑选答案。各报告保存安全校验通过的原始字段、服务端产物、提示SHA和上下文版本。

| 案例报告 | 原始文字评审 |
| --- | --- |
| [noise](../agent-quality-followup/live/order-noise-85bf1d71.json) | 有界通过：权限观测与不同请求的超时分开，缺少因果证明；不能将不同代号解释成原因必然独立。不是全面质量证明。 |
| [multiple](../agent-quality-followup/live/order-multiple-c2e28b32.json) | 部分：保留unknown并明确无因果连接；但把网关信号归为dependency_timeout，并编造本案例不存在的causal_trace unresolved。 |
| [conflicting](../agent-quality-followup/live/order-conflicting-c9a7dfa4.json) | 失败：保留矛盾，但仍说匿名轨迹声明Redis到应用到网关传播，文字前后不一致。 |
| [missing](../agent-quality-followup/live/order-missing-6b551b7e.json) | 部分：具体依赖未知，未确认跨组件传播；未清楚区分数据库成功空与Redis不可用，其他范围查询建议缺少人工另开调查要求。 |

相对前批，新样本的多方向category从resource变unknown，四例kind仍hypothesis、impact仍null；有服务端摘要辅助，不能把类别吻合归功于模型独立分类。自由因果推理、轨迹真实性及范围建议依旧存在限制。服务端保护继续拦住这些文字成为确认结论或自动行动。

## 费用

8次真实调用，4549+4830+4825+4705=18909微元（0.018909元），预留均归零。此前累计0.159536元，本批后累计估算0.178445元。以供应商账单为准，不改变原10元累计预算。
