# 模型语义指令调整与验收记录

日期2026-11-08，基线98e79ee，对应[工单02](issues/02-model-semantic-followup.md)。用户授权继续模拟日志下的语义改进，真实来源仍是低优先级待办21。

## 本批调整

仅增补系统提示，不改模型schema、工具、服务端确认权、保存边界或历史记录：自由分析只能是假设/未确定；无独立业务影响观测则impact=null；unresolved轨迹必须保留矛盾；匿名轨迹不能绑定具体依赖或扩展到网关；代号不同不证明原因独立，代号相同不证明传播；多个观测方向分别解释，汇总unknown。

质量报告增加提示SHA256，允许显式CHECK_RAW_AGENT_SEMANTICS=1检查原始kind、impact及多方向category。此开关不会启用付费测试，仍须RUN_BAILIAN_QUALITY=1。因果文本和反证质量继续人工逐项审查，不能以自动断言通过替代。

## 原定实验及执行限制

预先定义旧提示一例order-multiple失败对照、新提示四例各一次；不重复筛选好答案，继续原累计10元预算。两个旧提示调用命令均在自动审批阶段超时被拒绝启动，未执行、未产生费用；不能声称完成失败测试或模型新旧对照。既有原始失败报告继续作为历史证据，不能伪称本批红绿测试。

以上为首次受阻状态。用户再次授权后，在现有沙箱权限内使用工作区临时目录成功执行新提示四例；提权审批仍超时。实验调整为使用已保存的旧提示首次四例作为历史对照，新提示每例一次，不再额外付费重跑旧提示。不是同批随机对照，也不声称完成旧提示红绿实验。

## 真实模型固定对照结果

semantic-live.xml/txt：3通过、1失败、15未选中，78.74秒。四例均完成真实模型调用和服务端保存；失败发生在原始category断言。普通相关测试与完整套件已在前次执行，生产代码此后未改动。

| 案例及新报告 | 可观察变化 | 仍有问题 |
| --- | --- | --- |
| [noise](../agent-quality-followup/live/order-noise-9dbbd932.json) | 原confirmed变hypothesis，impact从无依据四级属性变null；区分不同请求且保留共同原因可能性。 | 建议核查受影响邮箱/账号，无身份依据；仍称查询Redis可排除参与。原分类unknown不作为语义错误单独定论。 |
| [multiple](../agent-quality-followup/live/order-multiple-495b7e33.json) | 原confirmed变hypothesis、impact=null；矛盾段承认没有直接因果证明。 | 原始category仍resource，应unknown，机器断言失败；现象仍断言连接池导致权限拒绝及504，编造连接池阻碍授权检查机制。 |
| [conflicting](../agent-quality-followup/live/order-conflicting-5f535a86.json) | 原confirmed变hypothesis、impact=null；明确保留unresolved矛盾。 | 现象和解释仍把匿名依赖认定为Redis并扩展网关传播，与自身矛盾段不一致。 |
| [missing](../agent-quality-followup/live/order-missing-d90af038.json) | 原confirmed变hypothesis、impact=null；承认依赖身份未知。 | 仍称应用到网关传播已被观测，混淆数据库空结果与Redis来源不可用。 |

每例2次调用，共8次。新增19183微元=0.019183元，累计估算159536微元=0.159536元，全部预留归零。旧四例和本批四例均保留；提示SHA记录于新报告，语义评审与受控结果分开。不是挑选重试的好答案，也不是统计意义的稳定性证明。4/4原始kind和impact满足本批有限断言，多方向category仍失败；文字均不评为完全通过。

结论：有限提示调整对假设状态、业务影响缺省和显式矛盾有本次样本上的改善，但不能解决完整自由推理语义问题。继续保留服务端证据约束和人工决策，完整A34仍未通过。不为追求全绿继续改提示并重复付费抽样。

## 已执行验证

- 首次默认临时目录回归：5通过、12跳过、11环境准备错误，旧系统pytest临时目录权限拒绝，不是业务断言失败。
- 更换为工作区唯一新临时目录后：semantic-targeted-workspace.xml/txt，16通过、12付费默认跳过，38.26秒。
- 类型检查和Vite构建通过；原有大包警告保留。
- 首次完整套件因同一临时目录限制中止；改用工作区临时目录后semantic-full-workspace.xml/txt：587通过、24付费跳过、6失败，1710.59秒。5项数据库导出安全检查在docker exec处因Docker管道权限拒绝失败；严重程度敏感数据测试在数据库反射时因PostgreSQL共享内存不足失败。后者隔离重跑仍复现，见semantic-severity.txt；不将本轮写成全绿。结论安全测试隔离重跑确认Docker拒绝，见semantic-isolated.txt。未修改Docker、数据库全局配置或删除诊断schema以掩盖环境问题。
- 首次双轴独立静态审查：Standards 0项，Spec 0项；当时真实语义实验未完成，不将提示词覆盖等同模型遵循。四例完成及审计连接修复后最终复审，两轴仍0项阻断；均确认已保留原始语义失败与权限限制，不宣称完整验证。
- 补验：严重程度审计辅助函数连接内增加SET LOCAL max_parallel_workers_per_gather=0，避免URL的search_path options覆盖进程PGOPTIONS；未改全局数据库设置或放宽敏感值断言。semantic-severity-fixed.xml/txt：原共享内存失败用例1通过，11.18秒。最终生产代码与前次全量相同，仅审计测试连接设置增补；不将补验称为再次全量全绿。剩余5项Docker导出检查未补验。

## 提交状态

2026-11-09更新：用户在本机执行脚本，semantic-docker-audit.xml/txt确认5项安全审计全部通过（420.40秒）。前次完整回归的6项环境失败现均分别补验通过，不能将分次补验描述为单次全量全绿。脚本完成暂存后停于git diff --cached --check：历史失败日志有行尾空格；现仅清理该日志行尾空格，并把Windows PowerShell生成的审计文本从UTF-16转为UTF-8，测试内容和结果保持。真实模型多方向分类及部分因果解释仍失败，未以补验通过宣称语义修复。

普通git add无法创建.git/index.lock（权限拒绝）。提权暂存及重试均在自动审批阶段超时，用户再次授权后的提权暂存也超时，命令未执行，因此本批仍未暂存/提交。原core/config.py空diff状态保留；未变更已有98e79ee提交。真实模型评估现已执行；Docker安全审计与Git提交仍需恢复相应执行权限，不是缺少用户业务授权。

用户再次回复“同意”后，提权Docker补验和git add仍在审批阶段超时，均未执行。为提供可执行收尾路径，增加[本机脚本](finish-semantic.ps1)，已通过PowerShell语法检查但未执行：在有Docker访问及Git写权限的本机终端运行 `powershell -NoProfile -File .scratch/simulated-incident-edge-cases/finish-semantic.ps1`。脚本检查暂存区为空，仅补跑5项受限审计；失败则不暂存/提交，成功才按显式文件清单暂存并提交。不会调用付费模型、纳入原有core/config.py改动或自动关闭语义工单。历史阻碍记录保留，新的审计结果写semantic-docker-audit.xml/txt。
