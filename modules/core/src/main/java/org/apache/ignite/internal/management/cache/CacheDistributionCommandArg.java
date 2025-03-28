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

package org.apache.ignite.internal.management.cache;

import java.io.IOException;
import java.io.ObjectInput;
import java.io.ObjectOutput;
import java.util.UUID;
import org.apache.ignite.internal.dto.IgniteDataTransferObject;
import org.apache.ignite.internal.management.api.Argument;
import org.apache.ignite.internal.management.api.CommandUtils;
import org.apache.ignite.internal.management.api.Positional;
import org.apache.ignite.internal.util.typedef.internal.U;

/** */
public class CacheDistributionCommandArg extends IgniteDataTransferObject {
    /** */
    private static final long serialVersionUID = 0;

    /** */
    @Positional
    @Argument(example = "nodeId|null")
    private String nodeIdOrNull;

    /** */
    @Positional
    @Argument(optional = true, example = "cacheName1,...,cacheNameN")
    private String[] caches;

    /** */
    private UUID nodeId;

    /** */
    @Argument(optional = true, example = "attrName1,...,attrNameN")
    private String[] userAttributes;

    /** {@inheritDoc} */
    @Override protected void writeExternalData(ObjectOutput out) throws IOException {
        U.writeString(out, nodeIdOrNull);
        U.writeArray(out, caches);
        U.writeUuid(out, nodeId);
        U.writeArray(out, userAttributes);
    }

    /** {@inheritDoc} */
    @Override protected void readExternalData(ObjectInput in) throws IOException, ClassNotFoundException {
        nodeIdOrNull = U.readString(in);
        caches = U.readArray(in, String.class);
        nodeId = U.readUuid(in);
        userAttributes = U.readArray(in, String.class);
    }

    /** */
    private void parse(String value) {
        if (!"null".equals(value))
            nodeId = CommandUtils.parseVal(value, UUID.class);
    }

    /** */
    public String nodeIdOrNull() {
        return nodeIdOrNull;
    }

    /** */
    public void nodeIdOrNull(String nodeIdOrNull) {
        this.nodeIdOrNull = nodeIdOrNull;

        parse(nodeIdOrNull);
    }

    /** */
    public String[] caches() {
        return caches;
    }

    /** */
    public void caches(String[] caches) {
        this.caches = caches;
    }

    /** */
    public UUID nodeId() {
        return nodeId;
    }

    /** */
    public void nodeId(UUID nodeId) {
        this.nodeId = nodeId;
    }

    /** */
    public String[] userAttributes() {
        return userAttributes;
    }

    /** */
    public void userAttributes(String[] userAttributes) {
        this.userAttributes = userAttributes;
    }
}
