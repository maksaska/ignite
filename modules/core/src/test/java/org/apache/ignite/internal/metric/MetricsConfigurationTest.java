/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.ignite.internal.metric;

import org.apache.ignite.cluster.ClusterState;
import org.apache.ignite.configuration.CacheConfiguration;
import org.apache.ignite.configuration.DataStorageConfiguration;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.internal.processors.metric.MetricRegistryImpl;
import org.apache.ignite.internal.processors.metric.MetricsMxBeanImpl;
import org.apache.ignite.internal.processors.metric.impl.HistogramMetricImpl;
import org.apache.ignite.internal.processors.metric.impl.HitRateMetric;
import org.apache.ignite.internal.processors.metric.impl.PeriodicHistogramMetricImpl;
import org.apache.ignite.mxbean.MetricsMxBean;
import org.apache.ignite.spi.metric.HistogramMetric;
import org.apache.ignite.testframework.junits.common.GridCommonAbstractTest;
import org.junit.Test;

import static org.apache.ignite.configuration.WALMode.FSYNC;
import static org.apache.ignite.internal.binary.BinaryUtils.arrayEq;
import static org.apache.ignite.internal.processors.cache.transactions.TransactionMetricsAdapter.METRIC_SYSTEM_TIME_HISTOGRAM;
import static org.apache.ignite.internal.processors.metric.GridMetricManager.HISTOGRAM_CFG_PREFIX;
import static org.apache.ignite.internal.processors.metric.GridMetricManager.HITRATE_CFG_PREFIX;
import static org.apache.ignite.internal.processors.metric.GridMetricManager.TX_METRICS;
import static org.apache.ignite.internal.processors.metric.impl.MetricUtils.cacheMetricsRegistryName;
import static org.apache.ignite.internal.processors.metric.impl.MetricUtils.metricName;
import static org.apache.ignite.internal.processors.pool.PoolProcessor.TASK_EXEC_TIME;
import static org.apache.ignite.internal.processors.pool.PoolProcessor.THREAD_POOLS;
import static org.apache.ignite.testframework.GridTestUtils.assertThrowsWithCause;
import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertNotEquals;

/** Tests metrics configuration. */
public class MetricsConfigurationTest extends GridCommonAbstractTest {
    /** Test metric registry. */
    public static final String TEST_REG = "testReg";

    /** Test hitrate metric name. */
    public static final String HITRATE_NAME = "hitrate";

    /** Test histogram metric name. */
    public static final String HISTOGRAM_NAME = "histogram";

    /** Test bounds. */
    public static final long[] BOUNDS = new long[] {50, 100};

    /** {@inheritDoc} */
    @Override protected void beforeTest() throws Exception {
        super.beforeTest();

        cleanPersistenceDir();
    }

    /** {@inheritDoc} */
    @Override protected void afterTest() throws Exception {
        super.afterTest();

        stopAllGrids();
    }

    /** {@inheritDoc} */
    @Override protected IgniteConfiguration getConfiguration(String igniteInstanceName) throws Exception {
        IgniteConfiguration cfg = super.getConfiguration(igniteInstanceName);

        if (igniteInstanceName.startsWith("persistent")) {
            DataStorageConfiguration dsCfg = new DataStorageConfiguration();

            dsCfg.getDefaultDataRegionConfiguration().setPersistenceEnabled(true);
            dsCfg.setWalMode(FSYNC);

            cfg.setDataStorageConfiguration(dsCfg);
        }

        return cfg;
    }

    /** Tests configuration of {@link HitRateMetric}. */
    @Test
    public void testHitRateConfiguration() throws Exception {
        try (IgniteEx g = startGrid(0)) {
            MetricsMxBean bean = metricsBean(g);

            //Empty name.
            assertThrowsWithCause(
                () -> bean.configureHitRateMetric(null, 1),
                NullPointerException.class);

            //Wrong rateTimeInterval value.
            assertThrowsWithCause(
                () -> bean.configureHitRateMetric("io.dataregion.default.AllocationRate", 0),
                IllegalArgumentException.class);

            assertThrowsWithCause(
                () -> bean.configureHitRateMetric("io.dataregion.default.AllocationRate", -1),
                IllegalArgumentException.class);

            bean.configureHitRateMetric("io.dataregion.default.AllocationRate", 5000);

            HitRateMetric allocationRate = g.context().metric().registry(metricName("io.dataregion.default"))
                .findMetric("AllocationRate");

            assertEquals(5000, allocationRate.rateTimeInterval());
        }
    }

    /** Tests configuration of {@link HistogramMetric}. */
    @Test
    public void testHistogramConfiguration() throws Exception {
        try (IgniteEx g = startGrid(0)) {
            MetricsMxBean bean = metricsBean(g);

            //Empty name.
            assertThrowsWithCause(() -> bean.configureHistogramMetric(null, BOUNDS), NullPointerException.class);

            //Wrong bounds value.
            assertThrowsWithCause(
                () -> bean.configureHistogramMetric(metricName(TX_METRICS, METRIC_SYSTEM_TIME_HISTOGRAM), null),
                NullPointerException.class);

            assertThrowsWithCause(
                () -> bean.configureHistogramMetric(metricName(TX_METRICS, METRIC_SYSTEM_TIME_HISTOGRAM), new long[0]),
                IllegalArgumentException.class);

            bean.configureHistogramMetric(metricName(TX_METRICS, METRIC_SYSTEM_TIME_HISTOGRAM), BOUNDS);

            HistogramMetric sysTime = g.context().metric().registry(TX_METRICS)
                .findMetric(METRIC_SYSTEM_TIME_HISTOGRAM);

            assertArrayEquals(BOUNDS, sysTime.bounds());
        }
    }

    /** Tests configuration of {@link PeriodicHistogramMetricImpl}. */
    @Test
    public void testPageTimestampHistogramConfiguration() throws Exception {
        String registry = "io.dataregion.default";
        String metricName = "PageTimestampHistogram";

        IgniteEx g = startGrid("persistent-0");

        try {
            g.cluster().state(ClusterState.ACTIVE);

            MetricsMxBean bean = metricsBean(g);

            PeriodicHistogramMetricImpl histogram = g.context().metric().registry(registry).findMetric(metricName);

            // Check buckets count including dummy bucket.
            assertEquals(PeriodicHistogramMetricImpl.DFLT_BUCKETS_CNT + 1, histogram.bucketsCount());

            // Reconfigure with 5 buckets and 1 minute interval.
            long interval = 60_000L;
            long[] bounds = new long[] {0L, interval, 0L, 0L, 0L};

            bean.configureHistogramMetric(metricName(registry, metricName), bounds);

            assertEquals(bounds.length + 1, histogram.bucketsCount());
            assertEquals(interval, histogram.bucketsInterval());

            // Check configuration after restart.
            stopGrid("persistent-0", false);

            g = startGrid("persistent-0");
            g.cluster().state(ClusterState.ACTIVE);

            histogram = g.context().metric().registry(registry).findMetric(metricName);

            assertEquals(bounds.length + 1, histogram.bucketsCount());
            assertEquals(interval, histogram.bucketsInterval());
        }
        finally {
            g.close();
        }
    }

    /** Tests metric configuration applied on all nodes. */
    @Test
    public void testConfigurationSeveralNodes() throws Exception {
        try (IgniteEx g0 = startGrid(0); IgniteEx g1 = startGrid(1)) {
            assertNotEquals(BOUNDS.length, g0.context().metric().registry(TX_METRICS)
                .<HistogramMetric>findMetric(METRIC_SYSTEM_TIME_HISTOGRAM).bounds().length);

            assertNotEquals(BOUNDS.length, g1.context().metric().registry(TX_METRICS)
                .<HistogramMetric>findMetric(METRIC_SYSTEM_TIME_HISTOGRAM).bounds().length);

            metricsBean(g0).configureHistogramMetric(metricName(TX_METRICS, METRIC_SYSTEM_TIME_HISTOGRAM), BOUNDS);

            assertArrayEquals(BOUNDS, g0.context().metric().registry(TX_METRICS)
                .<HistogramMetric>findMetric(METRIC_SYSTEM_TIME_HISTOGRAM).bounds());

            assertArrayEquals(BOUNDS, g1.context().metric().registry(TX_METRICS)
                .<HistogramMetric>findMetric(METRIC_SYSTEM_TIME_HISTOGRAM).bounds());
        }
    }

    /** Tests metric configuration applied on all nodes. */
    @Test
    public void testNodeRestart() throws Exception {
        checkOnStartAndRestart((g0, g1) -> {
            assertNotEquals(BOUNDS.length, g0.context().metric().registry(TX_METRICS)
                .<HistogramMetric>findMetric(METRIC_SYSTEM_TIME_HISTOGRAM).bounds().length);

            assertNotEquals(BOUNDS.length, g1.context().metric().registry(TX_METRICS)
                .<HistogramMetric>findMetric(METRIC_SYSTEM_TIME_HISTOGRAM).bounds().length);

            metricsBean(g0).configureHistogramMetric(metricName(TX_METRICS, METRIC_SYSTEM_TIME_HISTOGRAM), BOUNDS);

            assertArrayEquals(BOUNDS, g0.context().metric().registry(TX_METRICS)
                .<HistogramMetric>findMetric(METRIC_SYSTEM_TIME_HISTOGRAM).bounds());

            assertArrayEquals(BOUNDS, g1.context().metric().registry(TX_METRICS)
                .<HistogramMetric>findMetric(METRIC_SYSTEM_TIME_HISTOGRAM).bounds());
        }, (g0, g1) -> {
            assertArrayEquals(BOUNDS, g0.context().metric().registry(TX_METRICS)
                .<HistogramMetric>findMetric(METRIC_SYSTEM_TIME_HISTOGRAM).bounds());

            assertArrayEquals(BOUNDS, g1.context().metric().registry(TX_METRICS)
                .<HistogramMetric>findMetric(METRIC_SYSTEM_TIME_HISTOGRAM).bounds());
        });
    }

    /** Tests metric configuration removed on registry remove. */
    @Test
    public void testConfigRemovedOnRegistryRemove() throws Exception {
        checkOnStartAndRestart((g0, g1) -> {
            MetricRegistryImpl mreg = g0.context().metric().registry(TEST_REG);

            mreg.hitRateMetric(HITRATE_NAME, "test", 10000, 5);
            mreg.histogram(HISTOGRAM_NAME, new long[] {250, 500}, "test");

            metricsBean(g0).configureHistogramMetric(metricName(TEST_REG, HISTOGRAM_NAME), BOUNDS);
            metricsBean(g0).configureHitRateMetric(metricName(TEST_REG, HITRATE_NAME), 1000);
        }, (g0, g1) -> {
            MetricRegistryImpl mreg = g0.context().metric().registry(TEST_REG);

            HitRateMetric hitRate = mreg.hitRateMetric(HITRATE_NAME, "test", 10000, 5);
            HistogramMetricImpl histogram = mreg.histogram(HISTOGRAM_NAME, new long[] {250, 500}, "test");

            assertEquals(1000, hitRate.rateTimeInterval());
            assertArrayEquals(BOUNDS, histogram.bounds());

            assertEquals((Long)1000L,
                g0.context().distributedMetastorage().read(metricName(HITRATE_CFG_PREFIX, TEST_REG, HITRATE_NAME)));

            assertArrayEquals(BOUNDS,
                g0.context().distributedMetastorage().read(metricName(HISTOGRAM_CFG_PREFIX, TEST_REG, HISTOGRAM_NAME)));

            assertEquals((Long)1000L,
                g1.context().distributedMetastorage().read(metricName(HITRATE_CFG_PREFIX, TEST_REG, HITRATE_NAME)));

            assertArrayEquals(BOUNDS,
                g1.context().distributedMetastorage().read(metricName(HISTOGRAM_CFG_PREFIX, TEST_REG, HISTOGRAM_NAME)));

            g0.context().metric().remove(TEST_REG);

            assertNull(
                g0.context().distributedMetastorage().read(metricName(HITRATE_CFG_PREFIX, TEST_REG, HITRATE_NAME)));
            assertNull(
                g0.context().distributedMetastorage().read(metricName(HISTOGRAM_CFG_PREFIX, TEST_REG, HISTOGRAM_NAME)));

            assertNull(
                g1.context().distributedMetastorage().read(metricName(HITRATE_CFG_PREFIX, TEST_REG, HITRATE_NAME)));
            assertNull(
                g1.context().distributedMetastorage().read(metricName(HISTOGRAM_CFG_PREFIX, TEST_REG, HISTOGRAM_NAME)));
        });
    }

    /** Tests metric configuration removed on registry remove. */
    @Test
    public void testConfigRemovedOnCacheRemove() throws Exception {
        String cacheRegName = cacheMetricsRegistryName("test", false);

        checkOnStartAndRestart((g0, g1) -> {
            g0.createCache("test");

            awaitPartitionMapExchange();

            HistogramMetricImpl getTime = g0.context().metric().registry(cacheRegName).findMetric("GetTime");

            assertNotEquals(BOUNDS.length, getTime.bounds().length);

            metricsBean(g0).configureHistogramMetric(metricName(cacheRegName, "GetTime"), BOUNDS);

            assertArrayEquals(BOUNDS,
                g0.context().metric().registry(cacheRegName).<HistogramMetric>findMetric("GetTime").bounds());

            assertArrayEquals(BOUNDS,
                g1.context().metric().registry(cacheRegName).<HistogramMetric>findMetric("GetTime").bounds());
        }, (g0, g1) -> {
            assertArrayEquals(BOUNDS,
                g0.context().metric().registry(cacheRegName).<HistogramMetric>findMetric("GetTime").bounds());

            assertArrayEquals(BOUNDS,
                g1.context().metric().registry(cacheRegName).<HistogramMetric>findMetric("GetTime").bounds());

            g0.destroyCache("test");

            awaitPartitionMapExchange();

            assertNull(g0.context().distributedMetastorage().read(metricName(cacheRegName, "GetTime")));
            assertNull(g1.context().distributedMetastorage().read(metricName(cacheRegName, "GetTime")));
        });
    }

    /**
     * Tests that histogram configuration is read again after node restart.
     */
    @Test
    public void testHistogramConfigIsKeptAfterNodeRestart() throws Exception {
        IgniteEx ignite = startGrid("persistent-0");

        ignite.cluster().state(ClusterState.ACTIVE);

        String regName = metricName(THREAD_POOLS, "StripedExecutor");

        HistogramMetric hist = ignite.context().metric().registry(regName).findMetric(TASK_EXEC_TIME);

        assertFalse(arrayEq(BOUNDS, hist.bounds()));

        ignite.context().metric().configureHistogram(metricName(regName, TASK_EXEC_TIME), BOUNDS);

        assertTrue(arrayEq(BOUNDS, hist.bounds()));

        stopGrid("persistent-0", false);

        ignite = startGrid("persistent-0");

        hist = ignite.context().metric().registry(regName).findMetric(TASK_EXEC_TIME);

        assertTrue(arrayEq(BOUNDS, hist.bounds()));
    }

    /** Tests metric configuration removed on registry remove. */
    @Test
    public void testConfigRemovedOnCacheGroupRemove() throws Exception {
        String cacheRegName = cacheMetricsRegistryName("test", false);
        String mname = metricName(cacheRegName, "GetTime");

        checkOnStartAndRestart((g0, g1) -> {
            CacheConfiguration<String, String> ccfg = new CacheConfiguration<>("test");

            ccfg.setGroupName("group");

            g0.createCache(ccfg);

            awaitPartitionMapExchange();

            HistogramMetricImpl getTime = g0.context().metric().registry(cacheRegName).findMetric("GetTime");

            assertNotEquals(BOUNDS.length, getTime.bounds().length);

            metricsBean(g0).configureHistogramMetric(mname, BOUNDS);

            assertArrayEquals(BOUNDS,
                g0.context().metric().registry(cacheRegName).<HistogramMetric>findMetric("GetTime").bounds());

            assertArrayEquals(BOUNDS,
                g1.context().metric().registry(cacheRegName).<HistogramMetric>findMetric("GetTime").bounds());
        }, (g0, g1) -> {
            assertArrayEquals(BOUNDS,
                g0.context().metric().registry(cacheRegName).<HistogramMetric>findMetric("GetTime").bounds());

            assertArrayEquals(BOUNDS,
                g1.context().metric().registry(cacheRegName).<HistogramMetric>findMetric("GetTime").bounds());

            g0.destroyCache("test");

            awaitPartitionMapExchange();

            assertNull(g0.context().distributedMetastorage().read(mname));
            assertNull(g1.context().distributedMetastorage().read(mname));
        });
    }

    /** Executes provided closures after cluster start and after cluster restart. */
    public void checkOnStartAndRestart(IgniteBiInClosureX<IgniteEx, IgniteEx> afterStart,
        IgniteBiInClosureX<IgniteEx, IgniteEx> afterRestart) throws Exception {

        IgniteEx g0 = startGrid("persistent-0");
        IgniteEx g1 = startGrid("persistent-1");

        try {
            g0.cluster().state(ClusterState.ACTIVE);

            afterStart.apply(g0, g1);

            g0.close();
            g1.close();

            g0 = startGrid("persistent-0");
            g1 = startGrid("persistent-1");

            g0.cluster().state(ClusterState.ACTIVE);

            afterRestart.apply(g0, g1);
        }
        finally {
            g0.close();
            g1.close();
        }
    }

    /** */
    public static MetricsMxBean metricsBean(IgniteEx g) {
        return getMxBean(g.name(), "Metrics", MetricsMxBeanImpl.class, MetricsMxBean.class);
    }

    /** */
    public interface IgniteBiInClosureX<E1, E2> {
        /**
         * Closure body.
         *
         * @param e1 First parameter.
         * @param e2 Second parameter.
         */
        public default void apply(E1 e1, E2 e2) {
            try {
                applyx(e1, e2);
            }
            catch (Exception e) {
                throw new RuntimeException(e);
            }
        }

        /**
         * Closure body that can throw exception.
         *
         * @param e1 First parameter.
         * @param e2 Second parameter.
         */
        public void applyx(E1 e1, E2 e2) throws Exception;
    }
}
