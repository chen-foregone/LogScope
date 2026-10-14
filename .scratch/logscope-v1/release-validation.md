# 工单20 发布验收记录

## 范围与证据口径

2026-11-06，Windows 本机、真实 PostgreSQL 17、真实业务 API、Chromium、模拟 HTTP 日志、北京百炼 qwen-plus。用户已授权继续使用模拟日志；本记录不代表生产日志供应商或完整首版规格验收。A33 及完整 A34 延后，不能以本单状态替代这些门槛。

归档按已批准的 [配套规格](../investigation-archive/spec.md) 验证：未归档无限期保留；主动归档30天后自动清理；手动删除需预览确认。原 A30/A31 的“结束起算、到期后仅手动清理”已被取代。父工单17保留历史原文。

## 最终结果

- 全量既有回归 **537 passed, 1 skipped, 1948.68s**。跳过项是默认关闭的付费真实模型测试；该轮开始于本次缺陷修复前，不能冒称其完整覆盖最终修改。
- 修复后最终相关回归 **83 passed, 194.17s**：9项发布专项、Agent运行、结论及两个真实浏览器主流程，见 `issue20-final-focused.xml` / `.txt`。这组与全量有重复，不将数量相加宣传。
- 最终真实付费案例 **4 passed, 52.41s**，见 `issue20-live-final.xml` / `.txt` 和 `issue20-live-final*.json`。全部走隔离真实PG/API、HTTP模拟来源、真实北京qwen-plus。普通超时/因果轨迹/矛盾均保留假设，空证据为未确定。矛盾结果由服务端结构规则强制保护，不能宣称模型自己正确处理了矛盾。
- 本次与前序全部成功调用跨schema累计本地估算 **31547微元 = ¥0.031547**，其中前序5151、本次初次2850、首轮四案例11695、最终四案例11851微元。401失败未获计费用量；供应商账单为准。不能将这笔跨schema汇总宣称为单schema共享硬限额。
- 类型检查及最终production build通过；`git diff --check`通过。独立标准轴、规格轴复审在限定范围内无剩余阻塞；模型建议仍可能包含未经验证的推测。

## 执行记录

- 前端 `vue-tsc -b`、Vite production build 通过；现存大于500kB bundle 提示保留。
- 全量回归：`issue20-full.xml` / `issue20-full.txt`，使用生产 preview、独立5273/8100端口、连接级禁用PG并行查询；最终结果见工单 Answer。
- 新增发布专项：`backend/tests/test_release.py`。`issue20-release.xml` 记录第一轮修复后的13项发布/Agent专项；`issue20-focused.xml` 记录增加私密问题上下文后的8项发布测试及UI初测失败。`issue20-agent-ui.xml` 为移动端溢出修复前的失败记录。最终全部通过证据是 `issue20-final-focused.xml`。
- 第一次缺陷复现：跨截止提交、过度确认各失败1项；修复后专项通过。不能把最初失败报告当作最终结果。
- 真实模型初轮：`issue20-live.xml`、`issue20-live-report*.json`；其中矛盾案例虽连通成功，人工语义检查失败，`issue20-live-before-guard.json` 保留该失败证据。最终通过证据以 `issue20-live-final*` 为准。普通套件默认跳过付费测试。
- 未删除历史测试schema或他人诊断文件；迁移/重启仅操作隔离测试数据库。

## A01–A35 / US责任映射

下表列出可复核行为及测试入口，最终执行结果由上述 JUnit 文件约束。“模拟范围”只说明已经授权的验收边界，不能扩张为真实供应商证明。

| 组 | 用户故事 | 行为证据（文件均相对仓库） | 口径 |
| --- | --- | --- | --- |
| A01 | US-01 | `backend/tests/test_investigations.py`、`test_attempts.py`：待处理、缺服务/范围拒绝、刷新 | API/PG |
| A02 | US-02,03 | `test_attempts.py`、`tests/test_ui.py`：固定最近小时、24h边界、绝对范围、运行不可改 | API/PG/UI |
| A03 | US-05,06,07 | `test_conclusions.py`、`test_queries.py`：证据不足正常完成、必要来源失败、旧轮不变 | API/PG |
| A04 | US-14,15 | `test_attempt_history.py`：三种终态重开、八轮同钟顺序、历史请求键重放、材料不继承 | API/PG |
| A05 | US-16,47 | `test_attempts.py`、`test_attempt_history.py`：同键并发、不同键竞争、接纳事务失败回滚 | API/PG |
| A06 | US-17 | `test_attempts.py`、`tests/test_ui.py::test_capacity_ui_explains_no_start_and_only_manual_retry`：三名额、拒绝不造轮 | 多进程/PG/UI |
| A07 | US-08 | `test_queries.py`、`test_log_sources.py`：30s、额外两次、2s/5s、来源请求行为 | 受控时钟/真实HTTP模拟来源 |
| A08 | US-08,10 | 同上：401/403/参数不重试、429闸门、错误体/凭据不回显 | 模拟协议边界 |
| A09 | US-09,47 | `test_deadlines.py`、`test_release.py::test_agent_save_crossing_deadline_discards_all_products`：10分钟及最后SQL跨界回滚 | API/PG |
| A10 | US-10,29 | `test_queries.py`、`tests/test_ui.py`：混合覆盖、未查来源、技术失败区别 | API/PG/UI |
| A11 | US-11,12,17 | `test_cancellation.py`、`test_agent_runtime.py`：停止过渡、实际退出前占名额、取消抵抗 | API/PG/受控异步边界 |
| A12 | US-13,47 | `test_cancellation.py`、`test_interruption.py`、`test_release.py`：已保存保留、迟到写与旧身份拒绝 | API/PG |
| A13 | US-18,19,47 | `test_interruption.py`：存活/退出/未知身份、承载与接纳进程、失联隔离 | Windows真实子进程 |
| A14 | US-19,36,47 | `test_release.py::test_active_agent_process_exit_is_required_before_restart_releases_slot`、`test_interruption.py`：不续跑、存活拒绝新轮、退出才释放 | Windows真实Agent子进程（模型替身） |
| A15 | US-09,11,18,47 | `test_deadlines.py`、`test_interruption.py`：事件排列、双锁序、既有终态归属与旧写隔离 | API/PG竞争 |
| A16 | US-20,21 | `test_evidence.py`、`test_agent_runtime.py`、`test_release.py`：证据和问题上下文先脱敏、非法材料拒绝 | API/PG/模型替身 |
| A17 | US-21,22 | `test_evidence.py`、`test_historical_evidence.py`：跨轮代号稳定、跨调查独立、秘密屏蔽 | API/PG |
| A18 | US-23,26 | `test_evidence.py`：稳定ID/文件偏移、并发去重与冲突 | API/PG |
| A19 | US-23,24 | 同上：不同来源/时刻、不可靠ID文本等同仍独立 | API/PG |
| A20 | US-15,25,26 | `test_historical_evidence.py`、`test_release.py`：范围、来源轮、引用计数、原轮不变、结论引用有效性 | API/PG |
| A21 | US-27 | `test_evidence.py`、`test_historical_evidence.py`：结构材料可核验、仅链接不可核验 | API/PG |
| A22 | US-06,28,29 | `test_conclusions.py`、`test_release.py::test_trace_does_not_confirm_unsupported_model_explanation`：相关不确认、单条充分受控轨迹、自由模型解释仅假设 | 受控分析及真实模型边界分开 |
| A23 | US-30,31 | `test_verification.py`、`tests/test_verification_ui.py`：证据失效、替代支撑、新轮恢复、只读历史 | API/PG/UI |
| A24 | US-01,32,33 | `test_severity.py`：定性四级、信息不足、版本历史、不以根因可信度降级 | API/PG |
| A25 | US-34,35 | `test_severity.py`、`test_agent_runtime.py`、`tests/test_ui.py`：人工优先、建议需确认、过时版本和跨轮保护 | API/PG/UI |
| A26 | US-37 | `test_investigation_listing.py`、各写入测试：业务变更更新时间、读取不变 | API/PG |
| A27 | US-38,39 | `test_investigation_listing.py`、列表UI：组合筛选、UTC+8、左闭右开、最新轮服务 | API/PG/UI |
| A28 | US-40,41 | 同上：相对范围固定快照、翻页沿用、全集统计 | API/PG/UI |
| A29 | US-05,26,41,42 | 同上：无轮/多轮/过渡/终态混合四统计，无虚构告警完整率 | API/PG |
| A30 | US-43,44 | `test_archive.py`、`test_retention.py`、`tests/test_retention_ui.py`：归档30d、前7d、读取不延期、运行保护 | 已批准归档新契约 |
| A31 | US-45,46,47 | `test_archive.py`、`tests/test_archive_ui.py`：手动预览确认、自动到期、启动竞争、跨调查引用保护、不删来源 | 已批准归档新契约 |
| A32 | US-36,48 | `test_release.py`：空schema真实迁移；完整多轮、引用、两结论版本、人工等级、归档信息经0017→0018和两次重启完全相等 | API/PG；0018纯增量表由空Agent表降级建立0017前置状态，非原始旧版二进制 |
| A33 | US-04,20,21,23,48 | `test_log_sources.py` / `tests/test_log_source_ui.py`只覆盖模拟HTTP协议、脱敏和只读工具 | **真实来源未验收；用户本期排除** |
| A34 | US-04,28,29,34,47,48 | `test_agent_live.py`真实北京调用；`test_agent_runtime.py`、`test_release.py`、`tests/test_agent_ui.py`受控身份、预算、取消、重启、等级建议 | **模拟来源+真实模型限定验收；不等于完整A34** |
| A35 | US-01,02,05,12,14,38,41,45,48 | `tests/test_ui.py`、`test_archive_ui.py`、`test_retention_ui.py`、`test_log_source_ui.py`、`test_verification_ui.py`、`test_agent_ui.py` | Chromium/真实API/PG；付费模型替身单独标注 |

## 审查及修复

独立标准轴、规格轴审查共同发现了模型解释过度确认；标准轴另发现最后SQL跨截止和旧UI文案；规格轴另要求提供案件上下文及补真正活动Agent重启证明。

- 自由模型解释统一保留假设/未确定；自报 confirmed 和有效传播轨迹都不为任意解释背书。旧受控协议仍可凭明确轨迹产生固定的确认结论。
- Agent结论、影响建议及关联核验共用savepoint和 `finalize_deadline`，最后SQL跨截止全部回滚，持久隔离保留。
- 案件上下文只含白名单症状标签及数据库固化的服务/时间。标签来自未验证报告（包括可能被否定的词），不是观测事实；无法匹配时为空。不发送任意问题描述、人名、URL、令牌或注入指令。此方案只提供有限上下文，不声称完整自然语言理解。
- 子进程活动Agent退出前阻止新轮；退出后留存未结算费用预留，不自动恢复旧图；迟到旧身份仍拒绝。
- 真实Agent不再显示“未接Agent”；异常结束不再回落到“正常排查结束”。新增390px浏览器验收。
- 长来源代号等详情文本可在任意位置换行，修复390px横向溢出；页面成功和失败、刷新保持、无秘密回显及实际宽度均已重测通过。

## 发布边界

### Standards 复审

初审3项：P1模型解释过度确认、P2保存跨截止、P2旧UI文案。均已修复并复测；独立复审未发现剩余具体阻塞。移动端溢出在复审后由浏览器测试发现，补换行并在最终83项中通过。

### Spec 复审

初审3项：P1模型解释过度确认、P1案件上下文缺失、P2缺少活动Agent实际进程重启验收。已分别通过假设边界、有限安全上下文和真实子进程测试解决。独立复审对限定模拟来源范围未发现剩余可执行阻塞；保留模型推测和A33/完整A34例外。审查人员只读检查，没有冒称亲自执行付费调用或测试。

本机受控部署，无用户登录/RBAC/组织隔离声明；仅支持本机Windows执行身份。未知执行或未知费用保持保守占用。升级前停旧写入，禁止新旧版本混跑；关闭真实Agent再回滚，不能用删除预算表重置已发生费用。供应商账单为准，测试各隔离schema的费用需人工汇总，schema内预算不能充当账号级硬限额。

真实日志供应商、真实凭据只读权限和完整 A33/A34 继续留待后续接入；本次可交付的是用户已授权的模拟来源发布验收，不能据此宣称原规格首版全部验收通过。
