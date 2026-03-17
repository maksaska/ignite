package org.apache.ignite.testsuites;

import static org.apache.ignite.IgniteSystemProperties.IGNITE_DEFAULT_DATA_STORAGE_PAGE_SIZE;
import static org.apache.ignite.IgniteSystemProperties.IGNITE_DEFAULT_DISK_PAGE_COMPRESSION;
import static org.apache.ignite.configuration.DiskPageCompression.ZSTD;

/** Abstract class for Ignite PDS compression test suites. */
public class AbstractIgnitePdsCompressionTestSuite {
    /** */
    static void enableCompressionByDefault() {
        System.setProperty(IGNITE_DEFAULT_DISK_PAGE_COMPRESSION, ZSTD.name());
        System.setProperty(IGNITE_DEFAULT_DATA_STORAGE_PAGE_SIZE, String.valueOf(8 * 1024));
    }
}
