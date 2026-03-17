package org.apache.ignite.testsuites;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.dump.BufferedFileIOTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.dump.IgniteCacheDumpDataStructuresTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.dump.IgniteCacheDumpFilterTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.dump.IgniteCacheDumpSelf2Test;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.dump.IgniteCacheDumpSelfTest;
import org.apache.ignite.internal.processors.cache.persistence.snapshot.dump.IgniteConcurrentCacheDumpTest;
import org.apache.ignite.testframework.GridTestUtils;
import org.apache.ignite.testframework.junits.DynamicSuite;
import org.junit.runner.RunWith;

/** */
@RunWith(DynamicSuite.class)
public class IgniteSnapshotTestSuite4 {
    /** */
    public static List<Class<?>> suite() {
        List<Class<?>> suite = new ArrayList<>();

        addSnapshotTests(suite, null);

        return suite;
    }

    /** */
    public static void addSnapshotTests(List<Class<?>> suite, Collection<Class> ignoredTests) {
        GridTestUtils.addTestIfNeeded(suite, IgniteCacheDumpSelfTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IgniteCacheDumpSelf2Test.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IgniteCacheDumpDataStructuresTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IgniteConcurrentCacheDumpTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, IgniteCacheDumpFilterTest.class, ignoredTests);
        GridTestUtils.addTestIfNeeded(suite, BufferedFileIOTest.class, ignoredTests);
    }
}
