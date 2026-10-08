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
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import org.apache.ignite.IgniteCache;
import org.apache.ignite.cache.CacheWriteSynchronizationMode;
import org.apache.ignite.cache.affinity.Affinity;
import org.apache.ignite.cluster.ClusterNode;
import org.apache.ignite.internal.IgniteInternalFuture;
import org.apache.ignite.internal.TestRecordingCommunicationSpi;
import org.apache.ignite.internal.processors.cache.distributed.near.GridNearTxPrepareResponse;
import org.apache.ignite.internal.util.typedef.G;
import org.apache.ignite.plugin.extensions.communication.Message;
import org.apache.ignite.testframework.GridTestUtils;
import org.apache.ignite.transactions.Transaction;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.Parameterized;

import static org.apache.ignite.cache.CacheAtomicityMode.TRANSACTIONAL;
import static org.apache.ignite.cache.CacheWriteSynchronizationMode.FULL_SYNC;
import static org.apache.ignite.cache.CacheWriteSynchronizationMode.PRIMARY_SYNC;
import static org.apache.ignite.transactions.TransactionConcurrency.OPTIMISTIC;
import static org.apache.ignite.transactions.TransactionIsolation.REPEATABLE_READ;

/**
 * An optimistic transaction is committed partly when a split of two data centers cuts the prepare of a primary in
 * the main data center.
 *
 * <p>DC1 is the main DC, so after the split only DC1 may write. The client of DC2 runs a transaction over two keys:
 * key A with its primary in DC1 and its backup in DC2, key B with its primary in DC2 and its backup in DC1, the four
 * copies on four different servers. The link between the DCs drops while a prepare message of A's primary is on its
 * way between the DCs. The client gets an error, yet DC1 commits A and not B. When the split heals, DC2's nodes
 * restart and take their data from DC1, so every DC ends up with the transaction committed partly.</p>
 */
@RunWith(Parameterized.class)
public class MdcTxSplitPartialCommitTest extends MdcTopologySplitAbstractTest {
    /** Time for the transaction to finish once the cluster is split. */
    private static final long TX_TIMEOUT = 60_000;

    /** Value of the keys before the transaction. */
    private static final int OLD = 0;

    /** Value the transaction writes. */
    private static final int NEW = 1;

    /** */
    @Parameterized.Parameter(0)
    public CacheWriteSynchronizationMode syncMode;

    /** */
    @Parameterized.Parameter(1)
    public CutMessage cutMsg;

    /** @return Both synchronization modes with every cut message. */
    @Parameterized.Parameters(name = "syncMode={0}, cut={1}")
    public static Collection<Object[]> parameters() {
        List<Object[]> params = new ArrayList<>();

        for (CacheWriteSynchronizationMode syncMode : new CacheWriteSynchronizationMode[] {FULL_SYNC, PRIMARY_SYNC}) {
            for (CutMessage cutMsg : CutMessage.values())
                params.add(new Object[] {syncMode, cutMsg});
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
    public void testTransactionCommittedWhole() throws Exception {
        startCluster();

        IgniteCache<Integer, Integer> cache = client(DC2).createCache(
            cacheConfiguration(DEFAULT_CACHE_NAME, TRANSACTIONAL, mainDcValidator(DC1))
                .setWriteSynchronizationMode(syncMode));

        List<ClusterNode> owners = new ArrayList<>();

        int keyA = pickKey(DC1, owners);
        int keyB = pickKey(DC2, owners);

        ClusterNode primaryA = owners.get(0);
        ClusterNode backupA = owners.get(1);

        cache.put(keyA, OLD);
        cache.put(keyB, OLD);

        ClusterNode snd;
        ClusterNode rcv;

        switch (cutMsg) {
            case DHT_PREPARE_REQUEST:
                snd = primaryA;
                rcv = backupA;

                break;

            case DHT_PREPARE_RESPONSE:
                snd = backupA;
                rcv = primaryA;

                break;

            default:
                snd = primaryA;
                rcv = client(DC2).cluster().localNode();
        }

        UUID rcvId = rcv.id();

        AtomicBoolean held = new AtomicBoolean();

        // Hold the first such message on its way between the DCs.
        TestRecordingCommunicationSpi.spi(G.ignite(snd.id())).blockMessages((node, msg) ->
            node.id().equals(rcvId) && cutMsg.cls.isInstance(msg) && held.compareAndSet(false, true));

        IgniteInternalFuture<?> txFut = GridTestUtils.runAsync(() -> {
            try (Transaction tx = client(DC2).transactions().txStart(OPTIMISTIC, REPEATABLE_READ)) {
                cache.put(keyA, NEW);
                cache.put(keyB, NEW);

                tx.commit();
            }
        });

        assertTrue("Prepare message was not sent", GridTestUtils.waitForCondition(held::get, TX_TIMEOUT));

        // Cut the link between the DCs while the message is held: it never arrives.
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

        Map<Integer, Integer> inDc1 = valuesIn(DC1, keyA, keyB);
        Map<Integer, Integer> inDc2 = valuesIn(DC2, keyA, keyB);

        // Heal: drop the held message, restart DC2's nodes, they rejoin DC1 and take their data from it.
        restartAllSegmentsExcept(DC1);

        Map<Integer, Integer> afterHeal = new TreeMap<>(client(DC1).<Integer, Integer>cache(DEFAULT_CACHE_NAME)
            .getAll(new HashSet<>(Arrays.asList(keyA, keyB))));

        String outcome = "A=" + keyA + ", B=" + keyB + "; commit " + (committed ? "succeeded" : "failed") +
            "; during the split: DC1=" + inDc1 + ", DC2=" + inDc2 + "; after the heal: " + afterHeal;

        log.info(">>> " + outcome);

        assertEquals("Transaction committed partly (" + outcome + ')', 1,
            afterHeal.values().stream().distinct().count());
    }

    /**
     * Picks a key with its primary in the given DC and its backup in the other one, on servers that hold no key picked
     * before.
     *
     * @param primaryDc DC of the primary.
     * @param owners Primaries and backups of the keys picked before, in this order; the key's ones are added.
     * @return Key.
     */
    private int pickKey(String primaryDc, List<ClusterNode> owners) {
        Affinity<Integer> aff = client(DC2).affinity(DEFAULT_CACHE_NAME);

        for (int key = 0; ; key++) {
            List<ClusterNode> keyOwners = new ArrayList<>(aff.mapKeyToPrimaryAndBackups(key));

            if (primaryDc.equals(keyOwners.get(0).dataCenterId()) && keyOwners.stream().noneMatch(owners::contains)) {
                assertFalse(primaryDc.equals(keyOwners.get(1).dataCenterId()));

                owners.addAll(keyOwners);

                return key;
            }
        }
    }

    /**
     * @param dc DC.
     * @param keys Keys.
     * @return Values of the keys held by the DC's servers, only for the keys the DC has.
     */
    private Map<Integer, Integer> valuesIn(String dc, int... keys) {
        Map<Integer, Integer> vals = new TreeMap<>();

        for (int idx : serverIndexes(dc)) {
            IgniteCache<Integer, Integer> cache = grid(idx).cache(DEFAULT_CACHE_NAME);

            for (int key : keys) {
                Integer val = cache.localPeek(key);

                if (val != null)
                    vals.put(key, val);
            }
        }

        return vals;
    }

    /** Prepare message of A's primary in DC1 that the split cuts. */
    public enum CutMessage {
        /** Prepare request from A's primary in DC1 to A's backup in DC2. */
        DHT_PREPARE_REQUEST(GridDhtTxPrepareRequest.class),

        /** Prepare response from A's backup in DC2 to A's primary in DC1. */
        DHT_PREPARE_RESPONSE(GridDhtTxPrepareResponse.class),

        /** Prepare response from A's primary in DC1 to the client of DC2. */
        NEAR_PREPARE_RESPONSE(GridNearTxPrepareResponse.class);

        /** Message class. */
        final Class<? extends Message> cls;

        /** @param cls Message class. */
        CutMessage(Class<? extends Message> cls) {
            this.cls = cls;
        }
    }
}
