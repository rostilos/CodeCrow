package org.rostilos.codecrow.pipelineagent.generic.service.vcs;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * Factory for resolving VCS-specific services based on the provider type.
 */
@Component
public class VcsServiceFactory {

    private final Map<EVcsProvider, VcsAiClientService> aiClientServices;
    private final Map<EVcsProvider, VcsReportingService> reportingServices;
    private final Map<EVcsProvider, VcsOperationsService> operationsServices;

    public VcsServiceFactory(
            List<VcsAiClientService> aiClientServiceList,
            List<VcsReportingService> reportingServiceList,
            List<VcsOperationsService> operationsServiceList
    ) {
        this.aiClientServices = aiClientServiceList.stream()
                .collect(Collectors.toMap(VcsAiClientService::getProvider, Function.identity()));
        this.reportingServices = reportingServiceList.stream()
                .collect(Collectors.toMap(VcsReportingService::getProvider, Function.identity()));
        this.operationsServices = operationsServiceList.stream()
                .collect(Collectors.toMap(VcsOperationsService::getProvider, Function.identity()));
    }

    public VcsAiClientService getAiClientService(EVcsProvider provider) {
        VcsAiClientService service = aiClientServices.get(provider);
        if (service == null) {
            throw new UnsupportedOperationException("No AI client service registered for provider: " + provider);
        }
        return service;
    }

    public VcsReportingService getReportingService(EVcsProvider provider) {
        VcsReportingService service = reportingServices.get(provider);
        if (service == null) {
            throw new UnsupportedOperationException("No reporting service registered for provider: " + provider);
        }
        return service;
    }

    public VcsOperationsService getOperationsService(EVcsProvider provider) {
        VcsOperationsService service = operationsServices.get(provider);
        if (service == null) {
            throw new UnsupportedOperationException("No operations service registered for provider: " + provider);
        }
        return service;
    }
}
