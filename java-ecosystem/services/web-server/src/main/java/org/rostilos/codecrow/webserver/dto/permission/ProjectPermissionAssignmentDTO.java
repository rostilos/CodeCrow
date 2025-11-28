package org.rostilos.codecrow.webserver.dto.permission;

public class ProjectPermissionAssignmentDTO {
    private Long id;
    private String projectName;
    private Long userId;
    private PermissionTemplateDTO template;

    public ProjectPermissionAssignmentDTO() {
    }

    public ProjectPermissionAssignmentDTO(Long id, String projectName, Long userId, PermissionTemplateDTO template) {
        this.id = id;
        this.projectName = projectName;
        this.userId = userId;
        this.template = template;
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getProjectName() {
        return projectName;
    }

    public void setProjectName(String projectName) {
        this.projectName = projectName;
    }

    public Long getUserId() {
        return userId;
    }

    public void setUserId(Long userId) {
        this.userId = userId;
    }

    public PermissionTemplateDTO getTemplate() {
        return template;
    }

    public void setTemplate(PermissionTemplateDTO template) {
        this.template = template;
    }
}
