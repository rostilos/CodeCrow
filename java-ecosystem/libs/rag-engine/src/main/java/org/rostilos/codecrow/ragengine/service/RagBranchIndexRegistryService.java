package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.*;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagIndexOperationRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.OffsetDateTime;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;

/**
 * Owns durable branch-index identities and their immutable generations. It does
 * not know how Qdrant or a VCS works; callers build and validate the physical
 * generation, then atomically publish or fail it through this service.
 */
@Service
public class RagBranchIndexRegistryService {

    private final RagBranchIndexRepository branchIndexRepository;
    private final RagBranchIndexGenerationRepository generationRepository;
    private final RagIndexOperationRepository operationRepository;

    public RagBranchIndexRegistryService(
            RagBranchIndexRepository branchIndexRepository,
            RagBranchIndexGenerationRepository generationRepository,
            RagIndexOperationRepository operationRepository) {
        this.branchIndexRepository = branchIndexRepository;
        this.generationRepository = generationRepository;
        this.operationRepository = operationRepository;
    }

    public record BuildRegistration(
            RagBranchIndex branchIndex,
            RagBranchIndexGeneration generation,
            RagIndexOperation operation,
            boolean existingOperation) {
    }

    @Transactional
    public BuildRegistration registerBuild(
            Project project,
            String branchName,
            RagBranchIndexKind indexKind,
            String fromRevision,
            String toRevision,
            String representationFingerprint) {
        requireProjectIdentity(project);
        String branch = requireText(branchName, "branchName");
        String targetRevision = requireText(toRevision, "toRevision");
        RagBranchIndexKind kind = indexKind != null ? indexKind : RagBranchIndexKind.DURABLE;

        String operationKey = operationKey(
                project.getId(), branch, fromRevision, targetRevision, representationFingerprint);
        Optional<RagIndexOperation> existing = operationRepository
                .findByProjectIdAndOperationKey(project.getId(), operationKey);
        if (existing.isPresent()) {
            RagIndexOperation operation = existing.get();
            return new BuildRegistration(
                    operation.getGeneration().getBranchIndex(),
                    operation.getGeneration(),
                    operation,
                    true);
        }

        RagBranchIndex branchIndex = branchIndexRepository
                .findByProjectIdAndBranchName(project.getId(), branch)
                .orElseGet(() -> new RagBranchIndex(project, branch, kind));
        if (branchIndex.getIndexKind() == RagBranchIndexKind.LEGACY
                || kind == RagBranchIndexKind.PRIMARY
                || (branchIndex.getIndexKind() == RagBranchIndexKind.TRANSIENT
                    && kind == RagBranchIndexKind.DURABLE)) {
            branchIndex.setIndexKind(kind);
        }
        branchIndex.requestRevision(targetRevision);
        branchIndex = branchIndexRepository.save(branchIndex);

        RagBranchIndexGeneration parent = branchIndex.getActiveGeneration();
        String collectionName = physicalCollectionName(project, branch, targetRevision, operationKey);
        RagBranchIndexGeneration generation = new RagBranchIndexGeneration(
                branchIndex,
                targetRevision,
                collectionName,
                parent,
                fromRevision,
                representationFingerprint);
        generation = generationRepository.save(generation);

        RagIndexOperation operation = new RagIndexOperation(
                project, branch, fromRevision, targetRevision, operationKey);
        operation.setGeneration(generation);
        operation = operationRepository.save(operation);

        return new BuildRegistration(branchIndex, generation, operation, false);
    }

    @Transactional
    public void startBuild(long operationId, Long jobId) {
        RagIndexOperation operation = requireOperation(operationId);
        if (operation.getStatus() == RagIndexOperationStatus.SUCCEEDED) {
            return;
        }
        if (operation.getStatus() == RagIndexOperationStatus.FAILED) {
            operation.getGeneration().retry();
            generationRepository.save(operation.getGeneration());
            operation.getGeneration().getBranchIndex().requestRevision(
                    operation.getToRevision());
            branchIndexRepository.save(
                    operation.getGeneration().getBranchIndex());
        }
        operation.setJobId(jobId);
        operation.start();
        operationRepository.save(operation);
    }

    @Transactional
    public RagBranchIndexGeneration publish(
            long operationId,
            String manifestDigest,
            int fileCount,
            int chunkCount) {
        RagIndexOperation operation = requireOperation(operationId);
        RagBranchIndexGeneration generation = operation.getGeneration();
        if (operation.getStatus() == RagIndexOperationStatus.SUCCEEDED) {
            return generation;
        }
        if (generation.getStatus() != RagBranchIndexGenerationStatus.BUILDING) {
            throw new IllegalStateException("Only a building generation can be published");
        }

        RagBranchIndex branchIndex = generation.getBranchIndex();
        RagBranchIndexGeneration previous = branchIndex.getActiveGeneration();
        generation.activate(requireText(manifestDigest, "manifestDigest"), fileCount, chunkCount);
        generationRepository.save(generation);
        if (previous != null && previous.getId() != null && !previous.getId().equals(generation.getId())) {
            previous.supersede();
            generationRepository.save(previous);
        }
        branchIndex.activate(generation);
        branchIndexRepository.save(branchIndex);
        operation.succeed(generation);
        operationRepository.save(operation);
        return generation;
    }

    @Transactional
    public void fail(long operationId, String errorMessage) {
        RagIndexOperation operation = requireOperation(operationId);
        if (operation.getStatus() == RagIndexOperationStatus.SUCCEEDED) {
            return;
        }
        String failure = requireText(errorMessage, "errorMessage");
        operation.getGeneration().fail(failure);
        generationRepository.save(operation.getGeneration());
        operation.getGeneration().getBranchIndex().failUpdate(failure);
        branchIndexRepository.save(operation.getGeneration().getBranchIndex());
        operation.fail(failure);
        operationRepository.save(operation);
    }

    @Transactional
    public Optional<RagBranchIndexGeneration> findAvailableGeneration(
            long projectId,
            String branchName,
            String revision) {
        Optional<RagBranchIndex> branchIndex = branchIndexRepository
                .findByProjectIdAndBranchName(projectId, requireText(branchName, "branchName"));
        if (branchIndex.isEmpty()) {
            return Optional.empty();
        }
        RagBranchIndex index = branchIndex.get();
        index.markAccessed();
        branchIndexRepository.save(index);
        if (index.getActiveGeneration() != null
                && index.getActiveGeneration().getRevision().equals(revision)) {
            return Optional.of(index.getActiveGeneration());
        }
        return generationRepository
                .findFirstByBranchIndexIdAndRevisionAndStatusInOrderByCreatedAtDesc(
                        index.getId(), revision,
                        List.of(RagBranchIndexGenerationStatus.ACTIVE,
                                RagBranchIndexGenerationStatus.SUPERSEDED));
    }

    @Transactional
    public void heartbeatBuild(long operationId) {
        RagIndexOperation operation = requireOperation(operationId);
        operation.heartbeat();
        operationRepository.save(operation);
    }

    public List<RagIndexOperation> findRecoverableOperations(OffsetDateTime updatedBefore) {
        return operationRepository.findByStatusInAndUpdatedAtBefore(
                List.of(RagIndexOperationStatus.PENDING, RagIndexOperationStatus.RUNNING),
                updatedBefore);
    }

    static String physicalCollectionName(
            Project project,
            String branchName,
            String revision,
            String operationKey) {
        long workspaceId = project.getWorkspace() != null && project.getWorkspace().getId() != null
                ? project.getWorkspace().getId()
                : 0L;
        return "cc_w" + workspaceId
                + "_p" + project.getId()
                + "_b" + digest(branchName).substring(0, 12)
                + "_r" + digest(revision).substring(0, 12)
                + "_g" + operationKey.substring(0, 12);
    }

    static String operationKey(
            long projectId,
            String branchName,
            String fromRevision,
            String toRevision,
            String representationFingerprint) {
        return digest(projectId + "\n"
                + branchName + "\n"
                + nullToEmpty(fromRevision) + "\n"
                + toRevision + "\n"
                + nullToEmpty(representationFingerprint));
    }

    private RagIndexOperation requireOperation(long operationId) {
        return operationRepository.findById(operationId)
                .orElseThrow(() -> new IllegalArgumentException("RAG index operation not found: " + operationId));
    }

    private static void requireProjectIdentity(Project project) {
        if (project == null || project.getId() == null) {
            throw new IllegalArgumentException("A persisted project is required for branch indexing");
        }
    }

    private static String requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " is required");
        }
        return value.trim();
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String digest(String value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256")
                            .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is unavailable", e);
        }
    }
}
