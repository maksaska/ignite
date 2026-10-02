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

package org.apache.ignite.spi.failover.topology.validator;

import java.util.Set;
import java.util.concurrent.ThreadLocalRandom;
import javax.cache.CacheException;
import org.apache.ignite.IgniteCache;
import org.apache.ignite.IgniteCheckedException;
import org.apache.ignite.IgniteException;
import org.apache.ignite.IgniteSystemProperties;
import org.apache.ignite.cache.CacheMode;
import org.apache.ignite.cache.affinity.rendezvous.MdcAffinityBackupFilter;
import org.apache.ignite.cache.affinity.rendezvous.RendezvousAffinityFunction;
import org.apache.ignite.cluster.ClusterState;
import org.apache.ignite.configuration.CacheConfiguration;
import org.apache.ignite.configuration.DataRegionConfiguration;
import org.apache.ignite.configuration.DataStorageConfiguration;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.configuration.TopologyValidator;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.testframework.GridTestUtils;
import org.apache.ignite.testframework.junits.common.GridCommonAbstractTest;
import org.apache.ignite.topology.MdcTopologyValidator;
import org.junit.Ignore;
import org.junit.Test;

/** */
public class MultiDataCenterTopologyValidatorTest extends GridCommonAbstractTest {
    /** */
    private static final String DC_ID_0 = "DC0";

    /** */
    private static final String DC_ID_1 = "DC1";

    /** */
    private static final String DC_ID_2 = "DC2";

    /** Data center outside the validator's set. */
    private static final String DC_ID_OUTSIDE = "DC3";

    /** */
    private static final String CACHE_NAME = "cache";

    /** */
    private static final int KEYS_CNT = 100;

    /** */
    private static final String KEY = "key";

    /** */
    private static final String VAL = "val";

    /** {@inheritDoc} */
    @Override protected void afterTest() throws Exception {
        super.afterTest();

        stopAllGrids();

        cleanPersistenceDir();

        System.clearProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID);
    }

    /** {@inheritDoc} */
    @Override protected IgniteConfiguration getConfiguration(String igniteInstanceName) throws Exception {
        return super.getConfiguration(igniteInstanceName)
            .setDataStorageConfiguration(
                new DataStorageConfiguration()
                    .setDefaultDataRegionConfiguration(
                        new DataRegionConfiguration()
                            .setPersistenceEnabled(true)
                    )
            )
            .setConsistentId(igniteInstanceName);
    }

    /** */
    @Test
    public void testEmptyConfig() {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        GridTestUtils.assertThrows(log,
            () -> createClusterWithCache(topValidator, false),
            CacheException.class,
            "Either set of datacenters or main datacenter should be specified.");
    }

    /** */
    @Test
    public void testOddDcsWithMain() {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1, DC_ID_2));
        topValidator.setMainDatacenter(DC_ID_1);

        GridTestUtils.assertThrows(log,
            () -> createClusterWithCache(topValidator, false),
            CacheException.class,
            "Uneven number of datacenters cannot be used along with main datacenter.");
    }

    /** Checks 1DC case with MdcTopologyValidator usage.*/
    @Test
    public void testEmptyDc() {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setDatacenters(Set.of());

        GridTestUtils.assertThrows(log,
            () -> createClusterWithCache(topValidator, false),
            CacheException.class,
            "Please provide a non-empty set of datacenters.");
    }

    /** */
    @Test
    public void testNodeWithoutDcSpecifiedWithMajorityBasedValidator() throws Exception {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1, DC_ID_2));

        IgniteEx srv = startGrid(0);

        waitForTopology(1);

        srv.cluster().state(ClusterState.ACTIVE);

        CacheConfiguration<Object, Object> cfgCache = new CacheConfiguration<>("cache").setTopologyValidator(topValidator);

        IgniteCache cache = srv.createCache(cfgCache);

        GridTestUtils.assertThrows(log, () -> cache.put(KEY, VAL), IgniteException.class, "cache topology is not valid");
    }

    /** */
    @Test
    public void testNodeWithoutDcSpecifiedWithMainBasedValidator() throws Exception {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setMainDatacenter(DC_ID_1);

        IgniteEx srv = startGrid(0);

        waitForTopology(1);

        srv.cluster().state(ClusterState.ACTIVE);

        CacheConfiguration<Object, Object> cfgCache = new CacheConfiguration<>("cache").setTopologyValidator(topValidator);

        IgniteCache cache = srv.createCache(cfgCache);

        GridTestUtils.assertThrows(log, () -> cache.put(KEY, VAL), IgniteException.class, "cache topology is not valid");
    }

    /** */
    @Test
    public void testClientDoesNotAffectValidation() throws Exception {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setMainDatacenter(DC_ID_1);
        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1));

        System.setProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID, DC_ID_0);
        startGrid(0);

        System.setProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID, DC_ID_1);
        IgniteEx client = startClientGrid("client");

        waitForTopology(2);

        client.cluster().state(ClusterState.ACTIVE);

        CacheConfiguration<Object, Object> cfgCache = new CacheConfiguration<>("cache").setTopologyValidator(topValidator);

        IgniteCache<Object, Object> cache = client.getOrCreateCache(cfgCache);

        GridTestUtils.assertThrows(log, () -> cache.put(KEY, VAL), IgniteException.class, "cache topology is not valid");
    }

    /** */
    @Test
    public void testTopologyValidatorEqualityCheck() throws Exception {
        System.setProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID, DC_ID_1);
        IgniteEx srv0 = startGrid(0);

        startGrid(1);

        waitForTopology(2);

        srv0.cluster().state(ClusterState.ACTIVE);

        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setMainDatacenter(DC_ID_1);
        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1));

        CacheConfiguration<Object, Object> cfgCache1 = new CacheConfiguration<>(DEFAULT_CACHE_NAME).setTopologyValidator(topValidator);

        srv0.getOrCreateCache(cfgCache1);

        stopGrid(1);

        srv0.destroyCache(cfgCache1.getName());

        topValidator.setMainDatacenter(DC_ID_0); // Changed

        CacheConfiguration<Object, Object> cfgCache2 = new CacheConfiguration<>(DEFAULT_CACHE_NAME).setTopologyValidator(topValidator);

        srv0.getOrCreateCache(cfgCache2);

        startGrid(2);

        waitForTopology(2);

        GridTestUtils.assertThrows(log, () -> startGrid(1), IgniteCheckedException.class, "Cache topology validator mismatch");
    }

    /** */
    @Test
    public void testMainDcBasedValidator() throws Exception {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1, DC_ID_2, "DC4"));
        topValidator.setMainDatacenter(DC_ID_1);

        IgniteCache<Object, Object> cache = createClusterWithCache(topValidator, true);

        cache.put(KEY, VAL);
        assertEquals(VAL, cache.get(KEY));

        stopGrid(2);

        cache.put(KEY, VAL + 1);
        assertEquals(VAL + 1, cache.get(KEY));

        stopGrid(1);

        GridTestUtils.assertThrows(log, () -> cache.put(KEY, VAL + 2), IgniteException.class, "cache topology is not valid");
    }

    /** */
    @Test
    public void testMajorityBasedValidator() throws Exception {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1, DC_ID_2));

        IgniteCache<Object, Object> cache = createClusterWithCache(topValidator, true);

        cache.put(KEY, VAL);
        assertEquals(VAL, cache.get(KEY));

        int randomNode = ThreadLocalRandom.current().nextInt(1, 3);

        stopGrid(randomNode);

        cache.put(KEY, VAL + 1);
        assertEquals(VAL + 1, cache.get(KEY));

        stopGrid(randomNode == 1 ? 2 : 1);

        GridTestUtils.assertThrows(log, () -> cache.put(KEY, VAL + 2), IgniteException.class, "cache topology is not valid");
    }

    /** */
    @Test
    public void testBigCluster() throws Exception {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1, DC_ID_2));

        System.setProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID, DC_ID_0);

        startGrid(0);

        System.setProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID, DC_ID_1);

        IgniteEx srv = startGrid(1);
        startGrid(10);
        startGrid(11);
        startGrid(12);

        System.setProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID, DC_ID_2);

        startGrid(2);

        waitForTopology(6);

        srv.cluster().state(ClusterState.ACTIVE);

        CacheConfiguration<Object, Object> cfgCache = new CacheConfiguration<>("cache").setTopologyValidator(topValidator);

        IgniteCache<Object, Object> cache = srv.createCache(cfgCache);

        cache.put(KEY, VAL);
        assertEquals(VAL, cache.get(KEY));

        stopGrid(0);
        stopGrid(2);

        // Checking case when 4 nodes are alive, but only in single DC
        GridTestUtils.assertThrows(log, () -> cache.put(KEY, VAL + 2), IgniteException.class, "cache topology is not valid");
    }

    /**
     * Checks the loss and return of the main data center: the other data center rejects writes but reads
     * every key from its backups, and after the main data center returns the copies are equal.
     */
    @Test
    public void testMainDcLossAndReturn() throws Exception {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1));
        topValidator.setMainDatacenter(DC_ID_0);

        startDataCenter(DC_ID_0, 0, 1);
        IgniteEx srv = startDataCenter(DC_ID_1, 2, 3);

        waitForTopology(4);

        srv.cluster().state(ClusterState.ACTIVE);

        IgniteCache<Integer, Integer> cache = srv.createCache(partitionedCacheConfiguration(topValidator, 2, 1));

        for (int i = 0; i < KEYS_CNT; i++)
            cache.put(i, i);

        stopGrid(0);
        stopGrid(1);

        awaitPartitionMapExchange();

        assertWritesRejected(cache);
        assertAllKeysReadable(cache);

        startDataCenter(DC_ID_0, 0, 1);

        awaitPartitionMapExchange();

        cache.put(KEYS_CNT, KEYS_CNT);

        assertPartitionsSame(idleVerify(srv, CACHE_NAME));

        assertAllKeysReadable(grid(0).cache(CACHE_NAME));
    }

    /**
     * Checks that after the majority of data centers is lost the remaining one rejects writes but reads every key
     * written before.
     */
    @Test
    public void testReadAfterMajorityLoss() throws Exception {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1, DC_ID_2));

        IgniteEx srv = startDataCenter(DC_ID_0, 0, 1);
        startDataCenter(DC_ID_1, 2, 3);
        startDataCenter(DC_ID_2, 4, 5);

        waitForTopology(6);

        srv.cluster().state(ClusterState.ACTIVE);

        IgniteCache<Integer, Integer> cache = srv.createCache(partitionedCacheConfiguration(topValidator, 3, 2));

        for (int i = 0; i < KEYS_CNT; i++)
            cache.put(i, i);

        for (int i = 2; i < 6; i++)
            stopGrid(i);

        awaitPartitionMapExchange();

        assertWritesRejected(cache);
        assertAllKeysReadable(cache);
    }

    /**
     * Checks that majority mode counts only the configured data centers: a side holding one of three configured
     * data centers and a data center outside the set has no majority.
     */
    @Test
    @Ignore("https://issues.apache.org/jira/browse/IGNITE-TBD")
    public void testMajorityIgnoresDcOutsideConfiguredSet() throws Exception {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1, DC_ID_2));

        startDataCenter(DC_ID_0, 0);
        IgniteEx srv = startDataCenter(DC_ID_1, 1);
        startDataCenter(DC_ID_OUTSIDE, 2);

        waitForTopology(3);

        srv.cluster().state(ClusterState.ACTIVE);

        IgniteCache<Object, Object> cache = srv.createCache(new CacheConfiguration<>(CACHE_NAME)
            .setTopologyValidator(topValidator)
            .setCacheMode(CacheMode.REPLICATED));

        cache.put(KEY, VAL);

        stopGrid(0);

        awaitPartitionMapExchange();

        GridTestUtils.assertThrows(log, () -> cache.put(KEY, VAL + 1), IgniteException.class, "cache topology is not valid");
    }

    /**
     * Checks that the configuration check rejects an even set of data centers without a main one: after a split
     * between two halves neither of them could write.
     */
    @Test
    @Ignore("https://issues.apache.org/jira/browse/IGNITE-TBD")
    public void testEvenDcsWithoutMain() {
        MdcTopologyValidator topValidator = new MdcTopologyValidator();

        topValidator.setDatacenters(Set.of(DC_ID_0, DC_ID_1));

        GridTestUtils.assertThrows(log, () -> createClusterWithCache(topValidator, false), CacheException.class, null);
    }

    /**
     * Starts servers in a data center.
     *
     * @param dcId Data center ID.
     * @param idxs Node indexes.
     * @return The first started node.
     */
    private IgniteEx startDataCenter(String dcId, int... idxs) throws Exception {
        System.setProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID, dcId);

        IgniteEx first = null;

        for (int idx : idxs) {
            IgniteEx ignite = startGrid(idx);

            if (first == null)
                first = ignite;
        }

        return first;
    }

    /**
     * @param topValidator Topology validator.
     * @param dcsNum Number of data centers.
     * @param backups Number of backups, one copy per data center.
     * @return Configuration of a partitioned cache keeping a copy of every partition in each data center.
     */
    private CacheConfiguration<Integer, Integer> partitionedCacheConfiguration(
        TopologyValidator topValidator,
        int dcsNum,
        int backups
    ) {
        return new CacheConfiguration<Integer, Integer>(CACHE_NAME)
            .setTopologyValidator(topValidator)
            .setCacheMode(CacheMode.PARTITIONED)
            .setBackups(backups)
            .setAffinity(new RendezvousAffinityFunction(false, 64)
                .setAffinityBackupFilter(new MdcAffinityBackupFilter(dcsNum, backups)));
    }

    /** */
    private void assertWritesRejected(IgniteCache<Integer, Integer> cache) {
        GridTestUtils.assertThrows(log, () -> cache.put(0, -1), IgniteException.class, "cache topology is not valid");
        GridTestUtils.assertThrows(log, () -> cache.put(KEYS_CNT, KEYS_CNT), IgniteException.class, "cache topology is not valid");
    }

    /** */
    private void assertAllKeysReadable(IgniteCache<Integer, Integer> cache) {
        for (int i = 0; i < KEYS_CNT; i++)
            assertEquals("Unexpected value of key " + i, (Integer)i, cache.get(i));
    }

    /** */
    private IgniteCache<Object, Object> createClusterWithCache(TopologyValidator topValidator, boolean setDc) throws Exception {
        if (setDc)
            System.setProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID, DC_ID_0);

        IgniteEx srv0 = startGrid(0);

        if (setDc)
            System.setProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID, DC_ID_1);

        startGrid(1);

        if (setDc)
            System.setProperty(IgniteSystemProperties.IGNITE_DATA_CENTER_ID, DC_ID_2);

        startGrid(2);

        waitForTopology(3);

        srv0.cluster().state(ClusterState.ACTIVE);

        CacheConfiguration<Object, Object> cfgCache = new CacheConfiguration<>("cache")
            .setTopologyValidator(topValidator)
            .setCacheMode(CacheMode.REPLICATED);

        return srv0.createCache(cfgCache);
    }
}
