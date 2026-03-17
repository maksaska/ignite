package org.apache.ignite.testsuites;

import java.util.ArrayList;
import java.util.List;
import org.apache.ignite.testframework.junits.DynamicSuite;
import org.junit.runner.RunWith;

/** */
@RunWith(DynamicSuite.class)
public class IgnitePdsCompressionTestSuite8 extends AbstractIgnitePdsCompressionTestSuite {
    /**
     * @return Suite.
     */
    public static List<Class<?>> suite() {
        List<Class<?>> suite = new ArrayList<>();

        enableCompressionByDefault();
        IgnitePdsTestSuite10.addRealPageStoreTests(suite, null);

        return suite;
    }
}
