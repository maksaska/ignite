/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements. See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License. You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.ignite.internal.client.impl;

import java.util.Collection;
import java.util.UUID;
import org.apache.ignite.cluster.ClusterState;
import org.apache.ignite.internal.client.GridClientClusterState;
import org.apache.ignite.internal.client.GridClientException;
import org.apache.ignite.internal.client.GridClientNode;
import org.apache.ignite.internal.client.GridClientPredicate;
import org.apache.ignite.internal.client.balancer.GridClientLoadBalancer;
import org.apache.ignite.internal.client.impl.connection.GridClientConnection;
import org.apache.ignite.internal.visor.VisorTaskArgument;
import org.apache.ignite.internal.visor.VisorTaskResult;
import org.apache.ignite.internal.visor.misc.VisorIdAndTagViewTask;
import org.apache.ignite.internal.visor.misc.VisorIdAndTagViewTaskResult;

/**
 *
 */
public class GridClientClusterStateImpl extends GridClientAbstractProjection<GridClientClusterStateImpl>
    implements GridClientClusterState {
    /**
     * Closure to execute Cluster ID and Tag view action on cluster.
     */
    private static final ClientProjectionClosure<VisorTaskResult<VisorIdAndTagViewTaskResult>> ID_AND_TAG_VIEW_CL =
        (conn, nodeId) -> conn.execute(
            VisorIdAndTagViewTask.class.getName(),
            new VisorTaskArgument<>(nodeId, null, false),
            nodeId,
            false
        );

    /**
     * Creates projection with specified client.
     *
     * @param client Client instance to use.
     * @param nodes Collections of nodes included in this projection.
     * @param filter Node filter to be applied.
     * @param balancer Balancer to use.
     */
    public GridClientClusterStateImpl(
        GridClientImpl client,
        Collection<GridClientNode> nodes,
        GridClientPredicate<? super GridClientNode> filter,
        GridClientLoadBalancer balancer
    ) {
        super(client, nodes, filter, balancer);
    }

    /** {@inheritDoc} */
    @Override public ClusterState state() throws GridClientException {
        return withReconnectHandling(GridClientConnection::state).get();
    }

    /** {@inheritDoc} */
    @Override public void state(ClusterState newState, boolean forceDeactivation) throws GridClientException {
        withReconnectHandling((con, nodeId) -> con.changeState(newState, nodeId, forceDeactivation)).get();
    }

    /** {@inheritDoc} */
    @Override public UUID id() throws Exception {
        return withReconnectHandling(ID_AND_TAG_VIEW_CL).get().result().id();
    }

    /** {@inheritDoc} */
    @Override public String tag() throws Exception {
        return withReconnectHandling(ID_AND_TAG_VIEW_CL).get().result().tag();
    }

    /** {@inheritDoc} */
    @Override public String clusterName() throws GridClientException {
        return withReconnectHandling(GridClientConnection::clusterName).get();
    }
}
