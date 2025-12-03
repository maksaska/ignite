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

package org.apache.ignite.internal.processors.service;

import org.apache.ignite.cluster.ClusterNode;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.lang.IgnitePredicate;
import org.apache.ignite.services.Service;
import org.apache.ignite.services.ServiceConfiguration;
import org.apache.ignite.testframework.junits.common.GridCommonAbstractTest;
import org.junit.Test;

/** */
public class GridServiceDeployWithAnonimousNodeFilterTest extends GridCommonAbstractTest {
    /** */
    private static final String SERVICE_NAME = "my-service";

    /** */
    @Test
    public void testServiceDeployWithAnonimousNodeFilter() throws Exception {
        try (IgniteEx ign = startGrid(0); IgniteEx client = startClientGrid(1)) {
            client.services().deploy(getServiceConfiguration());

            client.services().cancel(SERVICE_NAME);
        }
    }

    /** */
    private ServiceConfiguration getServiceConfiguration() {
        return new ServiceConfiguration()
            .setService(getAnonimousService())
            .setName(SERVICE_NAME)
            .setMaxPerNodeCount(1)
            .setNodeFilter(getAnonimousPredicate());
    }

    /** */
    private Service getAnonimousService() {
        return new Service() {
            /** {@inheritDoc} */
            @Override public void init() throws Exception {
                // No-op.
            }

            /** {@inheritDoc} */
            @Override public void execute() throws Exception {
                // No-op.
            }

            /** {@inheritDoc} */
            @Override public void cancel() {
                // No-op.
            }
        };
    }

    /** */
    private IgnitePredicate<ClusterNode> getAnonimousPredicate() {
        return new IgnitePredicate<ClusterNode>() {
            @Override public boolean apply(ClusterNode node) {
                return !node.isClient();
            }
        };
    }
}
