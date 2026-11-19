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

import java.util.ArrayList;
import java.util.List;
import org.apache.ignite.IgniteCache;
import org.apache.ignite.cache.query.SqlFieldsQuery;
import org.apache.ignite.cache.query.annotations.QuerySqlField;
import org.apache.ignite.calcite.CalciteQueryEngineConfiguration;
import org.apache.ignite.configuration.CacheConfiguration;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.configuration.SqlConfiguration;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.internal.processors.query.calcite.CalciteQueryProcessor;
import org.apache.ignite.internal.processors.query.calcite.GridCommonAbstractWrapperTest;
import org.apache.ignite.internal.processors.query.calcite.util.Commons;
import org.apache.ignite.internal.processors.query.running.SqlPlan;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import static org.apache.ignite.cache.CacheAtomicityMode.ATOMIC;
import static org.apache.ignite.cache.CacheWriteSynchronizationMode.PRIMARY_SYNC;

/**
 * Yardstick's select by an indexed salary range on a table without collected statistics. Calcite costs such a table by
 * one shared row count estimate, which starts at 1000 and is refreshed from the cluster on every 200th read. Under
 * load, a refresh can land in the middle of a planning; here the read counter is set so that it lands on read k + 1.
 */
public class RangeSelectTableScanReproducerTest extends GridCommonAbstractWrapperTest {
    /** */
    private static final int ROWS = 50;

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
    public void testRangeSelectUsesIndex() throws Exception {
        startGrids(3);

        IgniteEx client = startClientGrid(3);

        List<Integer> tableScans = new ArrayList<>();
        String tableScanPlan = null;

        for (int k = 1; k <= 15; k++) {
            String cacheName = "person" + k;

            IgniteCache<Integer, Person> cache = client.createCache(new CacheConfiguration<Integer, Person>(cacheName)
                .setAtomicityMode(ATOMIC)
                .setBackups(1)
                .setWriteSynchronizationMode(PRIMARY_SYNC)
                .setIndexedTypes(Integer.class, Person.class));

            for (int i = 0; i < ROWS; i++)
                cache.put(i, new Person(i, i * 1000));

            // Reads 1..k of the planning see the default 1000 rows, read k + 1 refreshes the estimate to the real 50.
            statistics(client, cacheName).cliReqCnt.set(200 - k);

            cache.query(new SqlFieldsQuery(SQL).setArgs(0, 1000)).getAll();

            String plan = plan(client, cacheName);

            log.info(">>> Refresh on read " + (k + 1) + ": "
                + (plan.contains("IgniteTableScan") ? "table scan\n" + plan : "index scan"));

            if (plan.contains("IgniteTableScan")) {
                tableScans.add(k + 1);
                tableScanPlan = plan;
            }

            client.destroyCache(cacheName);
        }

        assertTrue("A full table scan was chosen when the refresh landed on read " + tableScans + ", e.g.\n"
            + tableScanPlan, tableScans.isEmpty());
    }

    /** @return Statistics the client's Calcite costs the cache's table by. */
    private static IgniteStatisticsImpl statistics(IgniteEx client, String cacheName) {
        CalciteQueryProcessor proc = Commons.lookupComponent(client.context(), CalciteQueryProcessor.class);

        return (IgniteStatisticsImpl)proc.schemaHolder().schema(cacheName).getTable("PERSON").getStatistic();
    }

    /** @return The only plan the client used in the cache's schema. */
    private static String plan(IgniteEx client, String cacheName) {
        String plan = null;

        for (SqlPlan p : client.context().query().runningQueryManager().planHistoryTracker().sqlPlanHistory().keySet()) {
            if (cacheName.equals(p.schema())) {
                assertNull("More than one plan in " + cacheName, plan);

                plan = p.plan();
            }
        }

        assertNotNull("No plan in " + cacheName, plan);

        return plan;
    }

    /** Yardstick's person. */
    private static class Person {
        /** */
        @QuerySqlField(index = true)
        private final int id;

        /** */
        @QuerySqlField(index = true)
        private final int orgId;

        /** */
        @QuerySqlField
        private final String firstName;

        /** */
        @QuerySqlField
        private final String lastName;

        /** */
        @QuerySqlField(index = true)
        private final double salary;

        /** */
        Person(int id, double salary) {
            this.id = id;
            orgId = 0;
            firstName = "firstName" + id;
            lastName = "lastName" + id;
            this.salary = salary;
        }
    }
}
