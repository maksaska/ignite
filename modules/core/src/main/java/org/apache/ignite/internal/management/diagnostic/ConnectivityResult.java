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

package org.apache.ignite.internal.management.diagnostic;

import java.io.IOException;
import java.io.ObjectInput;
import java.io.ObjectOutput;
import java.util.Map;
import org.apache.ignite.cluster.ClusterNode;
import org.apache.ignite.internal.dto.IgniteDataTransferObject;
import org.apache.ignite.internal.util.typedef.internal.U;
import org.jetbrains.annotations.Nullable;

/**
 * Connectivity task result
 */
public class ConnectivityResult extends IgniteDataTransferObject {
    /** */
    private static final long serialVersionUID = 0L;

    /** */
    @Nullable private Map<ClusterNode, Boolean> nodeStatuses;

    /**
     * Default constructor.
     */
    public ConnectivityResult() {
    }

    /**
     * @param nodeStatuses Node statuses.
     */
    public ConnectivityResult(@Nullable Map<ClusterNode, Boolean> nodeStatuses) {
        this.nodeStatuses = nodeStatuses;
    }

    /** {@inheritDoc} */
    @Override protected void writeExternalData(ObjectOutput out) throws IOException {
        U.writeMap(out, nodeStatuses);
    }

    /** {@inheritDoc} */
    @Override protected void readExternalData(ObjectInput in) throws IOException, ClassNotFoundException {
        nodeStatuses = U.readMap(in);
    }

    /**
     * Get connectivity statuses for a node
     */
    public @Nullable Map<ClusterNode, Boolean> getNodeIds() {
        return nodeStatuses;
    }

}
