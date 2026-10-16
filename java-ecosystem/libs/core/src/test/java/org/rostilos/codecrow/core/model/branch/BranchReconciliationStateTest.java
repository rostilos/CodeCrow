package org.rostilos.codecrow.core.model.branch;

import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.assertThat;

class BranchReconciliationStateTest {
    @Test
    void retriesKeepCoreHealthAndNewRevisionResetsAttemptBudget() {
        Branch branch = new Branch();
        branch.markHealthy("one");
        branch.beginReconciliation(true);
        branch.finishReconciliation("provider unavailable");
        assertThat(branch.isReconciliationPending()).isTrue();
        assertThat(branch.getHealthStatus()).isEqualTo(BranchHealthStatus.HEALTHY);
        branch.beginReconciliation(false);
        assertThat(branch.getReconciliationAttempts()).isEqualTo(2);
        branch.beginReconciliation(true);
        assertThat(branch.getReconciliationAttempts()).isEqualTo(1);
        branch.finishReconciliation(null);
        assertThat(branch.isReconciliationPending()).isFalse();
        assertThat(branch.getReconciliationDiagnostic()).isNull();
    }
}
