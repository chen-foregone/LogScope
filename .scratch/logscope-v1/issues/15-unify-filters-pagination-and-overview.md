# 15: 统一筛选、更新时间、分页及四项真实概况
Status: resolved
Type: task
Blocked by: 11, 12, 14

## What to build
纵向交付筛选描述及时间快照 schema、列表/分页/概况API、统一UI；按持久业务事实计算全部匹配集合M，核对各写入路径更新时间，不用样例常量。

## Blocked by
- 11 执行实例级三调查并发限制
- 12 引用历史证据并保持跨轮一致性
- 14 评估严重程度并保持人工决策优先

## Spec
[首版实施规格](../spec.md)：D6、D8。
用户故事主要交付：US-37、US-38、US-39、US-40、US-41、US-42。
验收组主要交付：A26 最终逐项核对、A27、A28、A29；A35 筛选分页概况部分。

## 开工门槛
须明确多轮服务可变时调查级服务展示、搜索与服务筛选采用的字段/最新轮投影及无轮取值契约；现未确定，needs-info 待该具体契约齐备解除，不能暗混历史与当前服务。不重新批准四项公式。普通分页、搜索及快照实现可在授权内设计；不强加跨页数据库内容快照隔离。

## 验收标准
- [ ] 搜索、服务、最后更新时间共同决定M，按明示多轮服务投影契约一致作用于列表、搜索、概况；日志发生时间不替代调查更新时间。
- [ ] 创建、启动、状态、新增证据、结论、严重程度变化逐项更新最后更新时间；查看、搜索、筛选不更新也不延期，核对01起所有写入路径。
- [ ] 统一时间基准保存、UTC+8展示；固定时间范围左闭右开，边界精确一致；每轮日志范围独立展示。
- [ ] 相对范围仅应用或刷新时计算一次，列表与概况共用该边界，翻页保持不变；不把翻页时新算范围混进同次请求。
- [ ] 调查总数=|M|；活跃调查数=M中整体调查中的数量，每调查一次，含正在停止与正在确认执行状态；待处理数=M中整体待处理数量。
- [ ] 证据总条数=对M各调查最新轮已保存、去重、已纳入条数求和，无轮贡献0，不跨调查去重，不累计历史轮；四项统计覆盖全部M而非当前页。
- [ ] 已完成、失败、已取消、待处理不计活跃，混合多轮/无轮/各阶段数据下API与UI一致显示真实四项概况。
- [ ] 统一称证据条数，不解释为扫描量、故障数或根因数；不显示虚构告警、待处理告警、证据完整率或样例常量。

## 测试与完成证据
HTTP API + 真实 PostgreSQL 构造经业务API形成的多页混合集，验证全部四公式、各时间边界、所有更新时间触发及读取不变；可控时钟安排相对边界。增量UI E2E验证筛选、分页、刷新及概况联动并保留请求快照。完成包含 schema/API/UI 与全集统计测试，20只汇总回归不代做本单主要验收。

## Comments

### 2026-11-04 - Service projection and implementation approved
- User confirmed the implementation plan and authorized implementation.
- Investigation service is the latest attempt's service_id by attempt_number; no attempt means null (UI: 未选择服务).
- Exact service filtering and keyword search use that same current service ID, never historical attempt services. Directory names are display labels; removed IDs remain readable and filterable.
- Keep the approved four overview formulas. Freeze relative time boundaries per apply/refresh, not database contents across pages.
- First batch: HTTP input validation and the injected creation clock, tested through HTTP against PostgreSQL. Pagination, aggregation, UI, full regression and final review remain pending.
- Batch 1 verified: invalid listing parameters return 422 without changing the record; creation uses the injected business clock and replay/read preserves timestamps and retention. Red failures observed before each implementation. Targeted PostgreSQL/API regression: 22 passed; vue-tsc -b and git diff --check passed.
- Batch 1 does not implement valid filters, pagination or time snapshot resolution yet. The existing list response remains unchanged until batch 2; do not treat the accepted parameter schema as completed filtering support.

### Implementation and compatibility notes
- List API now returns bounded lightweight summaries (default 20, maximum 100), pagination, resolved filter and the four full-match aggregates. Detail remains the full record endpoint. This is an intentional list-response contract change; repository consumers/tests are updated.
- Latest attempt is selected by persistent attempt_number. Title/UUID/current service ID literal case-insensitive search, exact service ID, and updated_at [from,before) form one matched set. No attempt means null service/pending/zero evidence. No historic service matching or historic evidence accumulation.
- List and aggregate statements run in one REPEATABLE READ transaction. Relative boundaries resolve once on apply/refresh; pagination and detail-return revalidation reuse absolute boundaries. Cross-page database contents are not frozen. Browser refresh reapplies the relative range at page 1 per approved plan.
- UI uses lightweight components and a page-local composable, guards sequence and originating route, keeps prior results on error, displays timestamps UTC+8, and restores applied filters through the URL. No new runtime dependency or database migration for issue15.
- Creation now shares the injected business clock. HTTP clock tests cover creation/start/evidence/conclusion/severity/confirmation/cancel/reopen/historical inclusion/timeout/lost/completion/source failure and read/replay invariance. Existing deadline/query/concurrency suites retain terminal arbitration coverage.
- Concurrent issues13/16/18 changes are preserved. Issue15's commit uses an isolated index to exclude their work and never resets the shared index. Retained-but-unverifiable evidence remains an included record for count purposes; verification is not a hidden deletion filter.
- UI fixture supports UI_PORT/UI_API_PORT and optional UI_PREVIEW=1. Vite proxy target is configurable for isolated ports; defaults stay 5173/8000. Production preview is used because development resource loading stalled in this environment.

## Answer
Implementation and issue15 acceptance are complete. The PostgreSQL recovery blocker has been cleared for this scope. Full-suite results are recorded separately below; resolving issue15 does not claim that unrelated concurrent work passed.

### Verification evidence
- First TDD slices observed red for invalid pagination and non-injected creation clock, then green.
- Full backend run before the latest concurrent retention changes: 381 passed (issue15-backend-final.txt).
- Latest compatible issue15 HTTP/PostgreSQL suite: 27 passed. Includes mixed multi-page terminal/active/no-round records, exact formulas, changed service projection, reliable deduplication and no cross-investigation deduplication, fixed/relative boundaries, and write/read timestamp matrix.
- Isolated actual Chromium/API/PostgreSQL issue15 UI: 2 passed (23 records, full-set overview, fixed boundary pagination, detail return, refresh/reload, mobile empty service match, late route/search responses).
- Full isolated UI first run: 65 passed/8 failed while concurrent backend signatures changed; retry of those scenarios: 12 passed/1 timing failure. The existing held-start test now waits for actual committed attempt visibility before refresh.
- Latest full UI retry: 46 passed/27 environment setup errors. Latest compatible backend retry: 40 passed/79 environment errors. PostgreSQL reported shared-memory DiskFull (No space left on device), then recovery mode. These errors are NOT counted as passing or code regressions; no shared database restart or destructive schema cleanup performed.
- Typecheck and production build passed. Existing >500kB bundle warning remains. UI used production preview because Vite development resources stalled in this environment.

### Final code review
Standards axis: prior P2 route overwrite/stale list/replay assertion defects fixed; final incremental review found no remaining actionable defect.
Spec axis: latest-service contract, full-set formulas, UTC+8 presentation and timestamp coverage reviewed; prior display/return defects fixed; final review found no new defect. Browser refresh page1/relative recalculation is approved behavior, not a defect.
Review is static plus the executed tests above, not a claim that the final environment-blocked full run passed.

### Re-run after database recovery
Use PowerShell with PYTHONPATH=backend. Run `.venv/Scripts/python.exe -m pytest backend/tests tests -q -p no:cacheprovider --tb=short` after building with `node_modules/.bin/vue-tsc.cmd -b` and `node node_modules/vite/bin/vite.js build`. Set UI_PREVIEW=1, UI_PORT=5273 and UI_API_PORT=8100 for isolated existing UI tests. Other tasks' dedicated UI tests may have their own fixed-port assumptions; do not run those concurrently on the same ports.

### 2026-11-05 - Final scope acceptance
- User authorized continuing issue15 only, preserving unrelated changes, and explicitly confirmed that concurrent retention/archive work need not block this ticket's closure.
- Re-ran the actual PostgreSQL HTTP suite: `backend/tests/test_investigation_listing.py`, 27 passed in 70.30s. The initial sandboxed attempt had 18 passed and 9 temporary-directory permission errors; the approved unrestricted rerun passed all 27, without changing tests.
- Re-ran actual Chromium/API/PostgreSQL listing E2E: `tests/test_ui.py -k listing_`, 2 passed in 19.14s, 70 deselected. Covers full-match overview, time snapshot pagination, refresh/reload, mobile empty results, and stale route/search responses.
- `vue-tsc -b`, production Vite build, and `git diff --check` passed. Existing bundle-size warning remains; no runtime dependency or database migration was added for issue15.
- Executed the complete existing suite once: 492 passed, 4 failed in 1795.72s. All four failures are `tests/test_retention_ui.py::test_retention_ui_uses_real_api_and_refresh_does_not_extend` parameterizations. During the run, unrelated archive work changed retention from creation-based expiry to archive-based expiry, while these fixtures still arranged creation-based expiry. These are actual failed assertions, not passing tests or a PostgreSQL recovery failure. No retention/archive files were changed to mask them.
- Test environment: `PYTHONPATH=backend`, `PGOPTIONS=-c max_parallel_workers_per_gather=0`, `UI_PREVIEW=1`, `UI_PORT=5273`, `UI_API_PORT=8100`. This is connection-local configuration; no database restart, global setting change, or schema deletion was performed.
- Independent parallel Standards and Spec reviews used verified base `0a8d649`, restricted to issue15 hunks introduced by `72c8f74` and their committed HEAD counterparts. Standards: 0 actionable findings. Spec: 0 actionable findings. Uncommitted archive behavior was excluded from this review, and the full run is not a fixed-version all-green acceptance of other tickets.
- Closure changes only this ticket and its map entry. Existing code and untracked diagnostic artifacts belonging to other work remain untouched and excluded from the commit.
