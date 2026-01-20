package org.rostilos.codecrow.analysisengine.dto.request.validation;

import jakarta.validation.ConstraintValidatorContext;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("WebhookRequestValidator")
class WebhookRequestValidatorTest {

    private WebhookRequestValidator validator;

    @Mock
    private ConstraintValidatorContext context;

    @Mock
    private ConstraintValidatorContext.ConstraintViolationBuilder violationBuilder;

    @Mock
    private ConstraintValidatorContext.ConstraintViolationBuilder.NodeBuilderCustomizableContext nodeBuilder;

    @BeforeEach
    void setUp() {
        validator = new WebhookRequestValidator();
    }

    @Nested
    @DisplayName("isValid()")
    class IsValidTests {

        @Test
        @DisplayName("should return true for null request")
        void shouldReturnTrueForNullRequest() {
            boolean result = validator.isValid(null, context);
            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("should return true for PR_REVIEW with pullRequestId")
        void shouldReturnTrueForPrReviewWithPullRequestId() {
            PrProcessRequest request = new PrProcessRequest();
            request.analysisType = AnalysisType.PR_REVIEW;
            request.pullRequestId = 123L;

            boolean result = validator.isValid(request, context);
            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("should return false for PR_REVIEW without pullRequestId")
        void shouldReturnFalseForPrReviewWithoutPullRequestId() {
            PrProcessRequest request = new PrProcessRequest();
            request.analysisType = AnalysisType.PR_REVIEW;
            request.pullRequestId = null;

            when(context.buildConstraintViolationWithTemplate(anyString())).thenReturn(violationBuilder);
            when(violationBuilder.addPropertyNode(anyString())).thenReturn(nodeBuilder);
            when(nodeBuilder.addConstraintViolation()).thenReturn(context);

            boolean result = validator.isValid(request, context);

            assertThat(result).isFalse();
            verify(context).disableDefaultConstraintViolation();
            verify(context).buildConstraintViolationWithTemplate("Pull Request ID is required for PR_REVIEW analysis type");
            verify(violationBuilder).addPropertyNode("pullRequestId");
        }

        @Test
        @DisplayName("should return true for BRANCH_ANALYSIS without pullRequestId")
        void shouldReturnTrueForBranchAnalysisWithoutPullRequestId() {
            PrProcessRequest request = new PrProcessRequest();
            request.analysisType = AnalysisType.BRANCH_ANALYSIS;
            request.pullRequestId = null;

            boolean result = validator.isValid(request, context);
            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("should return true for null analysisType")
        void shouldReturnTrueForNullAnalysisType() {
            PrProcessRequest request = new PrProcessRequest();
            request.analysisType = null;
            request.pullRequestId = null;

            boolean result = validator.isValid(request, context);
            assertThat(result).isTrue();
        }
    }
}
