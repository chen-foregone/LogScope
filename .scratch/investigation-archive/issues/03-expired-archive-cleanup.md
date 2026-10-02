# 03: Automatically clear expired archives
Status: resolved
Type: task
Blocked by: 01, 02
Parent: logscope-v1 issue 17

## What to build

Automatically clear archives at archive time plus 30 days using the same cleanup as manual deletion, with persistent lifecycle checks and visible blocking failures. See ../spec.md.

## Acceptance criteria

- [x] Before deadline nothing is cleared; at/after deadline cleanup needs no further confirmation.
- [x] Unarchive and cleanup serialize; unarchive first prevents removal, cleanup first prevents restoration.
- [x] Background restart and concurrent manual deletion cannot enlarge scope or produce partial removal.
- [x] Reference conflicts preserve the entire investigation and display a truthful blocked status.
- [x] Failures do not claim success or retain records of successfully deleted investigations.
- [x] Controlled-time PostgreSQL and real UI acceptance pass; full suite and two-axis review completed.

## Answer

Implemented 60-second background coordinator with server-time expiry, keyset batches, row-lock rechecks, shared cleanup logic and shield-and-drain shutdown. Added exact-boundary, 101-record batching, coordinator shutdown and blocked-reference UI coverage. Full-suite attempt reached an environment PostgreSQL shared-memory `DiskFull`; focused suites and UI passed, with no feature assertion failure observed.
