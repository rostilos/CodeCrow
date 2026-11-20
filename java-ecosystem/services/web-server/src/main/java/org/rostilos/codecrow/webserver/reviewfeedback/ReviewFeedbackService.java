package org.rostilos.codecrow.webserver.reviewfeedback;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.rostilos.codecrow.core.model.project.config.ProjectRulesConfig;
import org.rostilos.codecrow.core.model.project.config.RuleType;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.security.web.WorkspaceSecurity;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.UUID;

/**
 * Optional learning at the management boundary. Review workers only consume
 * existing project rules and never need this service or its storage tables.
 * A project row lock serializes feedback, consensus and rule projection.
 */
@Service
public class ReviewFeedbackService {
    public enum Verdict { VALID, FALSE_POSITIVE }
    public enum State { CANDIDATE, ACTIVE, CONTESTED, DISABLED }
    public record Evidence(long id, long issueId, long analysisId, String commitHash,
                           long userId, Verdict verdict, String explanation, OffsetDateTime createdAt) {}
    public record RuleView(String id, String title, String filePath, String issueCategory,
                           String description, RuleType ruleType, State state, boolean effective,
                           long validVotes, long falsePositiveVotes, long distinctIssues,
                           Long promotedBy, OffsetDateTime promotedAt, List<Evidence> evidence) {}
    private record Scope(long workspaceId, long projectId, String configuration, long userId) {}
    private record Finding(long analysisId, String commitHash, String title, String filePath, String category) {}
    private record Rule(String id, String title, String filePath, String category, String description,
                        RuleType type, State state, Long promotedBy, OffsetDateTime promotedAt) {}
    private record Votes(long valid, long falsePositive, long issues) {
        boolean contested() { return valid > 0 && falsePositive > 0; }
        RuleType type() { return falsePositive > 0 ? RuleType.SUPPRESS : RuleType.ENFORCE; }
    }

    private final JdbcTemplate jdbc;
    private final WorkspaceSecurity security;
    private final ObjectMapper mapper;
    private final TransactionTemplate transactions;
    private final int maxRules;

    public ReviewFeedbackService(JdbcTemplate jdbc, WorkspaceSecurity security, ObjectMapper mapper,
                                 PlatformTransactionManager transactionManager,
                                 @Value("${codecrow.project.rules.max-count:20}") int maxRules) {
        this.jdbc = jdbc;
        this.security = security;
        this.mapper = mapper;
        this.transactions = new TransactionTemplate(transactionManager);
        this.maxRules = maxRules;
    }

    public RuleView record(String workspace, String project, long issueId, Verdict verdict,
                           String explanation, Authentication authentication) {
        return transactions.execute(status -> {
            Scope scope = scope(workspace, project, authentication, false);
            if (verdict == null || explanation == null || explanation.isBlank() || explanation.length() > 2000) {
                throw badRequest("A verdict and explanation of 1–2000 characters are required");
            }
            Finding finding = jdbc.query("""
                    SELECT a.id, a.commit_hash, i.title, i.file_path, i.issue_category
                    FROM code_analysis_issue i JOIN code_analysis a ON a.id = i.analysis_id
                    JOIN project p ON p.id = a.project_id
                    WHERE i.id = ? AND p.id = ? AND p.workspace_id = ?
                    """, (rs, row) -> new Finding(rs.getLong(1), rs.getString(2), rs.getString(3),
                    rs.getString(4), rs.getString(5)), issueId, scope.projectId(), scope.workspaceId())
                    .stream().findFirst().orElseThrow(ReviewFeedbackService::notFound);
            String title = finding.title() == null || finding.title().isBlank() ? "Review finding #" + issueId : finding.title().strip();
            String key = patternKey(title, finding.filePath(), finding.category());
            List<String> existing = jdbc.queryForList("""
                    SELECT id FROM review_learned_rule WHERE workspace_id = ? AND project_id = ? AND pattern_key = ?
                    """, String.class, scope.workspaceId(), scope.projectId(), key);
            String ruleId = existing.isEmpty() ? UUID.randomUUID().toString() : existing.get(0);
            if (existing.isEmpty()) {
                RuleType type = verdict == Verdict.VALID ? RuleType.ENFORCE : RuleType.SUPPRESS;
                jdbc.update("""
                        INSERT INTO review_learned_rule(id,workspace_id,project_id,pattern_key,title,file_path,
                            issue_category,description,rule_type,state) VALUES (?,?,?,?,?,?,?,?,?,?)
                        """, ruleId, scope.workspaceId(), scope.projectId(), key, abbreviate(title, 200),
                        finding.filePath(), finding.category(), description(type, title, finding.filePath(), explanation),
                        type.name(), State.CANDIDATE.name());
            }
            // Repeated delivery is a no-op; a changed opinion appends an audit event.
            List<Evidence> prior = jdbc.query("""
                    SELECT * FROM review_finding_feedback WHERE workspace_id = ? AND project_id = ?
                    AND rule_id = ? AND issue_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1
                    """, this::evidence, scope.workspaceId(), scope.projectId(), ruleId, issueId, scope.userId());
            String note = explanation.strip();
            if (prior.isEmpty() || prior.get(0).verdict() != verdict || !prior.get(0).explanation().equals(note)) {
                jdbc.update("""
                        INSERT INTO review_finding_feedback(rule_id,workspace_id,project_id,issue_id,analysis_id,
                            commit_hash,user_id,verdict,explanation) VALUES (?,?,?,?,?,?,?,?,?)
                        """, ruleId, scope.workspaceId(), scope.projectId(), issueId, finding.analysisId(),
                        finding.commitHash(), scope.userId(), verdict.name(), note);
            }
            Rule rule = rule(scope, ruleId);
            Votes votes = votes(scope, ruleId);
            if (votes.contested() || (rule.state() == State.ACTIVE && votes.type() != rule.type())) {
                setState(scope, ruleId, votes.contested() ? State.CONTESTED : State.CANDIDATE);
                projectRule(scope, rule, false);
                if (!votes.contested()) {
                    jdbc.update("""
                            UPDATE review_learned_rule SET rule_type=?, description=?
                            WHERE workspace_id=? AND project_id=? AND id=?
                            """, votes.type().name(), description(votes.type(), title, finding.filePath(), note),
                            scope.workspaceId(), scope.projectId(), ruleId);
                }
            } else if (rule.state() != State.ACTIVE) {
                State next = rule.state() == State.DISABLED ? State.DISABLED : State.CANDIDATE;
                jdbc.update("""
                        UPDATE review_learned_rule SET state=?, rule_type=?, description=?, updated_at=now()
                        WHERE workspace_id=? AND project_id=? AND id=?
                        """, next.name(), votes.type().name(), description(votes.type(), title, finding.filePath(), note),
                        scope.workspaceId(), scope.projectId(), ruleId);
            }
            return view(scope, rule(scope, ruleId));
        });
    }

    public List<RuleView> list(String workspace, String project, Authentication authentication) {
        return transactions.execute(status -> {
            Scope scope = scope(workspace, project, authentication, true);
            return jdbc.query("""
                    SELECT * FROM review_learned_rule WHERE workspace_id=? AND project_id=? ORDER BY updated_at DESC,id
                    """, this::readRule, scope.workspaceId(), scope.projectId()).stream()
                    .map(rule -> view(scope, rule)).toList();
        });
    }

    public RuleView promote(String workspace, String project, String ruleId, String description,
                            Authentication authentication) {
        return transactions.execute(status -> {
            Scope scope = scope(workspace, project, authentication, true);
            Rule rule = rule(scope, ruleId);
            Votes votes = votes(scope, ruleId);
            if (votes.contested()) throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "The finding feedback is contradictory; resolve the feedback before promoting this rule");
            String text = description == null ? rule.description() : description.strip();
            if (text.isBlank() || text.length() > 2000) throw badRequest("Rule description must have 1–2000 characters");
            Rule promoted = new Rule(rule.id(), rule.title(), rule.filePath(), rule.category(), text,
                    votes.type(), State.ACTIVE, scope.userId(), OffsetDateTime.now());
            projectRule(scope, promoted, true);
            jdbc.update("""
                    UPDATE review_learned_rule SET state='ACTIVE', rule_type=?, description=?, promoted_by=?,
                        promoted_at=now(), updated_at=now() WHERE workspace_id=? AND project_id=? AND id=?
                    """, votes.type().name(), text, scope.userId(), scope.workspaceId(), scope.projectId(), ruleId);
            return view(scope, rule(scope, ruleId));
        });
    }

    public RuleView disable(String workspace, String project, String ruleId, Authentication authentication) {
        return transactions.execute(status -> {
            Scope scope = scope(workspace, project, authentication, true);
            Rule rule = rule(scope, ruleId);
            projectRule(scope, rule, false);
            setState(scope, ruleId, State.DISABLED);
            return view(scope, rule(scope, ruleId));
        });
    }

    private Scope scope(String workspace, String project, Authentication authentication, boolean owner) {
        if (authentication == null || !authentication.isAuthenticated()
                || !(authentication.getPrincipal() instanceof UserDetailsImpl user)
                || !security.isWorkspaceProjectMember(workspace, project, authentication)
                || (owner && !security.hasOwnerOrAdminRights(workspace, authentication))) {
            throw new AccessDeniedException("Project access is required");
        }
        return jdbc.query("""
                SELECT p.workspace_id,p.id,p.configuration::text FROM project p
                JOIN workspace w ON w.id=p.workspace_id WHERE w.slug=? AND p.namespace=? FOR UPDATE OF p
                """, (rs, row) -> new Scope(rs.getLong(1), rs.getLong(2), rs.getString(3), user.getId()),
                workspace, project).stream().findFirst().orElseThrow(ReviewFeedbackService::notFound);
    }

    private Rule rule(Scope scope, String id) {
        return jdbc.query("SELECT * FROM review_learned_rule WHERE workspace_id=? AND project_id=? AND id=?",
                this::readRule, scope.workspaceId(), scope.projectId(), id).stream().findFirst()
                .orElseThrow(ReviewFeedbackService::notFound);
    }

    private Rule readRule(java.sql.ResultSet rs, int row) throws java.sql.SQLException {
        return new Rule(rs.getString("id"), rs.getString("title"), rs.getString("file_path"),
                rs.getString("issue_category"), rs.getString("description"), RuleType.valueOf(rs.getString("rule_type")),
                State.valueOf(rs.getString("state")), rs.getObject("promoted_by", Long.class),
                rs.getObject("promoted_at", OffsetDateTime.class));
    }

    private Evidence evidence(java.sql.ResultSet rs, int row) throws java.sql.SQLException {
        return new Evidence(rs.getLong("id"), rs.getLong("issue_id"), rs.getLong("analysis_id"),
                rs.getString("commit_hash"), rs.getLong("user_id"), Verdict.valueOf(rs.getString("verdict")),
                rs.getString("explanation"), rs.getObject("created_at", OffsetDateTime.class));
    }

    private Votes votes(Scope scope, String ruleId) {
        return jdbc.queryForObject("""
                SELECT count(*) FILTER (WHERE verdict='VALID'), count(*) FILTER (WHERE verdict='FALSE_POSITIVE'),
                    count(DISTINCT issue_id) FROM (
                    SELECT DISTINCT ON (issue_id,user_id) issue_id,verdict FROM review_finding_feedback
                    WHERE workspace_id=? AND project_id=? AND rule_id=? ORDER BY issue_id,user_id,id DESC
                ) current_votes
                """, (rs, row) -> new Votes(rs.getLong(1), rs.getLong(2), rs.getLong(3)),
                scope.workspaceId(), scope.projectId(), ruleId);
    }

    private RuleView view(Scope scope, Rule rule) {
        Votes votes = votes(scope, rule.id());
        List<Evidence> evidence = jdbc.query("""
                SELECT * FROM review_finding_feedback WHERE workspace_id=? AND project_id=? AND rule_id=? ORDER BY id
                """, this::evidence, scope.workspaceId(), scope.projectId(), rule.id());
        // Read after projection so the returned effective status is accurate.
        String configuration = jdbc.queryForObject("SELECT configuration::text FROM project WHERE workspace_id=? AND id=?",
                String.class, scope.workspaceId(), scope.projectId());
        boolean effective = false;
        for (JsonNode entry : configuration(configuration).path("projectRules").path("rules")) {
            if (projectedId(rule).equals(entry.path("id").asText()) && entry.path("enabled").asBoolean()) effective = true;
        }
        return new RuleView(rule.id(), rule.title(), rule.filePath(), rule.category(), rule.description(), rule.type(),
                rule.state(), effective, votes.valid(), votes.falsePositive(), votes.issues(), rule.promotedBy(),
                rule.promotedAt(), evidence);
    }

    private void setState(Scope scope, String ruleId, State state) {
        jdbc.update("UPDATE review_learned_rule SET state=?,updated_at=now() WHERE workspace_id=? AND project_id=? AND id=?",
                state.name(), scope.workspaceId(), scope.projectId(), ruleId);
    }

    private void projectRule(Scope scope, Rule rule, boolean enabled) {
        ObjectNode config = configuration(scope.configuration());
        JsonNode previous = config.path("projectRules").path("rules");
        ArrayNode rules = mapper.createArrayNode();
        for (JsonNode entry : previous) {
            if (!projectedId(rule).equals(entry.path("id").asText())) rules.add(entry);
        }
        if (enabled) {
            if (rules.size() >= maxRules) throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "Maximum number of custom rules is " + maxRules);
            var projected = new ProjectRulesConfig.CustomRule(projectedId(rule), rule.title(), rule.description(),
                    rule.type(), List.of(literalGlob(rule.filePath())), true, 0);
            rules.add(mapper.valueToTree(projected));
        }
        ObjectNode projectRules = config.path("projectRules").isObject()
                ? (ObjectNode) config.path("projectRules") : mapper.createObjectNode();
        projectRules.set("rules", rules);
        config.set("projectRules", projectRules);
        jdbc.update("UPDATE project SET configuration=CAST(? AS jsonb),updated_at=now() WHERE workspace_id=? AND id=?",
                config.toString(), scope.workspaceId(), scope.projectId());
    }

    private ObjectNode configuration(String json) {
        try {
            if (json == null || json.isBlank()) return mapper.createObjectNode();
            JsonNode node = mapper.readTree(json);
            if (node.isObject()) return (ObjectNode) node;
            throw new IllegalArgumentException("Project configuration must be a JSON object");
        } catch (java.io.IOException e) {
            throw new IllegalStateException("Cannot read existing project configuration", e);
        }
    }

    private static String projectedId(Rule rule) { return "learned:" + rule.id(); }
    private static String literalGlob(String path) {
        return path.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]");
    }
    private static String description(RuleType type, String title, String path, String note) {
        String instruction = type == RuleType.SUPPRESS
                ? "Avoid repeating this finding pattern unless new source evidence demonstrates a concrete defect: "
                : "Check for independently verified instances of this defect pattern: ";
        return abbreviate(instruction + title + ". Repository path: " + path
                + ". Maintainer feedback: " + note.strip()
                + ". Similarity alone is not evidence; verify the current source.", 2000);
    }
    private static String patternKey(String title, String path, String category) {
        try {
            String key = Objects.toString(category, "") + "\n" + path + "\n"
                    + title.toLowerCase(Locale.ROOT).replaceAll("\\s+", " ").strip();
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(key.getBytes(StandardCharsets.UTF_8)));
        } catch (java.security.NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
    private static String abbreviate(String text, int max) { return text.length() <= max ? text : text.substring(0, max); }
    private static ResponseStatusException notFound() { return new ResponseStatusException(HttpStatus.NOT_FOUND, "Not found"); }
    private static ResponseStatusException badRequest(String reason) { return new ResponseStatusException(HttpStatus.BAD_REQUEST, reason); }
}
