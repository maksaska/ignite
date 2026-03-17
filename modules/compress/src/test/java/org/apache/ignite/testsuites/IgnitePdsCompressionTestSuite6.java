package org.apache.ignite.testsuites;

import java.util.ArrayList;
import java.util.List;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.IgnitePdsCheckpointSimulationWithRealCpDisabledAndWalCompressionTest;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.WalCompactionAndPageCompressionTest;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.WalRecoveryWithPageCompressionAndTdeTest;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.WalRecoveryWithPageCompressionTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.SnapshotCompressionBasicTest;
import org.apache.ignite.internal.processors.compress.DiskPageCompressionConfigValidationTest;
import org.apache.ignite.internal.processors.compress.DiskPageCompressionIntegrationTest;
import org.apache.ignite.internal.processors.compress.WalPageCompressionIntegrationTest;
import org.apache.ignite.testframework.junits.DynamicSuite;
import org.junit.runner.RunWith;

/** */
@RunWith(DynamicSuite.class)
public class IgnitePdsCompressionTestSuite6 extends AbstractIgnitePdsCompressionTestSuite {
    /**
     * @return Suite.
     */
    public static List<Class<?>> suite() {
        List<Class<?>> suite = new ArrayList<>();

        suite.add(DiskPageCompressionIntegrationTest.class);
        suite.add(DiskPageCompressionConfigValidationTest.class);

        suite.add(WalPageCompressionIntegrationTest.class);
        suite.add(WalRecoveryWithPageCompressionTest.class);
        suite.add(WalRecoveryWithPageCompressionAndTdeTest.class);
        suite.add(IgnitePdsCheckpointSimulationWithRealCpDisabledAndWalCompressionTest.class);
        suite.add(WalCompactionAndPageCompressionTest.class);

        suite.add(SnapshotCompressionBasicTest.class);

        enableCompressionByDefault();
        IgniteSnapshotWithIndexingTestSuite.addSnapshotTests(suite, null);

        return suite;
    }
}
