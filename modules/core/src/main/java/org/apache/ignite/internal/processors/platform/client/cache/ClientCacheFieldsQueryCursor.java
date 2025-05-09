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

package org.apache.ignite.internal.processors.platform.client.cache;

import java.util.List;
import org.apache.ignite.cache.query.FieldsQueryCursor;
import org.apache.ignite.internal.binary.BinaryWriterEx;
import org.apache.ignite.internal.processors.platform.client.ClientConnectionContext;

/**
 * Query cursor holder.
  */
class ClientCacheFieldsQueryCursor extends ClientCacheQueryCursor<List> {
    /** Column count. */
    private final int columnCount;

    /**
     * Ctor.
     *
     * @param cursor   Cursor.
     * @param pageSize Page size.
     * @param ctx      Context.
     */
    ClientCacheFieldsQueryCursor(FieldsQueryCursor<List> cursor, int pageSize, ClientConnectionContext ctx) {
        super(cursor, pageSize, ctx);

        columnCount = cursor.getColumnsCount();
    }

    /** {@inheritDoc} */
    @Override void writeEntry(BinaryWriterEx writer, List e) {
        assert e.size() >= columnCount : "Column count less then requested: " + e.size() + " < " + columnCount;

        // H2 engine can add extra columns at the end of result set.
        // See, GridH2ValueMessageFactory#toMessages
        // See ResultInterface#currentRow, ResultInterface#getVisibleColumnCount
        for (int i = 0; i < columnCount; i++)
            writer.writeObjectDetached(e.get(i));
    }
}
