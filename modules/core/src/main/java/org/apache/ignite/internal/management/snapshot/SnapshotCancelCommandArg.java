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

package org.apache.ignite.internal.management.snapshot;

import java.io.IOException;
import java.io.ObjectInput;
import java.io.ObjectOutput;
import java.util.UUID;
import org.apache.ignite.internal.management.api.Argument;
import org.apache.ignite.internal.management.api.ArgumentGroup;
import org.apache.ignite.internal.management.kill.SnapshotCancelTask.CancelSnapshotArg;
import org.apache.ignite.internal.util.typedef.internal.U;

/** */
@ArgumentGroup(value = {"id", "name"}, optional = false, onlyOneOf = true)
public class SnapshotCancelCommandArg extends CancelSnapshotArg {
    /** */
    private static final long serialVersionUID = 0;

    /** */
    @Argument(description = "Snapshot operation request ID", optional = true)
    private UUID id;

    /** */
    @Argument(description = "Snapshot name (deprecated)", optional = true)
    private String name;

    /** {@inheritDoc} */
    @Override protected void writeExternalData(ObjectOutput out) throws IOException {
        U.writeUuid(out, id);
        U.writeString(out, name);
    }

    /** {@inheritDoc} */
    @Override protected void readExternalData(ObjectInput in) throws IOException, ClassNotFoundException {
        id = U.readUuid(in);
        name = U.readString(in);
    }

    /** */
    public UUID id() {
        return id;
    }

    /** */
    public void id(UUID id) {
        this.id = id;
    }

    /** */
    public String name() {
        return name;
    }

    /** */
    public void name(String name) {
        this.name = name;
    }

    /** {@inheritDoc} */
    @Override public UUID requestId() {
        return id;
    }

    /** {@inheritDoc} */
    @Override public String snapshotName() {
        return name;
    }
}
