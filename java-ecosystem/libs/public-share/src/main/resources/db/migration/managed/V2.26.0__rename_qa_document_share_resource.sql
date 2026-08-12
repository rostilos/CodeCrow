-- The QA share exposes the deliberately public Overview, Test cases, and
-- Environment tabs. Keep previously issued opaque credentials resolvable while
-- aligning the persisted resource type with that complete contract.
UPDATE public_share_link
SET resource_type = 'qa-document'
WHERE resource_type = 'qa-doc-test-cases';
