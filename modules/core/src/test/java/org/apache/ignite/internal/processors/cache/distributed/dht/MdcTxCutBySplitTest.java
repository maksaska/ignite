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
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.apache.ignite.Ignite;
import org.apache.ignite.IgniteCache;
import org.apache.ignite.cache.CacheWriteSynchronizationMode;
import org.apache.ignite.cache.affinity.Affinity;
import org.apache.ignite.cluster.ClusterNode;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.internal.IgniteInternalFuture;
import org.apache.ignite.internal.TestRecordingCommunicationSpi;
import org.apache.ignite.internal.processors.cache.distributed.near.GridNearLockRequest;
import org.apache.ignite.internal.processors.cache.distributed.near.GridNearLockResponse;
import org.apache.ignite.internal.processors.cache.distributed.near.GridNearTxFinishRequest;
import org.apache.ignite.internal.processors.cache.distributed.near.GridNearTxFinishResponse;
import org.apache.ignite.internal.processors.cache.distributed.near.GridNearTxPrepareRequest;
import org.apache.ignite.internal.processors.cache.distributed.near.GridNearTxPrepareResponse;
import org.apache.ignite.internal.util.typedef.G;
import org.apache.ignite.internal.util.typedef.internal.U;
import org.apache.ignite.plugin.extensions.communication.Message;
import org.apache.ignite.testframework.GridTestUtils;
import org.apache.ignite.transactions.Transaction;
import org.apache.ignite.transactions.TransactionConcurrency;
import org.jetbrains.annotations.Nullable;
import org.junit.Ignore;
import org.junit.Test;

import static org.apache.ignite.cache.CacheAtomicityMode.TRANSACTIONAL;
import static org.apache.ignite.cache.CacheWriteSynchronizationMode.FULL_SYNC;
import static org.apache.ignite.cache.CacheWriteSynchronizationMode.PRIMARY_SYNC;
import static org.apache.ignite.transactions.TransactionConcurrency.OPTIMISTIC;
import static org.apache.ignite.transactions.TransactionConcurrency.PESSIMISTIC;
import static org.apache.ignite.transactions.TransactionIsolation.REPEATABLE_READ;

/**
 * Cuts a transaction by a split of two DCs at every message its commit sends from one DC to the other. DC1 is the
 * main DC, DC2 is cut off. The message is held on its way, then the DCs are split, so it never arrives, as when the
 * link drops while the message is in flight.
 *
 * <p>Each row runs one transaction, either over one key with its primary in DC2 (one-phase commit) or over two keys
 * with primaries in both DCs (two-phase commit), from the client of DC1 or DC2. Before DC2 rejoins, the transaction
 * must have finished, no node may keep an active transaction, and DC1 must hold either all old or all new values,
 * the new ones if the client was told the commit succeeded. After DC2 rejoins, every DC holds what DC1 held.</p>
 */
public class MdcTxCutBySplitTest extends MdcTopologySplitAbstractTest {
    /** DC that keeps writing. */
    private static final String MAIN_DC = DC1;

    /** DC cut off by the split. */
    private static final String CUT_DC = DC2;

    /** Failure detection timeout of every node. */
    private static final long FAILURE_DETECTION_TIMEOUT = 1_000;

    /** Time for a cut transaction to finish, and for its nodes to forget it. */
    private static final long TX_TIMEOUT = 15_000;

    /** Keys each client of the continuous load can insert, at most. */
    private static final int LOAD_KEYS_PER_CLIENT = 1_000_000;

    /** Value of a key before a row's transaction. */
    private static final int OLD = 0;

    /** Value a row's transaction writes. */
    private static final int NEW = 1;

    /** Issue of the rows that lose an acknowledged write: the backup prepare from DC2 to DC1 is cut. */
    private static final String LOST_WRITE_ISSUE = "https://issues.apache.org/jira/browse/IGNITE-TBD1";

    /** Issue of the rows that commit a transaction partly: the backup prepare response from DC2 to DC1 is cut. */
    private static final String PARTIAL_COMMIT_ISSUE = "https://issues.apache.org/jira/browse/IGNITE-TBD2";

    /** Next key to try when picking a key for a row. */
    private int nextKey;

    /** Content of each cache: the keys of all rows run so far, with the values DC1 kept. */
    private final Map<String, Map<Integer, Integer>> expected = new HashMap<>();

    /** {@inheritDoc} */
    @Override protected List<String> dataCenters() {
        return Arrays.asList(MAIN_DC, CUT_DC);
    }

    /** {@inheritDoc} */
    @Override protected IgniteConfiguration getConfiguration(String igniteInstanceName) throws Exception {
        // Each row splits the cluster once, and a split takes the failure detection time: 10 s by default.
        return super.getConfiguration(igniteInstanceName)
            .setFailureDetectionTimeout(FAILURE_DETECTION_TIMEOUT)
            .setClientFailureDetectionTimeout(FAILURE_DETECTION_TIMEOUT);
    }

    /** {@inheritDoc} */
    @Override protected long getTestTimeout() {
        return 10 * 60_000;
    }

    /** {@inheritDoc} */
    @Override protected void afterTest() throws Exception {
        nextKey = 0;

        expected.clear();

        super.afterTest();
    }

    /** */
    @Test
    public void testOptimisticFullSync() throws Exception {
        checkRows(rows(OPTIMISTIC, FULL_SYNC, null));
    }

    /** */
    @Test
    public void testOptimisticPrimarySync() throws Exception {
        checkRows(rows(OPTIMISTIC, PRIMARY_SYNC, null));
    }

    /** */
    @Test
    public void testPessimisticFullSync() throws Exception {
        checkRows(rows(PESSIMISTIC, FULL_SYNC, null));
    }

    /** */
    @Test
    public void testPessimisticPrimarySync() throws Exception {
        checkRows(rows(PESSIMISTIC, PRIMARY_SYNC, null));
    }

    /** Rows that lose an acknowledged write: DC2's primary commits alone once its prepare to DC1's backup is cut. */
    @Test
    @Ignore(LOST_WRITE_ISSUE)
    public void testCutBackupPrepareFromCutDc() throws Exception {
        checkRows(rowsOfIssue(LOST_WRITE_ISSUE));
    }

    /** Rows that commit a transaction partly: DC1 commits its part, DC2 rolls its part back. */
    @Test
    @Ignore(PARTIAL_COMMIT_ISSUE)
    public void testCutBackupPrepareResponseToMainDc() throws Exception {
        checkRows(rowsOfIssue(PARTIAL_COMMIT_ISSUE));
    }

    /**
     * Both clients insert one key per transaction without a pause while the link between the DCs drops: the split
     * cuts communication before discovery notices. Every insert a client was told succeeded must be in DC1 after the
     * split, and in every DC after DC2 rejoins.
     */
    @Test
    public void testContinuousLoad() throws Exception {
        startCluster();

        client(MAIN_DC).createCache(cacheConfiguration(DEFAULT_CACHE_NAME, TRANSACTIONAL, mainDcValidator(MAIN_DC)));

        AtomicBoolean stop = new AtomicBoolean();

        Map<String, List<Integer>> tried = new ConcurrentHashMap<>();
        Map<String, List<Integer>> acked = new ConcurrentHashMap<>();

        List<IgniteInternalFuture<?>> loadFuts = new ArrayList<>();

        for (String dc : dataCenters()) {
            List<Integer> dcTried = new CopyOnWriteArrayList<>();
            List<Integer> dcAcked = new CopyOnWriteArrayList<>();

            tried.put(dc, dcTried);
            acked.put(dc, dcAcked);

            IgniteCache<Integer, Integer> cache = client(dc).cache(DEFAULT_CACHE_NAME);

            int firstKey = dataCenters().indexOf(dc) * LOAD_KEYS_PER_CLIENT;

            loadFuts.add(GridTestUtils.runAsync(() -> {
                for (int key = firstKey; key < firstKey + LOAD_KEYS_PER_CLIENT && !stop.get(); key++) {
                    dcTried.add(key);

                    try {
                        cache.put(key, key);

                        dcAcked.add(key);
                    }
                    catch (Exception ignored) {
                        // Rejected or failed by the split: the client knows the insert may be lost.
                    }
                }
            }, "load-" + dc));
        }

        assertTrue(GridTestUtils.waitForCondition(() -> acked.values().stream().allMatch(l -> l.size() >= 100),
            TX_TIMEOUT));

        split(CUT_DC);

        // Keep loading the split cluster a little: DC1 keeps writing, DC2's writes get rejected.
        U.sleep(500);

        stop.set(true);

        for (IgniteInternalFuture<?> fut : loadFuts)
            fut.get(TX_TIMEOUT);

        assertNoActiveTransactions();

        List<Integer> keys = new ArrayList<>();

        tried.values().forEach(keys::addAll);

        Map<Integer, Integer> kept = valuesInDc(DEFAULT_CACHE_NAME, MAIN_DC, keys);

        for (Map.Entry<String, List<Integer>> e : acked.entrySet()) {
            log.info(">>> Inserts acknowledged to the client of " + e.getKey() + ": " + e.getValue().size() +
                " of " + tried.get(e.getKey()).size());

            for (Integer key : e.getValue())
                assertEquals("Acknowledged insert lost in " + MAIN_DC + " (client of " + e.getKey() + ')', key, kept.get(key));
        }

        restartAllSegmentsExcept(MAIN_DC);

        assertDataInEveryDc(DEFAULT_CACHE_NAME, kept);
    }

    /**
     * Runs the rows on one cluster, splitting it and letting DC2 rejoin once per row.
     *
     * @param rows Rows.
     */
    private void checkRows(List<Row> rows) throws Exception {
        startCluster();

        for (CacheWriteSynchronizationMode syncMode : new CacheWriteSynchronizationMode[] {FULL_SYNC, PRIMARY_SYNC}) {
            client(MAIN_DC).createCache(cacheConfiguration(syncMode.name(), TRANSACTIONAL, mainDcValidator(MAIN_DC))
                .setWriteSynchronizationMode(syncMode));

            expected.put(syncMode.name(), new HashMap<>());
        }

        List<String> failed = new ArrayList<>();

        for (Row row : rows) {
            long start = System.nanoTime();

            String problem = checkRow(row);

            if (problem != null)
                failed.add(row + ": " + problem);

            log.info(">>> Row done in " + TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start) + " ms" +
                (problem == null ? "" : ", FAILED: " + problem) + ": " + row);
        }

        assertTrue("Failed rows:\n  " + String.join("\n  ", failed), failed.isEmpty());
    }

    /**
     * Runs one row and lets DC2 rejoin, whatever the row's checks found, so the next row starts on a whole cluster.
     *
     * @param row Row to run.
     * @return What the row's checks found wrong, or {@code null} if nothing.
     */
    @Nullable private String checkRow(Row row) throws Exception {
        log.info(">>> Row: " + row);

        String cacheName = row.syncMode.name();

        IgniteCache<Integer, Integer> cache = client(row.clientDc).cache(cacheName);

        Map<Role, ClusterNode> nodes = new HashMap<>();

        List<Integer> keys = new ArrayList<>();

        if (row.twoPhase)
            keys.add(pickKey(cacheName, MAIN_DC, Role.PRIMARY_IN_DC1, Role.BACKUP_IN_DC2, nodes));

        keys.add(pickKey(cacheName, CUT_DC, Role.PRIMARY_IN_DC2, Role.BACKUP_IN_DC1, nodes));

        nodes.put(Role.CLIENT, client(row.clientDc).cluster().localNode());

        for (Integer key : keys)
            cache.put(key, OLD);

        ClusterNode rcv = nodes.get(row.to);

        AtomicBoolean held = new AtomicBoolean();

        TestRecordingCommunicationSpi.spi(ignite(nodes.get(row.from))).blockMessages((node, msg) ->
            node.id().equals(rcv.id()) && row.msgCls.isInstance(msg) && held.compareAndSet(false, true));

        IgniteInternalFuture<?> txFut = GridTestUtils.runAsync(() -> {
            try (Transaction tx = client(row.clientDc).transactions().txStart(row.concurrency, REPEATABLE_READ)) {
                for (Integer key : keys)
                    cache.put(key, NEW);

                tx.commit();
            }
        });

        assertTrue("Message was not sent: " + row, GridTestUtils.waitForCondition(held::get, TX_TIMEOUT));

        split(CUT_DC);

        String problem = null;

        try {
            checkCutTransaction(txFut, cacheName, keys);
        }
        catch (AssertionError e) {
            problem = e.getMessage();
        }

        Map<Integer, Integer> kept = valuesInDc(cacheName, MAIN_DC, keys);

        restartAllSegmentsExcept(MAIN_DC);

        expected.get(cacheName).putAll(kept);

        try {
            for (Map.Entry<String, Map<Integer, Integer>> e : expected.entrySet())
                assertDataInEveryDc(e.getKey(), e.getValue());
        }
        catch (AssertionError e) {
            problem = (problem == null ? "" : problem + "; ") + "after DC2 rejoined: " + e.getMessage();
        }

        return problem;
    }

    /**
     * Checks a row's transaction while the cluster is split: it has finished, no node keeps an active transaction,
     * and DC1 holds all old or all new values, the new ones if the client was told the commit succeeded.
     *
     * @param txFut Future of the row's transaction.
     * @param cacheName Cache name.
     * @param keys Keys the transaction writes.
     */
    private void checkCutTransaction(IgniteInternalFuture<?> txFut, String cacheName, List<Integer> keys)
        throws Exception {
        boolean committed;

        try {
            txFut.get(TX_TIMEOUT);

            committed = true;
        }
        catch (Exception e) {
            assertTrue("Transaction did not finish within " + TX_TIMEOUT + " ms", txFut.isDone());

            committed = false;

            log.info(">>> Transaction failed: " + e);
        }

        assertNoActiveTransactions();

        Map<Integer, Integer> kept = valuesInDc(cacheName, MAIN_DC, keys);

        assertEquals("Keys missing in " + MAIN_DC, keys.size(), kept.size());

        if (committed)
            assertTrue("Acknowledged write lost in " + MAIN_DC + ": " + kept, kept.values().stream().allMatch(v -> v == NEW));
        else
            assertEquals("Partly committed in " + MAIN_DC + ": " + kept, 1, kept.values().stream().distinct().count());
    }

    /** Waits until no node has an active transaction. */
    private static void assertNoActiveTransactions() throws Exception {
        assertTrue("Active transactions left", GridTestUtils.waitForCondition(() ->
            G.allGrids().stream().allMatch(ignite ->
                ((IgniteEx)ignite).context().cache().context().tm().activeTransactions().isEmpty()), TX_TIMEOUT));
    }

    /**
     * Picks a key not used yet whose primary is in the given DC and whose backup is in the other one.
     *
     * @param cacheName Cache name.
     * @param primaryDc DC of the primary.
     * @param primaryRole Role of the key's primary.
     * @param backupRole Role of the key's backup.
     * @param nodes Nodes by their role, filled with the key's primary and backup.
     * @return Key.
     */
    private int pickKey(String cacheName, String primaryDc, Role primaryRole, Role backupRole, Map<Role, ClusterNode> nodes) {
        Affinity<Integer> aff = client(MAIN_DC).affinity(cacheName);

        while (true) {
            int key = nextKey++;

            List<ClusterNode> owners = new ArrayList<>(aff.mapKeyToPrimaryAndBackups(key));

            if (!owners.get(0).dataCenterId().equals(primaryDc))
                continue;

            assertEquals(2, owners.size());
            assertFalse(owners.get(1).dataCenterId().equals(primaryDc));

            nodes.put(primaryRole, owners.get(0));
            nodes.put(backupRole, owners.get(1));

            return key;
        }
    }

    /**
     * @param cacheName Cache name.
     * @param dc DC.
     * @param keys Keys.
     * @return Values of the keys held by the DC's servers, only for the keys the DC has.
     */
    private Map<Integer, Integer> valuesInDc(String cacheName, String dc, List<Integer> keys) {
        Map<Integer, Integer> vals = new HashMap<>();

        for (int idx : serverIndexes(dc)) {
            IgniteCache<Integer, Integer> cache = grid(idx).cache(cacheName);

            for (Integer key : keys) {
                Integer val = cache.localPeek(key);

                if (val != null)
                    vals.put(key, val);
            }
        }

        return vals;
    }

    /**
     * @param node Node.
     * @return Ignite instance of the node.
     */
    private static Ignite ignite(ClusterNode node) {
        return G.ignite(node.id());
    }

    /**
     * Rows of the matrix: every message a commit sends from one DC to the other. The transactions run from DC2's
     * client; the client of DC1 adds the client's messages in the other direction.
     *
     * @param concurrency Transaction concurrency.
     * @param syncMode Write synchronization mode.
     * @param issue Issue of the rows to return, {@code null} for the rows without a known issue.
     * @return Rows.
     */
    private static List<Row> rows(
        TransactionConcurrency concurrency,
        CacheWriteSynchronizationMode syncMode,
        @Nullable String issue
    ) {
        boolean pessimistic = concurrency == PESSIMISTIC;

        List<Row> rows = new ArrayList<>();

        // One-phase commit, one key with its primary in DC2 and its backup in DC1.
        rows.add(new Row(concurrency, syncMode, false, CUT_DC, GridDhtTxPrepareRequest.class, Role.PRIMARY_IN_DC2, Role.BACKUP_IN_DC1));
        rows.add(new Row(concurrency, syncMode, false, CUT_DC, GridDhtTxPrepareResponse.class, Role.BACKUP_IN_DC1, Role.PRIMARY_IN_DC2));

        if (pessimistic) {
            rows.add(new Row(concurrency, syncMode, false, MAIN_DC, GridNearLockRequest.class, Role.CLIENT, Role.PRIMARY_IN_DC2));
            rows.add(new Row(concurrency, syncMode, false, MAIN_DC, GridNearLockResponse.class, Role.PRIMARY_IN_DC2, Role.CLIENT));
        }

        rows.add(new Row(concurrency, syncMode, false, MAIN_DC, GridNearTxPrepareRequest.class, Role.CLIENT, Role.PRIMARY_IN_DC2));
        rows.add(new Row(concurrency, syncMode, false, MAIN_DC, GridNearTxPrepareResponse.class, Role.PRIMARY_IN_DC2, Role.CLIENT));

        // Two-phase commit, from DC2's client.
        if (pessimistic) {
            rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridNearLockRequest.class, Role.CLIENT, Role.PRIMARY_IN_DC1));
            rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridNearLockResponse.class, Role.PRIMARY_IN_DC1, Role.CLIENT));
        }

        rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridNearTxPrepareRequest.class, Role.CLIENT, Role.PRIMARY_IN_DC1));
        rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridNearTxPrepareResponse.class, Role.PRIMARY_IN_DC1, Role.CLIENT));
        rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridDhtTxPrepareRequest.class, Role.PRIMARY_IN_DC1, Role.BACKUP_IN_DC2));
        rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridDhtTxPrepareResponse.class, Role.BACKUP_IN_DC2, Role.PRIMARY_IN_DC1));
        rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridDhtTxPrepareRequest.class, Role.PRIMARY_IN_DC2, Role.BACKUP_IN_DC1));
        rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridDhtTxPrepareResponse.class, Role.BACKUP_IN_DC1, Role.PRIMARY_IN_DC2));
        rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridNearTxFinishRequest.class, Role.CLIENT, Role.PRIMARY_IN_DC1));
        rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridNearTxFinishResponse.class, Role.PRIMARY_IN_DC1, Role.CLIENT));
        rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridDhtTxFinishRequest.class, Role.PRIMARY_IN_DC1, Role.BACKUP_IN_DC2));
        rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridDhtTxFinishRequest.class, Role.PRIMARY_IN_DC2, Role.BACKUP_IN_DC1));

        if (syncMode == FULL_SYNC) {
            rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridDhtTxFinishResponse.class, Role.BACKUP_IN_DC2, Role.PRIMARY_IN_DC1));
            rows.add(new Row(concurrency, syncMode, true, CUT_DC, GridDhtTxFinishResponse.class, Role.BACKUP_IN_DC1, Role.PRIMARY_IN_DC2));
        }

        // Two-phase commit, from DC1's client: its messages to and from DC2's primary.
        if (pessimistic) {
            rows.add(new Row(concurrency, syncMode, true, MAIN_DC, GridNearLockRequest.class, Role.CLIENT, Role.PRIMARY_IN_DC2));
            rows.add(new Row(concurrency, syncMode, true, MAIN_DC, GridNearLockResponse.class, Role.PRIMARY_IN_DC2, Role.CLIENT));
        }

        rows.add(new Row(concurrency, syncMode, true, MAIN_DC, GridNearTxPrepareRequest.class, Role.CLIENT, Role.PRIMARY_IN_DC2));
        rows.add(new Row(concurrency, syncMode, true, MAIN_DC, GridNearTxPrepareResponse.class, Role.PRIMARY_IN_DC2, Role.CLIENT));
        rows.add(new Row(concurrency, syncMode, true, MAIN_DC, GridNearTxFinishRequest.class, Role.CLIENT, Role.PRIMARY_IN_DC2));
        rows.add(new Row(concurrency, syncMode, true, MAIN_DC, GridNearTxFinishResponse.class, Role.PRIMARY_IN_DC2, Role.CLIENT));

        rows.removeIf(row -> !Objects.equals(row.issue, issue));

        return rows;
    }

    /**
     * @param issue Issue.
     * @return Rows of every concurrency and synchronization mode that hit the issue.
     */
    private static List<Row> rowsOfIssue(String issue) {
        List<Row> rows = new ArrayList<>();

        for (TransactionConcurrency concurrency : TransactionConcurrency.values()) {
            for (CacheWriteSynchronizationMode syncMode : new CacheWriteSynchronizationMode[] {FULL_SYNC, PRIMARY_SYNC})
                rows.addAll(rows(concurrency, syncMode, issue));
        }

        return rows;
    }

    /** Node of a row's transaction. */
    private enum Role {
        /** Client that runs the transaction. */
        CLIENT,

        /** Primary of the key whose primary is in DC1. */
        PRIMARY_IN_DC1,

        /** Backup of the key whose primary is in DC1. */
        BACKUP_IN_DC2,

        /** Primary of the key whose primary is in DC2. */
        PRIMARY_IN_DC2,

        /** Backup of the key whose primary is in DC2. */
        BACKUP_IN_DC1
    }

    /** Transaction and the message the split cuts. */
    private static class Row {
        /** */
        final TransactionConcurrency concurrency;

        /** Write synchronization mode of the cache. */
        final CacheWriteSynchronizationMode syncMode;

        /** Two keys with primaries in both DCs if {@code true}, one key with its primary in DC2 otherwise. */
        final boolean twoPhase;

        /** DC of the client that runs the transaction. */
        final String clientDc;

        /** Class of the cut message. */
        final Class<? extends Message> msgCls;

        /** Sender of the cut message. */
        final Role from;

        /** Receiver of the cut message. */
        final Role to;

        /** Known issue the row hits, {@code null} if none. */
        @Nullable final String issue;

        /**
         * @param concurrency Transaction concurrency.
         * @param syncMode Write synchronization mode of the cache.
         * @param twoPhase Two keys with primaries in both DCs if {@code true}, one key with its primary in DC2 otherwise.
         * @param clientDc DC of the client that runs the transaction.
         * @param msgCls Class of the cut message.
         * @param from Sender of the cut message.
         * @param to Receiver of the cut message.
         */
        Row(
            TransactionConcurrency concurrency,
            CacheWriteSynchronizationMode syncMode,
            boolean twoPhase,
            String clientDc,
            Class<? extends Message> msgCls,
            Role from,
            Role to
        ) {
            this.concurrency = concurrency;
            this.syncMode = syncMode;
            this.twoPhase = twoPhase;
            this.clientDc = clientDc;
            this.msgCls = msgCls;
            this.from = from;
            this.to = to;

            if (msgCls == GridDhtTxPrepareRequest.class && from == Role.PRIMARY_IN_DC2 && to == Role.BACKUP_IN_DC1)
                issue = LOST_WRITE_ISSUE;
            else if (concurrency == OPTIMISTIC && msgCls == GridDhtTxPrepareResponse.class && from == Role.BACKUP_IN_DC2)
                issue = PARTIAL_COMMIT_ISSUE;
            else
                issue = null;
        }

        /** {@inheritDoc} */
        @Override public String toString() {
            return concurrency + " " + syncMode + (twoPhase ? " 2PC" : " 1PC") + " client=" + clientDc + ' ' +
                msgCls.getSimpleName() + ' ' + from + "->" + to;
        }
    }
}
