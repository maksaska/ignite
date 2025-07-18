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

package org.apache.ignite.internal.management;

import java.util.function.Consumer;
import org.apache.ignite.Ignite;
import org.apache.ignite.client.IgniteClient;
import org.apache.ignite.cluster.ClusterState;
import org.apache.ignite.internal.client.thin.ClientClusterImpl;
import org.apache.ignite.internal.cluster.IgniteClusterEx;
import org.apache.ignite.internal.management.api.NativeCommand;
import org.jetbrains.annotations.Nullable;

/** */
public class SetStateCommand implements NativeCommand<SetStateCommandArg, Boolean> {
    /** {@inheritDoc} */
    @Override public String description() {
        return "Change cluster state";
    }

    /** {@inheritDoc} */
    @Override public Class<SetStateCommandArg> argClass() {
        return SetStateCommandArg.class;
    }

    /** {@inheritDoc} */
    @Override public Boolean execute(
        @Nullable IgniteClient client,
        @Nullable Ignite ignite,
        SetStateCommandArg arg,
        Consumer<String> printer
    ) {
        ClusterState clusterState = client != null
            ? client.cluster().state()
            : ignite.cluster().state();

        if (clusterState == arg.state()) {
            printer.accept("Cluster state is already " + arg.state() + '.');

            return false;
        }

        if (ignite != null)
            ((IgniteClusterEx)ignite.cluster()).state(arg.state(), arg.force());
        else
            ((ClientClusterImpl)client.cluster()).state(arg.state(), arg.force());

        printer.accept("Cluster state changed to " + arg.state() + '.');

        return true;
    }

    /** {@inheritDoc} */
    @Override public String confirmationPrompt(SetStateCommandArg arg) {
        return "Warning: the command will change state of cluster " +
            "with name \"" + arg.clusterName() + "\" to " + arg.state() + ".";
    }
}
