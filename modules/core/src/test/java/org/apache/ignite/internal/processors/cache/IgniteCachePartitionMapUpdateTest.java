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

package org.apache.ignite.internal.processors.cache;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Random;
import org.apache.ignite.cache.PartitionLossPolicy;
import org.apache.ignite.cluster.ClusterNode;
import org.apache.ignite.configuration.CacheConfiguration;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.internal.util.typedef.internal.U;
import org.apache.ignite.lang.IgnitePredicate;
import org.apache.ignite.testframework.junits.common.GridCommonAbstractTest;
import org.junit.Test;

import static org.apache.ignite.cache.CacheMode.PARTITIONED;

/**
 *
 */
public class IgniteCachePartitionMapUpdateTest extends GridCommonAbstractTest {
    /** */
    private static final String CACHE1_ATTR = "cache1";

    /** */
    private static final String CACHE2_ATTR = "cache2";

    /** */
    protected static final String CACHE1 = "cache1";

    /** */
    protected static final String CACHE2 = "cache2";

    /** */
    private boolean startClientCache;

    /** */
    private boolean cache1;

    /** */
    private boolean cache2;

    /** {@inheritDoc} */
    @Override protected IgniteConfiguration getConfiguration(String igniteInstanceName) throws Exception {
        IgniteConfiguration cfg = super.getConfiguration(igniteInstanceName);

        CacheConfiguration ccfg1 = new CacheConfiguration(CACHE1);

        ccfg1.setCacheMode(PARTITIONED);
        ccfg1.setBackups(1);
        ccfg1.setPartitionLossPolicy(policy());
        ccfg1.setNodeFilter(new AttributeFilter(CACHE1_ATTR));

        CacheConfiguration ccfg2 = new CacheConfiguration(CACHE2);

        ccfg2.setCacheMode(PARTITIONED);
        ccfg2.setPartitionLossPolicy(policy());
        ccfg2.setNodeFilter(new AttributeFilter(CACHE2_ATTR));

        List<CacheConfiguration> ccfgs = new ArrayList<>();

        Map<String, String> attrs = new HashMap<>();

        if (cache1)
            attrs.put(CACHE1_ATTR, "true");

        if (cache1 || startClientCache)
            ccfgs.add(ccfg1);

        if (cache2)
            attrs.put(CACHE2_ATTR, "true");

        if (cache2 || startClientCache)
            ccfgs.add(ccfg2);

        cfg.setUserAttributes(attrs);

        cfg.setCacheConfiguration(ccfgs.toArray(new CacheConfiguration[ccfgs.size()]));

        return cfg;
    }

    /** {@inheritDoc} */
    @Override protected void afterTest() throws Exception {
        super.afterTest();

        stopAllGrids();
    }

    /**
     * @throws Exception If failed.
     */
    @Test
    public void testPartitionMapUpdate1() throws Exception {
        cache1 = false;
        cache2 = false;

        startGrid(0);

        cache1 = true;
        cache2 = false;

        startGrid(1);

        awaitPartitionMapExchange();

        cache1 = false;
        cache2 = true;

        startGrid(2);

        cache1 = true;
        cache2 = true;

        startGrid(3);

        awaitPartitionMapExchange();

        stopGrid(0);

        awaitPartitionMapExchange();

        stopGrid(1);

        awaitPartitionMapExchange();

        stopGrid(2);

        awaitPartitionMapExchange();
    }

    /**
     * @throws Exception If failed.
     */
    @Test
    public void testPartitionMapUpdate2() throws Exception {
        startClientCache = true;

        testPartitionMapUpdate1();
    }

    /**
     * @throws Exception If failed.
     */
    @Test
    public void testRandom() throws Exception {
        Random rnd = new Random();

        final int NODE_CNT = 10;

        for (int iter = 0; iter < 1; iter++) {
            log.info("Iteration: " + iter);

            for (int i = 0; i < NODE_CNT; i++) {
                cache1 = rnd.nextBoolean();
                cache2 = rnd.nextBoolean();

                log.info("Start node [idx=" + i + ", cache1=" + cache1 + ", cache2=" + cache2 + ']');

                startGrid(i);

                awaitPartitionMapExchange();
            }

            LinkedHashSet<Integer> stopSeq = new LinkedHashSet<>();

            while (stopSeq.size() != NODE_CNT)
                stopSeq.add(rnd.nextInt(NODE_CNT));

            log.info("Stop sequence: " + stopSeq + ", seed=" + U.field(rnd, "seed"));

            for (Integer idx : stopSeq) {
                log.info("Stop node: " + idx);

                stopGrid(idx);

                awaitPartitionMapExchange();
            }
        }
    }

    /**
     * @throws Exception If failed.
     */
    @Test
    public void testRandom2() throws Exception {
        startClientCache = true;

        testRandom();
    }

    /**
     * @throws Exception If failed.
     */
    @Test
    public void testNodeLeft() throws Exception {
        cache1 = false;
        cache2 = false;

        startGrid(0);

        awaitPartitionMapExchange();

        cache1 = true;
        cache2 = true;

        startGrid(1);

        awaitPartitionMapExchange();

        cache1 = true;
        cache2 = true;

        startGrid(2);

        awaitPartitionMapExchange();

        stopGrid(1);

        awaitPartitionMapExchange();
    }

    /**
     * @throws Exception If failed.
     */
    @Test
    public void testNodeLeft2() throws Exception {
        startClientCache = true;

        testNodeLeft();
    }

    /**
     * @return Policy.
     */
    protected PartitionLossPolicy policy() {
        return PartitionLossPolicy.IGNORE;
    }

    /**
     *
     */
    static class AttributeFilter implements IgnitePredicate<ClusterNode> {
        /** */
        private String attrName;

        /**
         * @param attrName Attribute name.
         */
        public AttributeFilter(String attrName) {
            this.attrName = attrName;
        }

        /** {@inheritDoc} */
        @Override public boolean apply(ClusterNode node) {
            return Objects.equals(node.attribute(attrName), "true");
        }
    }
}
