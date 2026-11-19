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

package org.apache.ignite.internal.processors.query.calcite.schema;

import java.util.concurrent.CyclicBarrier;
import org.apache.ignite.IgniteCache;
import org.apache.ignite.cache.query.SqlFieldsQuery;
import org.apache.ignite.cache.query.annotations.QuerySqlField;
import org.apache.ignite.calcite.CalciteQueryEngineConfiguration;
import org.apache.ignite.configuration.CacheConfiguration;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.configuration.SqlConfiguration;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.internal.processors.query.calcite.GridCommonAbstractWrapperTest;
import org.apache.ignite.internal.processors.query.running.SqlPlan;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import static org.apache.ignite.testframework.GridTestUtils.runMultiThreaded;

/**
 * Two threads send the same select by an indexed salary range at the same moment to a new table of 50 rows.
 * The plan is chosen once per table and then reused, so the test repeats this on new tables.
 */
public class RangeSelectTableScanReproducerTest extends GridCommonAbstractWrapperTest {
    /** */
    private static final int TABLES = 20;

    /** */
    private static final String SQL = "select _key, _val from Person where salary >= ? and salary <= ?";

    /** {@inheritDoc} */
    @Override protected IgniteConfiguration getConfiguration(String name) throws Exception {
        return super.getConfiguration(name).setSqlConfiguration(new SqlConfiguration()
            .setQueryEnginesConfiguration(new CalciteQueryEngineConfiguration().setDefault(true)));
    }

    /** */
    @AfterEach
    public void stopNodes() {
        stopAllGrids();
    }

    /** */
    @Test
    public void testConcurrentSelectsUseIndex() throws Exception {
        startGrids(3);

        IgniteEx client = startClientGrid(3);

        int tableScans = 0;
        String tableScanPlan = null;

        for (int t = 0; t < TABLES; t++) {
            String cacheName = "person" + t;

            IgniteCache<Integer, Person> cache = client.createCache(new CacheConfiguration<Integer, Person>(cacheName)
                .setIndexedTypes(Integer.class, Person.class));

            for (int i = 0; i < 50; i++)
                cache.put(i, new Person(i * 1000));

            CyclicBarrier start = new CyclicBarrier(2);

            runMultiThreaded(() -> {
                start.await();

                return cache.query(new SqlFieldsQuery(SQL).setArgs(0, 1000)).getAll();
            }, 2, "select");

            String plan = plan(client, cacheName);
            boolean tableScan = plan.contains("IgniteTableScan");

            log.info(">>> Table " + t + ": " + (tableScan ? "table scan" : "index scan"));

            if (tableScan) {
                tableScans++;
                tableScanPlan = plan;
            }

            client.destroyCache(cacheName);
        }

        assertEquals("Tables where the select chose a full table scan: " + tableScans + " of " + TABLES + ", e.g.\n"
            + tableScanPlan, 0, tableScans);
    }

    /** @return The plan the client used in the cache's schema. */
    private static String plan(IgniteEx client, String cacheName) {
        for (SqlPlan p : client.context().query().runningQueryManager().planHistoryTracker().sqlPlanHistory().keySet()) {
            if (cacheName.equals(p.schema()))
                return p.plan();
        }

        throw new AssertionError("No plan in " + cacheName);
    }

    /** */
    private static class Person {
        /** */
        @QuerySqlField(index = true)
        private final double salary;

        /** */
        Person(double salary) {
            this.salary = salary;
        }
    }
}
