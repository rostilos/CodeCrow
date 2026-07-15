# Reproducible baseline manifest

This tool captures and verifies the Gate 0/P0-01 provenance bundle for the PR-analysis rebuild. It records exact Git revisions and complete dirty-state inventories, dependency-input digests, runtime fingerprints, model/index availability, prompt and rule versions, deterministic seeds, commands, and artifact checksums.

Generated bundles live at `codecrow-public/.llm-handoff-artifacts/`. That directory is ignored by Git and is outside every component-scoped production Docker build context. The capture uses explicit file allowlists: it never reads `.env` files, credentials, API tokens, or `tools/environment/dumps/`, and it never contacts a model, embedding, VCS, Jira, email, telemetry, or other external provider.

## Commands

From `codecrow-public`:

```bash
node --test \
  --experimental-test-coverage \
  --test-coverage-lines=100 \
  --test-coverage-branches=100 \
  --test-coverage-functions=100 \
  --test-coverage-include='tools/baseline-manifest/lib/*.mjs' \
  tools/baseline-manifest/test/*.test.mjs

node tools/baseline-manifest/bin/capture-current-baseline.mjs

node tools/baseline-manifest/bin/verify-baseline.mjs \
  .llm-handoff-artifacts/p0-01/baseline-manifest.json
```

The verifier fails closed when a commit, branch, dirty entry, dirty-file digest, submodule state, lock/dependency input, prompt, rule, fixture, artifact, detached manifest checksum, or required identity field differs. An unavailable local provider/model or index is represented explicitly with a typed status and reason; it is never replaced by an empty identifier that looks ready.

The capture is intentionally local and offline. It does not run the application suites or deployment build. Their exact commands and known pre-existing failures are retained in the generated bundle, while the push-capable deployment build remains restricted to an authorized isolated release job.
