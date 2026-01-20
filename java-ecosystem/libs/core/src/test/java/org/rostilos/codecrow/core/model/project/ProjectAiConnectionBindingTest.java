package org.rostilos.codecrow.core.model.project;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.ai.AIConnection;

import static org.assertj.core.api.Assertions.assertThat;

class ProjectAiConnectionBindingTest {

    @Test
    void shouldCreateProjectAiConnectionBinding() {
        ProjectAiConnectionBinding binding = new ProjectAiConnectionBinding();
        assertThat(binding).isNotNull();
    }

    @Test
    void shouldSetAndGetId() {
        ProjectAiConnectionBinding binding = new ProjectAiConnectionBinding();
        // ID is auto-generated, verify it's null for new entity
        assertThat(binding.getId()).isNull();
    }

    @Test
    void shouldSetAndGetProject() {
        ProjectAiConnectionBinding binding = new ProjectAiConnectionBinding();
        Project project = new Project();
        
        binding.setProject(project);
        
        assertThat(binding.getProject()).isEqualTo(project);
    }

    @Test
    void shouldSetAndGetAiConnection() {
        ProjectAiConnectionBinding binding = new ProjectAiConnectionBinding();
        AIConnection aiConnection = new AIConnection();
        
        binding.setAiConnection(aiConnection);
        
        assertThat(binding.getAiConnection()).isEqualTo(aiConnection);
    }

    @Test
    void shouldSetAndGetPolicyJson() {
        ProjectAiConnectionBinding binding = new ProjectAiConnectionBinding();
        String policyJson = "{\"maxTokens\": 4000, \"temperature\": 0.7}";
        
        binding.setPolicyJson(policyJson);
        
        assertThat(binding.getPolicyJson()).isEqualTo(policyJson);
    }

    @Test
    void shouldBindProjectToAiConnection() {
        ProjectAiConnectionBinding binding = new ProjectAiConnectionBinding();
        Project project = new Project();
        AIConnection aiConnection = new AIConnection();
        String policy = "{\"model\": \"gpt-4\"}";
        
        binding.setProject(project);
        binding.setAiConnection(aiConnection);
        binding.setPolicyJson(policy);
        
        assertThat(binding.getProject()).isEqualTo(project);
        assertThat(binding.getAiConnection()).isEqualTo(aiConnection);
        assertThat(binding.getPolicyJson()).isEqualTo(policy);
    }

    @Test
    void shouldHandleNullPolicyJson() {
        ProjectAiConnectionBinding binding = new ProjectAiConnectionBinding();
        binding.setPolicyJson(null);
        
        assertThat(binding.getPolicyJson()).isNull();
    }
}
