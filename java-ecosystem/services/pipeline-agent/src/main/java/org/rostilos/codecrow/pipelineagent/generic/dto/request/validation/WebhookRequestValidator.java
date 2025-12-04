package org.rostilos.codecrow.pipelineagent.generic.dto.request.validation;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.PrProcessRequest;

public class WebhookRequestValidator implements ConstraintValidator<ValidWebhookRequest, PrProcessRequest> {

    @Override
    public boolean isValid(PrProcessRequest request, ConstraintValidatorContext context) {
        if (request == null) {
            return true;
        }

        if (request.analysisType == AnalysisType.PR_REVIEW && request.pullRequestId == null) {
            context.disableDefaultConstraintViolation();
            context.buildConstraintViolationWithTemplate(
                    "Pull Request ID is required for PR_REVIEW analysis type"
            ).addPropertyNode("pullRequestId").addConstraintViolation();
            return false;
        }

        return true;
    }
}

