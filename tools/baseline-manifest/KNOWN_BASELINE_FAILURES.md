# Known baseline failures and gate limitations

Captured on 2026-07-14 before PR-analysis production behavior changed.

## Reproduced failures

- `codecrow-public/frontend`: `npm run lint` exited `1` with 343 problems (274 errors and 69 warnings).
- `codecrow-static`: `npm run lint` exited `2` because ESLint 9 found no `eslint.config.*` configuration.
- `rag-pipeline`: `tests/test_api_models.py::TestVectorStorageInspectionModels::test_graph_limits_are_bounded` failed because `limit=1000` did not raise the expected `ValueError`.

These are pre-existing baseline failures. P0-01 owns provenance tooling only and does not silently fold unrelated frontend, static-site, or RAG repairs into its scope. They remain release blockers to be resolved by dependency-valid implementation tasks and recorded decisions.

## Unsafe or incomplete existing gates

- Java wrapper coverage scripts report percentages but do not enforce a threshold; the unit runner covers only part of the reactor and can false-green module failures.
- Python aggregate coverage counts test files, does not collect branch coverage, and relies on an ignored shared environment.
- No repository-wide outbound-network deny guard or zero-external-call ledger exists yet; P0-03 owns that harness.
- Existing Java integration profiles disable Flyway; migration/coexistence coverage is absent.
- Frontend and static-site packages have no deterministic component test script; the static lint configuration is absent.
- `deployment/ci/ci-build.sh` writes configuration and pushes images. It is not a local compilation command.
- The benchmark harness performs live GitHub, CodeCrow, model, and judge requests and is not an automated offline gate in its current form.

## Environment and process cautions

- Local wrappers use an ignored Python 3.13 environment while the direct command and production image target Python 3.11.
- Local Node/npm versions differ from the mutable Node 20 image tags used by current Dockerfiles.
- Live Redis and Qdrant services were exposed on their default localhost ports during reconnaissance, so broad suites are deferred until P0-03 supplies network denial and isolated fakes.
- A pre-existing, externally owned pytest process was observed in the untracked `repository-agent-runtime` area. It was not interrupted.
- `codecrow-cloud` is outside the two-worktree tracker snapshot and contains separate user-owned changes; no P0-01 command edits it.
