package org.rostilos.codecrow.events.project;

import org.rostilos.codecrow.events.CodecrowEvent;

/**
 * Event fired when a project configuration is changed.
 */
public class ProjectConfigChangedEvent extends CodecrowEvent {
    
    public enum ChangeType {
        CREATED,
        UPDATED,
        DELETED,
        ANALYSIS_CONFIG_CHANGED,
        RAG_CONFIG_CHANGED,
        QUALITY_GATE_CHANGED
    }
    
    private final Long projectId;
    private final String projectName;
    private final ChangeType changeType;
    private final String changedField;
    private final Object oldValue;
    private final Object newValue;
    
    public ProjectConfigChangedEvent(Object source, Long projectId, String projectName,
                                      ChangeType changeType, String changedField,
                                      Object oldValue, Object newValue) {
        super(source);
        this.projectId = projectId;
        this.projectName = projectName;
        this.changeType = changeType;
        this.changedField = changedField;
        this.oldValue = oldValue;
        this.newValue = newValue;
    }
    
    @Override
    public String getEventType() {
        return "PROJECT_CONFIG_CHANGED";
    }
    
    public Long getProjectId() {
        return projectId;
    }
    
    public String getProjectName() {
        return projectName;
    }
    
    public ChangeType getChangeType() {
        return changeType;
    }
    
    public String getChangedField() {
        return changedField;
    }
    
    public Object getOldValue() {
        return oldValue;
    }
    
    public Object getNewValue() {
        return newValue;
    }
}
