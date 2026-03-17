package org.apache.ignite.testsuites;

import java.util.ArrayList;
import java.util.List;
import org.apache.ignite.internal.processors.cache.persistence.db.IgnitePdsCheckpointRecoveryWithCompressionTest;
import org.apache.ignite.internal.processors.compress.CompressionConfigurationTest;
import org.apache.ignite.internal.processors.compress.CompressionProcessorTest;
import org.apache.ignite.internal.processors.compress.DiskPageCompressionIntegrationAsyncTest;
import org.apache.ignite.internal.processors.compress.FileSystemUtilsTest;
import org.apache.ignite.testframework.junits.DynamicSuite;
import org.junit.runner.RunWith;

/** */
@RunWith(DynamicSuite.class)
public class IgnitePdsCompressionTestSuite5 extends AbstractIgnitePdsCompressionTestSuite {
    /**
     * @return Suite.
     */
    public static List<Class<?>> suite() {
        List<Class<?>> suite = new ArrayList<>();

        suite.add(CompressionConfigurationTest.class);
        suite.add(CompressionProcessorTest.class);
        suite.add(FileSystemUtilsTest.class);
        suite.add(DiskPageCompressionIntegrationAsyncTest.class);

        suite.add(IgnitePdsCheckpointRecoveryWithCompressionTest.class);

        enableCompressionByDefault();
        IgniteSnapshotTestSuite2.addSnapshotTests1(suite, null);

        return suite;
    }
}
