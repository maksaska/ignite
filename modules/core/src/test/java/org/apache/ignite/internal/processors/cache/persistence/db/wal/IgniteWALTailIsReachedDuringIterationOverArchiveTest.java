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

package org.apache.ignite.internal.processors.cache.persistence.db.wal;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.LinkedList;
import java.util.List;
import java.util.Random;
import org.apache.ignite.Ignite;
import org.apache.ignite.IgniteCheckedException;
import org.apache.ignite.IgniteDataStreamer;
import org.apache.ignite.cluster.ClusterState;
import org.apache.ignite.configuration.CacheConfiguration;
import org.apache.ignite.configuration.DataRegionConfiguration;
import org.apache.ignite.configuration.DataStorageConfiguration;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.internal.pagemem.wal.IgniteWriteAheadLogManager;
import org.apache.ignite.internal.pagemem.wal.WALIterator;
import org.apache.ignite.internal.pagemem.wal.record.WALRecord;
import org.apache.ignite.internal.processors.cache.persistence.file.FileIO;
import org.apache.ignite.internal.processors.cache.persistence.file.FileIOFactory;
import org.apache.ignite.internal.processors.cache.persistence.file.RandomAccessFileIOFactory;
import org.apache.ignite.internal.processors.cache.persistence.filename.NodeFileTree;
import org.apache.ignite.internal.processors.cache.persistence.wal.FileDescriptor;
import org.apache.ignite.internal.processors.cache.persistence.wal.WALPointer;
import org.apache.ignite.internal.processors.cache.persistence.wal.reader.IgniteWalIteratorFactory;
import org.apache.ignite.internal.processors.cache.persistence.wal.reader.IgniteWalIteratorFactory.IteratorParametersBuilder;
import org.apache.ignite.lang.IgniteBiTuple;
import org.apache.ignite.testframework.junits.common.GridCommonAbstractTest;
import org.junit.Assert;
import org.junit.Test;

import static java.nio.ByteBuffer.allocate;
import static java.nio.file.StandardOpenOption.WRITE;
import static java.util.concurrent.ThreadLocalRandom.current;

/**
 *
 */
public class IgniteWALTailIsReachedDuringIterationOverArchiveTest extends GridCommonAbstractTest {
    /** WAL segment size. */
    private static final int WAL_SEGMENT_SIZE = 10 * 1024 * 1024;

    /** {@inheritDoc} */
    @Override protected IgniteConfiguration getConfiguration(String name) throws Exception {
        IgniteConfiguration cfg = super.getConfiguration(name);

        cfg.setDataStorageConfiguration(
            new DataStorageConfiguration()
                .setWalSegmentSize(WAL_SEGMENT_SIZE)
                .setWalSegments(2)
                .setDefaultDataRegionConfiguration(
                    new DataRegionConfiguration()
                        .setPersistenceEnabled(true)
                )
        );

        cfg.setCacheConfiguration(new CacheConfiguration(DEFAULT_CACHE_NAME));

        return cfg;
    }

    /** {@inheritDoc} */
    @Override protected void beforeTest() throws Exception {
        super.beforeTest();

        cleanPersistenceDir();

        Ignite ig = startGrid();

        ig.cluster().state(ClusterState.ACTIVE);

        try (IgniteDataStreamer<Integer, byte[]> st = ig.dataStreamer(DEFAULT_CACHE_NAME)) {
            st.allowOverwrite(true);

            byte[] payload = new byte[1024];

            // Generate WAL segment files.
            for (int i = 0; i < 100 * 1024; i++)
                st.addData(i, payload);
        }
    }

    /** {@inheritDoc} */
    @Override protected void afterTest() throws Exception {
        super.afterTest();

        stopAllGrids();

        cleanPersistenceDir();
    }

    /**
     * @throws Exception If failed.
     */
    @Test
    public void testStandAloneIterator() throws Exception {
        IgniteEx ig = grid();

        IgniteWalIteratorFactory iterFactory = new IgniteWalIteratorFactory();

        doTest(ig.context().pdsFolderResolver().fileTree(), iterFactory.iterator(ig.context().pdsFolderResolver().fileTree().walArchive()));
    }

    /**
     * @throws Exception If failed.
     */
    @Test
    public void testWALManagerIterator() throws Exception {
        IgniteEx ig = grid();

        IgniteWriteAheadLogManager wal = ig.context().cache().context().wal();

        doTest(ig.context().pdsFolderResolver().fileTree(), wal.replay(null));
    }

    /**
     *
     * @param ft Node file tree.
     * @param it WAL iterator.
     * @throws IOException If IO exception.
     * @throws IgniteCheckedException If WAL iterator failed.
     */
    private void doTest(NodeFileTree ft, WALIterator it) throws IOException, IgniteCheckedException {
        IgniteWalIteratorFactory iterFactory = new IgniteWalIteratorFactory();

        List<FileDescriptor> descs = iterFactory.resolveWalFiles(
            new IteratorParametersBuilder()
                .filesOrDirs(ft.walArchive())
        );

        int maxIdx = descs.size() - 1;
        int minIdx = 1;

        int corruptedIdx = current().nextInt(minIdx, maxIdx);

        log.info("Corrupted segment with idx:" + corruptedIdx);

        WALPointer corruptedPtr = corruptedWAlSegmentFile(
            descs.get(corruptedIdx),
            new RandomAccessFileIOFactory(),
            iterFactory
        );

        log.info("Should fail on ptr " + corruptedPtr);

        WALPointer lastReadPtr = null;

        boolean ex = false;

        try (WALIterator it0 = it) {
            while (it0.hasNextX()) {
                IgniteBiTuple<WALPointer, WALRecord> tup = it0.nextX();

                lastReadPtr = tup.get1();
            }
        }
        catch (IgniteCheckedException e) {
            if (e.getMessage().contains("WAL tail reached in archive directory, WAL segment file is corrupted")
                || e.getMessage().contains("WAL tail reached not in the last available segment"))
                ex = true;
        }

        Assert.assertNotNull(lastReadPtr);

        if (!ex) {
            fail("Last read ptr=" + lastReadPtr + ", corruptedPtr=" + corruptedPtr);
        }
    }

    /**
     *
     * @param desc WAL segment descriptor.
     * @param ioFactory IO factory.
     * @param iteratorFactory Iterator factory.
     * @return Corrupted position/
     * @throws IOException If IO exception.
     * @throws IgniteCheckedException If iterator failed.
     */
    private WALPointer corruptedWAlSegmentFile(
        FileDescriptor desc,
        FileIOFactory ioFactory,
        IgniteWalIteratorFactory iteratorFactory
    ) throws IOException, IgniteCheckedException {
        LinkedList<WALPointer> pointers = new LinkedList<>();

        try (WALIterator it = iteratorFactory.iterator(desc.file())) {
            while (it.hasNext()) {
                IgniteBiTuple<WALPointer, WALRecord> tup = it.next();

                pointers.add(tup.get1());
            }
        }

        int pointToCorrupt = current().nextInt(pointers.size());

        WALPointer ptr = pointers.get(pointToCorrupt);

        int offset = ptr.fileOffset();

        // 20 pointer size, 8 idx, 4 offset, 4 length.
        ByteBuffer buf = allocate(20);

        Random r = new Random();

        // Corrupt record pointer.
        r.nextBytes(buf.array());

        try (FileIO io = ioFactory.create(desc.file(), WRITE)) {
            io.write(buf, offset + 1);

            io.force(true);
        }

        return ptr;
    }
}
