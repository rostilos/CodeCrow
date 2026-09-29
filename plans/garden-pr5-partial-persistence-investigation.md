# Garden PR 5: partial review rejected during persistence

## Evidence

Garden project 1702, PR 5, job 10290 (`c02a98ee-98be-42f3-a7a9-e7f88bb44717`) failed at 2026-09-28 22:22 UTC (01:22 Kyiv). Its inference job was `55c6ab02-c8ed-4e66-88a8-8dff9867692b`.

The verifier could not inspect untracked/unavailable Stripe and Magento dependency source. It retained uncertainty as diagnostics, suppressed an unsupported vendor-dependent hypothesis, and returned a valid `partial` result. Inference logged successful completion at 22:21:58.851 UTC, and the Java client accepted that result at 22:21:58.913 UTC. All nine changed-code parts had been reviewed; the remaining uncertainty concerned evidence availability, not a failed model request.

At 22:21:59.349 UTC, PostgreSQL rejected the `code_analysis` insert with SQLSTATE 23514 and constraint `code_analysis_status_check`. The existing constraint allowed only `ACCEPTED`, `REJECTED`, `PENDING`, and `ERROR`; the application enum also contains `PARTIAL`. `CodeAnalysisService` wrapped the database exception as “Failed to create analysis from AI response,” obscuring the actual cause in the user-facing job error.

The missing vendor file was not a synthetic completion gate. Inference and Java already support partial reviews. The missing schema migration prevented their result from being persisted.

## Repair

The managed repeatable migration `java-ecosystem/libs/core/src/main/resources/db/migration/managed/R__code_analysis_status.sql` aligns the database constraint with the existing `AnalysisStatus` enum. It preserves every supported status and all existing rows. If the table is absent on a fresh database, the migration skips it; Hibernate creates the table using the current enum. It does not convert limited coverage into a clean result, drop findings, loosen tenant isolation, fetch dependencies, change prompts, or invoke the model again.

The equivalent migration was applied to the live database in one transaction after the isolated PostgreSQL tests passed. The transaction used a three-second DDL lock wait and ten-second statement timeout, affecting only this maintenance operation. A fresh read confirmed the validated constraint now accepts all five statuses, including `PARTIAL`. No service or benchmark was rebuilt, restarted, or rerun. Flyway history was not fabricated; the managed migration remains available for normal startup and other installations.

Job 10290 remains failed with no linked persisted analysis. Repairing the constraint does not retroactively restore a rejected insert. Its final Redis event was consumed and the event queue removed; the durable progress log did not retain the final result body. This task did not replay the paid review or publish a replacement VCS comment. Existing diagnostic captures and job events were inspected privately, without treating partial provider captures as an exact recoverable final response.

## Verification

The complete offline inference suite passed 894 tests, including new real local-source/verifier regressions for absent vendor source, preserved confirmed findings, suppressed uncertain claims, zero-finding partial results, and Redis/HTTP partial-result delivery. Two existing dependency warnings remain. Production inference behavior was unchanged.

All 174 focused Java/PostgreSQL checks passed, with no failures or skips: 76 analysis-service tests, 24 AI-client tests, 40 PR-processor tests, 30 branch-processor tests, and four real PostgreSQL migration tests. Coverage reproduces the legacy rejection and verifies upgraded partial-result persistence, idempotent migration, supported states, missing-constraint and absent-table bootstrap, source-unavailable persistence, and partial publication across all four VCS providers without granting complete coverage. These are offline regression checks with mocked provider publication and an isolated PostgreSQL container, not a live review replay.

Public developer and paired operator documentation describe partial outcomes and the schema repair. The isolated Docs build passed with 150 canonical pages and 6,273 root-relative anchors; all four changed pages’ internal links/fragments passed, and the original dist tree was unchanged.

Private diagnostic artifacts are under `/tmp/codecrow-partial-review-audit`; inference JUnit is `/tmp/codecrow-garden-partial-inference.xml`; the Java run log is `/tmp/codecrow-partial-java-final-tests.log`; Docs validation is under `/tmp/codecrow-partial-result-docs`. These tests establish persistence and transport behavior, not a measured precision, recall, cost, or latency change.
