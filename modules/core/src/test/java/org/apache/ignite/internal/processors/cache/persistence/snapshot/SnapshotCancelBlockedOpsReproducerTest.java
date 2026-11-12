/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements. See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License. You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.ignite.internal.processors.cache.persistence.snapshot;

import java.util.concurrent.CountDownLatch;
import org.apache.ignite.IgniteCache;
import org.apache.ignite.configuration.CacheConfiguration;
import org.apache.ignite.configuration.DataRegionConfiguration;
import org.apache.ignite.configuration.DataStorageConfiguration;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.internal.IgniteInternalFuture;
import org.apache.ignite.lang.IgniteFuture;
import org.apache.ignite.testframework.GridTestUtils;
import org.apache.ignite.testframework.junits.common.GridCommonAbstractTest;
import org.apache.ignite.transactions.Transaction;
import org.junit.Test;

import static org.apache.ignite.cache.CacheAtomicityMode.TRANSACTIONAL;
import static org.apache.ignite.cluster.ClusterState.ACTIVE;
import static org.apache.ignite.transactions.TransactionConcurrency.PESSIMISTIC;
import static org.apache.ignite.transactions.TransactionIsolation.REPEATABLE_READ;

/**
 * A full snapshot waits for a long transaction. Cancelling the snapshot ends it at once, but cache operations
 * on other keys stay blocked until the transaction ends: the exchange the snapshot started keeps waiting for it.
 */
public class SnapshotCancelBlockedOpsReproducerTest extends GridCommonAbstractTest {
    /** {@inheritDoc} */
    @Override protected IgniteConfiguration getConfiguration(String name) throws Exception {
        return super.getConfiguration(name)
            .setDataStorageConfiguration(new DataStorageConfiguration()
                .setDefaultDataRegionConfiguration(new DataRegionConfiguration().setPersistenceEnabled(true)))
            .setCacheConfiguration(new CacheConfiguration<>(DEFAULT_CACHE_NAME)
                .setAtomicityMode(TRANSACTIONAL)
                .setBackups(1));
    }

    /** {@inheritDoc} */
    @Override protected void beforeTest() throws Exception {
        stopAllGrids();
        cleanPersistenceDir();
    }

    /** {@inheritDoc} */
    @Override protected void afterTest() throws Exception {
        stopAllGrids();
        cleanPersistenceDir();
    }

    /** */
    @Test
    public void testCancelReleasesOperations() throws Exception {
        IgniteEx ign = startGrids(3);

        ign.cluster().state(ACTIVE);

        IgniteCache<Integer, Integer> cache = ign.cache(DEFAULT_CACHE_NAME);

        CountDownLatch locked = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);

        IgniteInternalFuture<?> longTx = GridTestUtils.runAsync(() -> {
            try (Transaction tx = grid(1).transactions().txStart(PESSIMISTIC, REPEATABLE_READ)) {
                grid(1).cache(DEFAULT_CACHE_NAME).put(0, 0);

                locked.countDown();
                release.await();

                tx.commit();
            }

            return null;
        });

        locked.await();

        IgniteInternalFuture<?> put = null;

        try {
            IgniteFuture<Void> snp = ign.snapshot().createSnapshot("snp");

            assertFalse(GridTestUtils.waitForCondition(snp::isDone, 2_000));

            ign.snapshot().cancelSnapshot("snp").get();

            assertTrue(GridTestUtils.waitForCondition(snp::isDone, 5_000));

            put = GridTestUtils.runAsync(() -> cache.put(1, 1));

            assertTrue("A cache operation is still blocked after the snapshot was cancelled",
                GridTestUtils.waitForCondition(put::isDone, 10_000));
        }
        finally {
            release.countDown();
            longTx.get();

            if (put != null)
                put.get();
        }
    }
}
