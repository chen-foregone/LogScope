# 02: Confirm immediate permanent deletion
Status: resolved
Type: task
Blocked by: 01
Parent: logscope-v1 issue 17

## What to build

Deliver actual-scope preview, irreversible confirmation and atomic permanent removal through API, UI and isolated database tests. See ../spec.md.

## Acceptance criteria

- [x] Manual deletion needs no prior archive; confirmation binds to current actual scope.
- [x] Running work and stale confirmations are rejected transactionally.
- [x] Incoming references needed by another investigation reject the whole removal.
- [x] All owned materials are cleared or the transaction rolls back; source logs and shared controls are untouched.
- [x] No title, receipt or tombstone remains; retry reports absence truthfully.
- [x] Real UI/API/database tests cover failure, races, navigation and list refresh.

## Answer

Implemented actual-scope preview, irreversible confirmation, local-access guard, child-first atomic deletion, rollback handling, reference protection and mobile UI. Archive API suite: 14 passed; archive UI suite: 2 passed.
