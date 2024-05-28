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

import java.util.Arrays;
import java.util.Collection;
import javax.cache.configuration.Factory;
import javax.cache.configuration.FactoryBuilder;
import javax.cache.expiry.CreatedExpiryPolicy;
import javax.cache.expiry.Duration;
import javax.cache.expiry.EternalExpiryPolicy;
import javax.cache.expiry.ExpiryPolicy;
import javax.cache.expiry.ModifiedExpiryPolicy;
import org.apache.ignite.configuration.CacheConfiguration;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.internal.processors.platform.cache.expiry.PlatformExpiryPolicyFactory;
import org.apache.ignite.spi.systemview.view.CacheView;
import org.apache.ignite.spi.systemview.view.SystemView;
import org.apache.ignite.testframework.junits.common.GridCommonAbstractTest;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.Parameterized;

import static java.util.concurrent.TimeUnit.MILLISECONDS;
import static org.apache.ignite.internal.processors.cache.ClusterCachesInfo.CACHES_VIEW;

/** Tests for {@link CacheView} expiry policy factory representation. */
@RunWith(Parameterized.class)
public class SystemViewCacheExpiryPolicyTest extends GridCommonAbstractTest {
    /** {@link Factory} instances for test with different expiry policy. */
    @SuppressWarnings("unchecked")
    private static final Factory[] TTL_FACTORIES = {
        null,
        new FactoryBuilder.SingletonFactory<ExpiryPolicy>(new EternalExpiryPolicy()),
        new FactoryBuilder.SingletonFactory<ExpiryPolicy>(new CreatedExpiryPolicy(new Duration(MILLISECONDS, 100L))),
        new FactoryBuilder.SingletonFactory<ExpiryPolicy>(new ModifiedExpiryPolicy(new Duration(MILLISECONDS, 5L))),
        new PlatformExpiryPolicyFactory(2, 4, 8),
        new PlatformExpiryPolicyFactory(1, -2, -1),
        new PlatformExpiryPolicyFactory(-1, 0, -1),
        new PlatformExpiryPolicyFactory(0, 1, -1)
    };

    /** {@link Factory} instance. */
    @Parameterized.Parameter
    public Factory<ExpiryPolicy> factory;

    /** Anticipated {@link String} expiry policy factory representation. */
    @Parameterized.Parameter(1)
    public String actual;

    /**
     * @return Test parameters.
     */
    @Parameterized.Parameters(name = "factory={0}, actual={1}")
    public static Collection parameters() {
        return Arrays.asList(new Object[][] {
            {TTL_FACTORIES[0], "Eternal"},
            {TTL_FACTORIES[1], "Eternal"},
            {TTL_FACTORIES[2], "[create=100 MILLISECONDS]"},
            {TTL_FACTORIES[3], "[create=5 MILLISECONDS][update=5 MILLISECONDS]"},
            {TTL_FACTORIES[4], "[create=2 MILLISECONDS][update=4 MILLISECONDS][access=8 MILLISECONDS]"},
            {TTL_FACTORIES[5], "[create=1 MILLISECONDS]"},
            {TTL_FACTORIES[6], "Eternal"},
            {TTL_FACTORIES[7], "[update=1 MILLISECONDS]"}
        });
    }

    /**
     * Test for {@link CacheView} expiry policy factory representation. The test initializes the {@link CacheConfiguration}
     * with custom {@link PlatformExpiryPolicyFactory}. Given different ttl input, the test checks the {@link String}
     * expiry policy factory outcome for {@link CacheView#expiryPolicyFactory()}.
     */
    @Test
    public void testCacheViewExpiryPolicy() throws Exception {
        try (IgniteEx g = startGrid()) {
            CacheConfiguration<Integer, Integer> ccfg = new CacheConfiguration<>();
            ccfg.setName("cache");
            ccfg.setExpiryPolicyFactory(factory);

            g.getOrCreateCache(ccfg);

            SystemView<CacheView> caches = g.context().systemView().view(CACHES_VIEW);

            for (CacheView row : caches)
                if (row.cacheName().equals("cache")) {
                    log.info(row.expiryPolicyFactory());
                    assertEquals(actual, row.expiryPolicyFactory());
                }
        }
    }
}
