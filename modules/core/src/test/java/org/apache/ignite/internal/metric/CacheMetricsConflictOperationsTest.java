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

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import org.apache.ignite.IgniteCheckedException;
import org.apache.ignite.Ignition;
import org.apache.ignite.cache.CacheAtomicityMode;
import org.apache.ignite.client.IgniteClient;
import org.apache.ignite.configuration.CacheConfiguration;
import org.apache.ignite.configuration.ClientConfiguration;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.internal.client.thin.TcpClientCache;
import org.apache.ignite.internal.processors.cache.CacheObject;
import org.apache.ignite.internal.processors.cache.CacheObjectImpl;
import org.apache.ignite.internal.processors.cache.IgniteInternalCache;
import org.apache.ignite.internal.processors.cache.KeyCacheObject;
import org.apache.ignite.internal.processors.cache.KeyCacheObjectImpl;
import org.apache.ignite.internal.processors.cache.dr.GridCacheDrInfo;
import org.apache.ignite.internal.processors.cache.version.GridCacheVersion;
import org.apache.ignite.internal.processors.metric.MetricRegistryImpl;
import org.apache.ignite.internal.processors.metric.impl.HistogramMetricImpl;
import org.apache.ignite.internal.util.typedef.T3;
import org.apache.ignite.spi.metric.LongMetric;
import org.apache.ignite.testframework.junits.common.GridCommonAbstractTest;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.Parameterized;

import static java.util.Arrays.stream;
import static org.apache.ignite.cache.CacheAtomicityMode.ATOMIC;
import static org.apache.ignite.cache.CacheAtomicityMode.TRANSACTIONAL;
import static org.apache.ignite.internal.processors.metric.impl.MetricUtils.cacheMetricsRegistryName;

/**
 * {@link IgniteInternalCache#putAllConflict(Map)} and {@link IgniteInternalCache#removeAllConflict(Map)} cache
 * operations test.
 * @see IgniteInternalCache#putAllConflict(Map)
 * @see IgniteInternalCache#removeAllConflict(Map)
 */
@RunWith(Parameterized.class)
public class CacheMetricsConflictOperationsTest extends GridCommonAbstractTest {
    /** */
    private static final int KEYS_CNT = 100;

    /** */
    private static final int CLIENT_CONNECTOR_PORT = 10800;

    /** */
    @Parameterized.Parameter
    public CacheAtomicityMode atomicityMode;

    /** */
    @Parameterized.Parameter(1)
    public boolean async;

    /** */
    private IgniteEx ign;

    /** */
    @Parameterized.Parameters(name = "atomicityMode={0}")
    public static Collection<?> parameters() {
        List<Object[]> params = new ArrayList<>();

        for (CacheAtomicityMode atomicityMode : Arrays.asList(ATOMIC, TRANSACTIONAL)) {
            params.add(new Object[] {atomicityMode, true});
            params.add(new Object[] {atomicityMode, false});
        }

        return params;
    }

    /** {@inheritDoc} */
    @Override protected IgniteConfiguration getConfiguration(String igniteInstanceName) throws Exception {
        IgniteConfiguration cfg = super.getConfiguration(igniteInstanceName);
        cfg.setMetricsUpdateFrequency(10);

        cfg.setCacheConfiguration(
            new CacheConfiguration<>(DEFAULT_CACHE_NAME)
                .setStatisticsEnabled(true)
                .setAtomicityMode(atomicityMode)
        );

        return cfg;
    }

    /** {@inheritDoc} */
    @Override protected void beforeTest() throws Exception {
        super.beforeTest();

        ign = startGrid(0);
    }

    /** {@inheritDoc} */
    @Override protected void afterTest() throws Exception {
        super.afterTest();

        stopAllGrids();
    }

    /** Checks metrics with thick client. */
    @Test
    public void testCacheMetricsConflictOperationsThick() throws Exception {
        try (IgniteEx cli = startClientGrid()) {
            IgniteInternalCache<Integer, Integer> cachex = cli.cachex(DEFAULT_CACHE_NAME);

            updateCacheFromThick(cachex, KEYS_CNT);
            checkMetrics(cli, KEYS_CNT);
        }
    }

    /** Checks metrics with thin client. */
    @Test
    public void testCacheMetricsConflictOperationsThin() throws ExecutionException, InterruptedException {
        try (IgniteClient cli = Ignition.startClient(getClientConfiguration())) {
            TcpClientCache<Object, Object> cache = (TcpClientCache<Object, Object>)cli.cache(DEFAULT_CACHE_NAME);

            updateCacheFromThin(cache, KEYS_CNT);
            checkMetrics(ign, KEYS_CNT);
        }
    }

    /** Performs {@link IgniteInternalCache#putAllConflict(Map)} and {@link IgniteInternalCache#removeAllConflict(Map)}
     * operation on a given cache.
     * @param cachex - {@link IgniteInternalCache} cache instance.
     * @param cnt - number of entities to put/remove
     * */
    private void updateCacheFromThick(IgniteInternalCache<Integer, Integer> cachex, int cnt) throws IgniteCheckedException {
        Map<KeyCacheObject, GridCacheDrInfo> drMapPuts = new HashMap<>();
        Map<KeyCacheObject, GridCacheVersion> drMapRemoves = new HashMap<>();

        KeyCacheObject key;

        CacheObject val = new CacheObjectImpl(1, null);
        val.prepareMarshal(cachex.context().cacheObjectContext());

        GridCacheVersion conflict = new GridCacheVersion(1, 0, 1, (byte)2);

        for (int i = 0; i < cnt; ++i) {
            key = new KeyCacheObjectImpl(i, null, cachex.affinity().partition(0));

            drMapPuts.put(key, new GridCacheDrInfo(val, conflict));
            drMapRemoves.put(key, conflict);
        }

        if (async) {
            cachex.putAllConflictAsync(drMapPuts).get();
            cachex.removeAllConflictAsync(drMapRemoves).get();
        }
        else {
            cachex.putAllConflict(drMapPuts);
            cachex.removeAllConflict(drMapRemoves);
        }
    }

    /** Performs {@link TcpClientCache#putAllConflict(Map)} and {@link TcpClientCache#removeAllConflict(Map)} operation on a given cache.
     * @param cache - {@link TcpClientCache} cache instance.
     * @param cnt - number of entities to put/remove
     * */
    private void updateCacheFromThin(TcpClientCache<Object, Object> cache, int cnt) throws ExecutionException, InterruptedException {
        Map<Object, T3<?, GridCacheVersion, Long>> drMapPuts = new HashMap<>();
        Map<Object, GridCacheVersion> drMapRemoves = new HashMap<>();

        GridCacheVersion conflict = new GridCacheVersion(1, 1, 1, (byte)2);
        T3<?, GridCacheVersion, Long> val = new T3<>(1, conflict, 0L);

        for (int i = 0; i < cnt; ++i) {
            drMapPuts.put(i, val);
            drMapRemoves.put(i, conflict);
        }

        if (async) {
            cache.putAllConflictAsync(drMapPuts).get();
            cache.removeAllConflictAsync(drMapRemoves).get();
        }
        else {
            cache.putAllConflict(drMapPuts);
            cache.removeAllConflict(drMapRemoves);
        }
    }

    /** Performs checks for #putAllConflict() and #removeAllConflict() operations related metrics. */
    private void checkMetrics(IgniteEx ign, int cnt) {
        MetricRegistryImpl mreg = ign.context().metric().registry(cacheMetricsRegistryName(DEFAULT_CACHE_NAME, false));

        assertTrue(mreg.<LongMetric>findMetric("PutAllConflictTimeTotal").value() > 0);
        assertTrue(mreg.<LongMetric>findMetric("RemoveAllConflictTimeTotal").value() > 0);

        assertTrue(stream(mreg.<HistogramMetricImpl>findMetric("PutAllConflictTime").value()).sum() > 0);
        assertTrue(stream(mreg.<HistogramMetricImpl>findMetric("RemoveAllConflictTime").value()).sum() > 0);

        checkMetricsDestinationCount(cnt);
    }

    /** Performs check on destination cluster */
    private void checkMetricsDestinationCount(int cnt) {
        MetricRegistryImpl mreg = ign.context().metric().registry(cacheMetricsRegistryName(DEFAULT_CACHE_NAME, false));

        assertEquals(cnt, mreg.<LongMetric>findMetric("CachePuts").value());
        assertEquals(cnt, mreg.<LongMetric>findMetric("CacheRemovals").value());
    }

    /** */
    private ClientConfiguration getClientConfiguration() {
        return new ClientConfiguration()
            .setAddresses("127.0.0.1:" + CLIENT_CONNECTOR_PORT);
    }
}
