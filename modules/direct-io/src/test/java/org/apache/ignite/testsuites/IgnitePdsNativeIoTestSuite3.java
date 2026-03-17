package org.apache.ignite.testsuites;

import java.util.ArrayList;
import java.util.List;
import org.apache.ignite.internal.processors.cache.persistence.IgniteNativeIoLocalWalModeChangeDuringRebalancingSelfTest;
import org.apache.ignite.internal.processors.cache.persistence.IgniteNativeIoPdsRecoveryAfterFileCorruptionTest;
import org.apache.ignite.testframework.junits.DynamicSuite;
import org.junit.runner.RunWith;

/**
 * Same as {@link IgnitePdsTestSuite2} but is started with direct-oi jar in classpath.
 */
@RunWith(DynamicSuite.class)
public class IgnitePdsNativeIoTestSuite3 {
    /**
     * @return Suite.
     */
    public static List<Class<?>> suite() {
        List<Class<?>> suite = new ArrayList<>();

        IgnitePdsTestSuite9.addRealPageStoreTests(suite, null);

        //Integrity test with reduced count of pages.
        suite.add(IgniteNativeIoPdsRecoveryAfterFileCorruptionTest.class);

        suite.add(IgniteNativeIoLocalWalModeChangeDuringRebalancingSelfTest.class);

        return suite;
    }
}
