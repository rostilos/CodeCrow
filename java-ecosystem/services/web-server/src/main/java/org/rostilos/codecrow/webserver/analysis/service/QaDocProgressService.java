package org.rostilos.codecrow.webserver.analysis.service;

import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.persistence.repository.qadoc.QaDocDocumentRepository;
import org.rostilos.codecrow.core.service.qadoc.QaDocContentParser;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

@Service
public class QaDocProgressService {
    public record Progress(String revision, Map<String, List<Integer>> environments) {}
    private final QaDocDocumentRepository documents;

    public QaDocProgressService(QaDocDocumentRepository documents) { this.documents = documents; }

    public static String revision(QaDocDocument document) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(
                    (document.getCommitHash() + "\n" + document.getMarkdownContent()).getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    @Transactional(readOnly = true)
    public Progress get(Long projectId, Long prNumber) {
        return progress(documents.findByProjectIdAndPrNumber(projectId, prNumber)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND)));
    }

    @Transactional
    public Progress update(Long projectId, Long prNumber, String revision, String environment,
                           Integer testCase, Boolean checked, String action) {
        QaDocDocument document = documents.findForUpdate(projectId, prNumber)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
        if (!revision(document).equals(revision)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "QA document changed. Reload it before updating progress.");
        }
        if (environment == null || environment.isBlank() || environment.length() > 80) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Environment name must contain 1–80 characters.");
        }
        environment = environment.trim();
        Map<String, List<Integer>> environments = document.getEnvironmentChecks();
        switch (action) {
            case "add" -> {
                if (environments.keySet().stream().anyMatch(environment::equalsIgnoreCase)) {
                    throw new ResponseStatusException(HttpStatus.CONFLICT, "Environment already exists.");
                }
                environments.put(environment, new ArrayList<>());
            }
            case "delete" -> {
                if (environments.remove(environment) == null) {
                    throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Environment not found.");
                }
            }
            case "check" -> {
                List<Integer> checks = environments.get(environment);
                if (checks == null) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Environment not found.");
                if (testCase == null || checked == null || testCase < 0 ||
                        testCase >= QaDocContentParser.parse(document.getMarkdownContent()).testCases().size()) {
                    throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Test case not found.");
                }
                checks = new ArrayList<>(checks);
                checks.remove(testCase);
                if (checked) checks.add(testCase);
                environments.put(environment, checks);
            }
            default -> throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Unknown progress action.");
        }
        return progress(documents.save(document));
    }

    private Progress progress(QaDocDocument document) {
        return new Progress(revision(document), document.getEnvironmentChecks());
    }
}
