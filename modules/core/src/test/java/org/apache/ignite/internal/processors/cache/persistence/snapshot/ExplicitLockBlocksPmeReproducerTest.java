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
import java.util.concurrent.locks.Lock;
import org.apache.ignite.configuration.CacheConfiguration;
import org.apache.ignite.configuration.DataRegionConfiguration;
import org.apache.ignite.configuration.DataStorageConfiguration;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.internal.IgniteInternalFuture;
import org.apache.ignite.lang.IgniteFuture;
import org.apache.ignite.testframework.GridTestUtils;
import org.apache.ignite.testframework.junits.common.GridCommonAbstractTest;
import org.junit.Test;

import static org.apache.ignite.cache.CacheAtomicityMode.TRANSACTIONAL;
import static org.apache.ignite.cluster.ClusterState.ACTIVE;

/**
 * An explicit lock ({@code IgniteCache#lock}) held on one server blocks a node join's PME and a full snapshot
 * for as long as it is held, although {@code txTimeoutOnPartitionMapExchange} is set.
 */
public class ExplicitLockBlocksPmeReproducerTest extends GridCommonAbstractTest {
    /** {@inheritDoc} */
    @Override protected IgniteConfiguration getConfiguration(String name) throws Exception {
        return super.getConfiguration(name)
            .setDataStorageConfiguration(new DataStorageConfiguration()
                .setDefaultDataRegionConfiguration(new DataRegionConfiguration().setPersistenceEnabled(true)))
            .setCacheConfiguration(new CacheConfiguration<>(DEFAULT_CACHE_NAME)
                .setAtomicityMode(TRANSACTIONAL)
                .setBackups(2));
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
    public void testSnapshot() throws Exception {
        IgniteEx ign = startCluster();

        CountDownLatch release = new CountDownLatch(1);
        IgniteInternalFuture<?> holder = holdLock(release);

        try {
            IgniteFuture<Void> snp = ign.snapshot().createSnapshot("snp");

            assertTrue("The snapshot is blocked by the explicit lock",
                GridTestUtils.waitForCondition(snp::isDone, 15_000));
        }
        finally {
            release.countDown();
            holder.get();
        }
    }

    /** */
    @Test
    public void testNodeJoin() throws Exception {
        startCluster();

        stopGrid(2);

        CountDownLatch release = new CountDownLatch(1);
        IgniteInternalFuture<?> holder = holdLock(release);
        IgniteInternalFuture<?> join = null;

        try {
            join = GridTestUtils.runAsync(() -> startGrid(2));

            assertTrue("The join PME is blocked by the explicit lock",
                GridTestUtils.waitForCondition(join::isDone, 15_000));
        }
        finally {
            release.countDown();
            holder.get();

            if (join != null)
                join.get();
        }
    }

    /** Starts three servers with {@code txTimeoutOnPartitionMapExchange} set to 3 seconds. */
    private IgniteEx startCluster() throws Exception {
        IgniteEx ign = startGrids(3);

        ign.cluster().state(ACTIVE);
        ign.cluster().setTxTimeoutOnPartitionMapExchange(3_000);

        ign.cache(DEFAULT_CACHE_NAME).put(0, 0);

        return ign;
    }

    /** Takes an explicit lock on key 0 on grid(1) and holds it until {@code release}. */
    private IgniteInternalFuture<?> holdLock(CountDownLatch release) throws Exception {
        CountDownLatch locked = new CountDownLatch(1);

        IgniteInternalFuture<?> fut = GridTestUtils.runAsync(() -> {
            Lock lock = grid(1).cache(DEFAULT_CACHE_NAME).lock(0);

            lock.lock();

            try {
                locked.countDown();
                release.await();
            }
            finally {
                lock.unlock();
            }

            return null;
        });

        locked.await();

        return fut;
    }
}
