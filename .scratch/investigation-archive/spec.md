# Investigation archive and permanent cleanup
Status: ready-for-agent
Parent: logscope-v1 issue 17

## Problem Statement

Users need to retain investigations until they choose to archive or delete them. The old creation/end-based retention period does not represent that choice.

## Solution

Archive preserves all investigation materials for 30 days from the server-confirmed archive operation. Unarchive cancels that deadline. Manual deletion clears immediately after explicit irreversible confirmation. Expired archives use the same atomic cleanup, without further confirmation. Successful cleanup leaves no investigation record, title, receipt or tombstone and never modifies source logs.

## User Stories

1. As a record keeper, I want unarchived investigations retained without an automatic deadline, so that materials remain available.
2. As a record keeper, I want to archive an ended or pending investigation, so that its materials are cleared after 30 days.
3. As a record keeper, I want to see the archive time and cleanup deadline, so that I know when materials disappear.
4. As a record keeper, I want to unarchive, so that automatic cleanup is canceled.
5. As a record keeper, I want a subsequent archive to start a fresh 30 days, so that a new explicit choice has a new deadline.
6. As a record keeper, I want repeated archive requests not to postpone cleanup, so that retries do not silently extend retention.
7. As an investigator, I want to unarchive before starting another attempt, so that active work is not scheduled for removal.
8. As an investigator, I want running attempts and active queries protected, so that cleanup cannot remove ongoing work.
9. As a record keeper, I want to inspect the actual removal scope before manual deletion, so that I understand its irreversible effect.
10. As a record keeper, I want manual deletion without first archiving, so that unwanted materials can be removed immediately.
11. As a record keeper, I want scope changes to invalidate a stale confirmation, so that I never confirm a different scope.
12. As a record keeper, I want expired archives cleared automatically, so that no second action is needed.
13. As a record keeper, I want a shared-reference conflict to reject the whole cleanup, so that other investigations remain usable.
14. As an investigator, I want original logs untouched, so that the investigated environment remains read-only.
15. As a record keeper, I want no title or deletion receipt retained after cleanup, so that no application investigation record remains.
16. As a user, I want missing resources described truthfully after an uncertain delete response, so that the UI does not invent proof of success.
17. As a maintainer, I want archive/unarchive/start/cleanup races serialized, so that the committed lifecycle is authoritative.
18. As a maintainer, I want rollback on cleanup failure, so that no partial removal is reported as success.

## Implementation Decisions

- Replace end-based retention with explicit archive time and a monotonically increasing archive version. Existing records remain unarchived; old deadlines are cleared by migration.
- Archive and unarchive take an expected archive version; stale transitions return conflict. Repeating an operation at the current desired state does not extend the deadline.
- Archive state is independent of attempt status. Running attempts and active queries block archive and deletion. Archived investigations cannot start new attempts.
- Server time is authoritative; archive deadline is archive time plus 30 days. At or after the deadline an archive is eligible, not proof that deletion succeeded.
- A shared cleanup operation serves manual confirmation and background expiration. It locks the investigation before rechecking lifecycle, active execution/query facts, scope and incoming references.
- Manual confirmation binds to actual scope. An application receipt or tombstone is not retained; after deletion a retry can only observe absence.
- Source-level shared admission controls are not investigation-owned data.
- Existing user routes have no login/RBAC model. This work does not introduce an account system or equate a preview identity with authorization. Deployment access protection remains an explicit security prerequisite before exposing destructive routes beyond a trusted environment.
- Application cleanup does not promise immediate physical erasure of database backups or transaction logs.

## Testing Decisions

- User-approved primary seam: real HTTP API, isolated PostgreSQL and injected server clock. Assert public behavior and persisted lifecycle through HTTP; inspect database facts where necessary to prove complete cleanup and reference safety.
- User-approved UI seam: Chromium against real API/database, including narrow mobile viewport, navigation and refresh.
- User-approved background seam: expiration entry point against isolated PostgreSQL with controlled time; verify before/at deadline, restart and races.
- Reuse existing investigation, attempt, retention and UI test facilities. TDD one behavior at a time, no mocks of internal business helpers.
- Destructive acceptance only uses isolated test data; never production/development investigation deletion.

## Out of Scope

Account/RBAC implementation, source-log mutation, restore after cleanup, deletion receipts, permanent archive history, backup erasure, retention extension and the superseded end-based retention policy.

## Further Notes

Approved in chat on 2026-11-05. Three vertical slices: archive lifecycle; manual deletion; background expiration. Parent issue 17 is retained unchanged as historical context. This specification supersedes its conflicting retention/confirmation rules. A first archive-only increment does not claim automatic cleanup is already available.
