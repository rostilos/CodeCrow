# Review quality phases

| Phase | Engine result | Depends on | Status |
|---|---|---|---|
| [1. Current-head incremental context](phases/01-current-head-context.md) | Direct review uses the newest valid delta while RAG and exact reads use the complete current PR state. | None | Done |
| [2. Hybrid review and exact evidence](phases/02-hybrid-review-evidence.md) | Deterministic units guarantee coverage; LLM planning adds focus; Stage 1 may retrieve one bounded exact evidence set. | Phase 1 | Done |
| [3. Targeted cross-file investigation](phases/03-targeted-cross-file.md) | Only falsifiable interaction hypotheses with exact dependency evidence reach cross-file analysis. | Phase 2 | Done |
| [4. Verification, lineage, and publication](phases/04-verification-lineage-publication.md) | All candidates share one verifier, stale history is not republished, and rendering is non-authoritative. | Phases 1–3 | Done |

All implementation phases are `Done`.
