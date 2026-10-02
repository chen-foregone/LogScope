# 01: Archive and unarchive investigations
Status: resolved
Type: task
Blocked by: None
Parent: logscope-v1 issue 17

## What to build

Deliver archive/unarchive through migration, HTTP API, real UI and tests. Replace creation/end-based expiry with server-confirmed archive plus 30 days. See ../spec.md.

## Acceptance criteria

- [x] Existing and new unarchived investigations have no automatic deadline.
- [x] Archive preserves all materials, displays archive time and deadline, and survives restart.
- [x] Repeated archive does not extend the deadline; stale versions cannot overwrite newer transitions.
- [x] Unarchive cancels the deadline; a later archive starts a fresh 30 days.
- [x] Running attempts/active queries cannot archive; archived investigations cannot start.
- [x] API and real UI tests cover state, errors and mobile display.

## Verification

Real HTTP with isolated PostgreSQL and controlled clock; Chromium with real API. Typecheck/build and focused tests for every batch. The later cleanup slices are required before claiming automatic deletion works.

## Progress (2026-11-05)

- Initial batch implemented migration, versioned archive/unarchive routes, start guard, removal of implicit terminal deadlines, archive UI and API/UI tests.
- HTTP red: creation still returned an implicit deadline. Green after implementation.
- UI dev-server navigation timeout was an environment failure, not feature red; preview-mode red reproduced missing archive region. Green after implementation.
- Two HTTP tests plus real Chromium mobile UI test: 3 passed in 13.27s. Typecheck and production build passed (existing large-chunk warning).
- Still pending: precise archive boundary, active-query/concurrency/migration regressions, replacement of old retention tests/documentation, two-axis review. No completion claim and no destructive production/development operations.

## Answer

Implemented with migration 0017, versioned archive/unarchive routes, start/replay guards, lifecycle projection and responsive UI. Focused archive/retention suite: 25 passed. Typecheck/build and review passed. Automatic and manual cleanup are covered by dependent slices in the same implementation branch.
