package org.rostilos.codecrow.core.model.project.config;

import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class BranchAnalysisConfigTest {

    @Test
    void testDefaultConstructor() {
        BranchAnalysisConfig config = new BranchAnalysisConfig();
        assertThat(config.prTargetBranches()).isNull();
        assertThat(config.branchPushPatterns()).isNull();
    }

    @Test
    void testFullConstructor() {
        List<String> prTargetBranches = Arrays.asList("main", "develop");
        List<String> branchPushPatterns = Arrays.asList("feature/*", "release/**");
        
        BranchAnalysisConfig config = new BranchAnalysisConfig(prTargetBranches, branchPushPatterns);
        
        assertThat(config.prTargetBranches()).isEqualTo(prTargetBranches);
        assertThat(config.branchPushPatterns()).isEqualTo(branchPushPatterns);
    }

    @Test
    void testConstructorWithPrTargetBranchesOnly() {
        List<String> prTargetBranches = Arrays.asList("main");
        BranchAnalysisConfig config = new BranchAnalysisConfig(prTargetBranches, null);
        
        assertThat(config.prTargetBranches()).isEqualTo(prTargetBranches);
        assertThat(config.branchPushPatterns()).isNull();
    }

    @Test
    void testConstructorWithBranchPushPatternsOnly() {
        List<String> branchPushPatterns = Arrays.asList("feature/*");
        BranchAnalysisConfig config = new BranchAnalysisConfig(null, branchPushPatterns);
        
        assertThat(config.prTargetBranches()).isNull();
        assertThat(config.branchPushPatterns()).isEqualTo(branchPushPatterns);
    }

    @Test
    void testRecordEquality() {
        List<String> prTargetBranches = Arrays.asList("main", "develop");
        List<String> branchPushPatterns = Arrays.asList("feature/*");
        
        BranchAnalysisConfig config1 = new BranchAnalysisConfig(prTargetBranches, branchPushPatterns);
        BranchAnalysisConfig config2 = new BranchAnalysisConfig(prTargetBranches, branchPushPatterns);
        
        assertThat(config1).isEqualTo(config2);
        assertThat(config1.hashCode()).isEqualTo(config2.hashCode());
    }

    @Test
    void testRecordInequality() {
        BranchAnalysisConfig config1 = new BranchAnalysisConfig(Arrays.asList("main"), null);
        BranchAnalysisConfig config2 = new BranchAnalysisConfig(Arrays.asList("develop"), null);
        
        assertThat(config1).isNotEqualTo(config2);
    }

    @Test
    void testWithGlobPatterns() {
        List<String> patterns = Arrays.asList("feature/*", "release/**", "hotfix/*");
        BranchAnalysisConfig config = new BranchAnalysisConfig(null, patterns);
        
        assertThat(config.branchPushPatterns()).containsExactly("feature/*", "release/**", "hotfix/*");
    }

    @Test
    void testWithExactBranchNames() {
        List<String> branches = Arrays.asList("main", "develop", "staging");
        BranchAnalysisConfig config = new BranchAnalysisConfig(branches, null);
        
        assertThat(config.prTargetBranches()).containsExactly("main", "develop", "staging");
    }
}
