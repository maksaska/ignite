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

package org.apache.ignite.internal.processors.cache.distributed.dht;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.List;
import org.apache.ignite.IgniteCache;
import org.apache.ignite.cache.CacheWriteSynchronizationMode;
import org.apache.ignite.cache.affinity.Affinity;
import org.apache.ignite.cluster.ClusterNode;
import org.apache.ignite.internal.IgniteInternalFuture;
import org.apache.ignite.internal.TestRecordingCommunicationSpi;
import org.apache.ignite.internal.util.typedef.G;
import org.apache.ignite.testframework.GridTestUtils;
import org.apache.ignite.transactions.Transaction;
import org.apache.ignite.transactions.TransactionConcurrency;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.Parameterized;

import static org.apache.ignite.cache.CacheAtomicityMode.TRANSACTIONAL;
import static org.apache.ignite.cache.CacheWriteSynchronizationMode.FULL_SYNC;
import static org.apache.ignite.cache.CacheWriteSynchronizationMode.PRIMARY_SYNC;
import static org.apache.ignite.transactions.TransactionIsolation.REPEATABLE_READ;

/**
 * A transaction the client was told is committed is lost when a split of two data centers cuts the prepare request
 * from the key's primary to its backup.
 *
 * <p>DC1 is the main DC, so after the split only DC1 may write. The key has its primary in DC2 and its backup in DC1.
 * The client of DC2 commits a transaction over this key, and the link between the DCs drops while the primary's
 * prepare request is on its way to the backup. The primary commits alone in DC2 and the client gets a successful
 * commit. DC1 never gets the transaction. When the split heals, DC2's nodes restart and take their data from DC1, so
 * the committed value is gone from every DC.</p>
 */
@RunWith(Parameterized.class)
public class MdcTxSplitLostCommitTest extends MdcTopologySplitAbstractTest {
    /** Time for the transaction to finish once the cluster is split. */
    private static final long TX_TIMEOUT = 60_000;

    /** Value of the key before the transaction. */
    private static final int OLD = 0;

    /** Value the transaction writes. */
    private static final int NEW = 1;

    /** */
    @Parameterized.Parameter(0)
    public TransactionConcurrency concurrency;

    /** */
    @Parameterized.Parameter(1)
    public CacheWriteSynchronizationMode syncMode;

    /** @return Every transaction concurrency with both synchronization modes. */
    @Parameterized.Parameters(name = "concurrency={0}, syncMode={1}")
    public static Collection<Object[]> parameters() {
        List<Object[]> params = new ArrayList<>();

        for (TransactionConcurrency concurrency : TransactionConcurrency.values()) {
            for (CacheWriteSynchronizationMode syncMode : new CacheWriteSynchronizationMode[] {FULL_SYNC, PRIMARY_SYNC})
                params.add(new Object[] {concurrency, syncMode});
        }

        return params;
    }

    /** {@inheritDoc} */
    @Override protected List<String> dataCenters() {
        return Arrays.asList(DC1, DC2);
    }

    /** {@inheritDoc} */
    @Override protected long getTestTimeout() {
        return 5 * 60_000;
    }

    /** */
    @Test
    public void testCommittedValueSurvivesSplit() throws Exception {
        startCluster();

        IgniteCache<Integer, Integer> cache = client(DC2).createCache(
            cacheConfiguration(DEFAULT_CACHE_NAME, TRANSACTIONAL, mainDcValidator(DC1))
                .setWriteSynchronizationMode(syncMode));

        Affinity<Integer> aff = client(DC2).affinity(DEFAULT_CACHE_NAME);

        int key = 0;

        while (!DC2.equals(aff.mapKeyToNode(key).dataCenterId()))
            key++;

        List<ClusterNode> owners = new ArrayList<>(aff.mapKeyToPrimaryAndBackups(key));

        ClusterNode primary = owners.get(0);
        ClusterNode backup = owners.get(1);

        assertEquals(DC1, backup.dataCenterId());

        cache.put(key, OLD);

        // Hold the prepare request from the primary in DC2 to the backup in DC1.
        TestRecordingCommunicationSpi primarySpi = TestRecordingCommunicationSpi.spi(G.ignite(primary.id()));

        primarySpi.blockMessages(GridDhtTxPrepareRequest.class, G.ignite(backup.id()).name());

        int txKey = key;

        IgniteInternalFuture<?> txFut = GridTestUtils.runAsync(() -> {
            try (Transaction tx = client(DC2).transactions().txStart(concurrency, REPEATABLE_READ)) {
                cache.put(txKey, NEW);

                tx.commit();
            }
        });

        assertTrue("Prepare request was not sent",
            GridTestUtils.waitForCondition(primarySpi::hasBlockedMessages, TX_TIMEOUT));

        // Cut the link between the DCs while the request is held: it never reaches DC1.
        split(DC2);

        boolean committed;

        try {
            txFut.get(TX_TIMEOUT);

            committed = true;
        }
        catch (Exception e) {
            log.info(">>> Transaction failed: " + e);

            committed = false;
        }

        Integer inDc1 = valueIn(DC1, key);
        Integer inDc2 = valueIn(DC2, key);

        // Heal: drop the held request, restart DC2's nodes, they rejoin DC1 and take their data from it.
        restartAllSegmentsExcept(DC1);

        Integer afterHeal = client(DC1).<Integer, Integer>cache(DEFAULT_CACHE_NAME).get(key);

        log.info(">>> Commit " + (committed ? "succeeded" : "failed") + "; during the split: DC1=" + inDc1 +
            ", DC2=" + inDc2 + "; after the heal: " + afterHeal);

        if (committed) {
            assertEquals("Committed value lost (during the split: DC1=" + inDc1 + ", DC2=" + inDc2 + ')',
                Integer.valueOf(NEW), afterHeal);
        }
    }

    /**
     * @param dc DC.
     * @param key Key.
     * @return Value of the key in the DC's servers.
     */
    private Integer valueIn(String dc, int key) {
        for (int idx : serverIndexes(dc)) {
            Integer val = grid(idx).<Integer, Integer>cache(DEFAULT_CACHE_NAME).localPeek(key);

            if (val != null)
                return val;
        }

        return null;
    }
}
