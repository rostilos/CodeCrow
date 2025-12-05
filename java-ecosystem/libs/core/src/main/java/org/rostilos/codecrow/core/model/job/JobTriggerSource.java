package org.rostilos.codecrow.core.model.job;

public enum JobTriggerSource {
    WEBHOOK,
    PIPELINE,
    API,
    UI,
    SCHEDULED,
    CHAINED
}
