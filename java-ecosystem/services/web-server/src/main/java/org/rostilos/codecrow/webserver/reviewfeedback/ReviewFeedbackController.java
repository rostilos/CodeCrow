package org.rostilos.codecrow.webserver.reviewfeedback;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.rostilos.codecrow.webserver.generic.dto.message.ErrorMessageResponse;

import java.util.List;

/** Authorization is repeated inside the service, including non-HTTP callers. */
@RestController
@RequestMapping("/api/{workspaceSlug}/project/{projectNamespace}/review-feedback")
public class ReviewFeedbackController {
    private final ReviewFeedbackService service;

    public ReviewFeedbackController(ReviewFeedbackService service) { this.service = service; }

    // Preserve deliberate 400/404/409 responses before the application's generic
    // RuntimeException advice translates unexpected failures into a 500.
    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<ErrorMessageResponse> expectedFailure(ResponseStatusException exception) {
        HttpStatus status = HttpStatus.valueOf(exception.getStatusCode().value());
        return ResponseEntity.status(status).body(new ErrorMessageResponse(exception.getReason(), status));
    }

    public record FeedbackRequest(@NotNull ReviewFeedbackService.Verdict verdict,
                                  @NotBlank @Size(max = 2000) String explanation) {}
    public record PromotionRequest(@Size(max = 2000) String description) {}

    @PutMapping("/issues/{issueId}")
    public ReviewFeedbackService.RuleView record(
            @PathVariable String workspaceSlug, @PathVariable String projectNamespace,
            @PathVariable long issueId, @Valid @RequestBody FeedbackRequest request,
            Authentication authentication) {
        return service.record(workspaceSlug, projectNamespace, issueId,
                request.verdict(), request.explanation(), authentication);
    }

    @GetMapping("/rules")
    public List<ReviewFeedbackService.RuleView> rules(
            @PathVariable String workspaceSlug, @PathVariable String projectNamespace,
            Authentication authentication) {
        return service.list(workspaceSlug, projectNamespace, authentication);
    }

    @PostMapping("/rules/{ruleId}/promote")
    public ReviewFeedbackService.RuleView promote(
            @PathVariable String workspaceSlug, @PathVariable String projectNamespace,
            @PathVariable String ruleId,
            @Valid @RequestBody(required = false) PromotionRequest request,
            Authentication authentication) {
        return service.promote(workspaceSlug, projectNamespace, ruleId,
                request == null ? null : request.description(), authentication);
    }

    @PostMapping("/rules/{ruleId}/disable")
    public ReviewFeedbackService.RuleView disable(
            @PathVariable String workspaceSlug, @PathVariable String projectNamespace,
            @PathVariable String ruleId, Authentication authentication) {
        return service.disable(workspaceSlug, projectNamespace, ruleId, authentication);
    }
}
