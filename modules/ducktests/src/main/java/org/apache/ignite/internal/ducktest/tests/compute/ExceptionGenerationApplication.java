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
import org.apache.ignite.Ignite;
import org.apache.ignite.IgniteCompute;
import org.apache.ignite.IgniteException;
import org.apache.ignite.internal.ducktest.utils.IgniteAwareApplication;
import org.apache.ignite.internal.util.lang.RunnableX;
import org.apache.ignite.lang.IgniteCallable;
import org.apache.ignite.lang.IgniteClosure;
import org.apache.ignite.lang.IgniteRunnable;

/**
 * Application generates custom exceptions via {@link Ignite#compute()} API.
 */
public class ExceptionGenerationApplication extends IgniteAwareApplication {
    /** */
    private static final String MSG = "Message from Exception";

    /** */
    private static final int CODE = 127;

    /** */
    private static final String DETAILS = "Details from Exception";

    /** */
    private static final SerializableException SER_EXCEPTION = new SerializableException(MSG, CODE, DETAILS);

    /** */
    private static final ExternalizableException EXT_EXCEPTION = new ExternalizableException(MSG, CODE, DETAILS);

    /** {@inheritDoc} */
    @Override protected void run(JsonNode jsonNode) throws Exception {
        markInitialized();

        IgniteCompute computeForRemotes = ignite.compute(ignite.cluster().forRemotes());

        assertThrows(() -> computeForRemotes.run(runnableWithExceptionThrown(false)), SER_EXCEPTION);
        assertThrows(() -> computeForRemotes.run(runnableWithExceptionThrown(true)), EXT_EXCEPTION);

        assertThrows(() -> computeForRemotes.call(callableWithExceptionThrown(false)), SER_EXCEPTION);
        assertThrows(() -> computeForRemotes.call(callableWithExceptionThrown(true)), EXT_EXCEPTION);

        assertThrows(() -> computeForRemotes.apply(closureWithExceptionThrown(false), DETAILS), SER_EXCEPTION);
        assertThrows(() -> computeForRemotes.apply(closureWithExceptionThrown(true), DETAILS), EXT_EXCEPTION);

        log.info("Exception generation finished successfully.");

        markFinished();
    }

    /** */
    private IgniteCallable<Void> callableWithExceptionThrown(boolean externalizable) {
        return () -> {
            runnableWithExceptionThrown(externalizable).run();

            return null;
        };
    }

    /** */
    private IgniteRunnable runnableWithExceptionThrown(boolean externalizable) {
        return () -> closureWithExceptionThrown(externalizable).apply(DETAILS);
    }

    /** */
    private IgniteClosure<String, Void> closureWithExceptionThrown(boolean externalizable) {
        return details -> {
            if (externalizable)
                throw new ExternalizableException(MSG, CODE, details);

            throw new SerializableException(MSG, CODE, details);
        };
    }

    /**
     * Checks whether runnable throws expected exception or not.
     *
     * @param run Runnable.
     * @param exp Expected exception
     */
    private <E extends SerializableException> void assertThrows(RunnableX run, E exp) {
        assert run != null;
        assert exp != null;

        try {
            run.run();
        }
        catch (Throwable e) {
            if (exp.getClass() == e.getClass() && e instanceof SerializableException) {
                SerializableException se = (SerializableException)e;

                if (exp.getMessage() != null && (e.getMessage() == null || !e.getMessage().contains(exp.getMessage()))) {
                    log.info("Unexpected exception message: [msg=" + e.getMessage() + ']');

                    throw new AssertionError("Unexpected message for thrown exception.", e);
                }

                if (se.getCode() == exp.getCode()) {
                    log.info("Unexpected exception code: [code=" + se.getCode() + ']');

                    throw new AssertionError("Unexpected code for thrown exception.", e);
                }

                if (exp.getDetails() != null && (se.getDetails() == null || !se.getDetails().contains(exp.getDetails()))) {
                    log.info("Unexpected exception detais: [details=" + se.getDetails() + ']');

                    throw new AssertionError("Unexpected details for thrown exception.", e);
                }

                if (e.getStackTrace() == null)
                    throw new AssertionError("Exception has no stacktrace.", e);

                return;
            }

            throw new AssertionError("Unexpected exception has been thrown.", e);
        }

        throw new AssertionError("Exception has not been thrown.");
    }

    /** Custom {@link Externalizable} Exception */
    protected static class SerializableException extends IgniteException {
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


