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

package org.apache.ignite.internal.management.encryption;

import java.io.IOException;
import java.io.ObjectInput;
import java.io.ObjectOutput;
import org.apache.ignite.IgniteCheckedException;
import org.apache.ignite.internal.processors.cache.CacheGroupContext;
import org.apache.ignite.internal.processors.task.GridInternal;
import org.apache.ignite.internal.visor.VisorJob;
import org.jetbrains.annotations.Nullable;

/**
 * Suspend re-encryption of the cache group.
 */
@GridInternal
public class ReencryptionSuspendTask extends CacheGroupEncryptionTask<Boolean> {
    /** Serial version uid. */
    private static final long serialVersionUID = 0L;

    /** {@inheritDoc} */
    @Override protected VisorJob<EncryptionCacheGroupArg, SingleFieldDto<Boolean>> job(
        EncryptionCacheGroupArg arg) {
        return new ReencryptionSuspendJob(arg, debug);
    }

    /** The job to suspend re-encryption of the cache group. */
    private static class ReencryptionSuspendJob extends ReencryptionBaseJob<Boolean> {
        /** Serial version uid. */
        private static final long serialVersionUID = 0L;

        /**
         * @param arg Job argument.
         * @param debug Flag indicating whether debug information should be printed into node log.
         */
        protected ReencryptionSuspendJob(@Nullable EncryptionCacheGroupArg arg, boolean debug) {
            super(arg, debug);
        }

        /** {@inheritDoc} */
        @Override protected SingleFieldDto<Boolean> run0(CacheGroupContext grp) throws IgniteCheckedException {
            return new ReencryptionSuspendResumeJobResult().value(
                ignite.context().encryption().suspendReencryption(grp.groupId()));
        }
    }

    /** */
    protected static class ReencryptionSuspendResumeJobResult extends SingleFieldDto<Boolean> {
        /** Serial version uid. */
        private static final long serialVersionUID = 0L;

        /** */
        public ReencryptionSuspendResumeJobResult() {
            // No-op.
        }

        /** {@inheritDoc} */
        @Override protected void writeExternalData(ObjectOutput out) throws IOException {
            out.writeBoolean(value());
        }

        /** {@inheritDoc} */
        @Override protected void readExternalData(ObjectInput in) throws IOException, ClassNotFoundException {
            value(in.readBoolean());
        }
    }
}
