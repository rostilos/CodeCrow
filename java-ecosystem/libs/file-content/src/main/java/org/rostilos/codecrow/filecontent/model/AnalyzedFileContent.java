package org.rostilos.codecrow.filecontent.model;

import jakarta.persistence.*;

import java.time.OffsetDateTime;

/**
 * Content-addressed storage for analyzed file contents.
 * Each unique file content (identified by SHA-256 hash) is stored exactly once.
 * Multiple analyses referencing the same file content share this single row.
 */
@Entity
@Table(name = "analyzed_file_content")
public class AnalyzedFileContent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    /** SHA-256 hex digest of the raw file content — the deduplication key. */
    @Column(name = "content_hash", nullable = false, unique = true, length = 64)
    private String contentHash;

    /** Raw file content stored as TEXT. */
    @Column(name = "content", nullable = false, columnDefinition = "TEXT")
    private String content;

    @Column(name = "size_bytes", nullable = false)
    private long sizeBytes;

    @Column(name = "line_count", nullable = false)
    private int lineCount;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    // ── Getters / Setters ────────────────────────────────────────────────

    public Long getId() { return id; }

    public String getContentHash() { return contentHash; }
    public void setContentHash(String contentHash) { this.contentHash = contentHash; }

    public String getContent() { return content; }
    public void setContent(String content) { this.content = content; }

    public long getSizeBytes() { return sizeBytes; }
    public void setSizeBytes(long sizeBytes) { this.sizeBytes = sizeBytes; }

    public int getLineCount() { return lineCount; }
    public void setLineCount(int lineCount) { this.lineCount = lineCount; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
}
