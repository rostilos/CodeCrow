"""Physical SQLite schema; logical graph contracts remain unchanged."""

_BASE_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS generation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    receipt_json TEXT NOT NULL,
    sealed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS units (
    unit_id TEXT PRIMARY KEY,
    record_type TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT,
    kind TEXT,
    name TEXT,
    qualified_name TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    search_names TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS unit_names (
    normalized_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    unit_id TEXT NOT NULL REFERENCES units(unit_id) ON DELETE CASCADE,
    PRIMARY KEY (normalized_name, unit_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS relations (
    relation_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    relation TEXT NOT NULL,
    target TEXT NOT NULL,
    source_unit_id TEXT REFERENCES units(unit_id) ON DELETE SET NULL,
    target_unit_id TEXT REFERENCES units(unit_id) ON DELETE SET NULL,
    path TEXT NOT NULL,
    line INTEGER NOT NULL,
    origin TEXT NOT NULL,
    plugin_id TEXT,
    attributes_json TEXT NOT NULL
);

-- Derived endpoint-name index used by cloned-generation invalidation. Keeping
-- normalized names beside the relation avoids invoking a Python normalization
-- callback over the complete graph for every changed symbol name.
CREATE TABLE IF NOT EXISTS relation_names (
    relation_id TEXT NOT NULL REFERENCES relations(relation_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('source', 'target')),
    normalized_name TEXT NOT NULL,
    PRIMARY KEY (relation_id, role)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS relation_manifest_cache (
    relation_id TEXT PRIMARY KEY
        REFERENCES relations(relation_id) ON DELETE CASCADE,
    member_json TEXT NOT NULL
) WITHOUT ROWID;

DROP TRIGGER IF EXISTS invalidate_relation_manifest_after_update;
CREATE TRIGGER invalidate_relation_manifest_after_update
AFTER UPDATE OF kind, source, relation, target, path, line, origin, attributes_json
ON relations
WHEN OLD.kind IS NOT NEW.kind
  OR OLD.source IS NOT NEW.source
  OR OLD.relation IS NOT NEW.relation
  OR OLD.target IS NOT NEW.target
  OR OLD.path IS NOT NEW.path
  OR OLD.line IS NOT NEW.line
  OR OLD.origin IS NOT NEW.origin
  OR OLD.attributes_json IS NOT NEW.attributes_json
BEGIN
    DELETE FROM relation_manifest_cache
    WHERE relation_id = NEW.relation_id;
END;

CREATE TABLE IF NOT EXISTS relation_plugins (
    relation_id TEXT NOT NULL REFERENCES relations(relation_id) ON DELETE CASCADE,
    plugin_id TEXT NOT NULL,
    PRIMARY KEY (relation_id, plugin_id)
) WITHOUT ROWID;

-- A logical relation can be emitted by both per-file parsing and repository
-- finalization. Keep stage ownership separate from the canonical contributor
-- union so repository reconciliation cannot remove an unchanged file owner.
CREATE TABLE IF NOT EXISTS relation_scopes (
    relation_id TEXT NOT NULL REFERENCES relations(relation_id) ON DELETE CASCADE,
    scope TEXT NOT NULL CHECK (scope IN ('file', 'repository')),
    PRIMARY KEY (relation_id, scope)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS relation_plugin_scopes (
    relation_id TEXT NOT NULL REFERENCES relations(relation_id) ON DELETE CASCADE,
    plugin_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('file', 'repository')),
    PRIMARY KEY (relation_id, plugin_id, scope)
) WITHOUT ROWID;

CREATE TRIGGER IF NOT EXISTS invalidate_relation_manifest_after_plugin_insert
AFTER INSERT ON relation_plugins
BEGIN
    DELETE FROM relation_manifest_cache
    WHERE relation_id = NEW.relation_id;
END;

CREATE TRIGGER IF NOT EXISTS invalidate_relation_manifest_after_plugin_delete
AFTER DELETE ON relation_plugins
BEGIN
    DELETE FROM relation_manifest_cache
    WHERE relation_id = OLD.relation_id;
END;

CREATE TABLE IF NOT EXISTS relation_paths (
    relation_id TEXT NOT NULL REFERENCES relations(relation_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    PRIMARY KEY (relation_id, path)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS repository_snapshots (
    plugin_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    PRIMARY KEY (plugin_id, kind)
) WITHOUT ROWID;
"""

_SECONDARY_INDEX_SQL = (
    'CREATE INDEX IF NOT EXISTS idx_units_path ON units(path, start_line, end_line);',
    'CREATE INDEX IF NOT EXISTS idx_units_name ON units(name);',
    'CREATE INDEX IF NOT EXISTS idx_units_qualified ON units(qualified_name);',
    'CREATE INDEX IF NOT EXISTS idx_units_record_type ON units(record_type);',
    'CREATE INDEX IF NOT EXISTS idx_unit_names_unit ON unit_names(unit_id);',
    'CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source);',
    'CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target);',
    'CREATE INDEX IF NOT EXISTS idx_relations_source_unit ON relations(source_unit_id);',
    'CREATE INDEX IF NOT EXISTS idx_relations_target_unit ON relations(target_unit_id);',
    'CREATE INDEX IF NOT EXISTS idx_relations_path ON relations(path, line);',
    'CREATE INDEX IF NOT EXISTS idx_relations_kind ON relations(kind);',
    'CREATE INDEX IF NOT EXISTS idx_relation_names_lookup ON relation_names(normalized_name, role, relation_id);',
    'CREATE INDEX IF NOT EXISTS idx_relation_plugins_plugin ON relation_plugins(plugin_id, relation_id);',
    'CREATE INDEX IF NOT EXISTS idx_relation_scopes_scope ON relation_scopes(scope, relation_id);',
    'CREATE INDEX IF NOT EXISTS idx_relation_plugin_scopes_scope ON relation_plugin_scopes(scope, relation_id, plugin_id);',
    'CREATE INDEX IF NOT EXISTS idx_relation_paths_path ON relation_paths(path);',
)

_SCHEMA_SQL = _BASE_SCHEMA_SQL + "\n".join(_SECONDARY_INDEX_SQL)

_FTS_CONTENT_SQL = """
-- Read source bodies from the canonical unit row instead of storing another
-- full copy in FTS5. The small search_names column retains the exact alias
-- token stream (including case variants) for ranking and correct deletion.
CREATE VIEW IF NOT EXISTS unit_search_content AS
SELECT rowid, unit_id, path, name, qualified_name,
       search_names AS symbols, content
FROM units;

"""

_FTS_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
    unit_id UNINDEXED,
    path,
    name,
    qualified_name,
    symbols,
    content,
    content = 'unit_search_content',
    content_rowid = 'rowid',
    tokenize = 'unicode61 remove_diacritics 2 tokenchars ''_:$\\.-'''
);
"""

_FTS_SCHEMA_SQL = _FTS_CONTENT_SQL + _FTS_TABLE_SQL
