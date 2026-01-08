-- Backfill commit_hash in code_analysis from the associated pull_request
-- For existing PR analyses that don't have a commit_hash, copy it from the PR entity
-- This is acceptable for historical data as we're capturing the PR's latest commit state

UPDATE code_analysis ca
SET commit_hash = pr.commit_hash
FROM pull_request pr
WHERE ca.pr_number = pr.pr_number
  AND ca.project_id = pr.project_id
  AND ca.commit_hash IS NULL
  AND pr.commit_hash IS NOT NULL;
