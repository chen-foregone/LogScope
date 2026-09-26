# 调查材料保留模块（工单16）

## 已批准范围

首版无延期。无轮创建起30天，有轮最近实际结束起30天；运行中保护，到期前7天提醒，到期仅待确认。未操作不删除，也不续期。17负责真实清理。

## 公开入口与调用链

`GET /api/investigations/{id}` → investigations.detail → attempts.current_view → attempt_views + retention.retention_view → InvestigationView。创建、启动、取消及失联响应复用同一投影。执行侧结论/证据接口返回自身模型，完成后GET详情读取保留状态。

`retention`包含phase与issue；旧retention_expires_at保留。没有每次变化的判定时间字段，避免破坏既有响应重放稳定性。判定使用传入的服务器时钟，不由浏览器决定。详情使用REPEATABLE READ；日期输出UTC，界面UTC+8。

## 表与事实

investigations保存created_at、updated_at、retention_expires_at；attempts以investigation_id关联，attempt_number确定轮序，ended_at是实际结束，不是日志ends_at。controlled_executions按attempt_id关联并保存valid；evidence属原轮，attempt_evidence关联本轮纳入材料，conclusions保存逐轮结论。本单没有新表、迁移或延期历史。

retention_view检查所有轮的运行状态，任一调查中即running。否则核查持久期限是否等于无轮created_at或最新轮ended_at加30天；缺失或不一致为inconsistent，不在读取时修复。正常事实按now < expires-7d、expires-7d <= now < expires、now >= expires分为retained、expiring、awaiting_cleanup。

## 事务、锁与幂等

保留投影没有写操作，也不占写锁。启动沿用READ COMMITTED，先锁调查行，再锁instance_admission固定行，锁后统计运行容量；数据库唯一约束兜底同调查运行唯一性。启动请求按调查+请求标识+规范请求体重放，相同返回当前详情，不是历史响应快照；不同内容409。本单未新增写操作或幂等协议。

启动成功同事务写新轮、接纳执行、必要的脱敏密钥并清空期限；失败回滚该事务新写入，旧已提交调查、轮次、证据、结论不撤销。正常完成在结论事务内写ended_at、终态、执行失效及结束+30天。取消须confirm_stop安全确认；失联先隔离后确认进程退出，不仅因请求停止就起算期限。

deadlines.enforce_deadline是特殊边界：先提交超时归属和执行隔离，再返回403；确认失败时先提交隔离再返回503。错误响应不保证数据库未变。pending保存点可撤销本次未接纳业务产物，不能撤销更早提交的证据。

## 与17的安全衔接（要求，不是已实现删除能力）

awaiting_cleanup仅展示，不是清理令牌或授权。17的预览须绑定调查、实际范围及生命周期版本/等价身份。确认事务至少先锁调查行，与启动相同锁域，再重读运行轮/活动查询、最新轮身份及结束时间、当前期限、预览范围和引用事实；未到期、运行保护、事实异常或预览过时必须拒绝并要求重预览。启动先提交，旧确认不得删除新轮；清理先提交，启动须看到资源不存在/清理事实，不能凭旧预览恢复材料。具体删除schema和重放结果由17设计并验收。本单没有清理版本schema或竞争最终证据。

## 测试证据边界

backend/tests/test_retention.py经真实HTTP TestClient、隔离PostgreSQL迁移及可控时钟覆盖精确边界、三终态、第二轮结束重算、材料仍可读、重启一致、停止/失联未确认保护。人工异常测试直接安排空期限/错期限/缺ended_at，明确不是正常流程。

tests/test_retention_ui.py经真实网络API和Chromium验证正常/即将到期/待确认/异常、390px布局和刷新不续期。UI时间场景通过隔离库安排一致的创建与期限，不伪造响应；不是浏览器内控制业务时钟。页面不自动倒计时，阶段以最近读取为准。未接入清理，不证明源日志删除保护或清理竞争。

## 明早问答（仅问题）

1. 无轮调查与有轮调查的30天各从何时起算？日志ends_at为什么不能代替ended_at？
2. 期限、轮次状态和证据关联分别在哪些表？phase为何不需要存表？
3. 11月4日12:00Z创建无轮记录，11月27日12:00Z和12月4日12:00Z各是什么阶段？刷新改哪些事实？
4. 到期后未确认清理，启动新轮再安全取消，期限如何变化？旧证据是否还在？
5. 首轮已提交证据，第二轮接纳时出错。新轮/执行/期限/旧证据各留下什么？同键重试意味着什么？
6. 总时限到达后提交证据，退出确认SQL故障导致503。数据库可能已经改变什么？这与普通启动失败有何不同？
7. 人工把调查中记录期限改为昨天，界面应是什么阶段？能否根据日期清理？区分已有实现与17待验证能力。
8. 为什么选择读取时派生阶段，而不是每天更新数据库阶段和updated_at？对列表筛选和长时间打开页面有什么影响？

回来后一次讨论一道，先推理、再核对代码证据、最后复述；答题不影响工程完成条件。
