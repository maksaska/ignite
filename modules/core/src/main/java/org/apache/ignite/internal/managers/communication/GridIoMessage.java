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

package org.apache.ignite.internal.managers.communication;

import java.nio.ByteBuffer;
import org.apache.ignite.internal.ExecutorAwareMessage;
import org.apache.ignite.internal.GridDirectTransient;
import org.apache.ignite.internal.processors.cache.GridCacheMessage;
import org.apache.ignite.internal.processors.datastreamer.DataStreamerRequest;
import org.apache.ignite.internal.processors.tracing.messages.SpanTransport;
import org.apache.ignite.internal.util.tostring.GridToStringInclude;
import org.apache.ignite.internal.util.typedef.internal.S;
import org.apache.ignite.plugin.extensions.communication.Message;
import org.apache.ignite.plugin.extensions.communication.MessageReader;
import org.apache.ignite.plugin.extensions.communication.MessageWriter;
import org.jetbrains.annotations.Nullable;

/**
 * Wrapper for all grid messages.
 */
public class GridIoMessage implements Message, SpanTransport {
    /** */
    public static final Integer STRIPE_DISABLED_PART = Integer.MIN_VALUE;

    /** Policy. */
    private byte plc;

    /** Message topic. */
    @GridToStringInclude
    @GridDirectTransient
    private Object topic;

    /** Topic bytes. */
    private byte[] topicBytes;

    /** Topic ordinal. */
    private int topicOrd = -1;

    /** Message ordered flag. */
    private boolean ordered;

    /** Message timeout. */
    private long timeout;

    /** Whether message can be skipped on timeout. */
    private boolean skipOnTimeout;

    /** Message. */
    private Message msg;

    /** Serialized span */
    private byte[] span;

    /**
     * Default constructor.
     */
    public GridIoMessage() {
        // No-op.
    }

    /**
     * @param plc Policy.
     * @param topic Communication topic.
     * @param topicOrd Topic ordinal value.
     * @param msg Message.
     * @param ordered Message ordered flag.
     * @param timeout Timeout.
     * @param skipOnTimeout Whether message can be skipped on timeout.
     */
    public GridIoMessage(
        byte plc,
        Object topic,
        int topicOrd,
        Message msg,
        boolean ordered,
        long timeout,
        boolean skipOnTimeout
    ) {
        assert topic != null;
        assert topicOrd <= Byte.MAX_VALUE;
        assert msg != null;

        this.plc = plc;
        this.msg = msg;
        this.topic = topic;
        this.topicOrd = topicOrd;
        this.ordered = ordered;
        this.timeout = timeout;
        this.skipOnTimeout = skipOnTimeout;
    }

    /**
     * @return Policy.
     */
    byte policy() {
        return plc;
    }

    /**
     * @return Topic.
     */
    Object topic() {
        return topic;
    }

    /**
     * @param topic Topic.
     */
    void topic(Object topic) {
        this.topic = topic;
    }

    /**
     * @return Topic bytes.
     */
    byte[] topicBytes() {
        return topicBytes;
    }

    /**
     * @param topicBytes Topic bytes.
     */
    void topicBytes(byte[] topicBytes) {
        this.topicBytes = topicBytes;
    }

    /**
     * @return Topic ordinal.
     */
    int topicOrdinal() {
        return topicOrd;
    }

    /**
     * @return Message.
     */
    public Message message() {
        return msg;
    }

    /**
     * @return Message timeout.
     */
    public long timeout() {
        return timeout;
    }

    /**
     * @return Whether message can be skipped on timeout.
     */
    public boolean skipOnTimeout() {
        return skipOnTimeout;
    }

    /**
     * @return {@code True} if message is ordered, {@code false} otherwise.
     */
    boolean isOrdered() {
        return ordered;
    }

    /** {@inheritDoc} */
    @Override public void onAckReceived() {
        msg.onAckReceived();
    }

    /** {@inheritDoc} */
    @Override public boolean equals(Object obj) {
        throw new AssertionError();
    }

    /** {@inheritDoc} */
    @Override public int hashCode() {
        throw new AssertionError();
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
                if (!writer.writeMessage(msg))
                    return false;

                writer.incrementState();

            case 1:
                if (!writer.writeBoolean(ordered))
                    return false;

                writer.incrementState();

            case 2:
                if (!writer.writeByte(plc))
                    return false;

                writer.incrementState();

            case 3:
                if (!writer.writeBoolean(skipOnTimeout))
                    return false;

                writer.incrementState();

            case 4:
                if (!writer.writeByteArray(span))
                    return false;

                writer.incrementState();

            case 5:
                if (!writer.writeLong(timeout))
                    return false;

                writer.incrementState();

            case 6:
                if (!writer.writeByteArray(topicBytes))
                    return false;

                writer.incrementState();

            case 7:
                if (!writer.writeInt(topicOrd))
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
                msg = reader.readMessage();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 1:
                ordered = reader.readBoolean();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 2:
                plc = reader.readByte();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 3:
                skipOnTimeout = reader.readBoolean();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 4:
                span = reader.readByteArray();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 5:
                timeout = reader.readLong();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 6:
                topicBytes = reader.readByteArray();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

            case 7:
                topicOrd = reader.readInt();

                if (!reader.isLastRead())
                    return false;

                reader.incrementState();

        }

        return true;
    }

    /** {@inheritDoc} */
    @Override public short directType() {
        return 8;
    }

    /** {@inheritDoc} */
    @Override public void span(byte[] span) {
        this.span = span;
    }

    /** {@inheritDoc} */
    @Override public byte[] span() {
        return span;
    }

    /**
     * Get single partition for this message (if applicable).
     *
     * @return Partition ID.
     */
    public int partition() {
        if (msg instanceof GridCacheMessage)
            return ((GridCacheMessage)msg).partition();
        if (msg instanceof DataStreamerRequest)
            return ((DataStreamerRequest)msg).partition();
        else
            return STRIPE_DISABLED_PART;
    }

    /**
     * @return Executor name (if available).
     */
    @Nullable public String executorName() {
        if (msg instanceof ExecutorAwareMessage)
            return ((ExecutorAwareMessage)msg).executorName();

        return null;
    }

    /** {@inheritDoc} */
    @Override public String toString() {
        return S.toString(GridIoMessage.class, this);
    }
}
