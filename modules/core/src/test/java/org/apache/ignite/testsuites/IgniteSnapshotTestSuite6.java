package org.apache.ignite.testsuites;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.IncrementalSnapshotRebalanceTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.IncrementalSnapshotTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.incremental.ConcurrentTxsIncrementalSnapshotTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.incremental.IncrementalSnapshotCheckBeforeRestoreTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.incremental.IncrementalSnapshotNoBackupWALBlockingTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.incremental.IncrementalSnapshotNodeFailureTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.incremental.IncrementalSnapshotRestoreTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.incremental.IncrementalSnapshotSingleBackupWALBlockingTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.incremental.IncrementalSnapshotTwoBackupWALBlockingTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.incremental.IncrementalSnapshotWarnAtomicCachesTest;
import org.apache.ignite.testframework.GridTestUtils;
import org.apache.ignite.testframework.junits.DynamicSuite;
import org.junit.runner.RunWith;

/**
 * Test suite for incremental snapshots.
 */
@RunWith(DynamicSuite.class)
public class IgniteSnapshotTestSuite6 {
    /** */
    public static List<Class<?>> suite() {
        List<Class<?>> suite = new ArrayList<>();

        addSnapshotTests(suite, null);

        return suite;
    }

    /** */
    public static void addSnapshotTests(List<Class<?>> suite, Collection<Class> ignoredTests) {
        GridTestUtils.addTestIfNeeded(suite, ConcurrentTxsIncrementalSnapshotTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IncrementalSnapshotCheckBeforeRestoreTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IncrementalSnapshotNoBackupWALBlockingTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IncrementalSnapshotNodeFailureTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IncrementalSnapshotRebalanceTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IncrementalSnapshotRestoreTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IncrementalSnapshotSingleBackupWALBlockingTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IncrementalSnapshotTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IncrementalSnapshotTwoBackupWALBlockingTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IncrementalSnapshotWarnAtomicCachesTest.class, ignoredTests);
    }
}
