/*
 * Zero-label Magento 2 review candidate acquisition query.
 *
 * Execute against ClickHouse's public github.github_events mirror with
 * `FORMAT JSONEachRow`.  This query discovers candidates only.  The builder
 * independently rejects records unless local Git proves B = merge-base(H,
 * eventBase), the exact B..H anchor, and the H..F tree/path transition behind
 * an objective acceptance/fix signal; selected roots are then attested against
 * GitHub REST. H need not remain an ancestor of F after a force-push.
 */
WITH merged AS (
    SELECT
        number,
        argMax(title, created_at) AS pr_title,
        argMax(body, created_at) AS pr_body,
        argMax(head_sha, created_at) AS final_head_sha,
        argMax(base_sha, created_at) AS final_base_sha,
        argMax(base_ref, created_at) AS target_ref,
        argMax(merge_commit_sha, created_at) AS merge_commit_sha,
        argMax(changed_files, created_at) AS final_changed_files,
        argMax(additions, created_at) AS final_additions,
        argMax(deletions, created_at) AS final_deletions,
        max(merged_at) AS merged_at,
        max(created_at) AS closed_event_at
    FROM github.github_events
    WHERE repo_name = 'magento/magento2'
      AND event_type = 'PullRequestEvent'
      AND action = 'closed'
      AND merged = 1
      AND base_ref IN (
          '2.4-develop',
          '2.3-develop',
          '2.2-develop',
          'develop'
      )
    GROUP BY number
), authors AS (
    SELECT
        number,
        argMin(actor_login, created_at) AS pr_author,
        min(created_at) AS opened_at
    FROM github.github_events
    WHERE repo_name = 'magento/magento2'
      AND event_type = 'PullRequestEvent'
      AND action = 'opened'
    GROUP BY number
), approvals AS (
    SELECT
        number,
        actor_login,
        max(created_at) AS approved_at,
        argMax(head_sha, created_at) AS approved_head_sha
    FROM github.github_events
    WHERE repo_name = 'magento/magento2'
      AND event_type = 'PullRequestReviewEvent'
      AND action = 'created'
      AND review_state = 'approved'
    GROUP BY number, actor_login
), requests AS (
    SELECT
        number,
        actor_login,
        head_sha,
        min(created_at) AS requested_at
    FROM github.github_events
    WHERE repo_name = 'magento/magento2'
      AND event_type = 'PullRequestReviewEvent'
      AND action = 'created'
      AND review_state = 'changes_requested'
    GROUP BY number, actor_login, head_sha
), replies AS (
    SELECT
        number,
        actor_login,
        path,
        original_commit_id,
        original_position,
        min(created_at) AS reply_at,
        argMin(body, created_at) AS reply_body,
        argMin(comment_id, created_at) AS reply_comment_id
    FROM github.github_events
    WHERE repo_name = 'magento/magento2'
      AND event_type = 'PullRequestReviewCommentEvent'
      AND action = 'created'
      AND match(
          lowerUTF8(body),
          '(^|[^a-z])(fixed|done|implemented|resolved|addressed|changed|updated|removed|corrected)([^a-z]|$)'
      )
    GROUP BY number, actor_login, path, original_commit_id, original_position
)
SELECT
    c.number AS pr_number,
    m.pr_title AS pr_title,
    m.pr_body AS pr_body,
    au.pr_author AS pr_author,
    au.opened_at AS opened_at,
    m.merged_at AS merged_at,
    m.closed_event_at AS closed_event_at,
    m.final_head_sha AS final_head_sha,
    m.final_base_sha AS final_base_sha,
    m.target_ref AS target_ref,
    m.merge_commit_sha AS merge_commit_sha,
    m.final_changed_files AS final_changed_files,
    m.final_additions AS final_additions,
    m.final_deletions AS final_deletions,
    c.comment_id AS comment_id,
    c.actor_login AS reviewer,
    toString(c.author_association) AS author_association,
    c.created_at AS comment_created_at,
    c.updated_at AS comment_updated_at,
    c.body AS comment_body,
    c.path AS path,
    c.diff_hunk AS diff_hunk,
    c.position AS position,
    c.line AS line,
    c.original_position AS original_position,
    c.commit_id AS commit_id,
    c.original_commit_id AS original_commit_id,
    c.head_sha AS event_head_sha,
    c.base_sha AS event_base_sha,
    ap.actor_login AS approval_reviewer,
    ap.approved_at AS approved_at,
    ap.approved_head_sha AS approved_head_sha,
    rq.actor_login AS requested_reviewer,
    rq.requested_at AS requested_at,
    rp.actor_login AS reply_author,
    rp.reply_at AS reply_at,
    rp.reply_body AS reply_body,
    rp.reply_comment_id AS reply_comment_id,
    position(c.body, concat(repeat(char(96), 3), 'suggestion')) > 0
        AS has_suggestion_block
FROM github.github_events AS c
INNER JOIN merged AS m ON m.number = c.number
INNER JOIN authors AS au ON au.number = c.number
LEFT JOIN approvals AS ap
    ON ap.number = c.number AND ap.actor_login = c.actor_login
LEFT JOIN requests AS rq
    ON rq.number = c.number
   AND rq.actor_login = c.actor_login
   AND rq.head_sha = c.head_sha
LEFT JOIN replies AS rp
    ON rp.number = c.number
   AND rp.actor_login = au.pr_author
   AND rp.path = c.path
   AND rp.original_commit_id = c.original_commit_id
   AND rp.original_position = c.original_position
WHERE c.repo_name = 'magento/magento2'
  AND c.event_type = 'PullRequestReviewCommentEvent'
  AND c.action = 'created'
  AND m.final_changed_files BETWEEN 1 AND 120
  AND c.original_commit_id = c.head_sha
  AND c.position > 0
  AND c.actor_login != au.pr_author
  AND c.actor_login NOT LIKE '%[bot]'
  AND c.actor_login NOT IN ('Copilot', 'github-actions', 'magento-engcom-team')
  AND lengthUTF8(trim(c.body)) >= 20
  AND (
      ap.approved_at > c.created_at
      OR rp.reply_at > c.created_at
      OR dateDiff('minute', c.created_at, rq.requested_at) BETWEEN 0 AND 60
      OR position(c.body, concat(repeat(char(96), 3), 'suggestion')) > 0
      OR match(
          lowerUTF8(c.body),
          '\\b(should|must|please|incorrect|wrong|missing|remove|change|instead|avoid|bug|break|risk|need|recommend|could|consider|suggest|add|rename|replace)\\b'
      )
  )
ORDER BY m.merged_at DESC, c.number DESC, c.created_at, c.comment_id
FORMAT JSONEachRow
