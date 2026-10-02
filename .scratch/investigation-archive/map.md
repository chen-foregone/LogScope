# Investigation archive

## Notes

- Baseline: 33ae95847cf2aed17891074d10f3cc79bd46f73d.
- Pre-existing modifications in investigations.py remove an unused InvestigationList and a blank line; preserve and exclude them from this work's commit.
- Parent: logscope-v1 issue 17, not modified.

## Decisions-so-far

- User approved spec scope, three vertical slices and HTTP/PostgreSQL, Chromium and expiration-entry testing seams on 2026-11-05. See spec.md and issues/01-archive-lifecycle.md through issues/03-expired-archive-cleanup.md.

## Fog

- Destructive routes must not be represented as user-authorized by possession of a scope token. Deployment access boundary requires explicit treatment in cleanup slice.

## Current batch

- Slice 01 initial implementation is in progress; 2 HTTP and 1 Chromium test passed. No commit, full-suite run or final review yet; old retention expectations require explicit updates under the approved policy change.
- Application database has not been migrated; migrations were exercised only in newly isolated test schemas.
- No cleanup/manual delete/expiration coordinator implemented yet. Do not deploy the archive-only increment as a complete automatic-cleanup feature.

## Decisions-so-far

- All three slices are implemented and marked resolved. Focused API/retention evidence is green (14 archive + 11 retention); real archive/delete UI is green (2), plus prior retention UI (3). The full-suite attempt was blocked by PostgreSQL shared-memory `DiskFull` during unrelated sensitive-data reflection; this is recorded rather than called a product failure.
- Two-axis read-only review found and corrected replay ordering, cleanup shutdown draining, broad Python material scans, and unbounded expiry selection. Final review reported no actionable findings.
