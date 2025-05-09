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

package org.apache.ignite.internal.processors.platform;

import org.apache.ignite.IgniteCheckedException;
import org.apache.ignite.internal.binary.BinaryReaderEx;
import org.apache.ignite.internal.binary.BinaryWriterEx;
import org.apache.ignite.internal.processors.platform.memory.PlatformMemory;
import org.jetbrains.annotations.Nullable;

/**
 * Interop target abstraction.
 */
public interface PlatformTarget {
    /**
     * Process IN operation.
     *
     * @param type Type.
     * @param val Value.
     * @return Result.
     * @throws IgniteCheckedException In case of exception.
     */
    long processInLongOutLong(int type, long val) throws IgniteCheckedException;

    /**
     * Process IN operation.
     *
     * @param type Type.
     * @param reader Binary reader.
     * @return Result.
     * @throws IgniteCheckedException In case of exception.
     */
    long processInStreamOutLong(int type, BinaryReaderEx reader) throws IgniteCheckedException;

    /**
     * Process IN operation.
     *
     * @param type Type.
     * @param reader Binary reader.
     * @return Result.
     * @throws IgniteCheckedException In case of exception.
     */
    long processInStreamOutLong(int type, BinaryReaderEx reader, PlatformMemory mem) throws IgniteCheckedException;

    /**
     * Process IN-OUT operation.
     *
     * @param type Type.
     * @param reader Binary reader.
     * @param writer Binary writer.
     * @throws IgniteCheckedException In case of exception.
     */
    void processInStreamOutStream(int type, BinaryReaderEx reader, BinaryWriterEx writer)
        throws IgniteCheckedException;

    /**
     * Process IN-OUT operation.
     *
     * @param type Type.
     * @param reader Binary reader.
     * @throws IgniteCheckedException In case of exception.
     */
    PlatformTarget processInStreamOutObject(int type, BinaryReaderEx reader) throws IgniteCheckedException;

    /**
     * Process IN-OUT operation.
     *
     * @param type Type.
     * @param arg Argument.
     * @param reader Binary reader.
     * @param writer Binary writer.
     * @throws IgniteCheckedException In case of exception.
     */
    PlatformTarget processInObjectStreamOutObjectStream(int type, @Nullable PlatformTarget arg, BinaryReaderEx reader,
        BinaryWriterEx writer) throws IgniteCheckedException;

    /**
     * Process OUT operation.
     *
     * @param type Type.
     * @param writer Binary writer.
     * @throws IgniteCheckedException In case of exception.
     */
    void processOutStream(int type, BinaryWriterEx writer) throws IgniteCheckedException;

    /**
     * Process OUT operation.
     *
     * @param type Type.
     * @throws IgniteCheckedException In case of exception.
     */
    PlatformTarget processOutObject(int type) throws IgniteCheckedException;

    /**
     * Process asynchronous operation.
     *
     * @param type Type.
     * @param reader Binary reader.
     * @return Async result (should not be null).
     * @throws IgniteCheckedException In case of exception.
     */
    PlatformAsyncResult processInStreamAsync(int type, BinaryReaderEx reader) throws IgniteCheckedException;

    /**
     * Convert caught exception.
     *
     * @param e Exception to convert.
     * @return Converted exception.
     */
    Exception convertException(Exception e);
}
