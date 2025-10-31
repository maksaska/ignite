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

package org.apache.ignite.internal.ducktest.tests.compute;

import java.io.Externalizable;
import java.io.IOException;
import java.io.ObjectInput;
import java.io.ObjectOutput;
import java.lang.reflect.Field;
import com.fasterxml.jackson.databind.JsonNode;
import org.apache.ignite.internal.ducktest.utils.IgniteAwareApplication;

/**
 * Application generates custom exceptions via {@code Ignite#compute()} API.
 */
public class ExceptionGenerationApplication extends IgniteAwareApplication {
    /** {@inheritDoc} */
    @Override protected void run(JsonNode jsonNode) throws Exception {
        markInitialized();

//        ignite.compute()

        markFinished();
    }

    /** Custom {@link Externalizable} Exception */
    protected static class SerializableException extends Exception {
        /** */
        protected int code;

        /** */
        protected String details;

        /** */
        public SerializableException() {
        }

        /** */
        public SerializableException(String msg, int code, String details) {
            super(msg);

            this.code = code;
            this.details = details;
        }

        /** */
        public int getCode() {
            return code;
        }

        /** */
        public void setCode(int code) {
            this.code = code;
        }

        /** */
        public String getDetails() {
            return details;
        }

        /** */
        public void setDetails(String details) {
            this.details = details;
        }
    }

    /** Custom {@link Externalizable} Exception */
    protected static class ExternalizableException extends SerializableException implements Externalizable {
        /** */
        public ExternalizableException() {
        }

        /** */
        public ExternalizableException(String msg, int code, String details) {
            super(msg, code, details);
        }

        /** {@inheritDoc} */
        @Override public void writeExternal(ObjectOutput out) throws IOException {
            out.writeObject(getMessage());
            out.writeInt(code);
            out.writeObject(details);
            out.writeObject(getStackTrace());
            out.writeObject(getCause());

            Throwable[] suppressed = getSuppressed();

            out.writeInt(suppressed.length);

            for (Throwable t : suppressed)
                out.writeObject(t);
        }

        /** {@inheritDoc} */
        @Override public void readExternal(ObjectInput in) throws IOException, ClassNotFoundException {
            String msg = (String)in.readObject();

            try {
                Field detailMsg = Throwable.class.getDeclaredField("detailMessage");

                detailMsg.setAccessible(true);
                detailMsg.set(this, msg);
            }
            catch (Exception ignored) {
                // No-op.
            }

            code = in.readInt();
            details = (String)in.readObject();

            setStackTrace((StackTraceElement[])in.readObject());

            Throwable cause = (Throwable) in.readObject();

            if (cause != null)
                initCause(cause);

            int suppressedLen = in.readInt();

            for (int i = 0; i < suppressedLen; i++)
                addSuppressed((Throwable)in.readObject());
        }
    }
}


