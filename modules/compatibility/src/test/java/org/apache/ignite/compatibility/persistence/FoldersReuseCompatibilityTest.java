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

package org.apache.ignite.compatibility.persistence;

import java.io.Externalizable;
import java.io.File;
import java.io.IOException;
import java.io.ObjectInput;
import java.io.ObjectOutput;
import java.io.Serializable;
import java.util.Arrays;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Consumer;
import org.apache.ignite.Ignite;
import org.apache.ignite.IgniteCache;
import org.apache.ignite.compatibility.testframework.junits.SkipTestIfIsJdkNewer;
import org.apache.ignite.configuration.IgniteConfiguration;
import org.apache.ignite.configuration.MemoryConfiguration;
import org.apache.ignite.configuration.MemoryPolicyConfiguration;
import org.apache.ignite.configuration.PersistentStoreConfiguration;
import org.apache.ignite.internal.IgniteEx;
import org.apache.ignite.internal.processors.cache.GridCacheAbstractFullApiSelfTest;
import org.apache.ignite.internal.processors.cache.persistence.filename.NodeFileTree;
import org.apache.ignite.internal.processors.cache.persistence.filename.PdsFolderResolver;
import org.apache.ignite.internal.util.typedef.internal.U;
import org.apache.ignite.lang.IgniteInClosure;
import org.apache.ignite.spi.discovery.tcp.TcpDiscoverySpi;
import org.jetbrains.annotations.NotNull;
import org.junit.Test;

import static org.apache.ignite.internal.processors.cache.persistence.filename.PdsFolderResolver.parseSubFolderName;

/**
 * Test for new and old style persistent storage folders generation and compatible startup of current ignite version
 */
@SkipTestIfIsJdkNewer(8)
public class FoldersReuseCompatibilityTest extends IgnitePersistenceCompatibilityAbstractTest {
    /** Cache name for test. */
    private static final String CACHE_NAME = "dummy";

    /** Key to store in previous version of ignite */
    private static final String KEY = "StringFromPrevVersion";

    /** Value to store in previous version of ignite */
    private static final String VAL = "ValueFromPrevVersion";

    /** Key to store in previous version of ignite */
    private static final String KEY_OBJ = "ObjectFromPrevVersion";

    /** {@inheritDoc} */
    @Override protected IgniteConfiguration getConfiguration(String igniteInstanceName) throws Exception {
        final IgniteConfiguration cfg = super.getConfiguration(igniteInstanceName);

        configPersistence(cfg);

        return cfg;
    }

    /**
     * Test startup of current ignite version using DB storage folder from previous version of Ignite. Expected to start
     * successfully with existing DB
     *
     * @throws Exception if failed.
     */
    public void ignored_testFoldersReuseCompatibility_2_3() throws Exception {
        runFoldersReuse("2.3.0");
    }

    /**
     * Test startup of current ignite version using DB storage folder from previous version of Ignite. Expected to start
     * successfully with existing DB
     *
     * @throws Exception if failed.
     */
    @Test
    public void testFoldersReuseCompatibility_2_2() throws Exception {
        runFoldersReuse("2.2.0");
    }

    /**
     * Test startup of current ignite version using DB storage folder from previous version of Ignite. Expected to start
     * successfully with existing DB
     *
     * @throws Exception if failed.
     */
    @Test
    public void testFoldersReuseCompatibility_2_1() throws Exception {
        runFoldersReuse("2.1.0");
    }

    /**
     * Test startup of current ignite version using DB storage folder from previous version of Ignite. Expected to start
     * successfully with existing DB
     *
     * @param ver 3 digit Ignite version to check compatibility with
     * @throws Exception if failed.
     */
    private void runFoldersReuse(String ver) throws Exception {
        final IgniteEx oldVer = startGrid(1, ver, new ConfigurationClosure(), new PostStartupClosure());

        stopAllGrids();

        IgniteEx ignite = startGrid(0);

        ignite.active(true);
        ignite.getOrCreateCache("cache2createdForNewGrid").put("Object", "Value");
        assertEquals(1, ignite.context().discovery().topologyVersion());

        assertPdsDirsDefaultExist(U.maskForFileName(ignite.cluster().node().consistentId().toString()));

        assertEquals(VAL, ignite.cache(CACHE_NAME).get(KEY));

        final TestStringContainerToBePrinted actual =
            (TestStringContainerToBePrinted)ignite.cache(CACHE_NAME).get(KEY_OBJ);
        assertEquals(VAL, actual.data);

        assertNodeIndexesInFolder(); // should not create any new style directories

        stopAllGrids();
    }

    /** Started node test actions closure. */
    private static class PostStartupClosure implements IgniteInClosure<Ignite> {
        /** {@inheritDoc} */
        @Override public void apply(Ignite ignite) {
            ignite.active(true);

            final IgniteCache<Object, Object> cache = ignite.getOrCreateCache(CACHE_NAME);
            cache.put(KEY, VAL);
            cache.put("1", "2");
            cache.put(1, 2);
            cache.put(1L, 2L);
            cache.put(TestEnum.A, "Enum_As_Key");
            cache.put("Enum_As_Value", TestEnum.B);
            cache.put(TestEnum.C, TestEnum.C);

            cache.put("Serializable", new TestSerializable(42));
            cache.put(new TestSerializable(42), "Serializable_As_Key");
            cache.put("Externalizable", new TestExternalizable(42));
            cache.put(new TestExternalizable(42), "Externalizable_As_Key");
            cache.put(KEY_OBJ, new TestStringContainerToBePrinted(VAL));

        }
    }

    /** Setup compatible node closure. */
    private static class ConfigurationClosure implements IgniteInClosure<IgniteConfiguration> {
        /** {@inheritDoc} */
        @Override public void apply(IgniteConfiguration cfg) {
            cfg.setLocalHost("127.0.0.1");
            TcpDiscoverySpi disco = new TcpDiscoverySpi();
            disco.setIpFinder(GridCacheAbstractFullApiSelfTest.LOCAL_IP_FINDER);

            cfg.setDiscoverySpi(disco);

            configPersistence(cfg);
        }
    }

    /**
     * Setup persistence for compatible and current version node.
     *
     * @param cfg ignite config to setup.
     */
    private static void configPersistence(IgniteConfiguration cfg) {
        final PersistentStoreConfiguration psCfg = new PersistentStoreConfiguration();

        cfg.setPersistentStoreConfiguration(psCfg);

        final MemoryConfiguration memCfg = new MemoryConfiguration();
        final MemoryPolicyConfiguration memPolCfg = new MemoryPolicyConfiguration();

        memPolCfg.setMaxSize(32L * 1024 * 1024); // we don't need much memory for this test
        memCfg.setMemoryPolicies(memPolCfg);
        cfg.setMemoryConfiguration(memCfg);
    }

    /**
     * @param indexes expected new style node indexes in folders
     */
    private void assertNodeIndexesInFolder(Integer... indexes) {
        assertEquals(new TreeSet<>(Arrays.asList(indexes)), getAllNodeIndexesInFolder());
    }

    /**
     * @return set of all indexes of nodes found in work folder
     */
    @NotNull private Set<Integer> getAllNodeIndexesInFolder() {
        final File curFolder = sharedFileTree().db();
        final Set<Integer> indexes = new TreeSet<>();
        final File[] files = curFolder.listFiles(PdsFolderResolver.DB_SUBFOLDERS_NEW_STYLE_FILTER);

        for (File file : files) {
            final PdsFolderResolver.FolderCandidate uid
                = parseSubFolderName(file, log);

            if (uid != null)
                indexes.add(uid.nodeIndex());
        }

        return indexes;
    }

    /**
     * Checks existence of all storage-related directories
     *
     * @param subDirName sub directories name expected
     */
    private void assertPdsDirsDefaultExist(String subDirName) {
        NodeFileTree ft = nodeFileTree(subDirName);

        Consumer<File> check = dir -> assertTrue(dir.exists() && dir.isDirectory());

        check.accept(ft.binaryMeta());
        check.accept(ft.wal());
        check.accept(ft.walArchive());
        check.accept(ft.nodeStorage());
    }

    /** Enum for cover binaryObject enum save/load. */
    public enum TestEnum {
        /** */
        A,

        /** */
        B,

        /** */
        C
    }

    /** Special class to test WAL reader resistance to Serializable interface. */
    static class TestSerializable implements Serializable {
        /** */
        private static final long serialVersionUID = 0L;

        /** I value. */
        private int iVal;

        /**
         * Creates test object
         *
         * @param iVal I value.
         */
        TestSerializable(int iVal) {
            this.iVal = iVal;
        }

        /** {@inheritDoc} */
        @Override public String toString() {
            return "TestSerializable{" +
                "iVal=" + iVal +
                '}';
        }

        /** {@inheritDoc} */
        @Override public boolean equals(Object o) {
            if (this == o)
                return true;
            if (o == null || getClass() != o.getClass())
                return false;

            TestSerializable that = (TestSerializable)o;

            return iVal == that.iVal;
        }

        /** {@inheritDoc} */
        @Override public int hashCode() {
            return iVal;
        }
    }

    /** Special class to test WAL reader resistance to Serializable interface. */
    static class TestExternalizable implements Externalizable {
        /** */
        private static final long serialVersionUID = 0L;

        /** I value. */
        private int iVal;

        /** Noop ctor for unmarshalling */
        public TestExternalizable() {

        }

        /**
         * Creates test object with provided value.
         *
         * @param iVal I value.
         */
        public TestExternalizable(int iVal) {
            this.iVal = iVal;
        }

        /** {@inheritDoc} */
        @Override public String toString() {
            return "TestExternalizable{" +
                "iVal=" + iVal +
                '}';
        }

        /** {@inheritDoc} */
        @Override public void writeExternal(ObjectOutput out) throws IOException {
            out.writeInt(iVal);
        }

        /** {@inheritDoc} */
        @Override public void readExternal(ObjectInput in) throws IOException, ClassNotFoundException {
            iVal = in.readInt();
        }

        /** {@inheritDoc} */
        @Override public boolean equals(Object o) {
            if (this == o)
                return true;
            if (o == null || getClass() != o.getClass())
                return false;

            TestExternalizable that = (TestExternalizable)o;

            return iVal == that.iVal;
        }

        /** {@inheritDoc} */
        @Override public int hashCode() {
            return iVal;
        }
    }

    /** Container class to test toString of data records. */
    static class TestStringContainerToBePrinted {
        /** */
        String data;

        /**
         * Creates container.
         *
         * @param data value to be searched in to String.
         */
        public TestStringContainerToBePrinted(String data) {
            this.data = data;
        }

        /** {@inheritDoc} */
        @Override public boolean equals(Object o) {
            if (this == o)
                return true;
            if (o == null || getClass() != o.getClass())
                return false;

            TestStringContainerToBePrinted printed = (TestStringContainerToBePrinted)o;

            return data != null ? data.equals(printed.data) : printed.data == null;
        }

        /** {@inheritDoc} */
        @Override public int hashCode() {
            return data != null ? data.hashCode() : 0;
        }

        /** {@inheritDoc} */
        @Override public String toString() {
            return "TestStringContainerToBePrinted{" +
                "data='" + data + '\'' +
                '}';
        }
    }
}
