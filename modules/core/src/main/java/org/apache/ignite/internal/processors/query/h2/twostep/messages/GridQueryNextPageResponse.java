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

package org.apache.ignite.internal.processors.query.h2.twostep.messages;

import java.io.Serializable;
import java.nio.ByteBuffer;
import java.util.Collection;
import org.apache.ignite.internal.GridDirectCollection;
import org.apache.ignite.internal.GridDirectTransient;
import org.apache.ignite.internal.IgniteCodeGeneratingFail;
import org.apache.ignite.internal.processors.affinity.AffinityTopologyVersion;
import org.apache.ignite.internal.util.typedef.internal.S;
import org.apache.ignite.plugin.extensions.communication.Message;
import org.apache.ignite.plugin.extensions.communication.MessageCollectionItemType;
import org.apache.ignite.plugin.extensions.communication.MessageReader;
import org.apache.ignite.plugin.extensions.communication.MessageWriter;

/**
 * Next page response.
 */
@IgniteCodeGeneratingFail
public class GridQueryNextPageResponse implements Message, Serializable {
    /** */
    private static final long serialVersionUID = 0L;

    /** */
    private long qryReqId;

    /** */
    private int segmentId;

    /** */
    private int qry;

    /** */
    private int page;

    /** */
    private int allRows;

    /** */
    private int cols;

    /** */
    @GridDirectCollection(Message.class)
    private Collection<Message> vals;

    /**
     * Note, columns count in plain row can differ from {@link #cols}.
     * See {@code org.apache.ignite.internal.processors.query.h2.twostep.msg.GridH2ValueMessageFactory#toMessages}.
     * See javadoc for {@code org.h2.result.ResultInterface#getVisibleColumnCount()} and {@code org.h2.result.ResultInterface#currentRow()}.
     */
    @GridDirectTransient
    private transient Collection<?> plainRows;

    /** */
    private AffinityTopologyVersion retry;

    /** Retry cause description. */
    private String retryCause;

    /** Last page flag. */
    private boolean last;

    /**
     * Empty constructor.
     */
    public GridQueryNextPageResponse() {
        // No-op.
    }

    /**
     * @param qryReqId Query request ID.
     * @param segmentId Index segment ID.
     * @param qry Query.
     * @param page Page.
     * @param allRows All rows count.
     * @param cols Number of columns in row.
     * @param vals Values for rows in this page added sequentially.
     * @param plainRows Not marshalled rows for local node.
     * @param last Last page flag.
     */
    public GridQueryNextPageResponse(long qryReqId, int segmentId, int qry, int page, int allRows, int cols,
        Collection<Message> vals, Collection<?> plainRows, boolean last) {
        assert vals != null ^ plainRows != null;
        assert cols > 0 : cols;

        this.qryReqId = qryReqId;
        this.segmentId = segmentId;
        this.qry = qry;
        this.page = page;
        this.allRows = allRows;
        this.cols = cols;
        this.vals = vals;
        this.plainRows = plainRows;
        this.last = last;
    }

    /**
     * @return Query request ID.
     */
    public long queryRequestId() {
        return qryReqId;
    }

    /**
     * @return Index segment ID.
     */
    public int segmentId() {
        return segmentId;
    }

    /**
     * @return Query.
     */
    public int query() {
        return qry;
    }

    /**
     * @return Page.
     */
    public int page() {
        return page;
    }

    /**
     * @return All rows.
     */
    public int allRows() {
        return allRows;
    }

    /**
     * @return Columns in row.
     */
    public int columns() {
        return cols;
    }

    /**
     * @return Values.
     */
    public Collection<Message> values() {
        return vals;
    }

    /**
     * @return Plain rows.
     */
    public Collection<?> plainRows() {
        return plainRows;
    }

    /** {@inheritDoc} */
    @Override public void onAckReceived() {
        // No-op.
    }

    /** {@inheritDoc} */
    @Override public boolean writeTo(ByteBuffer buf, MessageWriter writer) {
        writer.setBuffer(buf);

        if (!writer.isHeaderWritten()) {
            if (!writer.writeHeader(directType()))
                return false;

            writer.onHeaderWritten();
        }

        switch (writer.state()) {
            case 0:
                if (!writer.writeInt(allRows))
                    return false;

                writer.incrementState();

            case 1:
                if (!writer.writeInt(cols))
                    return false;

                writer.incrementState();

            case 2:
                if (!writer.writeInt(page))
                    return false;

                writer.incrementState();

            case 3:
                if (!writer.writeInt(qry))
                    return false;

                writer.incrementState();

            case 4:
                if (!writer.writeLong(qryReqId))
                    return false;

                writer.incrementState();

            case 5:
                if (!writer.writeCollection(vals, MessageCollectionItemType.MSG))
                    return false;

                writer.incrementState();

            case 6:
                if (!writer.writeAffinityTopologyVersion(retry))
                    return false;

                writer.incrementState();

            case 7:
                if (!writer.writeInt(segmentId))
                    return false;

                writer.incrementState();

            case 8:
                if (!writer.writeBoolean(last))
                    return false;

                writer.incrementState();

            case 9:
                if (!writer.writeString(retryCause))
                    return false;

                writer.incrementState();
        }

        return true;
    }

    /** {@inheritDoc} */
    @Override public boolean readFrom(ByteBuffer buf, MessageReader reader) {
        reader.setBuffer(buf);

        switch (reader.state()) {
            case 0:
                allRows = reader.readInt();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 1:
                cols = reader.readInt();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 2:
                page = reader.readInt();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 3:
                qry = reader.readInt();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 4:
                qryReqId = reader.readLong();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 5:
                vals = reader.readCollection(MessageCollectionItemType.MSG);

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 6:
                retry = reader.readAffinityTopologyVersion();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 7:
                segmentId = reader.readInt();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 8:
                last = reader.readBoolean();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 9:
                retryCause = reader.readString();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();
        }

        return true;
    }

    /** {@inheritDoc} */
    @Override public short directType() {
        return 109;
    }

    /**
     * @return Retry topology version.
     */
    public AffinityTopologyVersion retry() {
        return retry;
    }

    /**
     * @param retry Retry topology version.
     */
    public void retry(AffinityTopologyVersion retry) {
        this.retry = retry;
    }

    /**
     * @return Retry Ccause message.
     */
    public String retryCause() {
        return retryCause;
    }

    /**
     * @param retryCause Retry Ccause message.
     */
    public void retryCause(String retryCause) {
        this.retryCause = retryCause;
    }

    /**
     * @return Last page flag.
     */
    public boolean last() {
        return last;
    }

    /**
     * @param last Last page flag.
     */
    public void last(boolean last) {
        this.last = last;
    }

    /** {@inheritDoc} */
    @Override public String toString() {
        return S.toString(GridQueryNextPageResponse.class, this,
            "valsSize", vals != null ? vals.size() : 0,
            "rowsSize", plainRows != null ? plainRows.size() : 0);
    }
}
