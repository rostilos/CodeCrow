package org.rostilos.codecrow.analysisengine.coverage;

import java.util.Optional;

/** Atomic durable boundary for exact coverage-ledger state. */
public interface CoverageLedgerPersistencePort {
    CoverageLedgerSnapshot createOrLoad(CoverageLedgerSeed seed);

    Optional<CoverageLedgerSnapshot> findByExecutionId(String executionId);

    CoverageLedgerSnapshot compareAndSet(
            CoverageLedgerSnapshot expected,
            CoverageLedgerSnapshot replacement);
}
