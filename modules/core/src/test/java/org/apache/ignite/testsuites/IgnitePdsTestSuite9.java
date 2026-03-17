package org.apache.ignite.testsuites;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import org.apache.ignite.cdc.CdcIgniteNodeActiveModeTest;
import org.apache.ignite.cdc.CorruptedCdcConsumerStateTest;
import org.apache.ignite.cdc.TransformedCdcSelfTest;
import org.apache.ignite.cdc.WalRolloverOnStopTest;
import org.apache.ignite.internal.processors.cache.distributed.dht.preloader.HistoricalRebalanceHeuristicsTest;
import org.apache.ignite.internal.processors.cache.distributed.dht.preloader.HistoricalRebalanceTwoPartsInDifferentCheckpointsTest;
import org.apache.ignite.internal.processors.cache.persistence.WalPreloadingConcurrentTest;
import org.apache.ignite.internal.processors.cache.persistence.db.IgnitePdsReserveWalSegmentsWithCompactionTest;
import org.apache.ignite.internal.processors.cache.persistence.db.checkpoint.LightweightCheckpointTest;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.IgniteWalFlushFailoverTest;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.IgniteWalFlushLogOnlySelfTest;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.IgniteWalFlushLogOnlyWithMmapBufferSelfTest;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.IgniteWalIteratorSwitchSegmentTest;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.WalCompactionNoArchiverTest;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.WalCompactionSwitchOnTest;
import org.apache.ignite.internal.processors.cache.persistence.db.wal.WalDeletionArchiveLogOnlyTest;
import org.apache.ignite.internal.processors.cache.persistence.freelist.FreeListCachingTest;
import org.apache.ignite.testframework.GridTestUtils;
import org.apache.ignite.testframework.junits.DynamicSuite;
import org.junit.runner.RunWith;

/** */
@RunWith(DynamicSuite.class)
public class IgnitePdsTestSuite9 {
    /**
     * @return Suite.
     */
    public static List<Class<?>> suite() {
        return suite(null);
    }

    /**
     * @param ignoredTests Tests to ignore.
     * @return Test suite.
     */
    public static List<Class<?>> suite(Collection<Class> ignoredTests) {
        List<Class<?>> suite = new ArrayList<>();

        addRealPageStoreTests(suite, ignoredTests);

        return suite;
    }

    /**
     * Fills {@code suite} with PDS test subset, which operates with real page store and does actual disk operations.
     *
     * NOTE: These tests are also executed using I/O plugins.
     *
     * @param suite suite to add tests into.
     * @param ignoredTests Ignored tests.
     */
    public static void addRealPageStoreTests(List<Class<?>> suite, Collection<Class> ignoredTests) {
        GridTestUtils.addTestIfNeeded(suite, CdcIgniteNodeActiveModeTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, CorruptedCdcConsumerStateTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, FreeListCachingTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, HistoricalRebalanceHeuristicsTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, HistoricalRebalanceTwoPartsInDifferentCheckpointsTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IgnitePdsReserveWalSegmentsWithCompactionTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IgniteWalFlushFailoverTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IgniteWalFlushLogOnlySelfTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IgniteWalFlushLogOnlyWithMmapBufferSelfTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IgniteWalIteratorSwitchSegmentTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, LightweightCheckpointTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, TransformedCdcSelfTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, WalCompactionNoArchiverTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, WalCompactionSwitchOnTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, WalDeletionArchiveLogOnlyTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, WalPreloadingConcurrentTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, WalRolloverOnStopTest.class, ignoredTests);
    }
}
