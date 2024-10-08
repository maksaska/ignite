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

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.ObjectInputStream;
import java.io.ObjectOutputStream;
import java.util.HashMap;
import java.util.UUID;
import org.apache.ignite.IgniteCheckedException;
import org.apache.ignite.cluster.ClusterNode;
import org.apache.ignite.internal.util.typedef.internal.U;
import org.apache.ignite.lang.IgniteUuid;
import org.apache.ignite.marshaller.jdk.JdkMarshaller;
import org.apache.ignite.services.Service;
import org.apache.ignite.services.ServiceConfiguration;
import org.apache.ignite.services.ServiceContext;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotSame;

/**
 * Tests of {@link ServiceInfo} class.
 */
public class ServiceInfoSelfTest {
    /** Origin node id. */
    private UUID nodeId = UUID.randomUUID();

    /** Service id. */
    private IgniteUuid srvcId = IgniteUuid.randomUuid();

    /** Service configuration. */
    private ServiceConfiguration cfg = configuration();

    /** Subject under test. */
    private ServiceInfo sut = serviceInfo(cfg);

    /**
     * Tests {@link ServiceInfo#configuration()}.
     */
    @Test
    public void testConfigurationEquality() {
        assertEquals(cfg.getService().getClass(), sut.serviceClass());

        assertEquals(cfg.getName(), sut.name());

        assertEquals(cfg.getCacheName(), sut.cacheName());

        assertEquals(cfg.getAffinityKey(), sut.affinityKey());

        assertEquals(cfg.getService().getClass(), sut.serviceClass());
    }

    /**
     * Tests {@link ServiceInfo#originNodeId()}.
     */
    @Test
    public void testOriginNodeIdEquality() {
        assertEquals(nodeId, sut.originNodeId());
    }

    /**
     * Tests {@link ServiceInfo#serviceId()}.
     */
    @Test
    public void testServiceNodeEquality() {
        assertEquals(srvcId, sut.serviceId());
    }

    /**
     * Tests {@link ServiceInfo#topologySnapshot()}.
     */
    @Test
    public void testTopologySnapshotEquality() {
        assertEquals(new HashMap<>(), sut.topologySnapshot());

        HashMap<UUID, Integer> top = new HashMap<>();

        top.put(nodeId, 5);

        sut.topologySnapshot(top);

        assertEquals(top, sut.topologySnapshot());

        assertNotSame(top, sut.topologySnapshot());
    }

    /**
     * Tests serialization/deserialization of ServiceInfo.
     */
    @Test
    public void testSerializeDeserialize() throws Exception {
        ByteArrayOutputStream os = new ByteArrayOutputStream();

        new ObjectOutputStream(os).writeObject(sut);

        ObjectInputStream is = new ObjectInputStream(new ByteArrayInputStream(os.toByteArray()));

        ServiceInfo srvcInfo = (ServiceInfo)is.readObject();

        assertEquals(sut.name(), srvcInfo.name());
        assertEquals(sut.originNodeId(), srvcInfo.originNodeId());
        assertEquals(sut.serviceId(), srvcInfo.serviceId());
        assertEquals(sut.serviceClass(), srvcInfo.serviceClass());
    }

    /**
     * @return Service configuration.
     */
    private ServiceConfiguration configuration() {
        ServiceConfiguration cfg = new ServiceConfiguration();

        cfg.setName("testConfig");
        cfg.setTotalCount(10);
        cfg.setMaxPerNodeCount(3);
        cfg.setCacheName("testCacheName");
        cfg.setAffinityKey("testAffKey");
        cfg.setService(new TestService());
        cfg.setNodeFilter(ClusterNode::isLocal);

        return cfg;
    }

    /** */
    private ServiceInfo serviceInfo(ServiceConfiguration cfg) {
        try {
            JdkMarshaller marsh = new JdkMarshaller();

            byte[] srvcBytes = U.marshal(marsh, cfg.getService());
            byte[] nodeFilterBytes = U.marshal(marsh, cfg.getNodeFilter());
            byte[] interceptorsBytes = U.marshal(marsh, cfg.getInterceptors());

            LazyServiceConfiguration lazyCfg = new LazyServiceConfiguration(cfg, srvcBytes, nodeFilterBytes, interceptorsBytes);

            return new ServiceInfo(nodeId, srvcId, lazyCfg);
        }
        catch (IgniteCheckedException e) {
            throw new RuntimeException(e);
        }
    }

    /**
     * Tests service implementation.
     */
    private static class TestService implements Service {
        /** {@inheritDoc} */
        @Override public void cancel(ServiceContext ctx) {
            // No-op.
        }

        /** {@inheritDoc} */
        @Override public void init(ServiceContext ctx) throws Exception {
            // No-op.
        }

        /** {@inheritDoc} */
        @Override public void execute(ServiceContext ctx) throws Exception {
            // No-op.
        }
    }
}
