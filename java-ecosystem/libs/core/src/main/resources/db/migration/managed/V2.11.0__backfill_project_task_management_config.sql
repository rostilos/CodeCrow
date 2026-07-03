-- =============================================================================
-- V2.11.0 - Backfill project task-management config from legacy QA auto-doc
-- =============================================================================
-- Before project-level Task Management existed, QA Auto-Documentation stored
-- the Jira connection and task key extraction settings under:
--   project.configuration.qaAutoDoc.taskManagementConnectionId
--   project.configuration.qaAutoDoc.taskIdPattern
--   project.configuration.qaAutoDoc.taskIdSource
--
-- Runtime now reads these values from project.configuration.taskManagement.
-- Backfill that new object for existing projects, without overwriting projects
-- that already have an explicit taskManagement binding.
-- =============================================================================

UPDATE project p
SET configuration = jsonb_set(
        p.configuration::jsonb,
        '{taskManagement}',
        jsonb_build_object(
                'taskManagementConnectionId',
                to_jsonb((p.configuration::jsonb #>> '{qaAutoDoc,taskManagementConnectionId}')::bigint),
                'taskIdPattern',
                to_jsonb(COALESCE(
                        NULLIF(p.configuration::jsonb #>> '{qaAutoDoc,taskIdPattern}', ''),
                        '[A-Z][A-Z0-9]+-\d+'
                )),
                'taskIdSource',
                to_jsonb(COALESCE(
                        NULLIF(p.configuration::jsonb #>> '{qaAutoDoc,taskIdSource}', ''),
                        'BRANCH_NAME'
                ))
        ),
        true
)
WHERE p.configuration IS NOT NULL
  AND p.configuration::jsonb #>> '{qaAutoDoc,taskManagementConnectionId}' IS NOT NULL
  AND p.configuration::jsonb #>> '{qaAutoDoc,taskManagementConnectionId}' ~ '^[0-9]+$'
  AND p.configuration::jsonb #>> '{taskManagement,taskManagementConnectionId}' IS NULL
  AND EXISTS (
      SELECT 1
      FROM task_management_connection tmc
      WHERE tmc.id = (p.configuration::jsonb #>> '{qaAutoDoc,taskManagementConnectionId}')::bigint
        AND tmc.workspace_id = p.workspace_id
  );
