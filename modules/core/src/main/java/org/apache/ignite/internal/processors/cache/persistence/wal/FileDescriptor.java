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

package org.apache.ignite.internal.processors.cache.persistence.wal;

import java.io.File;
import java.io.IOException;
import org.apache.ignite.internal.processors.cache.persistence.file.FileIO;
import org.apache.ignite.internal.processors.cache.persistence.file.FileIOFactory;
import org.apache.ignite.internal.processors.cache.persistence.file.UnzipFileIO;
import org.apache.ignite.internal.processors.cache.persistence.filename.NodeFileTree;
import org.apache.ignite.internal.processors.cache.persistence.wal.io.SegmentIO;
import org.apache.ignite.internal.util.typedef.internal.U;
import org.jetbrains.annotations.Nullable;

import static java.nio.file.StandardOpenOption.READ;
import static org.apache.ignite.internal.processors.cache.persistence.filename.NodeFileTree.WAL_SEGMENT_FILE_EXT;

/**
 * WAL file descriptor.
 */
public class FileDescriptor implements Comparable<FileDescriptor>, AbstractWalRecordsIterator.AbstractFileDescriptor {
    /** File represented by this class. */
    protected final File file;

    /** Absolute WAL segment file index. */
    protected final long idx;

    /**
     * Creates file descriptor. Index is restored from file name.
     *
     * @param file WAL segment file.
     */
    public FileDescriptor(File file) {
        this(file, null);
    }

    /**
     *  Creates file descriptor.
     *
     * @param file WAL segment file.
     * @param idx Absolute WAL segment file index. For null value index is restored from file name.
     */
    public FileDescriptor(File file, @Nullable Long idx) {
        this.file = file;

        String fileName = file.getName();

        assert fileName.contains(WAL_SEGMENT_FILE_EXT);

        this.idx = idx == null ? U.fixedLengthFileNumber(fileName) : idx;
    }

    /** {@inheritDoc} */
    @Override public int compareTo(FileDescriptor o) {
        return Long.compare(idx, o.idx);
    }

    /** {@inheritDoc} */
    @Override public boolean equals(Object o) {
        if (this == o)
            return true;

        if (!(o instanceof FileDescriptor))
            return false;

        FileDescriptor that = (FileDescriptor)o;

        return idx == that.idx;
    }

    /** {@inheritDoc} */
    @Override public int hashCode() {
        return (int)(idx ^ (idx >>> 32));
    }

    /**
     * Return absolute WAL segment file index.
     *
     * @return Absolute WAL segment file index.
     */
    public long getIdx() {
        return idx;
    }

    /**
     * Return absolute pathname string of this file descriptor pathname.
     *
     * @return Absolute pathname string of this file descriptor pathname.
     */
    public String getAbsolutePath() {
        return file.getAbsolutePath();
    }

    /** {@inheritDoc} */
    @Override public boolean isCompressed() {
        return file.getName().endsWith(NodeFileTree.ZIP_SUFFIX);
    }

    /** {@inheritDoc} */
    @Override public File file() {
        return file;
    }

    /** {@inheritDoc} */
    @Override public long idx() {
        return idx;
    }

    /** {@inheritDoc} */
    @Override public SegmentIO toReadOnlyIO(FileIOFactory fileIOFactory) throws IOException {
        FileIO fileIO = isCompressed() ? new UnzipFileIO(file()) : fileIOFactory.create(file(), READ);

        return new SegmentIO(idx, fileIO);
    }

    /** {@inheritDoc} */
    @Override public String toString() {
        return getClass().getSimpleName() + " [file=" + (file == null ? null : file.getAbsolutePath()) + ", idx=" + idx + ']';
    }
}
